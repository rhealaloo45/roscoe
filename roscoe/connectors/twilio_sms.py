"""Twilio SMS connector — send a text message.

```yaml
connectors:
  sms:
    type: twilio
    account_sid: ${TWILIO_ACCOUNT_SID}
    auth_token: ${TWILIO_AUTH_TOKEN}
    from: ${TWILIO_FROM_NUMBER}     # the Twilio number sending the message
```

No delete/cancel tool: a sent message cannot be recalled, so there is nothing
honest for one to do.
"""

from __future__ import annotations

import base64
from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector

_BASE = "https://api.twilio.com"


class TwilioConnector(BaseConnector):
    """Tools: send_sms."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        for key in ("account_sid", "auth_token"):
            if not config.get(key):
                raise ValueError(f"twilio connector config missing required key '{key}'.")
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        return _BASE

    def _auth_headers(self) -> dict[str, str]:
        raw = f"{self.config['account_sid']}:{self.config['auth_token']}".encode()
        return {
            "Authorization": "Basic " + base64.b64encode(raw).decode(),
            # Twilio's REST API takes form-encoded bodies, not JSON.
            "Content-Type": "application/x-www-form-urlencoded",
        }

    @property
    def tools(self) -> list[StructuredTool]:
        def send_sms(to: str, body: str) -> Any:
            """Send an SMS. `to` is E.164, e.g. +447700900123."""
            sender = self.config.get("from")
            if not sender:
                raise ValueError(
                    "twilio connector: no 'from' number configured, so there is "
                    "nothing to send this message from."
                )
            sid = self.config["account_sid"]
            return self._request(
                "POST",
                f"/2010-04-01/Accounts/{sid}/Messages.json",
                data={"To": to, "From": sender, "Body": body},
            )

        return [StructuredTool.from_function(send_sms, description=send_sms.__doc__)]
