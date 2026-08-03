"""Unit tests for Phase 5 connectors — mocked HTTP, no live APIs."""

import httpx
import pytest

from roscoe.connectors import (
    GitHubConnector,
    JiraConnector,
    NotionConnector,
    OutlookConnector,
    RESTConnector,
    ServiceNowConnector,
    SharePointConnector,
    SnowflakeConnector,
)


def _json(payload, status=200):
    return httpx.Response(status, json=payload)


# --- error surfacing ---


def test_a_failed_request_carries_the_server_s_own_explanation():
    """httpx's own message is just the status line. The reason an API actually
    rejected a request — 'Drive API has not been used in project ... before or
    it is disabled', a field-level validation error — is in the body, and
    that's the one thing needed to fix it without guessing.
    """
    conn = RESTConnector(
        {"base_url": "https://api.example.com", "auth": "bearer", "token": "t"},
        transport=httpx.MockTransport(lambda r: httpx.Response(
            403, json={"error": "Drive API has not been used in this project"}
        )),
    )
    get_tool = next(t for t in conn.tools if t.name == "rest_get")

    with pytest.raises(httpx.HTTPStatusError, match="Drive API has not been used"):
        get_tool.invoke({"path": "/things"})


# --- REST ---


def test_rest_get_sends_auth_and_returns_json():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return _json({"ok": True})

    conn = RESTConnector(
        {"base_url": "https://api.example.com", "auth": "bearer", "token": "t0ken"},
        transport=httpx.MockTransport(handler),
    )
    names = {t.name for t in conn.tools}
    assert names == {"rest_get", "rest_post", "rest_put", "rest_delete"}

    get_tool = next(t for t in conn.tools if t.name == "rest_get")
    result = get_tool.invoke({"path": "/things"})
    assert result == {"ok": True}
    assert seen["auth"] == "Bearer t0ken"
    assert seen["path"] == "/things"


# --- Jira ---


def test_jira_create_issue_hits_correct_endpoint():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization", "")
        return _json({"key": "PROJ-1", "id": "10001"})

    conn = JiraConnector(
        {"base_url": "https://org.atlassian.net", "email": "a@b.com", "api_token": "x"},
        transport=httpx.MockTransport(handler),
    )
    assert {t.name for t in conn.tools} == {
        "create_issue",
        "get_issue",
        "update_issue",
        "search_issues",
        "add_comment",
    }
    create = next(t for t in conn.tools if t.name == "create_issue")
    out = create.invoke({"project_key": "PROJ", "summary": "Bug"})
    assert out["key"] == "PROJ-1"
    assert seen["path"] == "/rest/api/3/issue"
    assert seen["auth"].startswith("Basic ")


# --- ServiceNow ---


def test_servicenow_get_ticket_status():
    def handler(request):
        assert request.url.path == "/api/now/table/incident"
        return _json({"result": [{"number": "INC0010001", "state": "2"}]})

    conn = ServiceNowConnector(
        {
            "instance_url": "https://dev.service-now.com",
            "username": "admin",
            "password": "pw",
        },
        transport=httpx.MockTransport(handler),
    )
    assert {t.name for t in conn.tools} == {
        "create_ticket",
        "update_ticket",
        "get_ticket_status",
        "search_kb",
    }
    tool = next(t for t in conn.tools if t.name == "get_ticket_status")
    out = tool.invoke({"number": "INC0010001"})
    assert out["result"][0]["number"] == "INC0010001"


# --- Outlook (OAuth2 token + Graph) ---


def test_outlook_acquires_token_then_sends_mail():
    calls = []

    def handler(request):
        host = request.url.host
        calls.append((host, request.url.path))
        if host == "login.microsoftonline.com":
            return _json({"access_token": "tok-123", "expires_in": 3600})
        # graph request must carry the bearer token
        assert request.headers.get("authorization") == "Bearer tok-123"
        return _json({"status": "sent"})

    conn = OutlookConnector(
        {
            "client_id": "cid",
            "client_secret": "secret",
            "tenant_id": "tid",
            "mailbox": "bot@org.com",
        },
        transport=httpx.MockTransport(handler),
    )
    assert {t.name for t in conn.tools} == {
        "send_email",
        "read_emails",
        "create_calendar_event",
        "get_availability",
        "create_teams_meeting",
        "send_teams_message",
        "list_todo_lists",
        "list_todo_tasks",
        "create_todo_task",
        "search_onedrive",
        "read_onedrive_file",
    }
    send = next(t for t in conn.tools if t.name == "send_email")
    out = send.invoke({"to": "x@y.com", "subject": "Hi", "body": "Hello"})
    assert out == {"status": "sent"}
    # token endpoint hit first, then the Graph sendMail endpoint
    assert calls[0][0] == "login.microsoftonline.com"
    assert calls[1][1] == "/v1.0/users/bot@org.com/sendMail"


def test_outlook_missing_config_raises():
    import pytest

    with pytest.raises(ValueError):
        OutlookConnector({"client_id": "cid"})


# --- SharePoint (Graph, shares OAuth2 base) ---


def test_sharepoint_search_uses_token_and_drive_path():
    def handler(request):
        if request.url.host == "login.microsoftonline.com":
            return _json({"access_token": "spt", "expires_in": 3600})
        assert request.headers.get("authorization") == "Bearer spt"
        assert request.url.path == "/v1.0/sites/SITE/drive/root/search(q='policy')"
        return _json({"value": [{"name": "policy.docx"}]})

    conn = SharePointConnector(
        {
            "client_id": "c",
            "client_secret": "s",
            "tenant_id": "t",
            "site_id": "SITE",
        },
        transport=httpx.MockTransport(handler),
    )
    assert {t.name for t in conn.tools} == {
        "search_documents",
        "get_document",
        "list_files",
        "upload_file",
    }
    tool = next(t for t in conn.tools if t.name == "search_documents")
    out = tool.invoke({"query": "policy"})
    assert out["value"][0]["name"] == "policy.docx"


# --- GitHub ---


def test_github_get_issue():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["api_version"] = request.headers.get("x-github-api-version")
        return _json({"number": 7, "title": "Bug"})

    conn = GitHubConnector(
        {"token": "ghp_x"}, transport=httpx.MockTransport(handler)
    )
    assert {t.name for t in conn.tools} == {
        "get_issue",
        "create_issue",
        "search_issues",
        "add_comment",
        "get_file",
        "list_repos",
    }
    tool = next(t for t in conn.tools if t.name == "get_issue")
    out = tool.invoke({"repo": "rhealaloo45/roscoe", "number": 7})
    assert out["number"] == 7
    assert seen["path"] == "/repos/rhealaloo45/roscoe/issues/7"
    assert seen["auth"] == "Bearer ghp_x"
    assert seen["api_version"] == "2022-11-28"


# --- Notion ---


def test_notion_search_sends_version_header():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["version"] = request.headers.get("notion-version")
        seen["auth"] = request.headers.get("authorization")
        return _json({"results": [{"id": "page1"}]})

    conn = NotionConnector(
        {"token": "secret_x"}, transport=httpx.MockTransport(handler)
    )
    assert {t.name for t in conn.tools} == {
        "search",
        "get_page",
        "create_page",
        "query_database",
        "append_block",
    }
    tool = next(t for t in conn.tools if t.name == "search")
    out = tool.invoke({"query": "roadmap"})
    assert out["results"][0]["id"] == "page1"
    assert seen["path"] == "/v1/search"
    assert seen["version"] == "2022-06-28"
    assert seen["auth"] == "Bearer secret_x"


# --- Snowflake (SQL, injected connection — no driver needed) ---


class _FakeCursor:
    description = [("ID",), ("NAME",)]

    def execute(self, sql):
        self.sql = sql

    def fetchmany(self, n):
        return [(1, "alpha"), (2, "beta")]

    def close(self):
        pass


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def close(self):
        pass


def test_snowflake_run_query_returns_row_dicts():
    conn = SnowflakeConnector(
        {"account": "acct", "user": "u"}, connection=_FakeConn()
    )
    assert {t.name for t in conn.tools} == {
        "run_query",
        "list_tables",
        "describe_table",
    }
    tool = next(t for t in conn.tools if t.name == "run_query")
    rows = tool.invoke({"sql": "SELECT id, name FROM t"})
    assert rows == [{"ID": 1, "NAME": "alpha"}, {"ID": 2, "NAME": "beta"}]


def test_snowflake_missing_driver_message(monkeypatch):
    import builtins
    import pytest

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("snowflake"):
            raise ImportError("no driver")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match="roscoe\\[snowflake\\]"):
        SnowflakeConnector({"account": "a", "user": "u"})  # no injected connection
