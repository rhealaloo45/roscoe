"""Generic REST connector — configurable GET/POST/PUT/DELETE tools.

Covers the common "just call our internal API" case. Auth is one of: ``api_key``
(custom header), ``bearer`` token, ``basic`` (username/password), or ``oauth``
(client-credentials-shaped token exchange, refreshed automatically).

```yaml
connectors:
  rest:
    base_url: ${SERVICE_URL}
    auth: bearer            # api_key | bearer | basic | oauth | none
    token: ${SERVICE_TOKEN}
```

OAuth mode names the token endpoint and the form to post it — this is
whatever *your* API's OAuth token exchange needs, so the shape stays
generic rather than assuming a specific provider:

```yaml
connectors:
  rest:
    base_url: ${SERVICE_URL}
    auth: oauth
    token_url: https://auth.example.com/oauth/token
    token_form:
      grant_type: client_credentials
      client_id: ${SERVICE_CLIENT_ID}
      client_secret: ${SERVICE_CLIENT_SECRET}
```

Same shape the exported standalone file uses for OAuth connectors, so a
project's config doesn't change when it moves from ``roscoe run`` to an
exported script.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector, raise_for_status


class RESTConnector(BaseConnector):
    """A configurable REST API connector."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        self._oauth_token: str | None = None
        self._oauth_expiry: float = 0.0
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        url = self.config.get("base_url")
        if not url:
            raise ValueError("rest connector config missing required key 'base_url'.")
        return url

    def _auth_headers(self) -> dict[str, str]:
        auth = self.config.get("auth", "none")
        if auth in ("none", "oauth"):
            # oauth's token is fetched lazily, per request (see _request) — not
            # baked in here, which would mean a live token exchange on every
            # connector construction, whether or not it's ever actually called.
            return {}
        if auth == "bearer":
            return {"Authorization": f"Bearer {self.config['token']}"}
        if auth == "api_key":
            header = self.config.get("header", "X-API-Key")
            return {header: self.config["api_key"]}
        if auth == "basic":
            raw = f"{self.config['username']}:{self.config['password']}".encode()
            return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}
        raise ValueError(f"rest connector: unknown auth mode '{auth}'.")

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self.config.get("auth") == "oauth":
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {self._ensure_oauth_token()}"
            kwargs["headers"] = headers
        return super()._request(method, path, **kwargs)

    def _ensure_oauth_token(self) -> str:
        if self._oauth_token and time.monotonic() < self._oauth_expiry:
            return self._oauth_token
        token_url = self.config.get("token_url")
        if not token_url:
            raise ValueError(
                "rest connector: auth: oauth needs 'token_url' — where to exchange "
                "'token_form' for an access token."
            )
        resp = self._client.post(
            token_url, data=self.config.get("token_form") or {},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        raise_for_status(resp)
        data = resp.json()
        self._oauth_token = data["access_token"]
        self._oauth_expiry = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._oauth_token

    @property
    def tools(self) -> list[StructuredTool]:
        def rest_get(path: str, params: dict | None = None) -> Any:
            """Send a GET request to the configured API and return the JSON response."""
            return self._request("GET", path, params=params)

        def rest_post(path: str, body: dict | None = None) -> Any:
            """Send a POST request with a JSON body and return the JSON response."""
            return self._request("POST", path, json=body)

        def rest_put(path: str, body: dict | None = None) -> Any:
            """Send a PUT request with a JSON body and return the JSON response."""
            return self._request("PUT", path, json=body)

        def rest_delete(path: str) -> Any:
            """Send a DELETE request and return the JSON/status response."""
            return self._request("DELETE", path)

        return [
            StructuredTool.from_function(rest_get, description=rest_get.__doc__),
            StructuredTool.from_function(rest_post, description=rest_post.__doc__),
            StructuredTool.from_function(rest_put, description=rest_put.__doc__),
            StructuredTool.from_function(rest_delete, description=rest_delete.__doc__),
        ]
