import json
from unittest.mock import patch

import pytest

from orchestrator import Orchestrator
from planner import ExecutionPlan, PlanStep
from tool_registry import ToolExecutionError, ToolRegistry


@pytest.fixture
def temp_db(tmp_path):
    return tmp_path / "agent_state.db"


@pytest.fixture
def sample_plan():
    return ExecutionPlan(
        goal="Find internships and prepare outreach.",
        steps=[
            PlanStep(
                id="step_1",
                title="Research internships",
                description="Find internship leads.",
                tool="research",
            ),
            PlanStep(
                id="step_2",
                title="Save results",
                description="Persist the shortlist.",
                tool="spreadsheet",
                depends_on=["step_1"],
            ),
            PlanStep(
                id="step_3",
                title="Draft outreach",
                description="Compose summarized draft emails.",
                tool="email_drafts",
                depends_on=["step_2"],
                requires_approval=True,
            ),
        ],
    )


def test_valid_plan_executes_in_dependency_order(sample_plan, temp_db):
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), max_attempts=2)
    result = orchestrator.execute()
    assert result["status"] == "awaiting_approval"
    step_1 = next(step for step in result["steps"] if step["id"] == "step_1")
    step_2 = next(step for step in result["steps"] if step["id"] == "step_2")
    step_3 = next(step for step in result["steps"] if step["id"] == "step_3")
    assert step_1["status"] == "completed"
    assert step_2["status"] == "completed"
    assert step_3["status"] == "awaiting_approval"


def test_unknown_tool_names_are_rejected(sample_plan, temp_db):
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.dispatch("python", sample_plan.steps[0], {})


def test_failed_prerequisite_blocks_dependent_steps(sample_plan, temp_db):
    registry = ToolRegistry(failure_map={"step_1": "simulate prereq failure"})
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), tool_registry=registry, max_attempts=2)
    result = orchestrator.execute()
    step_1 = next(step for step in result["steps"] if step["id"] == "step_1")
    step_2 = next(step for step in result["steps"] if step["id"] == "step_2")
    assert step_1["status"] == "failed"
    assert step_2["status"] == "pending"


def test_approval_required_step_stays_blocked(sample_plan, temp_db):
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), max_attempts=2)
    result = orchestrator.execute()
    step_3 = next(step for step in result["steps"] if step["id"] == "step_3")
    assert step_3["status"] == "awaiting_approval"
    assert step_3["approval_status"] == "pending"


def test_rejecting_step_prevents_execution(sample_plan, temp_db):
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), max_attempts=2)
    orchestrator.execute()
    result = orchestrator.approve_step(orchestrator.run_id, "step_3", approved=False, reason="User rejected draft send.")
    step_3 = next(step for step in result["steps"] if step["id"] == "step_3")
    assert step_3["status"] == "failed"
    assert step_3["approval_decision"] == "rejected"


def test_transient_spreadsheet_failure_retries_within_limit(sample_plan, temp_db):
    registry = ToolRegistry(failure_map={"step_2": "temporary spreadsheet failure"})
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), tool_registry=registry, max_attempts=2, transient_failure_step_ids={"step_2"})
    result = orchestrator.execute()
    step_2 = next(step for step in result["steps"] if step["id"] == "step_2")
    assert step_2["status"] == "completed"
    assert step_2["attempts"] >= 1


def test_retrying_upsert_does_not_duplicate_spreadsheet_rows(sample_plan, temp_db):
    registry = ToolRegistry()
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), tool_registry=registry, max_attempts=2)
    orchestrator.execute()
    assert len(registry.get_spreadsheet_rows()) == 1


def test_completed_steps_are_not_repeated_after_resume(sample_plan, temp_db):
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), max_attempts=2)
    first = orchestrator.execute()
    step_1 = next(step for step in first["steps"] if step["id"] == "step_1")
    assert step_1["status"] == "completed"

    resumed = Orchestrator.load_run(orchestrator.run_id, db_path=str(temp_db), max_attempts=2).execute()
    step_1_after = next(step for step in resumed["steps"] if step["id"] == "step_1")
    assert step_1_after["status"] == "completed"
    assert step_1_after["attempts"] == step_1["attempts"]


def test_execution_state_survives_db_reopen(sample_plan, temp_db):
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), max_attempts=2)
    orchestrator.execute()

    reopened = Orchestrator.load_run(orchestrator.run_id, db_path=str(temp_db), max_attempts=2)
    run = reopened.state_store.get_run(orchestrator.run_id)
    assert run["goal"] == sample_plan.goal
    assert any(step["id"] == "step_1" for step in run["steps"])


def test_invalid_plans_are_rejected():
    with pytest.raises(ValueError):
        ExecutionPlan(goal="bad", steps=[])


def test_api_keys_are_not_written_to_execution_state(sample_plan, temp_db):
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), max_attempts=2)
    result = orchestrator.execute()
    snapshot = json.dumps(result, default=str)
    assert "GROQ_API_KEY" not in snapshot
    assert "api_key" not in snapshot.lower()


def test_orchestrator_does_not_use_groq_for_each_step(sample_plan, temp_db):
    orchestrator = Orchestrator(sample_plan, db_path=str(temp_db), max_attempts=2)
    with patch("groq.Groq", side_effect=AssertionError("Groq should not be called by orchestrator")):
        orchestrator.execute()
