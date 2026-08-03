"""Jira connector — pre-built tools over the Jira Cloud REST API (v3).

Two auth modes, picked automatically from which config keys are set:

1. **API token** (simplest — good for a single account acting as itself):

```yaml
connectors:
  jira:
    base_url: ${JIRA_URL}      # https://your-org.atlassian.net
    email: ${JIRA_EMAIL}
    api_token: ${JIRA_TOKEN}
```

2. **OAuth 2.0 (3LO)** — Atlassian's actual OAuth flow, for an app acting on
   a user's behalf without holding their password-equivalent forever. Create
   the app at developer.atlassian.com, get a refresh token through the
   standard authorization-code exchange once, then:

```yaml
connectors:
  jira:
    cloud_id: ${JIRA_CLOUD_ID}            # from the /oauth/token/accessible-resources response
    client_id: ${JIRA_CLIENT_ID}
    client_secret: ${JIRA_CLIENT_SECRET}
    refresh_token: ${JIRA_REFRESH_TOKEN}
```

   Requests then go through Atlassian's API gateway
   (``api.atlassian.com/ex/jira/{cloud_id}``) rather than directly at the
   site, which is how 3LO access is scoped.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector, raise_for_status

_API = "/rest/api/3"
_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
_OAUTH_KEYS = ("cloud_id", "client_id", "client_secret", "refresh_token")


class JiraConnector(BaseConnector):
    """Tools: create_issue, get_issue, update_issue, search_issues, add_comment."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        self._use_oauth = all(config.get(k) for k in _OAUTH_KEYS)
        self._token: str | None = None
        self._token_expiry: float = 0.0
        if not self._use_oauth:
            for key in ("email", "api_token"):
                if not config.get(key):
                    raise ValueError(
                        f"jira connector config missing required key '{key}' (or set "
                        f"{_OAUTH_KEYS} for OAuth 2.0 instead)."
                    )
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        if self._use_oauth:
            return f"https://api.atlassian.com/ex/jira/{self.config['cloud_id']}"
        url = self.config.get("base_url")
        if not url:
            raise ValueError("jira connector config missing required key 'base_url'.")
        return url.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        common = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._use_oauth:
            # The token is fetched lazily, per request (see _request) — not
            # baked in here, which would mean a live token exchange on every
            # connector construction, whether or not it's ever actually called.
            return common
        raw = f"{self.config['email']}:{self.config['api_token']}".encode()
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
        resp = self._client.post(
            _TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self.config["client_id"],
                "client_secret": self.config["client_secret"],
                "refresh_token": self.config["refresh_token"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        raise_for_status(resp)
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token

    @property
    def tools(self) -> list[StructuredTool]:
        def create_issue(
            project_key: str, summary: str, description: str = "", issue_type: str = "Task"
        ) -> Any:
            """Create a Jira issue and return its key and id."""
            payload = {
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "description": _adf(description),
                    "issuetype": {"name": issue_type},
                }
            }
            return self._request("POST", f"{_API}/issue", json=payload)

        def get_issue(issue_key: str) -> Any:
            """Fetch a Jira issue by key (e.g. PROJ-123)."""
            return self._request("GET", f"{_API}/issue/{issue_key}")

        def update_issue(issue_key: str, fields: dict) -> Any:
            """Update fields on a Jira issue."""
            return self._request(
                "PUT", f"{_API}/issue/{issue_key}", json={"fields": fields}
            )

        def search_issues(jql: str, max_results: int = 20) -> Any:
            """Search Jira issues with a JQL query."""
            return self._request(
                "POST",
                f"{_API}/search",
                json={"jql": jql, "maxResults": max_results},
            )

        def add_comment(issue_key: str, body: str) -> Any:
            """Add a comment to a Jira issue."""
            return self._request(
                "POST", f"{_API}/issue/{issue_key}/comment", json={"body": _adf(body)}
            )

        return [
            StructuredTool.from_function(create_issue, description=create_issue.__doc__),
            StructuredTool.from_function(get_issue, description=get_issue.__doc__),
            StructuredTool.from_function(update_issue, description=update_issue.__doc__),
            StructuredTool.from_function(search_issues, description=search_issues.__doc__),
            StructuredTool.from_function(add_comment, description=add_comment.__doc__),
        ]


def _adf(text: str) -> dict:
    """Wrap plain text in Atlassian Document Format (required by Jira v3)."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text or " "}]}
        ],
    }
