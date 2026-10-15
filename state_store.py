import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from planner import ExecutionPlan, validate_plan

DEFAULT_DB_PATH = Path(__file__).resolve().parent / ".agent_state.db"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize_output(payload: Any) -> Any:
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            lowered = str(key).lower()
            if "api" in lowered or "key" in lowered or "token" in lowered or "secret" in lowered:
                continue
            cleaned[key] = _sanitize_output(value)
        return cleaned
    if isinstance(payload, list):
        return [_sanitize_output(item) for item in payload]
    return payload


class StateStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_database(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_steps (
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    depends_on TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    output_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    approval_status TEXT NOT NULL DEFAULT 'pending',
                    approval_decision TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, step_id),
                    FOREIGN KEY (run_id) REFERENCES runs(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_steps_run_id ON run_steps(run_id)"
            )

    def create_run(self, goal: str, plan: ExecutionPlan) -> str:
        plan = validate_plan(plan)
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        created_at = _utc_timestamp()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (id, goal, plan_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, goal, plan.model_dump_json(), "pending", created_at, created_at),
            )
            for step in plan.steps:
                depends_on_json = json.dumps(step.depends_on)
                conn.execute(
                    """
                    INSERT INTO run_steps (
                        run_id, step_id, title, description, tool, depends_on,
                        requires_approval, status, attempts, output_json, error,
                        approval_status, approval_decision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        step.id,
                        step.title,
                        step.description,
                        step.tool,
                        depends_on_json,
                        int(step.requires_approval),
                        "pending",
                        0,
                        json.dumps({}),
                        None,
                        "pending" if step.requires_approval else "not_required",
                        None,
                        created_at,
                        created_at,
                    ),
                )
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, goal, plan_json, status, created_at, updated_at FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Run {run_id!r} was not found.")

            plan = ExecutionPlan.model_validate_json(row["plan_json"])
            steps = conn.execute(
                """
                SELECT run_id, step_id, title, description, tool, depends_on, requires_approval,
                       status, attempts, output_json, error, approval_status, approval_decision,
                       created_at, updated_at
                FROM run_steps WHERE run_id = ? ORDER BY step_id
                """,
                (run_id,),
            ).fetchall()

            step_rows = [self._row_to_step_dict(step) for step in steps]
            validate_plan(plan)
            return {
                "id": row["id"],
                "goal": row["goal"],
                "plan": plan,
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "steps": step_rows,
            }

    @staticmethod
    def _row_to_step_dict(row: sqlite3.Row) -> dict[str, Any]:
        output_json = row["output_json"] or "{}"
        output = json.loads(output_json)
        return {
            "run_id": row["run_id"],
            "id": row["step_id"],
            "title": row["title"],
            "description": row["description"],
            "tool": row["tool"],
            "depends_on": json.loads(row["depends_on"]),
            "requires_approval": bool(row["requires_approval"]),
            "status": row["status"],
            "attempts": row["attempts"],
            "output": output,
            "error": row["error"],
            "approval_status": row["approval_status"],
            "approval_decision": row["approval_decision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_step(self, run_id: str, step_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, step_id, title, description, tool, depends_on, requires_approval,
                       status, attempts, output_json, error, approval_status, approval_decision,
                       created_at, updated_at
                FROM run_steps WHERE run_id = ? AND step_id = ?
                """,
                (run_id, step_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Step {step_id!r} was not found in run {run_id!r}.")
            return self._row_to_step_dict(row)

    def set_run_status(self, run_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (status, _utc_timestamp(), run_id),
            )

    def set_step_status(
        self,
        run_id: str,
        step_id: str,
        *,
        status: str | None = None,
        attempts: int | None = None,
        output: Any | None = None,
        error: str | None = None,
        approval_status: str | None = None,
        approval_decision: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_timestamp()
        update_fields = ["updated_at = ?"]
        values: list[Any] = [now]

        if status is not None:
            update_fields.append("status = ?")
            values.append(status)
        if attempts is not None:
            update_fields.append("attempts = ?")
            values.append(attempts)
        if output is not None:
            update_fields.append("output_json = ?")
            values.append(json.dumps(_sanitize_output(output)))
        if error is not None:
            update_fields.append("error = ?")
            values.append(error)
        if approval_status is not None:
            update_fields.append("approval_status = ?")
            values.append(approval_status)
        if approval_decision is not None:
            update_fields.append("approval_decision = ?")
            values.append(approval_decision)

        values.extend([run_id, step_id])
        with self._connect() as conn:
            conn.execute(
                f"UPDATE run_steps SET {', '.join(update_fields)} WHERE run_id = ? AND step_id = ?",
                values,
            )

        return self.get_step(run_id, step_id)

    def approve_step(self, run_id: str, step_id: str, approved: bool, reason: str | None = None) -> dict[str, Any]:
        step = self.get_step(run_id, step_id)
        if not step["requires_approval"]:
            raise ValueError(f"Step {step_id!r} does not require human approval.")

        if approved:
            decision = "approved"
            status = "pending"
            message = reason or "Approved by user."
            output = {"approval": {"status": "approved", "reason": message}}
            self.set_step_status(
                run_id,
                step_id,
                status=status,
                approval_status="approved",
                approval_decision=decision,
                output=output,
                error=None,
            )
            return self.get_step(run_id, step_id)

        decision = "rejected"
        status = "failed"
        message = reason or "Rejected by user."
        self.set_step_status(
            run_id,
            step_id,
            status=status,
            approval_status="rejected",
            approval_decision=decision,
            output={"approval": {"status": "rejected", "reason": message}},
            error=message,
        )
        return self.get_step(run_id, step_id)

    def close(self) -> None:
        # sqlite3 closes per-connection automatically; this is a no-op kept for clarity.
        return None
