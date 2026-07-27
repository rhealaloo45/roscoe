"""Phase 9 — template tests.

Templates are exercised with mocked connectors / a local keyword knowledge store, so no
live HR API, ServiceNow, or LLM is needed.
"""

import httpx
import pytest

from roscoe.connectors import (
    GoogleWorkspaceConnector,
    NotionConnector,
    OutlookConnector,
    RESTConnector,
    ServiceNowConnector,
)
from roscoe.memory.knowledge import KnowledgeMemory
from roscoe.templates import available_templates, template_path
from roscoe.templates.exec_assistant_agent.tools.exec_tools import build_tools as build_exec_tools
from roscoe.templates.google_workspace_agent.tools.gws_tools import build_tools as build_gws_tools
from roscoe.templates.hr_agent.tools.hr_tools import build_tools as build_hr_tools
from roscoe.templates.it_support_agent.tools.it_tools import build_tools as build_it_tools
from roscoe.templates.knowledge_base_agent.tools.kb_tools import build_tools as build_kb_tools
from roscoe.templates.legal_agent.tools.legal_tools import build_tools as build_legal_tools


def _json(payload, status=200):
    return httpx.Response(status, json=payload)


# --- registry / packaging ---


def test_available_templates_and_files_exist():
    names = available_templates()
    assert set(names) == {
        "hr_agent",
        "it_support_agent",
        "legal_agent",
        "knowledge_base_agent",
        "exec_assistant_agent",
        "google_workspace_agent",
    }
    for name in names:
        d = template_path(name)
        assert (d / "agent_config.yaml").exists()
        assert (d / "prompts" / "system.txt").exists()


def test_template_path_unknown_raises():
    with pytest.raises(ValueError):
        template_path("nope_agent")


# --- HR template (REST connector, mocked) ---


def test_hr_get_leave_balance_via_rest():
    def handler(request):
        assert request.url.path == "/employees/E1/leave-balance"
        return _json({"employee_id": "E1", "remaining_days": 12})

    rest = RESTConnector(
        {"base_url": "https://hr.example.com", "auth": "bearer", "token": "t"},
        transport=httpx.MockTransport(handler),
    )
    tools = build_hr_tools(rest)
    names = {t.name for t in tools}
    assert {"get_leave_balance", "submit_leave_request", "get_payslip", "update_personal_details"} <= names

    get_balance = next(t for t in tools if t.name == "get_leave_balance")
    out = get_balance.invoke({"employee_id": "E1"})
    assert out["remaining_days"] == 12


def test_hr_policy_tool_added_with_knowledge():
    rest = RESTConnector(
        {"base_url": "https://hr.example.com", "auth": "bearer", "token": "t"},
        transport=httpx.MockTransport(lambda r: _json({})),
    )
    km = KnowledgeMemory.from_texts(
        ["Annual leave accrues at 1.75 days per month."],
        metadatas=[{"source": "leave_policy.pdf"}],
    )
    tools = build_hr_tools(rest, knowledge=km)
    policy = next(t for t in tools if t.name == "get_policy_document")
    hits = policy.invoke({"query": "leave accrual"})
    assert hits and hits[0]["source"] == "leave_policy.pdf"


# --- IT support template (ServiceNow, mocked) ---


def test_it_create_ticket_returns_number():
    def handler(request):
        assert request.url.path == "/api/now/table/incident"
        return _json({"result": {"number": "INC0012345", "sys_id": "abc123"}})

    snow = ServiceNowConnector(
        {"instance_url": "https://dev.service-now.com", "username": "a", "password": "b"},
        transport=httpx.MockTransport(handler),
    )
    tools = build_it_tools(snow)
    assert {t.name for t in tools} == {
        "create_ticket",
        "check_ticket_status",
        "escalate_ticket",
        "search_knowledge_base",
    }
    create = next(t for t in tools if t.name == "create_ticket")
    out = create.invoke({"short_description": "laptop won't boot"})
    assert out["number"] == "INC0012345"
    assert out["sys_id"] == "abc123"


# --- Legal template (knowledge memory, keyword store) ---


def test_legal_extract_clause_cites_source():
    km = KnowledgeMemory.from_texts(
        [
            "Limitation of liability: in no event shall either party's liability exceed fees paid.",
            "Term and termination: either party may terminate with 30 days notice.",
        ],
        metadatas=[{"source": "msa_acme.pdf"}, {"source": "msa_acme.pdf"}],
        top_k=2,
    )
    tools = build_legal_tools(km)
    assert {t.name for t in tools} == {
        "search_contracts",
        "extract_clause",
        "compare_documents",
        "flag_risk",
    }
    extract = next(t for t in tools if t.name == "extract_clause")
    out = extract.invoke({"clause_type": "liability"})
    assert out["found"]
    assert "liability" in out["text"].lower()
    assert out["source"] == "msa_acme.pdf"


# --- Knowledge-base template (Notion + knowledge) ---


def test_kb_requires_a_source():
    with pytest.raises(ValueError):
        build_kb_tools()


def test_kb_knowledge_search_cites_source():
    km = KnowledgeMemory.from_texts(
        ["Remote work is allowed up to 3 days per week."],
        metadatas=[{"source": "remote_policy"}],
    )
    tools = build_kb_tools(knowledge=km)
    search = next(t for t in tools if t.name == "search_knowledge")
    hits = search.invoke({"query": "remote work days"})
    assert hits[0]["source"] == "remote_policy"


def test_kb_includes_notion_read_tools():
    def handler(request):
        return _json({"results": [{"id": "p1"}]})

    notion = NotionConnector({"token": "secret"}, transport=httpx.MockTransport(handler))
    tools = build_kb_tools(notion=notion)
    names = {t.name for t in tools}
    assert names == {"search", "get_page"}  # read-only subset, no create/append
    search = next(t for t in tools if t.name == "search")
    assert search.invoke({"query": "policy"})["results"][0]["id"] == "p1"


# --- Executive assistant template (Outlook) ---


def test_exec_assistant_sends_email_via_outlook():
    def handler(request):
        if request.url.host == "login.microsoftonline.com":
            return _json({"access_token": "tok", "expires_in": 3600})
        assert request.headers.get("authorization") == "Bearer tok"
        return _json({"status": "sent"})

    outlook = OutlookConnector(
        {"client_id": "c", "client_secret": "s", "tenant_id": "t", "mailbox": "exec@org.com"},
        transport=httpx.MockTransport(handler),
    )
    tools = build_exec_tools(outlook)
    assert {t.name for t in tools} == {
        "send_email",
        "read_emails",
        "create_calendar_event",
        "get_availability",
    }
    send = next(t for t in tools if t.name == "send_email")
    assert send.invoke({"to": "x@y.com", "subject": "Hi", "body": "Hello"}) == {"status": "sent"}


# --- Google Workspace template (Gmail + Calendar + Tasks + Drive) ---


def _google_handler(request):
    """Mock handler for Google APIs — returns minimal valid responses."""
    url = str(request.url)
    if "oauth2" in url or "token" in url:
        return _json({"access_token": "goog_tok", "expires_in": 3600})
    if "messages/send" in url:
        return _json({"id": "msg_1", "labelIds": ["SENT"]})
    if "/messages" in url:
        return _json({"messages": [{"id": "msg_1"}]})
    if "/events" in url and request.method == "POST":
        return _json({"id": "evt_1", "status": "confirmed"})
    if "/events" in url:
        return _json({"items": [{"id": "evt_1", "summary": "Standup"}]})
    if "/tasks" in url and request.method == "POST":
        return _json({"id": "task_1", "title": "Review PR"})
    if "/tasks" in url:
        return _json({"items": [{"id": "task_1"}]})
    if "/files" in url:
        return _json({"files": [{"id": "f1", "name": "report.pdf"}]})
    return _json({"ok": True})


def _make_google_connector(tmp_path):
    import json as _json_mod
    sa_file = tmp_path / "sa.json"
    sa_file.write_text(_json_mod.dumps({
        "client_email": "test@test.iam.gserviceaccount.com",
        "private_key": "fake",
        "token_uri": "https://oauth2.googleapis.com/token",
    }))
    return GoogleWorkspaceConnector(
        {"credentials_file": str(sa_file), "subject": "user@example.com"},
        transport=httpx.MockTransport(_google_handler),
    )


def test_google_workspace_tools_present(tmp_path):
    gws = _make_google_connector(tmp_path)
    tools = build_gws_tools(gws)
    assert {t.name for t in tools} == {
        "send_email",
        "read_emails",
        "list_events",
        "create_event",
        "list_tasks",
        "create_task",
        "search_drive",
        "read_drive_file",
    }


def test_google_workspace_read_emails(tmp_path):
    gws = _make_google_connector(tmp_path)
    tools = build_gws_tools(gws)
    read = next(t for t in tools if t.name == "read_emails")
    out = read.invoke({"max_results": 5})
    assert "messages" in out


def test_google_workspace_search_drive(tmp_path):
    gws = _make_google_connector(tmp_path)
    tools = build_gws_tools(gws)
    search = next(t for t in tools if t.name == "search_drive")
    out = search.invoke({"query": "name contains 'report'"})
    assert out["files"][0]["name"] == "report.pdf"
