"""Slack connector — post and read messages through a bot token.

```yaml
connectors:
  slack:
    type: slack
    bot_token: ${SLACK_BOT_TOKEN}   # xoxb-..., from an installed Slack app
```

Slack's Web API returns HTTP 200 even on failure, with `{"ok": false, "error":
"..."}` in the body — that's checked explicitly rather than left for a caller
to notice the answer looks wrong.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector

_BASE = "https://slack.com/api"


class SlackError(RuntimeError):
    """Raised when Slack accepts the request but reports `ok: false`."""


class SlackConnector(BaseConnector):
    """Tools: send_message, read_channel."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        if not config.get("bot_token"):
            raise ValueError("slack connector config missing required key 'bot_token'.")
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        return _BASE

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config['bot_token']}"}

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        reply = self._request(method, path, **kwargs)
        if isinstance(reply, dict) and not reply.get("ok", True):
            raise SlackError(f"Slack rejected the request: {reply.get('error')}")
        return reply

    @property
    def tools(self) -> list[StructuredTool]:
        def send_message(channel: str, text: str) -> Any:
            """Post a message. `channel` is a channel id (e.g. C0123ABC) or name
            (e.g. #general) the bot has been invited to."""
            return self._call("POST", "/chat.postMessage", json={"channel": channel, "text": text})

        def read_channel(channel: str, limit: int = 20) -> Any:
            """Read the most recent messages in a channel, newest first."""
            reply = self._call(
                "GET", "/conversations.history",
                params={"channel": channel, "limit": limit},
            )
            return reply.get("messages", reply)

        return [
            StructuredTool.from_function(send_message, description=send_message.__doc__),
            StructuredTool.from_function(read_channel, description=read_channel.__doc__),
        ]
