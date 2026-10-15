from __future__ import annotations

from typing import Any

from planner import ExecutionPlan, PlanStep, validate_plan
from state_store import StateStore
from tool_registry import RetryableToolError, ToolExecutionError, ToolRegistry


class Orchestrator:
    def __init__(
        self,
        plan: ExecutionPlan | dict[str, Any],
        *,
        db_path: str | None = None,
        tool_registry: ToolRegistry | None = None,
        max_attempts: int = 3,
        transient_failure_step_ids: set[str] | None = None,
        run_id: str | None = None,
    ):
        self.plan = plan if isinstance(plan, ExecutionPlan) else ExecutionPlan.model_validate(plan)
        self.plan = validate_plan(self.plan)
        self.tool_registry = tool_registry or ToolRegistry()
        self.max_attempts = max_attempts
        self.transient_failure_step_ids = set(transient_failure_step_ids or ())
        self.state_store = StateStore(db_path)
        self.run_id = run_id if run_id is not None else self.state_store.create_run(self.plan.goal, self.plan)

    @classmethod
    def load_run(
        cls,
        run_id: str,
        *,
        db_path: str | None = None,
        tool_registry: ToolRegistry | None = None,
        max_attempts: int = 3,
    ) -> "Orchestrator":
        store = StateStore(db_path)
        run_data = store.get_run(run_id)
        orchestrator = cls(
            run_data["plan"],
            db_path=db_path,
            tool_registry=tool_registry,
            max_attempts=max_attempts,
            run_id=run_id,
        )
        return orchestrator

    def _ordered_steps(self) -> list[PlanStep]:
        return list(self.plan.steps)

    def _step_record(self, step_id: str) -> dict[str, Any]:
        return self.state_store.get_step(self.run_id, step_id)

    def _compute_run_status(self) -> str:
        steps = self.state_store.get_run(self.run_id)["steps"]
        if any(step["status"] == "failed" for step in steps):
            return "failed"
        if all(step["status"] == "completed" for step in steps):
            return "completed"
        if any(step["status"] == "awaiting_approval" for step in steps):
            return "awaiting_approval"
        if any(step["status"] == "running" for step in steps):
            return "running"
        return "pending"

    def step_ready(self, step: PlanStep) -> bool:
        if not step.depends_on:
            return True
        step_records = {item["id"]: item for item in self.state_store.get_run(self.run_id)["steps"]}
        for dependency_id in step.depends_on:
            dependency = step_records.get(dependency_id)
            if dependency is None:
                return False
            if dependency["status"] != "completed":
                return False
        return True

    def execute(self) -> dict[str, Any]:
        self.state_store.set_run_status(self.run_id, "running")

        while True:
            progress = False
            run_steps = self.state_store.get_run(self.run_id)["steps"]
            step_by_id = {step["id"]: step for step in run_steps}

            for step in self._ordered_steps():
                record = step_by_id.get(step.id)
                if record is None:
                    continue

                if record["status"] in {"completed", "failed"}:
                    continue

                if record["status"] == "awaiting_approval":
                    continue

                if not self.step_ready(step):
                    continue

                if step.requires_approval and record["approval_decision"] != "approved":
                    if record["status"] != "awaiting_approval":
                        self.state_store.set_step_status(
                            self.run_id,
                            step.id,
                            status="awaiting_approval",
                            approval_status="pending",
                            output={"approval_required": True, "reason": "Human approval required before execution."},
                        )
                    progress = True
                    continue

                if record["approval_decision"] == "rejected":
                    self.state_store.set_step_status(
                        self.run_id,
                        step.id,
                        status="failed",
                        error="Step rejected by human approval decision.",
                    )
                    progress = True
                    continue

                attempts = int(record["attempts"]) + 1
                self.state_store.set_step_status(
                    self.run_id,
                    step.id,
                    status="running",
                    attempts=attempts,
                    output={"tool": step.tool, "attempt": attempts},
                    error=None,
                )

                try:
                    tool_result = self.tool_registry.dispatch(step.tool, step, {"id": step.id})
                    self.state_store.set_step_status(
                        self.run_id,
                        step.id,
                        status="completed",
                        output=tool_result,
                        error=None,
                    )
                    self.state_store.set_run_status(self.run_id, self._compute_run_status())
                    progress = True
                except RetryableToolError as exc:
                    if step.id in self.transient_failure_step_ids and attempts < self.max_attempts:
                        self.state_store.set_step_status(
                            self.run_id,
                            step.id,
                            status="pending",
                            attempts=attempts,
                            error=str(exc),
                            output={"retry": True, "attempt": attempts},
                        )
                    else:
                        self.state_store.set_step_status(
                            self.run_id,
                            step.id,
                            status="failed",
                            attempts=attempts,
                            error=str(exc) if step.id not in self.transient_failure_step_ids else f"Retry limit reached: {exc}",
                        )
                    progress = True
                except ToolExecutionError as exc:
                    self.state_store.set_step_status(
                        self.run_id,
                        step.id,
                        status="failed",
                        attempts=attempts,
                        error=str(exc),
                    )
                    progress = True
                except Exception as exc:
                    self.state_store.set_step_status(
                        self.run_id,
                        step.id,
                        status="failed",
                        attempts=attempts,
                        error=str(exc),
                    )
                    progress = True

            run_state = self.state_store.get_run(self.run_id)
            if not progress:
                final_status = self._compute_run_status()
                self.state_store.set_run_status(self.run_id, final_status)
                break

            if all(step["status"] == "completed" for step in self.state_store.get_run(self.run_id)["steps"]):
                self.state_store.set_run_status(self.run_id, "completed")
                break

            if any(step["status"] == "failed" for step in self.state_store.get_run(self.run_id)["steps"]):
                self.state_store.set_run_status(self.run_id, "failed")
                break

        return self.state_store.get_run(self.run_id)

    def approve_step(self, run_id: str, step_id: str, approved: bool, reason: str | None = None) -> dict[str, Any]:
        if run_id != self.run_id:
            raise ValueError(f"Run ID mismatch: expected {self.run_id!r}, got {run_id!r}.")
        step = self.state_store.get_step(run_id, step_id)
        if not step:
            raise KeyError(f"Step {step_id!r} not found in run {run_id!r}.")

        updated = self.state_store.approve_step(run_id, step_id, approved, reason)
        if updated["status"] == "failed":
            self.state_store.set_run_status(run_id, "failed")
        else:
            self.state_store.set_run_status(run_id, "running")
        return self.state_store.get_run(run_id)


def _build_demo_plan() -> ExecutionPlan:
    return ExecutionPlan(
        goal="Find internships and prepare a draft outreach plan.",
        steps=[
            PlanStep(
                id="step_1",
                title="Research internship leads",
                description="Find candidate internships and shortlist opportunities.",
                tool="research",
            ),
            PlanStep(
                id="step_2",
                title="Save to spreadsheet",
                description="Persist shortlisted opportunities in a local spreadsheet dataset.",
                tool="spreadsheet",
                depends_on=["step_1"],
            ),
            PlanStep(
                id="step_3",
                title="Draft outreach emails",
                description="Write draft emails for selected internships.",
                tool="email_drafts",
                depends_on=["step_2"],
                requires_approval=True,
            ),
        ],
    )


if __name__ == "__main__":
    orchestrator = Orchestrator(_build_demo_plan(), db_path=".agent_state.db")
    result = orchestrator.execute()
    print(result["status"])
    for step in result["steps"]:
        print(step["id"], step["status"], step["output"])
