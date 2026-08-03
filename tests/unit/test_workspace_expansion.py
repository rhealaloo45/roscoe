"""The Google Workspace and Microsoft 365 expansion — Meet, event/task
edits, Drive upload, Teams, To Do, and OneDrive. Mocked at the transport."""

import json

import httpx

from roscoe.connectors import GoogleWorkspaceConnector, OutlookConnector

_OAUTH = {"client_id": "id", "client_secret": "secret", "refresh_token": "r"}
_GRAPH_CFG = {"client_id": "c", "client_secret": "s", "tenant_id": "t", "mailbox": "bot@org.com"}


def _tool(conn, name):
    return next(t for t in conn.tools if t.name == name)


def _google(handler):
    return GoogleWorkspaceConnector(_OAUTH, transport=httpx.MockTransport(handler))


def _outlook(handler):
    return OutlookConnector(_GRAPH_CFG, transport=httpx.MockTransport(handler))


def _google_token(request):
    return request.url.host == "oauth2.googleapis.com"


def _graph_token(request):
    return request.url.host == "login.microsoftonline.com"


# --- Google Meet ---


def test_create_event_without_a_meet_link_sends_no_conference_data():
    seen = {}

    def handler(request):
        if _google_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["body"] = json.loads(request.content)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"id": "evt1"})

    _tool(_google(handler), "create_event").invoke(
        {"summary": "Sync", "start": "2026-01-01T10:00:00Z", "end": "2026-01-01T10:30:00Z"}
    )

    assert "conferenceData" not in seen["body"]
    assert "conferenceDataVersion" not in seen["params"]


def test_create_event_with_a_meet_link_requests_conference_data():
    seen = {}

    def handler(request):
        if _google_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["body"] = json.loads(request.content)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"id": "evt1", "hangoutLink": "https://meet.google.com/abc"})

    out = _tool(_google(handler), "create_event").invoke({
        "summary": "Sync", "start": "2026-01-01T10:00:00Z", "end": "2026-01-01T10:30:00Z",
        "add_meet_link": True,
    })

    assert seen["body"]["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"
    assert seen["params"]["conferenceDataVersion"] == "1"
    assert out["hangoutLink"] == "https://meet.google.com/abc"


# --- Calendar event edits ---


def test_update_event_only_sends_the_fields_given():
    seen = {}

    def handler(request):
        if _google_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "evt1"})

    _tool(_google(handler), "update_event").invoke({"event_id": "evt1", "summary": "Renamed"})

    assert seen["method"] == "PATCH"
    assert seen["path"].endswith("/events/evt1")
    assert seen["body"] == {"summary": "Renamed"}


def test_delete_event_hits_the_event_path():
    seen = {}

    def handler(request):
        if _google_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    _tool(_google(handler), "delete_event").invoke({"event_id": "evt1"})

    assert seen["method"] == "DELETE"
    assert seen["path"].endswith("/events/evt1")


# --- Tasks ---


def test_complete_task_patches_status():
    seen = {}

    def handler(request):
        if _google_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    _tool(_google(handler), "complete_task").invoke({"task_id": "tk1"})

    assert seen["method"] == "PATCH"
    assert seen["body"] == {"status": "completed"}


def test_delete_task_hits_the_task_path():
    seen = {}

    def handler(request):
        if _google_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    _tool(_google(handler), "delete_task").invoke({"task_id": "tk1"})

    assert seen["method"] == "DELETE"
    assert seen["path"].endswith("/tasks/tk1")


# --- Drive upload ---


def test_upload_drive_file_sends_a_multipart_body_with_name_and_content():
    seen = {}

    def handler(request):
        if _google_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "f1", "name": "notes.txt"})

    out = _tool(_google(handler), "upload_drive_file").invoke(
        {"name": "notes.txt", "content": "hello world"}
    )

    assert seen["content_type"].startswith("multipart/related")
    assert '"name": "notes.txt"' in seen["body"]
    assert "hello world" in seen["body"]
    assert out == {"id": "f1", "name": "notes.txt"}


# --- Microsoft Teams ---


def test_create_teams_meeting_hits_online_meetings():
    seen = {}

    def handler(request):
        if _graph_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"joinWebUrl": "https://teams.microsoft.com/l/x"})

    out = _tool(_outlook(handler), "create_teams_meeting").invoke({
        "subject": "Standup", "start": "2026-01-01T10:00:00Z", "end": "2026-01-01T10:15:00Z",
    })

    assert seen["path"].endswith("/users/bot@org.com/onlineMeetings")
    assert seen["body"]["subject"] == "Standup"
    assert out["joinWebUrl"] == "https://teams.microsoft.com/l/x"


def test_send_teams_message_posts_to_the_chat():
    seen = {}

    def handler(request):
        if _graph_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "msg1"})

    _tool(_outlook(handler), "send_teams_message").invoke(
        {"chat_id": "19:abc@thread.v2", "message": "hi team"}
    )

    assert seen["path"] == "/v1.0/chats/19:abc@thread.v2/messages"
    assert seen["body"]["body"]["content"] == "hi team"


# --- Microsoft To Do ---


def test_list_todo_lists_hits_the_lists_endpoint():
    seen = {}

    def handler(request):
        if _graph_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["path"] = request.url.path
        return httpx.Response(200, json={"value": [{"id": "l1", "displayName": "Tasks"}]})

    out = _tool(_outlook(handler), "list_todo_lists").invoke({})

    assert seen["path"].endswith("/todo/lists")
    assert out["value"][0]["displayName"] == "Tasks"


def test_create_todo_task_posts_the_title():
    seen = {}

    def handler(request):
        if _graph_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "task1"})

    _tool(_outlook(handler), "create_todo_task").invoke({"list_id": "l1", "title": "Ship it"})

    assert seen["path"].endswith("/todo/lists/l1/tasks")
    assert seen["body"] == {"title": "Ship it"}


# --- OneDrive ---


def test_search_onedrive_builds_the_odata_search_path():
    seen = {}

    def handler(request):
        if _graph_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        # httpx decodes .path for display; what matters is the query landed in
        # the OData search(q='...') segment rather than being dropped or malformed.
        seen["path"] = request.url.path
        seen["raw"] = str(request.url)
        return httpx.Response(200, json={"value": [{"id": "f1", "name": "report.docx"}]})

    out = _tool(_outlook(handler), "search_onedrive").invoke({"query": "quarterly report"})

    assert seen["path"] == "/v1.0/users/bot@org.com/drive/root/search(q='quarterly report')"
    assert "quarterly%20report" in seen["raw"]
    assert out["value"][0]["name"] == "report.docx"


def test_read_onedrive_file_returns_truncated_text():
    def handler(request):
        if _graph_token(request):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, text="the full document body", headers={"content-type": "text/plain"})

    out = _tool(_outlook(handler), "read_onedrive_file").invoke({"item_id": "f1", "max_chars": 8})

    assert out["truncated"] is True
    assert out["text"] == "the full"
