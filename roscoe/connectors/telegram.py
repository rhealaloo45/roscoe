"""Telegram connector — send messages and photos through a bot.

```yaml
connectors:
  telegram:
    type: telegram
    bot_token: ${TELEGRAM_BOT_TOKEN}
    default_chat_id: ${TELEGRAM_CHAT_ID}   # optional — used when a tool call omits chat_id
```

The bot token lives in the URL path, not a header — that's Telegram's own API
shape, not a roscoe convention, so there's no `_auth_headers()` to speak of.

No inbound tools here: reading messages is what a webhook trigger is for
(``roscoe run`` exposes ``POST /webhook`` when a workflow declares one), not
something this connector polls for.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector


class TelegramConnector(BaseConnector):
    """Tools: send_message, send_photo."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        if not config.get("bot_token"):
            raise ValueError("telegram connector config missing required key 'bot_token'.")
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.config['bot_token']}"

    def _auth_headers(self) -> dict[str, str]:
        return {}

    def _chat_id(self, chat_id: str) -> str:
        resolved = chat_id or self.config.get("default_chat_id")
        if not resolved:
            raise ValueError(
                "telegram connector: no chat_id given and no 'default_chat_id' "
                "configured, so there is nowhere to send this."
            )
        return str(resolved)

    @property
    def tools(self) -> list[StructuredTool]:
        def send_message(text: str, chat_id: str = "") -> Any:
            """Send a text message. `chat_id` falls back to the connector's
            `default_chat_id` if not given."""
            return self._request(
                "POST", "/sendMessage",
                json={"chat_id": self._chat_id(chat_id), "text": text},
            )

        def send_photo(photo_url: str, caption: str = "", chat_id: str = "") -> Any:
            """Send a photo by URL, with an optional caption."""
            body: dict[str, Any] = {"chat_id": self._chat_id(chat_id), "photo": photo_url}
            if caption:
                body["caption"] = caption
            return self._request("POST", "/sendPhoto", json=body)

        return [
            StructuredTool.from_function(send_message, description=send_message.__doc__),
            StructuredTool.from_function(send_photo, description=send_photo.__doc__),
        ]
