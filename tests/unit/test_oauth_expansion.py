"""OAuth added wherever a connector's platform actually supports it, alongside
its original simpler auth so existing configs keep working unchanged.

rest_api: a generic oauth mode (client-credentials-shaped, same field names
the exported standalone file already uses). jira: OAuth 2.0 (3LO), auto-
selected over the API-token mode when the OAuth keys are set. servicenow:
OAuth 2.0 (password or client_credentials grant), same auto-selection over
Basic. Every case is mocked at the transport — no live calls, no tokens.
"""

import httpx
import pytest

from roscoe.connectors import JiraConnector, RESTConnector, ServiceNowConnector
from roscoe.workflow.registry import build_connectors


def _tool(conn, name):
    return next(t for t in conn.tools if t.name == name)


# --- RESTConnector: oauth mode ---


def test_rest_oauth_exchanges_the_token_form_and_sends_a_bearer_token():
    seen = {}

    def handler(request):
        if request.url.host == "auth.test":
            seen["form"] = request.content.decode()
            seen["content_type"] = request.headers.get("content-type", "")
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    conn = RESTConnector(
        {
            "base_url": "https://api.test", "auth": "oauth",
            "token_url": "https://auth.test/token",
            "token_form": {"grant_type": "client_credentials", "client_id": "c", "client_secret": "s"},
        },
        transport=httpx.MockTransport(handler),
    )

    out = _tool(conn, "rest_get").invoke({"path": "/x"})

    assert out == {"ok": True}
    assert seen["auth"] == "Bearer tok-1"
    assert seen["content_type"].startswith("application/x-www-form-urlencoded")
    assert "grant_type=client_credentials" in seen["form"]


def test_rest_oauth_token_is_cached_across_calls():
    token_requests = []

    def handler(request):
        if request.url.host == "auth.test":
            token_requests.append(1)
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        return httpx.Response(200, json={"ok": True})

    conn = RESTConnector(
        {"base_url": "https://api.test", "auth": "oauth", "token_url": "https://auth.test/token",
         "token_form": {}},
        transport=httpx.MockTransport(handler),
    )

    get_tool = _tool(conn, "rest_get")
    get_tool.invoke({"path": "/a"})
    get_tool.invoke({"path": "/b"})

    assert len(token_requests) == 1


def test_rest_oauth_without_a_token_url_says_so_clearly():
    conn = RESTConnector(
        {"base_url": "https://api.test", "auth": "oauth"},
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )

    with pytest.raises(ValueError) as exc:
        _tool(conn, "rest_get").invoke({"path": "/x"})
    assert "token_url" in str(exc.value)


def test_rest_bearer_mode_still_works_unchanged():
    """The pre-existing modes must not regress."""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    conn = RESTConnector(
        {"base_url": "https://api.test", "auth": "bearer", "token": "plain-tok"},
        transport=httpx.MockTransport(handler),
    )
    _tool(conn, "rest_get").invoke({"path": "/x"})

    assert seen["auth"] == "Bearer plain-tok"


# --- Jira: OAuth 2.0 (3LO) vs API token ---


_JIRA_OAUTH = {"cloud_id": "cid1", "client_id": "c", "client_secret": "s", "refresh_token": "r"}


def test_jira_oauth_mode_hits_the_atlassian_gateway_with_a_bearer_token():
    seen = {}

    def handler(request):
        if request.url.host == "auth.atlassian.com":
            seen["form"] = request.content.decode()
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"key": "PROJ-1"})

    conn = JiraConnector(_JIRA_OAUTH, transport=httpx.MockTransport(handler))

    out = _tool(conn, "get_issue").invoke({"issue_key": "PROJ-1"})

    assert out == {"key": "PROJ-1"}
    assert seen["auth"] == "Bearer tok"
    assert "api.atlassian.com/ex/jira/cid1" in seen["url"]
    assert "grant_type=refresh_token" in seen["form"]


def test_jira_oauth_token_is_cached_across_calls():
    token_requests = []

    def handler(request):
        if request.url.host == "auth.atlassian.com":
            token_requests.append(1)
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, json={"key": "PROJ-1"})

    conn = JiraConnector(_JIRA_OAUTH, transport=httpx.MockTransport(handler))
    get = _tool(conn, "get_issue")
    get.invoke({"issue_key": "PROJ-1"})
    get.invoke({"issue_key": "PROJ-2"})

    assert len(token_requests) == 1


def test_jira_falls_back_to_api_token_when_oauth_keys_are_absent():
    """Existing configs (base_url/email/api_token, no OAuth keys) must keep
    working exactly as before this feature landed."""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"key": "PROJ-1"})

    conn = JiraConnector(
        {"base_url": "https://x.atlassian.net", "email": "e@x.com", "api_token": "tok"},
        transport=httpx.MockTransport(handler),
    )
    _tool(conn, "get_issue").invoke({"issue_key": "PROJ-1"})

    assert seen["auth"].startswith("Basic ")


def test_jira_with_neither_auth_mode_configured_is_refused():
    with pytest.raises(ValueError) as exc:
        JiraConnector({"base_url": "https://x.atlassian.net"})
    assert "email" in str(exc.value)
    assert "OAuth" in str(exc.value)


def test_jira_is_reachable_from_a_connectors_block_in_oauth_mode():
    built = build_connectors({"jira": {"type": "jira", **_JIRA_OAUTH}})
    assert type(built["jira"]).__name__ == "JiraConnector"


# --- ServiceNow: OAuth 2.0 (password + client_credentials) vs Basic ---


def test_servicenow_oauth_password_grant():
    seen = {}

    def handler(request):
        if request.url.path.endswith("oauth_token.do"):
            seen["form"] = request.content.decode()
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1800})
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"result": []})

    conn = ServiceNowConnector(
        {"instance_url": "https://x.service-now.com", "client_id": "c", "client_secret": "s",
         "username": "u", "password": "p"},
        transport=httpx.MockTransport(handler),
    )
    _tool(conn, "get_ticket_status").invoke({"number": "INC1"})

    assert seen["auth"] == "Bearer tok"
    assert "grant_type=password" in seen["form"]
    assert "username=u" in seen["form"]


def test_servicenow_oauth_client_credentials_when_no_user_given():
    seen = {}

    def handler(request):
        if request.url.path.endswith("oauth_token.do"):
            seen["form"] = request.content.decode()
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1800})
        return httpx.Response(200, json={"result": []})

    conn = ServiceNowConnector(
        {"instance_url": "https://x.service-now.com", "client_id": "c", "client_secret": "s"},
        transport=httpx.MockTransport(handler),
    )
    _tool(conn, "get_ticket_status").invoke({"number": "INC1"})

    assert "grant_type=client_credentials" in seen["form"]
    assert "username" not in seen["form"]


def test_servicenow_falls_back_to_basic_when_no_client_id():
    """Existing configs (instance_url/username/password, no OAuth keys) must
    keep working exactly as before this feature landed."""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"result": []})

    conn = ServiceNowConnector(
        {"instance_url": "https://x.service-now.com", "username": "u", "password": "p"},
        transport=httpx.MockTransport(handler),
    )
    _tool(conn, "get_ticket_status").invoke({"number": "INC1"})

    assert seen["auth"].startswith("Basic ")


def test_servicenow_with_neither_auth_mode_configured_is_refused():
    with pytest.raises(ValueError) as exc:
        ServiceNowConnector({"instance_url": "https://x.service-now.com"})
    assert "username" in str(exc.value)
    assert "OAuth" in str(exc.value)
