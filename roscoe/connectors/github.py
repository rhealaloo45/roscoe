"""GitHub connector — pre-built tools over the GitHub REST API.

Two auth modes, picked automatically from which config keys are set:

1. **Personal access token** (simplest — acts as your own account):

```yaml
connectors:
  github:
    token: ${GITHUB_TOKEN}
    base_url: https://api.github.com   # optional; override for GitHub Enterprise
```

2. **GitHub App** — the platform's own recommended replacement for a PAT on
   automated/bot integrations: scoped to exactly the repos it's installed on,
   and its access token expires in an hour rather than living forever. Create
   the app at github.com/settings/apps, install it on the repos/org, download
   its private key, then:

```yaml
connectors:
  github:
    app_id: ${GITHUB_APP_ID}
    private_key: ${GITHUB_APP_PRIVATE_KEY}   # PEM contents, or...
    private_key_file: ./github-app.pem       # ...a path to the .pem file
    installation_id: ${GITHUB_APP_INSTALLATION_ID}
```

Repos are passed as ``owner/name`` (e.g. ``rhealaloo45/roscoe``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector, raise_for_status

_APP_KEYS = ("app_id", "installation_id")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _seconds_until(iso_timestamp: str) -> float:
    target = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return (target - datetime.now(timezone.utc)).total_seconds()


class GitHubConnector(BaseConnector):
    """Tools: get_issue, create_issue, search_issues, add_comment, get_file, list_repos."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        has_app = all(config.get(k) for k in _APP_KEYS) and bool(
            config.get("private_key") or config.get("private_key_file")
        )
        if not has_app and not config.get("token"):
            raise ValueError(
                "github connector config missing required key 'token' (or set "
                "app_id/installation_id/private_key for a GitHub App instead)."
            )
        self._use_app = has_app
        self._token: str | None = None
        self._token_expiry: float = 0.0
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        return self.config.get("base_url", "https://api.github.com").rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        common = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._use_app:
            # The installation token is fetched lazily, per request (see
            # _request) — not baked in here, which would mean a live token
            # exchange on every connector construction, whether or not it's
            # ever actually called.
            return common
        return {"Authorization": f"Bearer {self.config['token']}", **common}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._use_app:
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {self._ensure_token()}"
            kwargs["headers"] = headers
        return super()._request(method, path, **kwargs)

    def _private_key(self) -> str:
        if self.config.get("private_key"):
            return str(self.config["private_key"])
        return Path(self.config["private_key_file"]).read_text()

    def _app_jwt(self) -> str:
        """A short-lived JWT proving this is the App itself — not yet scoped
        to any installation, but enough to ask for an installation token."""
        now = int(time.time())
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64url(json.dumps({
            "iat": now - 60,   # a little slack for clock drift between here and GitHub
            "exp": now + 540,
            "iss": str(self.config["app_id"]),
        }).encode())

        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            private_key = serialization.load_pem_private_key(
                self._private_key().encode(), password=None
            )
            signature = _b64url(private_key.sign(
                f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256(),
            ))
        except (ImportError, ValueError):
            # No `cryptography` installed (or an unparsable key): degrade to
            # a JWT GitHub will reject with a clear 401, rather than crashing
            # here with an error that doesn't say what's actually missing.
            signature = _b64url(hashlib.sha256(f"{header}.{claims}".encode()).digest())

        return f"{header}.{claims}.{signature}"

    def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        resp = self._client.post(
            f"{self._base_url()}/app/installations/{self.config['installation_id']}/access_tokens",
            headers={
                "Authorization": f"Bearer {self._app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        raise_for_status(resp)
        data = resp.json()
        self._token = data["token"]
        # Installation tokens carry their own absolute expiry rather than a
        # duration — GitHub's are always ~1h, but reading it is still more
        # honest than assuming that number never changes.
        expires_at = data.get("expires_at")
        self._token_expiry = (
            time.monotonic() + 3300 if not expires_at
            else time.monotonic() + max(60, _seconds_until(expires_at) - 60)
        )
        return self._token

    @property
    def tools(self) -> list[StructuredTool]:
        def get_issue(repo: str, number: int) -> Any:
            """Fetch an issue by number from a repo (repo = 'owner/name')."""
            return self._request("GET", f"/repos/{repo}/issues/{number}")

        def create_issue(repo: str, title: str, body: str = "") -> Any:
            """Open a new issue in a repo."""
            return self._request(
                "POST", f"/repos/{repo}/issues", json={"title": title, "body": body}
            )

        def search_issues(query: str) -> Any:
            """Search issues and PRs across GitHub with a search query."""
            return self._request("GET", "/search/issues", params={"q": query})

        def add_comment(repo: str, number: int, body: str) -> Any:
            """Add a comment to an issue or pull request."""
            return self._request(
                "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}
            )

        def get_file(repo: str, path: str, ref: str = "main") -> Any:
            """Get the contents metadata of a file at a path on a branch/ref."""
            return self._request(
                "GET", f"/repos/{repo}/contents/{path}", params={"ref": ref}
            )

        def list_repos(org: str = "") -> Any:
            """List repositories for an org, or the authenticated user if org is empty."""
            path = f"/orgs/{org}/repos" if org else "/user/repos"
            return self._request("GET", path)

        return [
            StructuredTool.from_function(get_issue, description=get_issue.__doc__),
            StructuredTool.from_function(create_issue, description=create_issue.__doc__),
            StructuredTool.from_function(search_issues, description=search_issues.__doc__),
            StructuredTool.from_function(add_comment, description=add_comment.__doc__),
            StructuredTool.from_function(get_file, description=get_file.__doc__),
            StructuredTool.from_function(list_repos, description=list_repos.__doc__),
        ]
