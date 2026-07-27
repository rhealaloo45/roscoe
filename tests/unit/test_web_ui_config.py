"""The optional ``ui:`` block — branding the built-in web page, and form mode.

The settings are interpolated straight into HTML and CSS, so the escaping and the
colour check matter as much as the rendering.
"""

from roscoe.cli.run_web import _clean_fields, _css_colour, _render_page, _UI_DEFAULTS


def _page(settings=None, fields=None):
    return _render_page({**_UI_DEFAULTS, **(settings or {})}, fields or [])


# --- settings reach the page ---


def test_defaults_render_without_any_config():
    page = _page()

    assert "__TITLE__" not in page and "__ACCENT__" not in page
    assert "roscoe" in page


def test_every_setting_is_substituted():
    page = _page({
        "title": "Ham Ventures", "subtitle": "IT desk", "heading": "Request access",
        "intro": "Fill this in.", "greeting": "Hello there", "placeholder": "Ask…",
        "submit": "Go",
    })

    for value in ("Ham Ventures", "IT desk", "Request access", "Fill this in.",
                  "Hello there", "Ask…", "Go"):
        assert value in page


def test_markup_in_a_setting_cannot_break_out():
    page = _page({"title": '<script>alert(1)</script>'})

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_accent_is_applied_as_a_css_variable():
    assert "--accent:#7c3aed" in _page({"accent": "#7c3aed"})


def test_only_colour_shaped_values_reach_the_stylesheet():
    assert _css_colour("#7c3aed") == "#7c3aed"
    assert _css_colour("#abc") == "#abc"
    # Anything else falls back rather than being interpolated into CSS.
    assert _css_colour("red; } body { display:none") == _UI_DEFAULTS["accent"]
    assert _css_colour("javascript:alert(1)") == _UI_DEFAULTS["accent"]
    assert _css_colour(None) == _UI_DEFAULTS["accent"]


# --- form fields ---


def test_fields_get_sensible_defaults_from_their_name():
    [field] = _clean_fields([{"name": "employee_id"}])

    assert field["label"] == "Employee id"
    assert field["type"] == "text"
    assert field["required"] is False


def test_a_bare_string_is_accepted_as_a_field():
    assert _clean_fields(["topic"])[0]["name"] == "topic"


def test_malformed_fields_are_dropped_not_fatal():
    fields = _clean_fields([{"label": "no name"}, None, 42, {"name": "ok"}])

    assert [f["name"] for f in fields] == ["ok"]


def test_select_fields_keep_their_options():
    [field] = _clean_fields([
        {"name": "action", "type": "select", "options": ["grant", "revoke"], "default": "grant"}
    ])

    assert field["options"] == ["grant", "revoke"]
    assert field["default"] == "grant"


def test_fields_are_embedded_for_the_page_to_render():
    page = _page(fields=_clean_fields([{"name": "employee_id", "required": True}]))

    assert "__FIELDS__" not in page
    assert '"employee_id"' in page


def test_no_fields_leaves_an_empty_list_so_chat_mode_stays():
    assert "const FIELDS=[];" in _page()


# --- live progress: polling /api/progress during a workflow run ---
#
# A workflow's run used to be one opaque blocking request — the page could show
# only a static "..." for however long the whole graph took. These start the
# real server (threaded HTTP, one dedicated worker thread for the agent call)
# and prove a concurrent poll actually observes the node mid-run, not just that
# the plumbing compiles.

import http.client
import json as _json
import socket
import threading
import time

from roscoe.cli.run_web import serve_chat
from roscoe.core.agent_result import AgentResult


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _SlowWorkflowAgent:
    """Reports two nodes with a pause between them, like a real multi-step run."""

    agent_name, provider, model = "demo", "test", "test-model"
    workflow = object()  # hasattr(agent, "workflow") is what switches on form mode

    def __init__(self):
        self._on_step = None

    def set_on_step(self, callback):
        self._on_step = callback

    def run(self, payload, *, user_id=None, session_id=None):
        if self._on_step:
            self._on_step("find_transcript")
        time.sleep(0.3)
        if self._on_step:
            self._on_step("summarise")
        time.sleep(0.1)
        return AgentResult(output="done", run_id="r1", total_tokens=5, cost_usd=0.01)


def _start_server(agent):
    port = _free_port()
    thread = threading.Thread(
        target=serve_chat, kwargs={
            "agent": agent, "host": "127.0.0.1", "port": port, "open_browser": False,
        },
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=0.2).connect()
            return port
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("server did not start")


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = _json.loads(resp.read())
    conn.close()
    return body


def _post(port, path, payload):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, body=_json.dumps(payload),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = _json.loads(resp.read())
    conn.close()
    return body


def test_progress_reports_no_steps_before_anything_runs():
    port = _start_server(_SlowWorkflowAgent())

    assert _get(port, "/api/progress") == {"steps": []}


def test_a_concurrent_poll_sees_the_steps_a_running_workflow_has_done():
    port = _start_server(_SlowWorkflowAgent())
    seen = []

    def poll_while_running():
        for _ in range(20):
            seen.append(list(_get(port, "/api/progress")["steps"]))
            time.sleep(0.05)

    poller = threading.Thread(target=poll_while_running)
    poller.start()
    result = _post(port, "/api/chat", {"inputs": {"topic": "x"}})
    poller.join(timeout=2)

    assert result["type"] == "final" and result["output"] == "done"
    # The chat POST blocked for ~0.4s total; a poll every 50ms on a genuinely
    # separate HTTP connection had to have caught it mid-flight to see this.
    assert any("find_transcript" in s or "summarise" in s for s in seen)


def test_the_steps_list_grows_in_order_as_a_checklist_would():
    port = _start_server(_SlowWorkflowAgent())
    seen = []

    def poll_while_running():
        for _ in range(20):
            seen.append(list(_get(port, "/api/progress")["steps"]))
            time.sleep(0.05)

    poller = threading.Thread(target=poll_while_running)
    poller.start()
    _post(port, "/api/chat", {"inputs": {"topic": "x"}})
    poller.join(timeout=2)

    # At the point both nodes had been seen, find_transcript must have arrived
    # first — that's what lets the UI mark it "done" once summarise starts.
    both = next((s for s in seen if len(s) >= 2), None)
    assert both is not None, f"never observed both steps in one poll: {seen}"
    assert both[:2] == ["find_transcript", "summarise"]


def test_steps_reset_when_the_next_run_starts():
    agent = _SlowWorkflowAgent()
    port = _start_server(agent)
    _post(port, "/api/chat", {"inputs": {"topic": "x"}})

    # Nothing clears it between runs — the finished checklist stays visible,
    # same as a terminal leaving its last output on screen.
    assert _get(port, "/api/progress") == {"steps": ["find_transcript", "summarise"]}

    seen = []

    def poll_while_running():
        for _ in range(20):
            seen.append(list(_get(port, "/api/progress")["steps"]))
            time.sleep(0.05)

    poller = threading.Thread(target=poll_while_running)
    poller.start()
    _post(port, "/api/chat", {"inputs": {"topic": "y"}})
    poller.join(timeout=2)

    # The second run resets to [] before reporting its own steps — a poller
    # that only ever saw the first run's stale two-item list would be a bug.
    assert any(len(s) < 2 for s in seen)


def test_a_plain_agent_without_set_on_step_never_breaks_the_endpoint():
    class _PlainAgent:
        agent_name, provider, model = "demo", "test", "test-model"

        def run(self, payload, *, user_id=None, session_id=None):
            return AgentResult(output="hi", run_id="r1")

    port = _start_server(_PlainAgent())

    assert _get(port, "/api/progress") == {"steps": []}
    result = _post(port, "/api/chat", {"message": "hi"})
    assert result["type"] == "final"
