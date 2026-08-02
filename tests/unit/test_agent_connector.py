"""One agent calling another.

The generic REST connector could always do this. What it couldn't do is hide
the envelope — that /api/chat is the path, that the answer is under `output`,
and that a failure arrives as {"type": "error"} with HTTP 200. This asks for a
URL and gives back the answer.
"""

import importlib.util
import json
import sys

import httpx
import pytest

from roscoe.connectors import AgentAPIError, AgentConnector
from roscoe.export import generate_python
from roscoe.workflow.registry import build_connectors
from roscoe.workflow.schema import Workflow


def _agent(handler, **overrides):
    config = {"base_url": "http://agent.test"}
    config.update(overrides)
    return AgentConnector(config, transport=httpx.MockTransport(handler))


def _ask(conn):
    return next(t for t in conn.tools if t.name == "ask")


def test_ask_returns_the_answer_not_the_envelope():
    """The whole point: a workflow reading {{ result }} should get the text,
    not {'type': 'final', 'output': ..., 'tokens': 12, 'cost': ...}."""
    conn = _agent(lambda r: httpx.Response(200, json={
        "type": "final", "output": "42", "tokens": 12, "cost": "$0.001"}))

    assert _ask(conn).invoke({"message": "what is 6x7"}) == "42"


def test_it_posts_the_message_to_the_chat_endpoint():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"type": "final", "output": "ok"})

    _ask(_agent(handler)).invoke({"message": "hello"})

    assert seen["path"] == "/api/chat"
    assert json.loads(seen["body"]) == {"message": "hello"}


def test_an_api_key_is_sent_when_the_other_agent_needs_one():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"type": "final", "output": "ok"})

    _ask(_agent(handler, api_key="s3cret")).invoke({"message": "x"})

    assert seen["auth"] == "Bearer s3cret"


def test_no_authorization_header_when_the_other_agent_is_open():
    seen = {}

    def handler(request):
        seen["has_auth"] = "authorization" in request.headers
        return httpx.Response(200, json={"type": "final", "output": "ok"})

    _ask(_agent(handler)).invoke({"message": "x"})

    assert seen["has_auth"] is False


def test_a_remote_failure_raises_rather_than_returning_the_error_payload():
    """The called agent reports failures with HTTP 200 and type=error. Returning
    that would let the calling workflow carry on with an error dict standing in
    for the answer it expected."""
    conn = _agent(lambda r: httpx.Response(200, json={
        "type": "error", "error": "AuthenticationError: bad key"}))

    with pytest.raises(AgentAPIError) as exc:
        _ask(conn).invoke({"message": "x"})
    assert "bad key" in str(exc.value)
    assert "agent.test" in str(exc.value)   # says which agent


def test_an_agent_waiting_for_approval_says_so_clearly():
    """A paused agent can never answer a machine caller — the message has to
    explain that rather than looking like a transient failure."""
    conn = _agent(lambda r: httpx.Response(200, json={
        "type": "paused", "run_id": "r1", "tool_calls": []}))

    with pytest.raises(AgentAPIError) as exc:
        _ask(conn).invoke({"message": "x"})
    assert "approve" in str(exc.value)


def test_a_missing_base_url_says_what_it_wanted():
    with pytest.raises(ValueError) as exc:
        AgentConnector({})
    assert "base_url" in str(exc.value)
    assert "8091" in str(exc.value)   # shows the shape of an answer


def test_it_is_reachable_from_a_connectors_block():
    built = build_connectors({"helper": {"type": "agent", "base_url": "http://x.test"}})

    assert type(built["helper"]).__name__ == "AgentConnector"


def test_it_appears_in_the_builders_picker():
    from roscoe.connectors.catalog import describe

    spec = describe("agent")
    assert spec["label"] == "Another agent"
    assert {f["name"] for f in spec["fields"]} == {"base_url", "api_key"}


# --- exported, an orchestrator still calls its sub-agents ---


ORCHESTRATOR = {
    "agent_name": "orchestrator",
    "model": {"provider": "openai", "model": "gpt-4o-mini", "api_key": "${K}"},
    "connectors": {
        "sales": {"type": "agent", "base_url": "http://sales.test"},
        "finance": {"type": "agent", "base_url": "http://finance.test",
                    "api_key": "${FIN_KEY}"},
    },
}

FLOW = {
    "entry": "ask_sales",
    "output": "{{ merged }}",
    "nodes": [
        {"id": "ask_sales", "type": "connector_action", "connector": "sales",
         "method": "ask", "inputs": {"message": "{{ input.question }}"},
         "output": "sales_answer", "next": "ask_finance"},
        {"id": "ask_finance", "type": "connector_action", "connector": "finance",
         "method": "ask", "inputs": {"message": "{{ input.question }}"},
         "output": "finance_answer", "next": "merge"},
        {"id": "merge", "type": "llm_step",
         "prompt": "Combine:\n{{ sales_answer }}\n{{ finance_answer }}",
         "output": "merged", "next": "END"},
    ],
}


def test_an_exported_orchestrator_calls_both_agents_and_merges(tmp_path, monkeypatch):
    source = generate_python(Workflow.from_dict(FLOW), ORCHESTRATOR, name="orch")
    path = tmp_path / "orch.py"
    path.write_text(source, encoding="utf-8")

    class _Blocker:
        def find_module(self, name, path=None):
            if name == "roscoe" or name.startswith("roscoe."):
                raise ImportError("roscoe is deliberately unavailable")

    monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])
    spec = importlib.util.spec_from_file_location("orch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    seen = []

    def handler(request):
        host = request.url.host
        seen.append(host)
        if "chat/completions" in str(request.url):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "Both agree."}}]})
        if host == "sales.test":
            return httpx.Response(200, json={"type": "final", "output": "Sales says up."})
        return httpx.Response(200, json={"type": "final", "output": "Finance says steady."})

    real_client = module.httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    result = module.run({"question": "how are we doing?"})

    assert result["status"] == "success"
    assert result["output"] == "Both agree."
    # Each sub-agent's plain answer reached state, not its envelope.
    assert result["state"]["sales_answer"] == "Sales says up."
    assert result["state"]["finance_answer"] == "Finance says steady."
    assert "sales.test" in seen and "finance.test" in seen


def test_an_exported_orchestrator_stops_when_a_sub_agent_fails(tmp_path, monkeypatch):
    source = generate_python(Workflow.from_dict(FLOW), ORCHESTRATOR, name="orch")
    path = tmp_path / "orch2.py"
    path.write_text(source, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("orch2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    real_client = module.httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"type": "error", "error": "boom"})), **kw))

    result = module.run({"question": "x"})

    assert result["status"] == "error"
    assert "boom" in result["error"]
    assert result["steps"] == ["ask_sales"]   # stopped at the failing call
