"""TickTick connector — pre-built tools over the TickTick Open API.

Auth: an OAuth2 access token (bearer). Register an app at
https://developer.ticktick.com to get a client id/secret, run the authorisation
code flow once, and keep the access token.

```yaml
connectors:
  ticktick:
    token: ${TICKTICK_TOKEN}
    default_project_id: ${TICKTICK_INBOX_ID}   # optional — used when a task omits one
```

There is deliberately no delete tool. Tasks are cheap to create and expensive to
lose, and a model cannot reach for a capability it was never handed. Complete a
task instead; if a project genuinely needs deletion, add it explicitly.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector

_BASE = "https://api.ticktick.com/open/v1"

#: TickTick priorities are a sparse scale, not 0-4 — naming them keeps the model
#: from inventing a 2 that the API silently rounds.
_PRIORITIES = {"none": 0, "low": 1, "medium": 3, "high": 5}


class TickTickConnector(BaseConnector):
    """Tools: list_projects, list_project_tasks, get_task, create_task, complete_task."""

    def _base_url(self) -> str:
        return self.config.get("base_url", _BASE)

    def _auth_headers(self) -> dict[str, str]:
        if not self.config.get("token"):
            raise ValueError("ticktick connector config missing required key 'token'.")
        return {
            "Authorization": f"Bearer {self.config['token']}",
            "Content-Type": "application/json",
        }

    def _project_id(self, project_id: str | None) -> str:
        resolved = project_id or self.config.get("default_project_id")
        if not resolved:
            raise ValueError(
                "No project_id given and no 'default_project_id' in the ticktick "
                "connector config. Call list_projects to find one."
            )
        return str(resolved)

    @property
    def tools(self) -> list[StructuredTool]:
        def list_projects() -> Any:
            """List TickTick projects (lists), with their ids."""
            return self._request("GET", "/project")

        def list_project_tasks(project_id: str) -> Any:
            """List the undone tasks in a TickTick project, with the project's columns."""
            return self._request("GET", f"/project/{project_id}/data")

        def get_task(project_id: str, task_id: str) -> Any:
            """Retrieve one TickTick task by id."""
            return self._request("GET", f"/project/{project_id}/task/{task_id}")

        def create_task(
            title: str,
            content: str = "",
            project_id: str | None = None,
            due_date: str | None = None,
            priority: str = "none",
            is_all_day: bool = True,
        ) -> Any:
            """Create a TickTick task.

            due_date is ISO 8601 with an offset, e.g. '2026-08-03T09:00:00+0000'.
            priority is one of: none, low, medium, high.
            """
            if priority not in _PRIORITIES:
                raise ValueError(
                    f"Unknown priority '{priority}'. Use one of: {', '.join(_PRIORITIES)}."
                )
            payload: dict[str, Any] = {
                "title": title,
                "projectId": self._project_id(project_id),
                "priority": _PRIORITIES[priority],
            }
            if content:
                payload["content"] = content
            if due_date:
                payload["dueDate"] = due_date
                payload["isAllDay"] = is_all_day
            return self._request("POST", "/task", json=payload)

        def complete_task(project_id: str, task_id: str) -> Any:
            """Mark a TickTick task as complete."""
            return self._request(
                "POST", f"/project/{project_id}/task/{task_id}/complete"
            )

        return [
            StructuredTool.from_function(list_projects, description=list_projects.__doc__),
            StructuredTool.from_function(
                list_project_tasks, description=list_project_tasks.__doc__
            ),
            StructuredTool.from_function(get_task, description=get_task.__doc__),
            StructuredTool.from_function(create_task, description=create_task.__doc__),
            StructuredTool.from_function(complete_task, description=complete_task.__doc__),
        ]
