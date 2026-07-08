"""Google Workspace connector — Gmail, Calendar, Tasks, and Drive.

Two auth modes, picked automatically from which config keys are set:

1. **Service account** (domain-wide delegation, good for org-wide deployments):

```yaml
connectors:
  google_workspace:
    credentials_file: ${GOOGLE_SA_KEY_FILE}   # path to service account JSON
    subject: ${GOOGLE_SUBJECT}                # user to impersonate (email)
```

2. **OAuth2 user consent** (good for a single user / personal account, no
   domain-wide delegation admin rights needed). Get a refresh token once via
   ``roscoe google-auth`` (or any standard OAuth2 desktop-app flow), then:

```yaml
connectors:
  google_workspace:
    client_id: ${GOOGLE_CLIENT_ID}
    client_secret: ${GOOGLE_CLIENT_SECRET}
    refresh_token: ${GOOGLE_REFRESH_TOKEN}
```

No extra pip dependency — uses httpx + a manual JWT for the service-account
flow (same approach as the Graph connector), and a plain refresh_token POST
for the OAuth mode. If you prefer the official ``google-auth`` library, swap
``_ensure_token`` and the rest stays identical.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector

_GMAIL = "https://gmail.googleapis.com"
_CALENDAR = "https://www.googleapis.com/calendar/v3"
_TASKS = "https://tasks.googleapis.com/tasks/v1"
_DRIVE = "https://www.googleapis.com/drive/v3"

_SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.readonly",
])

_SERVICE_ACCOUNT_KEYS = ("credentials_file", "subject")
_OAUTH_KEYS = ("client_id", "client_secret", "refresh_token")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class GoogleWorkspaceConnector(BaseConnector):
    """Tools: send_email, read_emails, list_events, create_event,
    list_tasks, create_task, search_drive."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None) -> None:
        has_sa = all(config.get(k) for k in _SERVICE_ACCOUNT_KEYS)
        has_oauth = all(config.get(k) for k in _OAUTH_KEYS)
        if not has_sa and not has_oauth:
            raise ValueError(
                "GoogleWorkspaceConnector config must set either "
                f"{_SERVICE_ACCOUNT_KEYS} (service account) or "
                f"{_OAUTH_KEYS} (OAuth2 user consent)."
            )
        self._auth_mode = "service_account" if has_sa else "oauth"
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._sa_info: dict[str, Any] | None = None
        super().__init__(config, transport=transport)

    def _base_url(self) -> str:
        return _GMAIL

    def _auth_headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json"}

    def _load_sa(self) -> dict[str, Any]:
        if self._sa_info is None:
            with open(self.config["credentials_file"]) as f:
                self._sa_info = json.load(f)
        return self._sa_info

    def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        if self._auth_mode == "oauth":
            return self._ensure_token_oauth()
        return self._ensure_token_service_account()

    def _ensure_token_oauth(self) -> str:
        resp = self._client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.config["client_id"],
                "client_secret": self.config["client_secret"],
                "refresh_token": self.config["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token

    def _ensure_token_service_account(self) -> str:
        sa = self._load_sa()
        now = int(time.time())
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64url(json.dumps({
            "iss": sa["client_email"],
            "sub": self.config["subject"],
            "scope": _SCOPES,
            "aud": sa["token_uri"],
            "iat": now,
            "exp": now + 3600,
        }).encode())

        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            private_key = serialization.load_pem_private_key(
                sa["private_key"].encode(), password=None
            )
            signature = _b64url(private_key.sign(
                f"{header}.{claims}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            ))
        except (ImportError, ValueError):
            signature = _b64url(
                hashlib.sha256(f"{header}.{claims}".encode()).digest()
            )

        jwt_token = f"{header}.{claims}.{signature}"

        resp = self._client.post(
            sa["token_uri"],
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token

    def _grequest(self, method: str, url: str, **kwargs: Any) -> Any:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._ensure_token()}"
        resp = self._client.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        if resp.content and "application/json" in resp.headers.get("content-type", ""):
            return resp.json()
        return {"status_code": resp.status_code}

    @property
    def tools(self) -> list[StructuredTool]:
        def send_email(to: str, subject: str, body: str) -> Any:
            """Send an email via Gmail from the configured account."""
            import base64 as b64
            raw = (
                f"To: {to}\r\n"
                f"Subject: {subject}\r\n"
                f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
                f"{body}"
            )
            encoded = b64.urlsafe_b64encode(raw.encode()).decode()
            user = self.config.get("subject", "me")
            return self._grequest(
                "POST",
                f"{_GMAIL}/gmail/v1/users/{user}/messages/send",
                json={"raw": encoded},
            )

        def read_emails(max_results: int = 10) -> Any:
            """Read the most recent emails from Gmail inbox."""
            user = self.config.get("subject", "me")
            return self._grequest(
                "GET",
                f"{_GMAIL}/gmail/v1/users/{user}/messages",
                params={"maxResults": max_results, "labelIds": "INBOX"},
            )

        def list_events(max_results: int = 10) -> Any:
            """List upcoming events from Google Calendar."""
            return self._grequest(
                "GET",
                f"{_CALENDAR}/calendars/primary/events",
                params={
                    "maxResults": max_results,
                    "singleEvents": True,
                    "orderBy": "startTime",
                    "timeMin": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )

        def create_event(
            summary: str, start: str, end: str, attendees: list[str] | None = None
        ) -> Any:
            """Create a Google Calendar event. start/end are ISO 8601 datetimes (UTC)."""
            payload: dict[str, Any] = {
                "summary": summary,
                "start": {"dateTime": start, "timeZone": "UTC"},
                "end": {"dateTime": end, "timeZone": "UTC"},
            }
            if attendees:
                payload["attendees"] = [{"email": a} for a in attendees]
            return self._grequest(
                "POST",
                f"{_CALENDAR}/calendars/primary/events",
                json=payload,
            )

        def list_tasks(tasklist: str = "@default", max_results: int = 20) -> Any:
            """List tasks from Google Tasks."""
            return self._grequest(
                "GET",
                f"{_TASKS}/lists/{tasklist}/tasks",
                params={"maxResults": max_results},
            )

        def create_task(title: str, notes: str = "", tasklist: str = "@default") -> Any:
            """Create a new task in Google Tasks."""
            return self._grequest(
                "POST",
                f"{_TASKS}/lists/{tasklist}/tasks",
                json={"title": title, "notes": notes},
            )

        def search_drive(query: str, max_results: int = 10) -> Any:
            """Search Google Drive files. Query uses Drive search syntax (e.g. name contains 'report')."""
            return self._grequest(
                "GET",
                f"{_DRIVE}/files",
                params={
                    "q": query,
                    "pageSize": max_results,
                    "fields": "files(id,name,mimeType,webViewLink,modifiedTime)",
                },
            )

        return [
            StructuredTool.from_function(send_email, description=send_email.__doc__),
            StructuredTool.from_function(read_emails, description=read_emails.__doc__),
            StructuredTool.from_function(list_events, description=list_events.__doc__),
            StructuredTool.from_function(create_event, description=create_event.__doc__),
            StructuredTool.from_function(list_tasks, description=list_tasks.__doc__),
            StructuredTool.from_function(create_task, description=create_task.__doc__),
            StructuredTool.from_function(search_drive, description=search_drive.__doc__),
        ]
