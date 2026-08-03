"""ServiceNow connector — pre-built tools over the Table API.

Two auth modes, picked automatically from which config keys are set:

1. **Basic** (simplest):

```yaml
connectors:
  servicenow:
    instance_url: ${SERVICENOW_URL}   # https://your-instance.service-now.com
    username: ${SERVICENOW_USER}
    password: ${SERVICENOW_PASSWORD}
```

2. **OAuth 2.0** — register an OAuth application in the instance (System OAuth
   > Application Registry), then use its client id/secret. With a username +
   password this is the resource-owner-password grant (a real user's access,
   token-based instead of sending their password on every call); without
   them it's client_credentials, for an app acting as itself:

```yaml
connectors:
  servicenow:
    instance_url: ${SERVICENOW_URL}
    client_id: ${SERVICENOW_CLIENT_ID}
    client_secret: ${SERVICENOW_CLIENT_SECRET}
    username: ${SERVICENOW_USER}        # omit for client_credentials instead
    password: ${SERVICENOW_PASSWORD}
```
"""

from __future__ import annotations

import base64
import time
from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector, raise_for_status

_TABLE = "/api/now/table"


class ServiceNowConnector(BaseConnector):
    """Tools: create_ticket, update_ticket, get_ticket_status, search_kb."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        self._use_oauth = bool(config.get("client_id") and config.get("client_secret"))
        self._token: str | None = None
        self._token_expiry: float = 0.0
        if not self._use_oauth:
            for key in ("username", "password"):
                if not config.get(key):
                    raise ValueError(
                        f"servicenow connector config missing required key '{key}' "
                        f"(or set client_id/client_secret for OAuth 2.0 instead)."
                    )
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        url = self.config.get("instance_url")
        if not url:
            raise ValueError(
                "servicenow connector config missing required key 'instance_url'."
            )
        return url.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        common = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._use_oauth:
            # The token is fetched lazily, per request (see _request) — not
            # baked in here, which would mean a live token exchange on every
            # connector construction, whether or not it's ever actually called.
            return common
        raw = f"{self.config['username']}:{self.config['password']}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}", **common}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._use_oauth:
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {self._ensure_token()}"
            kwargs["headers"] = headers
        return super()._request(method, path, **kwargs)

    def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        form = {
            "client_id": self.config["client_id"],
            "client_secret": self.config["client_secret"],
        }
        if self.config.get("username") and self.config.get("password"):
            form.update(
                grant_type="password",
                username=self.config["username"],
                password=self.config["password"],
            )
        else:
            form["grant_type"] = "client_credentials"

        resp = self._client.post(
            f"{self._base_url()}/oauth_token.do", data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        raise_for_status(resp)
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + int(data.get("expires_in", 1800)) - 60
        return self._token

    @property
    def tools(self) -> list[StructuredTool]:
        def create_ticket(
            short_description: str, description: str = "", urgency: str = "3"
        ) -> Any:
            """Create a ServiceNow incident and return its number and sys_id."""
            return self._request(
                "POST",
                f"{_TABLE}/incident",
                json={
                    "short_description": short_description,
                    "description": description,
                    "urgency": urgency,
                },
            )

        def update_ticket(sys_id: str, fields: dict) -> Any:
            """Update fields on a ServiceNow incident by sys_id."""
            return self._request("PATCH", f"{_TABLE}/incident/{sys_id}", json=fields)

        def get_ticket_status(number: str) -> Any:
            """Look up an incident by its number (e.g. INC0010001)."""
            return self._request(
                "GET",
                f"{_TABLE}/incident",
                params={"sysparm_query": f"number={number}", "sysparm_limit": 1},
            )

        def search_kb(query: str, limit: int = 5) -> Any:
            """Search the ServiceNow knowledge base."""
            return self._request(
                "GET",
                f"{_TABLE}/kb_knowledge",
                params={"sysparm_query": f"short_descriptionLIKE{query}", "sysparm_limit": limit},
            )

        return [
            StructuredTool.from_function(create_ticket, description=create_ticket.__doc__),
            StructuredTool.from_function(update_ticket, description=update_ticket.__doc__),
            StructuredTool.from_function(
                get_ticket_status, description=get_ticket_status.__doc__
            ),
            StructuredTool.from_function(search_kb, description=search_kb.__doc__),
        ]
