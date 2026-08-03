"""POST /webhook — letting an external service start a workflow.

Only a workflow that actually declares a webhook trigger gets the endpoint;
everything else keeps returning 404, so a project can't be poked by surprise
just because `roscoe run` happens to be listening.
"""

import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from roscoe.cli.run_web import serve_chat


class _Node:
    def __init__(self, node_type, kind="schedule"):
        self.type = node_type
        self.kind = kind


class _Workflow:
    def __init__(self, nodes):
        self.nodes = nodes


class _Result:
    status = "success"
    output = "ok"
    error = None
    run_id = "r1"
    total_tokens = 0
    cost_usd = None
    tool_calls = []
    pending_action = None


class _WorkflowAgent:
    agent_name = "demo"
    provider = "openai"
    model = "gpt-4o-mini"

    def __init__(self, nodes):
        self.workflow = _Workflow(nodes)
        self.received = None

    def run(self, payload, user_id=None, session_id=None):
        self.received = payload
        return _Result()


def _serve(agent, **kwargs):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    thread = threading.Thread(
        target=serve_chat,
        args=(agent,),
        kwargs={"host": "127.0.0.1", "port": port, "open_browser": False, **kwargs},
        daemon=True,
    )
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/", timeout=1).read()
            return base, agent
        except urllib.error.HTTPError:
            return base, agent
        except OSError:
            threading.Event().wait(0.05)
    raise RuntimeError("server did not start")


def _post(url, body):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=5)


def test_a_webhook_trigger_exposes_the_endpoint():
    base, agent = _serve(_WorkflowAgent([_Node("trigger", "webhook")]))

    response = _post(base + "/webhook", {"topic": "ai agents"})

    assert response.status == 200
    assert json.loads(response.read())["output"] == "ok"


def test_the_request_body_becomes_the_workflow_input_verbatim():
    """No 'message' fallback here — a webhook caller is a service posting
    structured data, not someone typing a chat sentence."""
    base, agent = _serve(_WorkflowAgent([_Node("trigger", "webhook")]))

    _post(base + "/webhook", {"topic": "ai agents", "urgency": "high"})

    assert agent.received == {"topic": "ai agents", "urgency": "high"}


def test_a_schedule_only_workflow_has_no_webhook_endpoint():
    base, _ = _serve(_WorkflowAgent([_Node("trigger", "schedule")]))

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base + "/webhook", {})
    assert exc.value.code == 404


def test_a_workflow_with_no_trigger_at_all_has_no_webhook_endpoint():
    base, _ = _serve(_WorkflowAgent([]))

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base + "/webhook", {})
    assert exc.value.code == 404


def test_a_plain_agent_without_a_workflow_has_no_webhook_endpoint():
    """The endpoint only makes sense for a workflow — a bare ReAct agent has
    no trigger node to opt in with."""
    class _PlainAgent:
        agent_name = "demo"
        provider = "openai"
        model = "gpt-4o-mini"

        def run(self, message, user_id=None, session_id=None):
            return _Result()

    base, _ = _serve(_PlainAgent())

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base + "/webhook", {})
    assert exc.value.code == 404
