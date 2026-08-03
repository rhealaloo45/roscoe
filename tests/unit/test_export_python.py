"""Exporting a workflow to a standalone script.

The promise is a file that runs where roscoe cannot be installed, so the tests
that matter are: does it compile, does it run with roscoe unavailable, and does
it refuse — clearly — the things it cannot reproduce.
"""

import importlib.util
import sys

import httpx
import pytest

from roscoe.export import ExportError, generate_python
from roscoe.workflow.schema import Workflow

CONFIG = {
    "agent_name": "demo",
    "model": {"provider": "openai", "model": "gpt-4o-mini",
              "api_key": "${DEMO_KEY}", "temperature": 0.1},
    "connectors": {"api": {"type": "rest_api", "base_url": "https://example.test"}},
}

FLOW = {
    "entry": "fetch",
    "output": "{{ answer }}",
    "nodes": [
        {"id": "fetch", "type": "connector_action", "connector": "api",
         "method": "rest_get", "inputs": {"path": "/thing"}, "output": "thing",
         "next": "decide"},
        {"id": "decide", "type": "condition", "when": "thing.count > 1",
         "then": "many", "else": "one"},
        {"id": "many", "type": "llm_step", "prompt": "There are {{ thing.count }}.",
         "output": "answer", "next": "END"},
        {"id": "one", "type": "llm_step", "prompt": "Just the one.",
         "output": "answer", "next": "END"},
    ],
}


def _export(flow=FLOW, config=CONFIG, agents=None):
    return generate_python(Workflow.from_dict(flow, agents or {}), config, name="demo")


def _load(source, tmp_path, monkeypatch):
    """Import generated source with roscoe blocked, proving it stands alone."""
    path = tmp_path / "demo_agent.py"
    path.write_text(source, encoding="utf-8")

    class _Blocker:
        def find_module(self, name, path=None):
            if name == "roscoe" or name.startswith("roscoe."):
                raise ImportError("roscoe is deliberately unavailable")

    monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])
    spec = importlib.util.spec_from_file_location("demo_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the file itself ---


def test_the_generated_file_compiles():
    """ast.parse is not enough — a misplaced __future__ import only fails at
    compile time, which is exactly the bug this caught."""
    compile(_export(), "demo.py", "exec")


def test_the_generated_file_imports_neither_roscoe_nor_langchain():
    source = _export()
    assert "import roscoe" not in source
    assert "langchain" not in source
    assert "import httpx" in source


def test_secrets_are_read_from_the_environment_not_baked_in():
    """An exported file gets committed. A resolved key in it would be a leak."""
    source = _export()
    assert "os.environ.get('DEMO_KEY'" in source
    assert "sk-" not in source


# --- running it, with roscoe unavailable ---


def test_it_runs_end_to_end_without_roscoe(tmp_path, monkeypatch):
    module = _load(_export(), tmp_path, monkeypatch)

    def handler(request):
        if "chat/completions" in str(request.url):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "There are 3."}}]})
        return httpx.Response(200, json={"count": 3})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client",
        lambda **kw: real_client(transport=transport, **kw),
    )

    result = module.run({})

    assert result["status"] == "success"
    assert result["output"] == "There are 3."
    # Took the `then` branch, and never touched the other one.
    assert result["steps"] == ["fetch", "decide", "many"]


def test_a_condition_takes_the_else_branch_too(tmp_path, monkeypatch):
    module = _load(_export(), tmp_path, monkeypatch)

    def handler(request):
        if "chat/completions" in str(request.url):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "Only one."}}]})
        return httpx.Response(200, json={"count": 1})

    real_client = httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )

    assert module.run({})["steps"] == ["fetch", "decide", "one"]


def test_a_failing_call_is_reported_not_raised(tmp_path, monkeypatch):
    """The exported file is imported into someone else's app — it should hand
    back an error, not blow a traceback through their request handler."""
    module = _load(_export(), tmp_path, monkeypatch)

    real_client = httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client",
        lambda **kw: real_client(
            transport=httpx.MockTransport(lambda r: httpx.Response(500)), **kw),
    )

    result = module.run({})

    assert result["status"] == "error"
    assert result["error"]
    assert result["steps"] == ["fetch"]   # says how far it got


def test_a_trigger_is_carried_through_and_does_nothing(tmp_path, monkeypatch):
    flow = {
        "entry": "daily",
        "output": "{{ thing }}",
        "nodes": [
            {"id": "daily", "type": "trigger", "every": "1d", "at": "06:00",
             "next": "fetch"},
            {"id": "fetch", "type": "connector_action", "connector": "api",
             "method": "rest_get", "inputs": {"path": "/x"}, "output": "thing",
             "next": "END"},
        ],
    }
    module = _load(_export(flow), tmp_path, monkeypatch)

    real_client = httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client",
        lambda **kw: real_client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": 1})), **kw),
    )

    result = module.run({})

    assert result["status"] == "success"
    assert result["steps"] == ["daily", "fetch"]
    assert module.NODES["daily"]["every"] == "1d"   # the cadence is documented


# --- refusing what it cannot do ---


def test_an_agent_node_is_refused_by_name():
    flow = {"entry": "a", "nodes": [
        {"id": "a", "type": "agent_step", "agent": "helper", "task": "go"}]}
    with pytest.raises(ExportError) as exc:
        _export(flow, agents={"helper": {"tools": []}})
    assert "'a'" in str(exc.value)
    assert "Agent node" in str(exc.value)


def test_a_connector_needing_roscoes_client_is_refused_by_name():
    config = {**CONFIG, "connectors": {"gmail": {"type": "google_workspace"}}}
    flow = {"entry": "a", "nodes": [
        {"id": "a", "type": "connector_action", "connector": "gmail",
         "method": "read_emails"}]}
    with pytest.raises(ExportError) as exc:
        _export(flow, config)
    assert "gmail" in str(exc.value)
    assert "REST" in str(exc.value)


def test_a_provider_with_a_different_api_shape_is_refused():
    config = {**CONFIG, "model": {"provider": "anthropic", "model": "x"}}
    flow = {"entry": "a", "nodes": [
        {"id": "a", "type": "llm_step", "prompt": "hi"}]}
    with pytest.raises(ExportError) as exc:
        _export(flow, config)
    assert "anthropic" in str(exc.value)
    assert "openai" in str(exc.value)   # names what would work


def test_a_local_python_tool_is_refused():
    """A bare method resolves to this project's own @tool functions, which an
    exported file has no way to reach."""
    flow = {"entry": "a", "nodes": [
        {"id": "a", "type": "connector_action", "method": "my_tool"}]}
    with pytest.raises(ExportError) as exc:
        _export(flow)
    assert "my_tool" in str(exc.value)


def test_a_workflow_with_no_llm_step_does_not_need_a_supported_provider():
    """Refusing on the provider only matters if something actually prompts a
    model — a pure connector pipeline should export whatever the model says."""
    config = {**CONFIG, "model": {"provider": "anthropic", "model": "x"}}
    flow = {"entry": "a", "nodes": [
        {"id": "a", "type": "connector_action", "connector": "api",
         "method": "rest_get", "inputs": {"path": "/x"}, "output": "o"}]}

    compile(_export(flow, config), "demo.py", "exec")


# --- the button in the editor ---


def test_the_editor_offers_the_download():
    from roscoe.cli.build_ui import PAGE

    assert "Download Python" in PAGE
    assert "/api/export" in PAGE
