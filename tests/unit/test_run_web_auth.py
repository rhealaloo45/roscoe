"""Guarding the chat API, so it can be pointed at from an existing application.

Until now `/api/chat` was open to anyone who could reach the port. That is fine
on localhost and not fine anywhere else, which made "call the agent from your
backend" advice we couldn't honestly give.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from roscoe.cli.run_web import check_auth, cors_headers, serve_chat


class _Headers(dict):
    """http.client.HTTPMessage is a mapping for our purposes."""


# --- the check itself ---


def test_no_key_configured_leaves_the_api_open():
    """A local `roscoe run` must stay as frictionless as it has always been."""
    assert check_auth(_Headers(), None) is True
    assert check_auth(_Headers({"Authorization": "nonsense"}), None) is True


def test_the_right_bearer_token_is_accepted():
    assert check_auth(_Headers({"Authorization": "Bearer s3cret"}), "s3cret") is True


@pytest.mark.parametrize("header", [
    {},                                    # nothing at all
    {"Authorization": ""},                 # empty
    {"Authorization": "s3cret"},           # key without the scheme
    {"Authorization": "Bearer wrong"},     # wrong key
    {"Authorization": "bearer s3cret"},    # scheme is case-sensitive
])
def test_anything_else_is_rejected(header):
    assert check_auth(_Headers(header), "s3cret") is False


# --- CORS ---


def test_no_origin_configured_sends_no_cors_headers():
    assert cors_headers(None) == {}


def test_a_configured_origin_is_allowed_with_the_headers_a_fetch_needs():
    out = cors_headers("https://app.example.com")

    assert out["Access-Control-Allow-Origin"] == "https://app.example.com"
    # Authorization must be allowed explicitly or the api-key flow can't be used
    # from a browser at all.
    assert "Authorization" in out["Access-Control-Allow-Headers"]
    assert "POST" in out["Access-Control-Allow-Methods"]


# --- against a real server ---


class _Agent:
    agent_name = "demo"
    provider = "openai"
    model = "gpt-4o-mini"

    class _Result:
        status = "success"
        output = "hello"
        error = None
        run_id = "r1"
        total_tokens = 3
        cost_usd = None
        tool_calls = []
        pending_action = None

    def run(self, message, user_id=None, session_id=None):
        return self._Result()


def _serve(**kwargs):
    """Start serve_chat on an ephemeral port and return its base URL."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    thread = threading.Thread(
        target=serve_chat,
        args=(_Agent(),),
        kwargs={"host": "127.0.0.1", "port": port, "open_browser": False, **kwargs},
        daemon=True,
    )
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(100):                       # wait for the socket to accept
        try:
            urllib.request.urlopen(base + "/", timeout=1).read()
            return base
        except urllib.error.HTTPError:
            return base
        except OSError:
            threading.Event().wait(0.05)
    raise RuntimeError("server did not start")


def _post(url, body, key=None):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {key}"} if key else {})},
    )
    return urllib.request.urlopen(request, timeout=5)


def test_a_guarded_server_rejects_a_call_without_the_key():
    base = _serve(api_key="s3cret")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base + "/api/chat", {"message": "hi"})

    assert exc.value.code == 401
    assert json.loads(exc.value.read())["error"] == "Unauthorized"


def test_a_guarded_server_accepts_the_call_with_the_key():
    base = _serve(api_key="s3cret")

    response = _post(base + "/api/chat", {"message": "hi"}, key="s3cret")

    assert response.status == 200
    assert json.loads(response.read())["output"] == "hello"


def test_the_page_itself_stays_reachable_without_a_key():
    """The key guards the API. Locking the page too would only stop someone
    seeing the login-less chat box, while breaking the local workflow."""
    base = _serve(api_key="s3cret")

    assert urllib.request.urlopen(base + "/", timeout=5).status == 200


def test_an_unguarded_server_still_works_exactly_as_before():
    base = _serve()

    response = _post(base + "/api/chat", {"message": "hi"})

    assert json.loads(response.read())["output"] == "hello"


def test_preflight_is_answered_so_a_cross_origin_post_can_follow():
    base = _serve(cors_origin="https://app.example.com")

    request = urllib.request.Request(base + "/api/chat", method="OPTIONS")
    response = urllib.request.urlopen(request, timeout=5)

    assert response.status == 204
    assert response.headers["Access-Control-Allow-Origin"] == "https://app.example.com"
