"""SMTP mail connector — send email from any mailbox, no OAuth.

The Gmail and Outlook connectors need an OAuth app registered before they will
send anything, which is a lot of setup for "email me the result". SMTP needs a
host and a password, which most providers hand out directly.

```yaml
connectors:
  mail:
    type: smtp
    host: smtp.gmail.com
    port: 587                 # 587 STARTTLS (default) | 465 SSL | 25 plain
    username: ${SMTP_USER}
    password: ${SMTP_PASSWORD}
    from: ${SMTP_USER}        # defaults to username
```

Gmail and most providers with 2FA require an app-specific password here, not the
account password.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector


class SMTPConnector(BaseConnector):
    """Tools: send_email."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        for key in ("host", "username", "password"):
            if not config.get(key):
                raise ValueError(f"smtp connector config missing required key '{key}'.")
        super().__init__(config, transport=transport)

    # SMTP is not HTTP; BaseConnector's client is unused but these keep its
    # contract satisfied rather than contorting the base class around one case.
    def _base_url(self) -> str:
        return "smtp://" + str(self.config["host"])

    def _auth_headers(self) -> dict[str, str]:
        return {}

    def _send(self, message: EmailMessage) -> None:
        host = str(self.config["host"])
        port = int(self.config.get("port", 587))
        user, password = self.config["username"], self.config["password"]

        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as server:
                server.login(user, password)
                server.send_message(message)
            return

        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if port != 25:
                server.starttls()
                server.ehlo()
            server.login(user, password)
            server.send_message(message)

    @property
    def tools(self) -> list[StructuredTool]:
        def send_email(to: str, subject: str, body: str, cc: str = "") -> Any:
            """Send a plain-text email. `to` and `cc` may be comma-separated."""
            message = EmailMessage()
            message["From"] = self.config.get("from") or self.config["username"]
            message["To"] = to
            if cc:
                message["Cc"] = cc
            message["Subject"] = subject
            message.set_content(body)
            self._send(message)
            # No message id comes back from SMTP — report what was sent so a
            # workflow can show something truthful.
            return {"sent": True, "to": to, "subject": subject}

        return [StructuredTool.from_function(send_email, description=send_email.__doc__)]
