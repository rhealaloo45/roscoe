"""Google Workspace connector — Gmail, Calendar, Meet, Tasks, and Drive.

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
import uuid
from typing import Any

import httpx
from langchain_core.tools import StructuredTool

from roscoe.connectors.base_connector import BaseConnector, raise_for_status

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
    """Tools: send_email, read_emails, list_events, create_event, update_event,
    delete_event, list_tasks, create_task, complete_task, delete_task,
    search_drive, read_drive_file, upload_drive_file."""

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
            # The client's default Content-Type is application/json, for the Gmail/
            # Calendar/Tasks calls below — but this is a form-encoded token request,
            # and Google 400s if the header says json while the body doesn't.
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        raise_for_status(resp)
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
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        raise_for_status(resp)
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token

    def _grequest(self, method: str, url: str, **kwargs: Any) -> Any:
        resp = self._gresponse(method, url, **kwargs)
        if resp.content and "application/json" in resp.headers.get("content-type", ""):
            return resp.json()
        return {"status_code": resp.status_code}

    def _gresponse(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """The raw response — for endpoints whose body is a document, not JSON."""
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._ensure_token()}"
        resp = self._client.request(method, url, headers=headers, **kwargs)
        raise_for_status(resp)
        return resp

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
            """Read the most recent emails from Gmail inbox — subject, sender,
            and snippet for each, not just message ids."""
            user = self.config.get("subject", "me")
            listing = self._grequest(
                "GET",
                f"{_GMAIL}/gmail/v1/users/{user}/messages",
                params={"maxResults": max_results, "labelIds": "INBOX"},
            )
            emails = []
            for stub in listing.get("messages", []):
                msg = self._grequest(
                    "GET",
                    f"{_GMAIL}/gmail/v1/users/{user}/messages/{stub['id']}",
                    params={"format": "metadata", "metadataHeaders": ["Subject", "From"]},
                )
                headers = {
                    h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])
                }
                emails.append({
                    "id": msg.get("id"),
                    "from": headers.get("From", ""),
                    "subject": headers.get("Subject", ""),
                    "snippet": msg.get("snippet", ""),
                })
            return {"emails": emails}

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
            summary: str, start: str, end: str, attendees: list[str] | None = None,
            add_meet_link: bool = False,
        ) -> Any:
            """Create a Google Calendar event. start/end are ISO 8601 datetimes
            (UTC). Set add_meet_link to attach a Google Meet video call —
            the returned event's `hangoutLink` is the join URL."""
            payload: dict[str, Any] = {
                "summary": summary,
                "start": {"dateTime": start, "timeZone": "UTC"},
                "end": {"dateTime": end, "timeZone": "UTC"},
            }
            if attendees:
                payload["attendees"] = [{"email": a} for a in attendees]
            params: dict[str, Any] = {}
            if add_meet_link:
                payload["conferenceData"] = {
                    "createRequest": {
                        "requestId": str(uuid.uuid4()),
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }
                params["conferenceDataVersion"] = 1
            return self._grequest(
                "POST",
                f"{_CALENDAR}/calendars/primary/events",
                json=payload,
                params=params,
            )

        def update_event(
            event_id: str, summary: str = "", start: str = "", end: str = "",
        ) -> Any:
            """Update a Calendar event's summary and/or time. Only the fields
            given are changed; leave the rest blank to keep them as-is."""
            payload: dict[str, Any] = {}
            if summary:
                payload["summary"] = summary
            if start:
                payload["start"] = {"dateTime": start, "timeZone": "UTC"}
            if end:
                payload["end"] = {"dateTime": end, "timeZone": "UTC"}
            return self._grequest(
                "PATCH",
                f"{_CALENDAR}/calendars/primary/events/{event_id}",
                json=payload,
            )

        def delete_event(event_id: str) -> Any:
            """Delete a Calendar event."""
            return self._grequest(
                "DELETE", f"{_CALENDAR}/calendars/primary/events/{event_id}"
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

        def complete_task(task_id: str, tasklist: str = "@default") -> Any:
            """Mark a Google Tasks task as completed."""
            return self._grequest(
                "PATCH",
                f"{_TASKS}/lists/{tasklist}/tasks/{task_id}",
                json={"status": "completed"},
            )

        def delete_task(task_id: str, tasklist: str = "@default") -> Any:
            """Delete a task from Google Tasks."""
            return self._grequest(
                "DELETE", f"{_TASKS}/lists/{tasklist}/tasks/{task_id}"
            )

        def upload_drive_file(name: str, content: str, mime_type: str = "text/plain") -> Any:
            """Create a new Drive file from text content (e.g. a generated
            report or note). For binary or very large files, upload through
            Drive directly instead."""
            metadata = json.dumps({"name": name, "mimeType": mime_type}).encode()
            boundary = uuid.uuid4().hex
            body = (
                f"--{boundary}\r\n"
                f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            ).encode() + metadata + (
                f"\r\n--{boundary}\r\n"
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode() + content.encode() + f"\r\n--{boundary}--".encode()
            resp = self._gresponse(
                "POST",
                "https://www.googleapis.com/upload/drive/v3/files",
                params={"uploadType": "multipart"},
                content=body,
                headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            )
            return resp.json()

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

        def read_drive_file(file_id: str, max_chars: int = 100_000) -> Any:
            """Read a Drive file's text. Google Docs are exported as plain text.

            Use this to pull the body of a Meet transcript doc found by search_drive.
            """
            meta = self._grequest(
                "GET", f"{_DRIVE}/files/{file_id}", params={"fields": "id,name,mimeType"}
            )
            mime = meta.get("mimeType", "")
            # Google-native files have no bytes to download — they have to be
            # exported to a format that does.
            if mime.startswith("application/vnd.google-apps."):
                resp = self._gresponse(
                    "GET",
                    f"{_DRIVE}/files/{file_id}/export",
                    params={"mimeType": "text/plain"},
                )
            else:
                resp = self._gresponse(
                    "GET", f"{_DRIVE}/files/{file_id}", params={"alt": "media"}
                )
            text = resp.text
            return {
                "id": meta.get("id", file_id),
                "name": meta.get("name"),
                "mime_type": mime,
                # A long meeting can run past any model's context. Truncate here
                # rather than failing three nodes later on a token limit.
                "truncated": len(text) > max_chars,
                "text": text[:max_chars],
            }

        return [
            StructuredTool.from_function(send_email, description=send_email.__doc__),
            StructuredTool.from_function(read_emails, description=read_emails.__doc__),
            StructuredTool.from_function(list_events, description=list_events.__doc__),
            StructuredTool.from_function(create_event, description=create_event.__doc__),
            StructuredTool.from_function(update_event, description=update_event.__doc__),
            StructuredTool.from_function(delete_event, description=delete_event.__doc__),
            StructuredTool.from_function(list_tasks, description=list_tasks.__doc__),
            StructuredTool.from_function(create_task, description=create_task.__doc__),
            StructuredTool.from_function(complete_task, description=complete_task.__doc__),
            StructuredTool.from_function(delete_task, description=delete_task.__doc__),
            StructuredTool.from_function(search_drive, description=search_drive.__doc__),
            StructuredTool.from_function(read_drive_file, description=read_drive_file.__doc__),
            StructuredTool.from_function(upload_drive_file, description=upload_drive_file.__doc__),
        ]
