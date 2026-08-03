"""Outlook connector — Mail, Calendar, Teams and To Do, and OneDrive, all
through Microsoft Graph (app-only OAuth2).

Authenticates with the client-credentials flow (Azure AD app registration), caches
the bearer token, and acts on a configured mailbox.

```yaml
connectors:
  outlook:
    client_id: ${OUTLOOK_CLIENT_ID}
    client_secret: ${OUTLOOK_CLIENT_SECRET}
    tenant_id: ${OUTLOOK_TENANT_ID}
    mailbox: ${OUTLOOK_MAILBOX}        # UPN/object id the app acts on behalf of
```

Note: app-only Graph access requires the app registration to hold the relevant
application permissions with admin consent — Mail.Send / Mail.Read,
Calendars.ReadWrite for mail and calendar; OnlineMeetings.ReadWrite.All for
Teams meetings; Chat.ReadWrite.All for Teams messages; Tasks.ReadWrite for
To Do; Files.ReadWrite.All for OneDrive.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from langchain_core.tools import StructuredTool

from roscoe.connectors._graph_base import GraphConnector


class OutlookConnector(GraphConnector):
    """Tools: send_email, read_emails, create_calendar_event, get_availability,
    create_teams_meeting, send_teams_message, list_todo_lists, list_todo_tasks,
    create_todo_task, search_onedrive, read_onedrive_file."""

    extra_required = ("mailbox",)

    @property
    def tools(self) -> list[StructuredTool]:
        mbx = self.config["mailbox"]

        def send_email(to: str, subject: str, body: str) -> Any:
            """Send an email from the configured mailbox."""
            payload = {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                }
            }
            return self._request("POST", f"/users/{mbx}/sendMail", json=payload)

        def read_emails(top: int = 10) -> Any:
            """Read the most recent emails in the mailbox inbox."""
            return self._request(
                "GET", f"/users/{mbx}/messages", params={"$top": top}
            )

        def create_calendar_event(
            subject: str, start: str, end: str, attendees: list[str] | None = None
        ) -> Any:
            """Create a calendar event. start/end are ISO 8601 datetimes (UTC)."""
            payload: dict[str, Any] = {
                "subject": subject,
                "start": {"dateTime": start, "timeZone": "UTC"},
                "end": {"dateTime": end, "timeZone": "UTC"},
            }
            if attendees:
                payload["attendees"] = [
                    {"emailAddress": {"address": a}, "type": "required"} for a in attendees
                ]
            return self._request("POST", f"/users/{mbx}/events", json=payload)

        def get_availability(emails: list[str], start: str, end: str) -> Any:
            """Get free/busy availability for the given mailboxes over a time window."""
            payload = {
                "schedules": emails,
                "startTime": {"dateTime": start, "timeZone": "UTC"},
                "endTime": {"dateTime": end, "timeZone": "UTC"},
                "availabilityViewInterval": 60,
            }
            return self._request(
                "POST", f"/users/{mbx}/calendar/getSchedule", json=payload
            )

        def create_teams_meeting(subject: str, start: str, end: str) -> Any:
            """Create a Microsoft Teams meeting. start/end are ISO 8601
            datetimes (UTC). The returned `joinWebUrl` is the meeting link."""
            payload = {
                "subject": subject,
                "startDateTime": start,
                "endDateTime": end,
            }
            return self._request("POST", f"/users/{mbx}/onlineMeetings", json=payload)

        def send_teams_message(chat_id: str, message: str) -> Any:
            """Send a message into an existing Teams chat. `chat_id` is the
            chat's Graph id, e.g. 19:abc...@thread.v2."""
            payload = {"body": {"content": message}}
            return self._request("POST", f"/chats/{chat_id}/messages", json=payload)

        def list_todo_lists() -> Any:
            """List the mailbox's Microsoft To Do lists — a task needs a
            list id from here (or a project's own) before it can be created."""
            return self._request("GET", f"/users/{mbx}/todo/lists")

        def list_todo_tasks(list_id: str, top: int = 20) -> Any:
            """List tasks in a Microsoft To Do list."""
            return self._request(
                "GET", f"/users/{mbx}/todo/lists/{list_id}/tasks", params={"$top": top}
            )

        def create_todo_task(list_id: str, title: str) -> Any:
            """Create a task in a Microsoft To Do list."""
            return self._request(
                "POST", f"/users/{mbx}/todo/lists/{list_id}/tasks",
                json={"title": title},
            )

        def search_onedrive(query: str, top: int = 10) -> Any:
            """Search the mailbox's OneDrive for files by name or content."""
            return self._request(
                "GET", f"/users/{mbx}/drive/root/search(q='{quote(query)}')",
                params={"$top": top},
            )

        def read_onedrive_file(item_id: str, max_chars: int = 100_000) -> Any:
            """Read a OneDrive file's text content by its item id (from
            search_onedrive)."""
            reply = self._request("GET", f"/users/{mbx}/drive/items/{item_id}/content")
            text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
            return {
                "id": item_id,
                # A long document can run past any model's context. Truncate
                # here rather than failing three nodes later on a token limit.
                "truncated": len(text) > max_chars,
                "text": text[:max_chars],
            }

        return [
            StructuredTool.from_function(send_email, description=send_email.__doc__),
            StructuredTool.from_function(read_emails, description=read_emails.__doc__),
            StructuredTool.from_function(
                create_calendar_event, description=create_calendar_event.__doc__
            ),
            StructuredTool.from_function(
                get_availability, description=get_availability.__doc__
            ),
            StructuredTool.from_function(
                create_teams_meeting, description=create_teams_meeting.__doc__
            ),
            StructuredTool.from_function(
                send_teams_message, description=send_teams_message.__doc__
            ),
            StructuredTool.from_function(list_todo_lists, description=list_todo_lists.__doc__),
            StructuredTool.from_function(list_todo_tasks, description=list_todo_tasks.__doc__),
            StructuredTool.from_function(create_todo_task, description=create_todo_task.__doc__),
            StructuredTool.from_function(search_onedrive, description=search_onedrive.__doc__),
            StructuredTool.from_function(
                read_onedrive_file, description=read_onedrive_file.__doc__
            ),
        ]
