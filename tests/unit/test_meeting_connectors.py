"""Connectors behind the meeting-assistant flow: Drive file reads and TickTick.

Both are mocked at the transport — no live APIs, no tokens.
"""

import json

import httpx
import pytest

from roscoe.connectors import GoogleWorkspaceConnector, TickTickConnector
from roscoe.workflow.registry import build_connectors

_OAUTH = {"client_id": "id", "client_secret": "secret", "refresh_token": "r"}


def _google(handler):
    return GoogleWorkspaceConnector(_OAUTH, transport=httpx.MockTransport(handler))


def _tool(conn, name):
    return next(t for t in conn.tools if t.name == name)


# --- OAuth token refresh ---


def test_token_refresh_is_form_encoded_not_json():
    """The client's default Content-Type is application/json, for the Gmail/
    Calendar/Tasks calls — but the token endpoint takes a form body, and Google
    400s a form-encoded request whose header claims json. Regression for a real
    failure: `roscoe run` 400'd against a token/credentials that curl accepted
    fine, because roscoe's own request lied about its content type.
    """
    seen = {}

    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            seen["content_type"] = request.headers.get("content-type", "")
            if not seen["content_type"].startswith("application/x-www-form-urlencoded"):
                return httpx.Response(400, json={"error": "invalid_request"})
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, json={"ok": True})

    conn = _google(handler)

    assert _tool(conn, "list_events").invoke({}) == {"ok": True}
    assert seen["content_type"].startswith("application/x-www-form-urlencoded")


# --- reading a Drive file (the Meet transcript) ---


def _drive_handler(mime, body, *, seen=None):
    def handler(request):
        path = request.url.path
        if path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if seen is not None:
            seen.setdefault("paths", []).append(path)
            seen["params"] = dict(request.url.params)
        if path.endswith("/export"):
            return httpx.Response(200, text=body, headers={"content-type": "text/plain"})
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, text=body, headers={"content-type": "text/plain"})
        return httpx.Response(
            200, json={"id": "f1", "name": "Standup - Transcript", "mimeType": mime}
        )

    return handler


def test_google_docs_are_exported_as_plain_text():
    seen = {}
    conn = _google(_drive_handler(
        "application/vnd.google-apps.document", "Rhea: let's ship it.", seen=seen
    ))

    out = _tool(conn, "read_drive_file").invoke({"file_id": "f1"})

    assert out["text"] == "Rhea: let's ship it."
    assert out["name"] == "Standup - Transcript"
    assert out["truncated"] is False
    # Metadata first, then the export endpoint — a Google-native file has no bytes.
    assert seen["paths"][-1].endswith("/files/f1/export")
    assert seen["params"]["mimeType"] == "text/plain"


def test_a_plain_uploaded_file_is_downloaded_not_exported():
    seen = {}
    conn = _google(_drive_handler("text/plain", "raw notes", seen=seen))

    out = _tool(conn, "read_drive_file").invoke({"file_id": "f1"})

    assert out["text"] == "raw notes"
    assert not any(p.endswith("/export") for p in seen["paths"])


def test_a_long_transcript_is_truncated_rather_than_blowing_the_context():
    conn = _google(_drive_handler("application/vnd.google-apps.document", "x" * 500))

    out = _tool(conn, "read_drive_file").invoke({"file_id": "f1", "max_chars": 100})

    assert len(out["text"]) == 100
    assert out["truncated"] is True


def test_read_drive_file_is_exposed_as_a_tool():
    conn = _google(_drive_handler("text/plain", ""))

    assert "read_drive_file" in {t.name for t in conn.tools}


# --- reading emails ---


def _email_handler():
    def handler(request):
        path = request.url.path
        if path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        if path.endswith("/messages/m1"):
            return httpx.Response(200, json={
                "id": "m1",
                "snippet": "Can you review the Q3 deck before Friday?",
                "payload": {"headers": [
                    {"name": "From", "value": "priya@example.com"},
                    {"name": "Subject", "value": "Q3 deck review"},
                ]},
            })
        if path.endswith("/messages/m2"):
            return httpx.Response(200, json={
                "id": "m2",
                "snippet": "Reminder: office closed Monday.",
                "payload": {"headers": [
                    {"name": "From", "value": "hr@example.com"},
                    {"name": "Subject", "value": "Office closure"},
                ]},
            })
        return httpx.Response(200, json={"ok": True})

    return handler


def test_read_emails_returns_subject_sender_and_snippet_not_just_ids():
    """Gmail's messages.list only returns bare ids — a digest agent needs the
    subject/sender/snippet to actually summarise, so read_emails fetches each
    message's metadata too rather than handing back ids alone.
    """
    conn = _google(_email_handler())

    out = _tool(conn, "read_emails").invoke({"max_results": 2})

    assert out["emails"] == [
        {
            "id": "m1",
            "from": "priya@example.com",
            "subject": "Q3 deck review",
            "snippet": "Can you review the Q3 deck before Friday?",
        },
        {
            "id": "m2",
            "from": "hr@example.com",
            "subject": "Office closure",
            "snippet": "Reminder: office closed Monday.",
        },
    ]


def test_read_emails_with_an_empty_inbox_returns_an_empty_list():
    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, json={})

    conn = _google(handler)

    out = _tool(conn, "read_emails").invoke({})

    assert out == {"emails": []}


# --- TickTick ---


def _ticktick(handler, **config):
    return TickTickConnector(
        {"token": "tt", **config}, transport=httpx.MockTransport(handler)
    )


def test_ticktick_create_task_sends_title_project_and_priority():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "t1", "title": "Ship the recap"})

    conn = _ticktick(handler)
    out = _tool(conn, "create_task").invoke({
        "title": "Ship the recap",
        "project_id": "p1",
        "due_date": "2026-08-03T09:00:00+0000",
        "priority": "high",
    })

    assert out["id"] == "t1"
    assert seen["path"] == "/open/v1/task"
    assert seen["auth"] == "Bearer tt"
    assert seen["body"]["projectId"] == "p1"
    assert seen["body"]["priority"] == 5           # 'high', not a made-up 2
    assert seen["body"]["dueDate"] == "2026-08-03T09:00:00+0000"


def test_checklist_becomes_native_ticktick_items():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "t1"})

    conn = _ticktick(handler)
    _tool(conn, "create_task").invoke({
        "title": "Prep for demo",
        "project_id": "p1",
        "checklist": ["Review deck", "Pull Q3 numbers"],
    })

    assert seen["body"]["kind"] == "CHECKLIST"
    assert seen["body"]["items"] == [
        {"title": "Review deck", "status": 0},
        {"title": "Pull Q3 numbers", "status": 0},
    ]


def test_a_task_with_no_checklist_omits_items_and_kind():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "t1"})

    conn = _ticktick(handler)
    _tool(conn, "create_task").invoke({"title": "Plain task", "project_id": "p1"})

    assert "items" not in seen["body"]
    assert "kind" not in seen["body"]


def test_default_project_is_used_when_a_task_omits_one():
    def handler(request):
        assert json.loads(request.content)["projectId"] == "inbox1"
        return httpx.Response(200, json={"id": "t1"})

    conn = _ticktick(handler, default_project_id="inbox1")

    assert _tool(conn, "create_task").invoke({"title": "Follow up"})["id"] == "t1"


def test_a_task_with_no_project_anywhere_says_so_instead_of_guessing():
    conn = _ticktick(lambda r: httpx.Response(200, json={}))

    with pytest.raises(Exception, match="default_project_id"):
        _tool(conn, "create_task").invoke({"title": "Follow up"})


def test_an_invented_priority_is_rejected_not_silently_rounded():
    conn = _ticktick(lambda r: httpx.Response(200, json={}), default_project_id="p1")

    with pytest.raises(Exception, match="Unknown priority"):
        _tool(conn, "create_task").invoke({"title": "x", "priority": "urgent"})


def test_create_tasks_batch_creates_each_one_and_summarises():
    seen_bodies = []

    def handler(request):
        body = json.loads(request.content)
        seen_bodies.append(body)
        return httpx.Response(200, json={"id": f"t{len(seen_bodies)}", "title": body["title"]})

    conn = _ticktick(handler, default_project_id="p1")
    out = _tool(conn, "create_tasks_batch").invoke({
        "tasks": [
            {"title": "Prep for demo", "priority": "high"},
            {"title": "Prep for review", "priority": "medium"},
        ]
    })

    assert out["created"] == 2
    assert [t["title"] for t in out["tasks"]] == ["Prep for demo", "Prep for review"]
    assert seen_bodies[0]["priority"] == 5   # high
    assert seen_bodies[1]["priority"] == 3   # medium
    assert all(b["projectId"] == "p1" for b in seen_bodies)   # default project applied per item


def test_ticktick_exposes_no_delete_tool():
    conn = _ticktick(lambda r: httpx.Response(200, json={}))

    names = {t.name for t in conn.tools}
    assert names == {
        "list_projects", "list_project_tasks", "get_task", "create_task",
        "create_tasks_batch", "complete_task",
    }


def test_missing_token_is_reported_by_name():
    with pytest.raises(ValueError, match="'token'"):
        TickTickConnector({})


# --- reachable from a workflow config ---


def test_ticktick_can_be_declared_in_a_connectors_block():
    connectors = build_connectors({"ticktick": {"token": "tt"}})

    assert "create_task" in {t.name for t in connectors["ticktick"].tools}
