from __future__ import annotations

from typing import Any

ALLOWED_TOOLS = ("research", "spreadsheet", "email_drafts")


class ToolExecutionError(RuntimeError):
    pass


class RetryableToolError(ToolExecutionError):
    pass


class ToolRegistry:
    def __init__(self, failure_map: dict[str, str] | None = None):
        self.failure_map = failure_map or {}
        self._failure_attempts: dict[str, int] = {}
        self._spreadsheet_rows: list[dict[str, Any]] = []
        self._draft_rows: list[dict[str, Any]] = []

    def allowed_tools(self) -> tuple[str, ...]:
        return ALLOWED_TOOLS

    def get_spreadsheet_rows(self) -> list[dict[str, Any]]:
        return list(self._spreadsheet_rows)

    def clear(self) -> None:
        self._failure_attempts.clear()
        self._spreadsheet_rows.clear()
        self._draft_rows.clear()

    def dispatch(self, tool_name: str, step: Any, run_data: dict[str, Any] | None = None) -> dict[str, Any]:
        if tool_name not in ALLOWED_TOOLS:
            raise ValueError(
                f"Unknown tool '{tool_name}'. Allowed tools: {', '.join(ALLOWED_TOOLS)}."
            )

        step_id = getattr(step, "id", None)
        if step_id is None and isinstance(run_data, dict):
            step_id = run_data.get("id")

        if step_id and step_id in self.failure_map:
            current_attempt = self._failure_attempts.get(step_id, 0)
            if current_attempt < 1:
                self._failure_attempts[step_id] = current_attempt + 1
                raise RetryableToolError(self.failure_map[step_id])

        if tool_name == "research":
            return self._research_tool(step)
        if tool_name == "spreadsheet":
            return self._spreadsheet_tool(step)
        if tool_name == "email_drafts":
            return self._drafts_tool(step)
        raise ValueError(f"Unsupported tool '{tool_name}'.")

    def _research_tool(self, step: Any) -> dict[str, Any]:
        result = {
            "simulated": True,
            "tool": "research",
            "step_id": getattr(step, "id", "unknown"),
            "summary": "Mock research completed. This is a local simulation, not live web data.",
            "records": [
                {
                    "title": getattr(step, "title", "research step"),
                    "source": "mock-local-research",
                    "notes": "This output is intentionally simulated for orchestration testing.",
                }
            ],
        }
        return result

    def _spreadsheet_tool(self, step: Any) -> dict[str, Any]:
        payload = {
            "source_step_id": getattr(step, "id", "unknown"),
            "title": getattr(step, "title", "spreadsheet update"),
            "tool": "spreadsheet",
            "simulated": True,
            "status": "upserted",
        }

        existing_index = None
        for index, row in enumerate(self._spreadsheet_rows):
            if row.get("source_step_id") == payload["source_step_id"]:
                existing_index = index
                break

        if existing_index is None:
            self._spreadsheet_rows.append(payload)
        else:
            self._spreadsheet_rows[existing_index] = payload

        return {
            "simulated": True,
            "tool": "spreadsheet",
            "step_id": payload["source_step_id"],
            "summary": "Mock spreadsheet upsert completed idempotently.",
            "rows": list(self._spreadsheet_rows),
            "duplicate_protection": "idempotent-local-upsert",
        }

    def _drafts_tool(self, step: Any) -> dict[str, Any]:
        draft = {
            "simulated": True,
            "tool": "email_drafts",
            "step_id": getattr(step, "id", "unknown"),
            "status": "draft-created",
            "subject": f"Draft for {getattr(step, 'title', 'outreach task')}",
            "content": "This is a local mock email draft. No real email was sent.",
        }
        self._draft_rows.append(draft)
        return {
            "simulated": True,
            "tool": "email_drafts",
            "step_id": draft["step_id"],
            "summary": "Mock email drafts created locally without sending.",
            "drafts": list(self._draft_rows),
        }
