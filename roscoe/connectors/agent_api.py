"""Call another roscoe agent.

A running agent already exposes JSON over HTTP, so one agent calling another
needs no new protocol — the generic REST connector could always do it. What it
couldn't do is hide the envelope: every caller had to know that ``/api/chat``
is the path, that the answer lives under ``output``, and that a failure arrives
as ``{"type": "error"}`` with HTTP 200. That is a lot to know before you can
join two agents together.

This asks for a URL and gives back the answer.

```yaml
connectors:
  research:
    type: agent
    base_url: http://localhost:8091
    api_key: ${RESEARCH_AGENT_KEY}   # if that agent runs with --api-key
```

Specialised agents can then be deployed and versioned separately, with one
orchestrator calling each and merging what comes back.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector


class AgentAPIError(RuntimeError):
    """Raised when the called agent reports a failure.

    A remote error is raised rather than returned: the calling workflow should
    stop with the reason, not carry on with an error payload in place of the
    answer it was expecting.
    """


class AgentConnector(BaseConnector):
    """Tools: ask."""

    # Calling another agent means waiting on its own LLM call — and possibly a
    # multi-step workflow behind it — so the base 30s built for a plain REST
    # call is often too tight.
    timeout: float = 120.0

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        if not config.get("base_url"):
            raise ValueError(
                "agent connector config missing required key 'base_url' — where the "
                "other agent is running, e.g. http://localhost:8091."
            )
        if config.get("timeout") is not None:
            self.timeout = float(config["timeout"])
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        return str(self.config["base_url"]).rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # Only when the other agent was started with --api-key.
        if self.config.get("api_key"):
            headers["Authorization"] = f"Bearer {self.config['api_key']}"
        return headers

    @property
    def tools(self) -> list[StructuredTool]:
        def ask(message: str) -> Any:
            """Ask another agent something and return its answer as text."""
            reply = self._request("POST", "/api/chat", json={"message": message})
            if not isinstance(reply, dict):
                return reply

            kind = reply.get("type")
            if kind == "error":
                raise AgentAPIError(
                    f"The agent at {self._base_url()} failed: {reply.get('error')}"
                )
            if kind == "paused":
                # It stopped for a human decision that this caller cannot make.
                raise AgentAPIError(
                    f"The agent at {self._base_url()} is waiting for someone to "
                    f"approve an action, so it cannot answer automatically. Remove "
                    f"the approval gate on that agent to call it from a workflow."
                )
            # The point of this connector: hand back the answer, not the envelope.
            return reply.get("output", reply)

        return [StructuredTool.from_function(ask, description=ask.__doc__)]
