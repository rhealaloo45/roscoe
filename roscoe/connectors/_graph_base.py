"""Shared base for Microsoft Graph connectors (Outlook, SharePoint).

Handles the app-only OAuth2 client-credentials flow: acquires + caches a bearer
token and injects it on every Graph request. Subclasses declare any extra required
config keys and provide their tools.
"""

from __future__ import annotations

import time
from typing import Any

from roscoe.connectors.base_connector import BaseConnector, raise_for_status

_GRAPH = "https://graph.microsoft.com/v1.0"
_BASE_REQUIRED = ("client_id", "client_secret", "tenant_id")


class GraphConnector(BaseConnector):
    """Base for connectors backed by Microsoft Graph with client-credentials auth."""

    #: Extra required config keys beyond the OAuth trio (subclasses override).
    extra_required: tuple[str, ...] = ()

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        self._token: str | None = None
        self._token_expiry: float = 0.0
        for key in _BASE_REQUIRED + self.extra_required:
            if not config.get(key):
                raise ValueError(
                    f"{type(self).__name__} config missing required key '{key}'."
                )
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        return _GRAPH

    def _auth_headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json"}

    def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        tenant = self.config["tenant_id"]
        resp = self._client.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.config["client_id"],
                "client_secret": self.config["client_secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
            # The client's default Content-Type is application/json, for the Graph
            # calls below — but this is a form-encoded token request, and the
            # endpoint 400s if the header says json while the body doesn't.
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        raise_for_status(resp)
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._ensure_token()}"
        return super()._request(method, path, headers=headers, **kwargs)
