"""Connectors, once roscoe isn't there to run them.

Every test here loads the *generated* file with roscoe blocked from the import
system and calls its tools, because the thing being checked is whether the
export works standalone — asserting on the generator's output as a string would
pass just as happily for code that doesn't run.
"""

import importlib.util
import itertools
import json
import sys

import httpx
import pytest

from roscoe.export import ExportError, generate_python
from roscoe.workflow.schema import Workflow

MODEL = {"provider": "openai", "model": "gpt-4o-mini", "api_key": "${DEMO_KEY}"}


def _export(connectors, nodes, *, model=MODEL):
    flow = {"entry": nodes[0]["id"], "output": "{{ out }}", "nodes": nodes}
    config = {"agent_name": "demo", "model": model, "connectors": connectors}
    return generate_python(Workflow.from_dict(flow, {}), config, name="demo")


#: Bumped per load so two exports in one test never collide in the bytecode
#: cache — same filename plus a coarse mtime means the second import silently
#: replays the first one's .pyc.
_LOADS = itertools.count()


def _load(source, tmp_path, monkeypatch):
    """Import generated source with roscoe blocked, proving it stands alone."""
    name = f"demo_agent_{next(_LOADS)}"
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")

    class _Blocker:
        def find_module(self, name, path=None):
            if name == "roscoe" or name.startswith("roscoe."):
                raise ImportError("roscoe is deliberately unavailable")

    monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serve(module, monkeypatch, handler):
    """Point the generated file's httpx at a mock transport."""
    real_client = httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )


def _one_node(connector, method, inputs):
    return [{"id": "step", "type": "connector_action", "connector": connector,
             "method": method, "inputs": inputs, "output": "out", "next": "END"}]


def _run(tmp_path, monkeypatch, connectors, nodes, handler):
    module = _load(_export(connectors, nodes), tmp_path, monkeypatch)
    _serve(module, monkeypatch, handler)
    return module.run({})


def _ok(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


# --- web search ---


def test_tavily_results_are_normalised_the_same_way_roscoe_normalises_them(
        tmp_path, monkeypatch):
    """The exported file has to agree with roscoe on the result shape, or a
    prompt written against `{{ results }}` in the editor breaks on export."""
    result = _run(
        tmp_path, monkeypatch,
        {"web": {"type": "web_search", "provider": "tavily", "api_key": "${K}"}},
        _one_node("web", "search", {"query": "a thing"}),
        _ok({"results": [{"title": "A", "url": "https://a.test", "content": "about A"}]}),
    )

    assert result["status"] == "success"
    assert result["state"]["out"] == {
        "query": "a thing",
        "results": [{"title": "A", "url": "https://a.test", "snippet": "about A"}],
    }


def test_brave_and_serper_reach_their_own_endpoints(tmp_path, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"web": {"results": [
            {"title": "B", "url": "https://b.test", "description": "about B"}]}})

    result = _run(
        tmp_path, monkeypatch,
        {"web": {"type": "web_search", "provider": "brave", "api_key": "${K}"}},
        _one_node("web", "search", {"query": "b"}),
        handler,
    )

    assert "api.search.brave.com/res/v1/web/search" in seen["url"]
    assert seen["headers"]["x-subscription-token"] == ""   # ${K} unset in this env
    assert result["state"]["out"]["results"][0]["snippet"] == "about B"


def test_max_results_is_enforced_even_if_the_provider_ignores_it(tmp_path, monkeypatch):
    result = _run(
        tmp_path, monkeypatch,
        {"web": {"type": "web_search", "provider": "tavily", "api_key": "${K}"}},
        _one_node("web", "search", {"query": "x", "max_results": 2}),
        _ok({"results": [{"title": str(i), "url": "", "content": ""} for i in range(9)]}),
    )

    assert len(result["state"]["out"]["results"]) == 2


def test_the_search_alias_exports_the_same_as_web_search(tmp_path, monkeypatch):
    """`type: search` is accepted in a connectors block, so it must export too."""
    result = _run(
        tmp_path, monkeypatch,
        {"web": {"type": "search", "api_key": "${K}"}},
        _one_node("web", "search", {"query": "x"}),
        _ok({"results": []}),
    )

    assert result["status"] == "success"


def test_an_unknown_search_provider_is_refused_by_name():
    with pytest.raises(ExportError) as exc:
        _export({"web": {"type": "web_search", "provider": "askjeeves"}},
                _one_node("web", "search", {"query": "x"}))

    assert "askjeeves" in str(exc.value)
    assert "tavily" in str(exc.value)     # says what would work


# --- email ---


class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in = None
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def _mail_agent(tmp_path, monkeypatch, port=587):
    module = _load(_export(
        {"mail": {"type": "smtp", "host": "smtp.test", "port": port,
                  "username": "me@test", "password": "pw"}},
        _one_node("mail", "send_email",
                  {"to": "you@test", "subject": "Hi", "body": "Hello there"}),
    ), tmp_path, monkeypatch)
    return module.run({})


def test_email_sends_the_message_it_was_given(tmp_path, monkeypatch, fake_smtp):
    result = _mail_agent(tmp_path, monkeypatch)

    message = fake_smtp.instances[0].sent[0]
    assert message["To"] == "you@test"
    assert message["From"] == "me@test"
    assert message["Subject"] == "Hi"
    assert "Hello there" in message.get_content()
    assert result["state"]["out"] == {"sent": True, "to": "you@test", "subject": "Hi"}


def test_port_587_upgrades_with_starttls_and_465_does_not(
        tmp_path, monkeypatch, fake_smtp):
    """SMTP_SSL is already encrypted — calling starttls on it is an error."""
    _mail_agent(tmp_path, monkeypatch, port=587)
    assert fake_smtp.instances[0].started_tls is True

    _FakeSMTP.instances = []
    _mail_agent(tmp_path, monkeypatch, port=465)
    assert fake_smtp.instances[0].started_tls is False


def test_an_email_agent_carries_no_search_or_jira_code():
    """Only what the workflow calls gets emitted, so the file stays readable."""
    source = _export(
        {"mail": {"type": "smtp", "host": "h", "username": "u", "password": "p"}},
        _one_node("mail", "send_email", {"to": "a@b", "subject": "s", "body": "b"}),
    )

    assert "_smtp_send_email" in source
    assert "_web_search_search" not in source
    assert "_jira_create_issue" not in source


# --- SMS ---


def test_sms_posts_form_encoded_with_basic_auth(tmp_path, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

    result = _run(
        tmp_path, monkeypatch,
        {"sms": {"type": "twilio", "account_sid": "AC1", "auth_token": "tok",
                 "from": "+15550000000"}},
        _one_node("sms", "send_sms", {"to": "+447700900123", "body": "Deploy finished"}),
        handler,
    )

    assert "/2010-04-01/Accounts/AC1/Messages.json" in seen["url"]
    assert "To=%2B447700900123" in seen["body"]      # form-encoded, not JSON
    assert "Deploy+finished" in seen["body"]
    assert seen["auth"].startswith("Basic ")
    assert result["state"]["out"]["sid"] == "SM1"


def test_sms_without_a_from_number_says_so_rather_than_failing_at_twilio(
        tmp_path, monkeypatch):
    result = _run(
        tmp_path, monkeypatch,
        {"sms": {"type": "twilio", "account_sid": "AC1", "auth_token": "tok"}},
        _one_node("sms", "send_sms", {"to": "+1", "body": "x"}),
        _ok({}),
    )

    assert result["status"] == "error"
    assert "from" in result["error"]


# --- GitHub / Jira / ServiceNow / Notion ---


def test_github_builds_the_issue_path_and_sends_its_api_version(tmp_path, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"number": 7, "title": "Broken"})

    result = _run(
        tmp_path, monkeypatch,
        {"gh": {"type": "github", "token": "${GH}"}},
        _one_node("gh", "get_issue", {"repo": "acme/app", "number": 7}),
        handler,
    )

    assert seen["url"] == "https://api.github.com/repos/acme/app/issues/7"
    assert seen["headers"]["x-github-api-version"] == "2022-11-28"
    assert result["state"]["out"]["title"] == "Broken"


def test_jira_wraps_comment_text_in_atlassian_document_format(tmp_path, monkeypatch):
    """Jira v3 rejects a plain string here, so the wrapper has to survive export."""
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(201, json={"id": "1"})

    _run(
        tmp_path, monkeypatch,
        {"jira": {"type": "jira", "base_url": "https://acme.atlassian.net/",
                  "email": "me@acme.com", "api_token": "${JT}"}},
        _one_node("jira", "add_comment", {"issue_key": "PROJ-1", "body": "on it"}),
        handler,
    )

    assert seen["auth"].startswith("Basic ")
    assert seen["body"]["body"]["type"] == "doc"
    assert seen["body"]["body"]["content"][0]["content"][0]["text"] == "on it"


def test_servicenow_creates_an_incident_on_the_table_api(tmp_path, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"result": {"number": "INC001"}})

    result = _run(
        tmp_path, monkeypatch,
        {"snow": {"type": "servicenow", "instance_url": "https://dev.service-now.com",
                  "username": "a", "password": "b"}},
        _one_node("snow", "create_ticket", {"short_description": "laptop dead"}),
        handler,
    )

    assert seen["url"].endswith("/api/now/table/incident")
    assert result["state"]["out"]["result"]["number"] == "INC001"


def test_notion_sends_the_version_header_it_was_configured_with(tmp_path, monkeypatch):
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"results": []})

    _run(
        tmp_path, monkeypatch,
        {"n": {"type": "notion", "token": "${NT}", "version": "2022-06-28"}},
        _one_node("n", "search", {"query": "policy"}),
        handler,
    )

    assert seen["headers"]["notion-version"] == "2022-06-28"


# --- TickTick ---


def test_ticktick_maps_named_priorities_to_its_sparse_scale(tmp_path, monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "t1"})

    _run(
        tmp_path, monkeypatch,
        {"tt": {"type": "ticktick", "token": "${TT}", "default_project_id": "p1"}},
        _one_node("tt", "create_task", {"title": "Ship it", "priority": "high"}),
        handler,
    )

    assert seen["body"]["priority"] == 5      # 'high', not a made-up 2
    assert seen["body"]["projectId"] == "p1"  # the configured default


def test_a_ticktick_task_with_no_project_anywhere_says_so(tmp_path, monkeypatch):
    result = _run(
        tmp_path, monkeypatch,
        {"tt": {"type": "ticktick", "token": "${TT}"}},
        _one_node("tt", "create_task", {"title": "Follow up"}),
        _ok({}),
    )

    assert result["status"] == "error"
    assert "default_project_id" in result["error"]


# --- SQLite ---


def test_sqlite_queries_a_real_database_with_no_server_and_no_driver(
        tmp_path, monkeypatch):
    """No mock here: the exported file talks to sqlite3 from the standard
    library, so this is the real thing end to end."""
    import sqlite3

    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (id TEXT, name TEXT)")
    conn.execute("INSERT INTO users VALUES ('E-1', 'Priya')")
    conn.commit()
    conn.close()

    module = _load(_export(
        {"db": {"type": "database", "path": str(db)}},
        _one_node("db", "query",
                  {"sql": "SELECT name FROM users WHERE id = ?", "params": ["E-1"]}),
    ), tmp_path, monkeypatch)

    assert module.run({})["state"]["out"] == [{"name": "Priya"}]


def test_sqlite_writes_stay_refused_unless_the_config_allowed_them(
        tmp_path, monkeypatch):
    """An export shouldn't quietly become more permissive than what you tested."""
    db = tmp_path / "app.db"
    module = _load(_export(
        {"db": {"type": "database", "path": str(db)}},
        _one_node("db", "execute", {"sql": "DELETE FROM users"}),
    ), tmp_path, monkeypatch)

    result = module.run({})

    assert result["status"] == "error"
    assert "read-only" in result["error"]


def test_a_second_statement_behind_a_semicolon_is_refused(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    module = _load(_export(
        {"db": {"type": "database", "path": str(db), "read_only": False}},
        _one_node("db", "query", {"sql": "SELECT 1; DROP TABLE users"}),
    ), tmp_path, monkeypatch)

    assert "one statement" in module.run({})["error"]


def test_a_database_driver_that_needs_a_package_is_refused():
    with pytest.raises(ExportError) as exc:
        _export({"db": {"type": "database", "driver": "psycopg2", "dsn": "x"}},
                _one_node("db", "query", {"sql": "SELECT 1"}))

    assert "psycopg2" in str(exc.value)
    assert "sqlite" in str(exc.value)


# --- OAuth connectors ---

GOOGLE = {"type": "google_workspace", "client_id": "cid",
          "client_secret": "${G_SECRET}", "refresh_token": "${G_REFRESH}"}
GRAPH = {"client_id": "cid", "client_secret": "${MS_SECRET}",
         "tenant_id": "tenant-1"}


def _oauth_handler(on_call, *, token="tok-1", expires_in=3600):
    """Answers the token endpoint, then hands everything else to `on_call`."""
    def handler(request):
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url or "/oauth2/v2.0/token" in url:
            on_call.setdefault("tokens", []).append(
                {"url": url, "body": request.content.decode(),
                 "content_type": request.headers.get("content-type", "")})
            return httpx.Response(200, json={"access_token": token,
                                             "expires_in": expires_in})
        on_call.setdefault("calls", []).append(
            {"url": url, "auth": request.headers.get("authorization", ""),
             "body": request.content.decode()})
        return httpx.Response(200, json=on_call.get("reply", {"ok": True}))
    return handler


def test_google_exchanges_its_refresh_token_before_calling_gmail(
        tmp_path, monkeypatch):
    """The whole point of phase 3: an exported file mints its own token."""
    seen = {"reply": {"messages": [{"id": "m1"}]}}

    module = _load(_export(
        {"gmail": GOOGLE}, _one_node("gmail", "read_emails", {"max_results": 1}),
    ), tmp_path, monkeypatch)
    _serve(module, monkeypatch, _oauth_handler(seen))
    module.run({})

    token_call = seen["tokens"][0]
    assert "oauth2.googleapis.com/token" in token_call["url"]
    assert "grant_type=refresh_token" in token_call["body"]
    # Google 400s a form body whose header claims JSON — roscoe hit this for real.
    assert token_call["content_type"].startswith("application/x-www-form-urlencoded")
    assert all(c["auth"] == "Bearer tok-1" for c in seen["calls"])


def test_the_token_is_fetched_once_and_reused_across_steps(tmp_path, monkeypatch):
    """Two Gmail steps shouldn't mean two token exchanges."""
    seen = {"reply": {"messages": []}}

    module = _load(_export({"gmail": GOOGLE}, [
        {"id": "a", "type": "connector_action", "connector": "gmail",
         "method": "read_emails", "inputs": {}, "output": "one", "next": "b"},
        {"id": "b", "type": "connector_action", "connector": "gmail",
         "method": "read_emails", "inputs": {}, "output": "out", "next": "END"},
    ]), tmp_path, monkeypatch)
    _serve(module, monkeypatch, _oauth_handler(seen))
    module.run({})

    assert len(seen["tokens"]) == 1
    assert len(seen["calls"]) == 2


def test_an_expired_token_is_refetched_rather_than_reused(tmp_path, monkeypatch):
    """A token good for 30s is already stale under the 60s safety margin, so the
    second step has to go get another one."""
    seen = {"reply": {"messages": []}}

    module = _load(_export({"gmail": GOOGLE}, [
        {"id": "a", "type": "connector_action", "connector": "gmail",
         "method": "read_emails", "inputs": {}, "output": "one", "next": "b"},
        {"id": "b", "type": "connector_action", "connector": "gmail",
         "method": "read_emails", "inputs": {}, "output": "out", "next": "END"},
    ]), tmp_path, monkeypatch)
    _serve(module, monkeypatch, _oauth_handler(seen, expires_in=30))
    module.run({})

    assert len(seen["tokens"]) == 2


def test_gmail_read_emails_returns_metadata_not_bare_ids(tmp_path, monkeypatch):
    """Same shape roscoe's connector returns, so a digest prompt written in the
    editor still works after export."""
    def handler(request):
        url = str(request.url)
        if "/token" in url:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        # Substring, not endswith: the metadata fetch carries query params.
        if "/messages/m1" in url:
            return httpx.Response(200, json={
                "id": "m1", "snippet": "Can you review the deck?",
                "payload": {"headers": [
                    {"name": "From", "value": "priya@example.com"},
                    {"name": "Subject", "value": "Q3 deck"}]}})
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    result = _run(tmp_path, monkeypatch, {"gmail": GOOGLE},
                  _one_node("gmail", "read_emails", {"max_results": 1}), handler)

    assert result["state"]["out"] == {"emails": [{
        "id": "m1", "from": "priya@example.com", "subject": "Q3 deck",
        "snippet": "Can you review the deck?"}]}


def test_google_reaches_calendar_on_its_own_host_not_gmails(tmp_path, monkeypatch):
    """Google spreads its APIs across four hosts, so these tools pass absolute
    URLs rather than hanging off one base_url."""
    seen = {"reply": {"items": []}}

    result = _run(tmp_path, monkeypatch, {"gmail": GOOGLE},
                  _one_node("gmail", "list_events", {}), _oauth_handler(seen))

    assert result["status"] == "success"
    assert "googleapis.com/calendar/v3/calendars/primary/events" in seen["calls"][0]["url"]


def test_google_in_service_account_mode_is_refused_with_a_way_forward():
    """That mode signs a JWT with RSA — an exported file can't assume a crypto
    library is installed, so it says so rather than shipping a broken signer."""
    with pytest.raises(ExportError) as exc:
        _export({"gmail": {"type": "google_workspace",
                           "credentials_file": "sa.json", "subject": "me@org.com"}},
                _one_node("gmail", "read_emails", {}))

    message = str(exc.value)
    assert "refresh_token" in message
    assert "roscoe google-auth" in message


def test_outlook_uses_the_client_credentials_flow_against_its_tenant(
        tmp_path, monkeypatch):
    seen = {}

    result = _run(
        tmp_path, monkeypatch,
        {"mail": {"type": "outlook", **GRAPH, "mailbox": "exec@org.com"}},
        _one_node("mail", "send_email",
                  {"to": "a@b.com", "subject": "Hi", "body": "Hello"}),
        _oauth_handler(seen),
    )

    assert result["status"] == "success"
    token_call = seen["tokens"][0]
    # The tenant is usually an env placeholder, so the URL is completed at run
    # time rather than baked in at export.
    assert "login.microsoftonline.com/tenant-1/oauth2/v2.0/token" in token_call["url"]
    assert "grant_type=client_credentials" in token_call["body"]
    assert "graph.microsoft.com" in seen["calls"][0]["url"]
    assert "/users/exec@org.com/sendMail" in seen["calls"][0]["url"]


def test_sharepoint_uploads_to_the_configured_site(tmp_path, monkeypatch):
    seen = {}

    result = _run(
        tmp_path, monkeypatch,
        {"docs": {"type": "sharepoint", **GRAPH, "site_id": "site-9"}},
        _one_node("docs", "upload_file", {"name": "notes.txt", "content": "hello"}),
        _oauth_handler(seen),
    )

    assert result["status"] == "success"
    assert "/sites/site-9/drive/root:/notes.txt:/content" in seen["calls"][0]["url"]
    assert seen["calls"][0]["body"] == "hello"


@pytest.mark.parametrize("missing", ["client_id", "client_secret", "tenant_id"])
def test_a_graph_connector_names_the_credential_it_is_missing(missing):
    config = {"type": "outlook", **GRAPH, "mailbox": "a@b.com"}
    del config[missing]

    with pytest.raises(ExportError) as exc:
        _export({"mail": config}, _one_node("mail", "read_emails", {}))

    assert missing in str(exc.value)


def test_oauth_secrets_stay_placeholders_too():
    source = _export({"gmail": GOOGLE}, _one_node("gmail", "read_emails", {}))

    assert "os.environ.get('G_SECRET', '')" in source
    assert "os.environ.get('G_REFRESH', '')" in source


# --- refusing what it still cannot do ---


def test_a_connector_needing_an_installed_driver_names_what_would_work():
    with pytest.raises(ExportError) as exc:
        _export({"warehouse": {"type": "snowflake"}},
                _one_node("warehouse", "query", {}))

    message = str(exc.value)
    assert "warehouse" in message
    assert "web search" in message      # lists the supported ones in plain language
    assert "Google Workspace" in message


def test_a_tool_the_connector_does_not_have_is_caught_at_export_not_at_runtime():
    """Catching this here means a typo surfaces in the editor, not at 3am in
    someone else's service."""
    with pytest.raises(ExportError) as exc:
        _export({"gh": {"type": "github", "token": "t"}},
                _one_node("gh", "merge_pull_request", {}))

    assert "merge_pull_request" in str(exc.value)
    assert "create_issue" in str(exc.value)   # says what it does offer


def test_secrets_stay_placeholders_across_every_connector_type():
    """An exported file gets committed. A resolved key in it would be a leak."""
    source = _export(
        {"web": {"type": "web_search", "api_key": "${SEARCH_KEY}"},
         "gh": {"type": "github", "token": "${GH_TOKEN}"}},
        [{"id": "a", "type": "connector_action", "connector": "web", "method": "search",
          "inputs": {"query": "x"}, "output": "r", "next": "b"},
         {"id": "b", "type": "connector_action", "connector": "gh",
          "method": "list_repos", "inputs": {}, "output": "out", "next": "END"}],
    )

    assert "os.environ.get('SEARCH_KEY', '')" in source
    assert "os.environ.get('GH_TOKEN', '')" in source


def test_a_dotenv_beside_the_file_is_read_but_never_beats_a_real_variable(
        tmp_path, monkeypatch):
    """The zip ships a .env.example, so the file has to actually read a .env —
    and a container's own configuration has to keep winning."""
    (tmp_path / ".env").write_text(
        'FROM_FILE=file-value\nDEMO_KEY="quoted"\n# a comment\nREAL=from-file\n',
        encoding="utf-8")
    monkeypatch.setenv("REAL", "from-environment")

    _load(_export({}, [{"id": "a", "type": "llm_step", "prompt": "hi",
                        "output": "out", "next": "END"}]), tmp_path, monkeypatch)

    import os
    assert os.environ["FROM_FILE"] == "file-value"
    assert os.environ["DEMO_KEY"] == "quoted"      # quotes stripped
    assert os.environ["REAL"] == "from-environment"
