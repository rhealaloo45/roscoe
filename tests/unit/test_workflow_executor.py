"""Phase 1 — workflow schema parsing and graph execution.

Connectors are fakes built from ``StructuredTool``, so the whole engine (routing,
templating, approval pause/resume, error handling) runs with no HTTP and no LLM.
"""

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import StructuredTool

from roscoe.approval.gate import ApprovalGate
from roscoe.workflow.executor import WorkflowExecutor
from roscoe.workflow.schema import Workflow, WorkflowError


class FakeLLM:
    """Returns a scripted reply and records the prompts it was handed."""

    def __init__(self, reply="drafted text"):
        self.reply = reply
        self.prompts = []

    async def ainvoke(self, messages, *args, **kwargs):
        self.prompts.append(messages)
        return AIMessage(content=self.reply)


def _get_employee(employee_id: str) -> dict:
    """Look up an employee."""
    return {"id": employee_id, "name": "Rhea", "department": "Engineering"}


def _grant_access(employee_id: str) -> dict:
    """Grant VPN access."""
    return {"granted": True, "employee_id": employee_id}


def _boom(employee_id: str) -> dict:
    """Always fails."""
    raise RuntimeError("connector exploded")


def _tool(fn, name):
    return StructuredTool.from_function(fn, name=name, description=fn.__doc__ or name)


def _connectors():
    return {
        "hr": [_tool(_get_employee, "get_employee")],
        "vpn": [_tool(_grant_access, "grant_access"), _tool(_boom, "explode")],
    }


VPN_FLOW = {
    "entry": "lookup",
    "nodes": [
        {
            "id": "lookup",
            "type": "connector_action",
            "connector": "hr",
            "method": "get_employee",
            "inputs": {"employee_id": "{{ input.employee_id }}"},
            "output": "employee",
        },
        {
            "id": "check",
            "type": "condition",
            "when": "employee.department in ['Engineering', 'Product']",
            "then": "grant",
            "else": "deny",
        },
        {
            "id": "grant",
            "type": "connector_action",
            "connector": "vpn",
            "method": "grant_access",
            "inputs": {"employee_id": "{{ employee.id }}"},
            "output": "result",
            "next": "END",
        },
        {
            "id": "deny",
            "type": "llm_step",
            "prompt": "Explain why {{ employee.name }} was denied.",
            "output": "message",
        },
    ],
}


# --- schema ---


def test_parses_a_valid_workflow():
    wf = Workflow.from_dict(VPN_FLOW)
    assert wf.entry == "lookup"
    assert [n.id for n in wf.nodes] == ["lookup", "check", "grant", "deny"]
    assert wf.get("check").type == "condition"


def test_entry_defaults_to_the_first_node():
    wf = Workflow.from_dict({"nodes": VPN_FLOW["nodes"]})
    assert wf.entry == "lookup"


def test_fall_through_routing_uses_the_next_node_in_the_list():
    wf = Workflow.from_dict(VPN_FLOW)
    assert wf.next_after(wf.get("lookup")) == "check"
    assert wf.next_after(wf.get("deny")) == "END"  # last node


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"nodes": []}, "at least one node"),
        ({"nodes": [{"type": "condition"}]}, "missing a string 'id'"),
        ({"nodes": [{"id": "a"}]}, "missing 'type'"),
        ({"nodes": [{"id": "a", "type": "wat"}]}, "unknown type 'wat'"),
        ({"nodes": [{"id": "a", "type": "connector_action", "connector": "x"}]}, "missing 'method'"),
        ({"nodes": [{"id": "a", "type": "connector_action", "method": "m",
                     "on_reject": "ghost"}]}, "routes to 'ghost'"),
        ({"nodes": [{"id": "a", "type": "condition", "when": "1"}]}, "missing 'then'"),
        ({"nodes": [{"id": "a", "type": "llm_step"}]}, "missing 'prompt'"),
        ({"nodes": [{"id": "END", "type": "llm_step", "prompt": "x"}]}, "reserved"),
    ],
)
def test_structural_problems_are_caught_at_parse_time(mutation, message):
    with pytest.raises(WorkflowError, match=message):
        Workflow.from_dict(mutation)


def test_duplicate_node_ids_are_rejected():
    nodes = [
        {"id": "a", "type": "llm_step", "prompt": "x"},
        {"id": "a", "type": "llm_step", "prompt": "y"},
    ]
    with pytest.raises(WorkflowError, match="Duplicate node id 'a'"):
        Workflow.from_dict({"nodes": nodes})


def test_edges_must_point_at_real_nodes():
    nodes = [{"id": "a", "type": "llm_step", "prompt": "x", "next": "ghost"}]
    with pytest.raises(WorkflowError, match="routes to 'ghost'"):
        Workflow.from_dict({"nodes": nodes})


def test_unknown_entry_is_rejected():
    with pytest.raises(WorkflowError, match="entry"):
        Workflow.from_dict({"entry": "ghost", "nodes": VPN_FLOW["nodes"]})


# --- execution ---


async def test_runs_the_happy_path_and_records_traversal():
    ex = WorkflowExecutor(Workflow.from_dict(VPN_FLOW), connectors=_connectors())
    result = await ex.run({"employee_id": "E-1042"})

    assert result.status == "success"
    assert result.nodes_traversed == ["lookup", "check", "grant"]
    assert result.state["employee"]["name"] == "Rhea"
    assert result.state["result"] == {"granted": True, "employee_id": "E-1042"}


async def test_condition_takes_the_else_branch():
    flow = {**VPN_FLOW, "nodes": [dict(n) for n in VPN_FLOW["nodes"]]}
    flow["nodes"][1] = {**flow["nodes"][1], "when": "employee.department == 'HR'"}
    llm = FakeLLM("Not eligible.")

    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors(), llm=llm)
    result = await ex.run({"employee_id": "E-1042"})

    assert result.nodes_traversed == ["lookup", "check", "deny"]
    assert result.state["message"] == "Not eligible."
    # The prompt template was resolved against state before the model saw it.
    assert "Rhea" in llm.prompts[0][0].content


async def test_workflow_system_prompt_applies_to_every_llm_step():
    flow = {
        "system": "Answer in one sentence. No greeting.",
        "nodes": [{"id": "a", "type": "llm_step", "prompt": "hi", "output": "out"}],
    }
    llm = FakeLLM()
    await WorkflowExecutor(Workflow.from_dict(flow), llm=llm).run()

    sent = llm.prompts[0]
    assert isinstance(sent[0], SystemMessage)
    assert "No greeting" in sent[0].content


async def test_a_nodes_own_system_prompt_overrides_the_workflows():
    flow = {
        "system": "shared",
        "nodes": [{"id": "a", "type": "llm_step", "prompt": "hi", "system": "specific"}],
    }
    llm = FakeLLM()
    await WorkflowExecutor(Workflow.from_dict(flow), llm=llm).run()

    assert llm.prompts[0][0].content == "specific"


async def test_no_system_message_is_sent_when_none_is_configured():
    flow = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "hi"}]}
    llm = FakeLLM()
    await WorkflowExecutor(Workflow.from_dict(flow), llm=llm).run()

    assert not any(isinstance(m, SystemMessage) for m in llm.prompts[0])


async def test_llm_messages_are_collected_for_cost_accounting():
    flow = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "hi", "output": "out"}]}
    ex = WorkflowExecutor(Workflow.from_dict(flow), llm=FakeLLM())
    result = await ex.run()

    assert len(result.messages) == 1
    assert isinstance(result.messages[0], AIMessage)


# --- llm_step parse: json ---
#
# A model's reply is plain text by default, so "notes.action_items" against a
# fenced-JSON string fails downstream with a confusing index error, several
# nodes away from the prompt that actually produced it.


async def test_parse_json_reads_a_plain_reply_into_a_dict():
    flow = {"nodes": [{
        "id": "a", "type": "llm_step", "prompt": "hi", "parse": "json", "output": "notes",
    }]}
    llm = FakeLLM(reply='{"summary": "ok", "items": ["a", "b"]}')
    result = await WorkflowExecutor(Workflow.from_dict(flow), llm=llm).run()

    assert result.state["notes"] == {"summary": "ok", "items": ["a", "b"]}


async def test_parse_json_strips_a_markdown_code_fence():
    """Models asked for JSON almost always wrap it in ```json anyway."""
    flow = {"nodes": [{
        "id": "a", "type": "llm_step", "prompt": "hi", "parse": "json", "output": "notes",
    }]}
    llm = FakeLLM(reply='```json\n{"action_items": [{"task": "ship it"}]}\n```')
    result = await WorkflowExecutor(Workflow.from_dict(flow), llm=llm).run()

    assert result.state["notes"]["action_items"] == [{"task": "ship it"}]


async def test_a_downstream_node_can_index_into_the_parsed_reply():
    flow = {
        "entry": "a",
        "nodes": [
            {"id": "a", "type": "llm_step", "prompt": "hi", "parse": "json", "output": "notes",
             "next": "b"},
            {"id": "b", "type": "llm_step", "prompt": "{{ notes.action_items }}", "output": "out"},
        ],
    }
    llm = FakeLLM(reply='{"action_items": ["call Rhea"]}')
    ex = WorkflowExecutor(Workflow.from_dict(flow), llm=llm)
    result = await ex.run()

    assert result.status == "success"
    assert "call Rhea" in llm.prompts[1][0].content


async def test_invalid_json_names_the_node_and_shows_what_the_model_said():
    flow = {"nodes": [{
        "id": "summarise", "type": "llm_step", "prompt": "hi", "parse": "json",
    }]}
    llm = FakeLLM(reply="Sorry, I could not summarise that.")
    result = await WorkflowExecutor(Workflow.from_dict(flow), llm=llm).run()

    assert result.status == "error"
    assert result.failed_node == "summarise"
    assert "could not summarise" in result.error


def test_an_unknown_parse_value_is_rejected_at_load_time():
    flow = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "hi", "parse": "yaml"}]}

    with pytest.raises(WorkflowError, match="Only 'json' is supported"):
        Workflow.from_dict(flow)


def test_parse_json_round_trips_through_the_builder():
    flow = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "hi", "parse": "json"}]}
    restored = Workflow.from_dict(Workflow.from_dict(flow).to_dict())

    assert restored.nodes[0].parse == "json"


async def test_workflow_output_template_wins_over_last_written_value():
    flow = {**VPN_FLOW, "output": "Access for {{ employee.name }}: {{ result.granted }}"}
    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors())
    result = await ex.run({"employee_id": "E-1042"})

    assert result.output == "Access for Rhea: true"


async def test_output_defaults_to_the_last_value_written():
    ex = WorkflowExecutor(Workflow.from_dict(VPN_FLOW), connectors=_connectors())
    result = await ex.run({"employee_id": "E-1042"})

    assert result.output == str({"granted": True, "employee_id": "E-1042"})


# --- approval ---


def _gated_flow():
    nodes = [dict(n) for n in VPN_FLOW["nodes"]]
    nodes[2] = {**nodes[2], "requires_approval": True}
    return {**VPN_FLOW, "nodes": nodes}


async def test_gated_node_pauses_before_running_with_resolved_args():
    ex = WorkflowExecutor(Workflow.from_dict(_gated_flow()), connectors=_connectors())
    result = await ex.run({"employee_id": "E-1042"})

    assert result.status == "paused"
    assert result.pending.node_id == "grant"
    # Reviewers see real values, not the template.
    assert result.pending.args == {"employee_id": "E-1042"}
    assert "result" not in result.state


async def test_globally_gated_method_pauses_without_a_per_node_flag():
    ex = WorkflowExecutor(
        Workflow.from_dict(VPN_FLOW),
        connectors=_connectors(),
        approval_gate=ApprovalGate(["grant_access"]),
    )
    result = await ex.run({"employee_id": "E-1042"})

    assert result.status == "paused"
    assert result.pending.method == "grant_access"


async def test_resume_approve_runs_the_node_and_finishes():
    ex = WorkflowExecutor(Workflow.from_dict(_gated_flow()), connectors=_connectors())
    paused = await ex.run({"employee_id": "E-1042"})
    result = await ex.resume(paused.pending, "approve")

    assert result.status == "success"
    assert result.state["result"] == {"granted": True, "employee_id": "E-1042"}


async def test_resume_modify_replaces_the_arguments():
    ex = WorkflowExecutor(Workflow.from_dict(_gated_flow()), connectors=_connectors())
    paused = await ex.run({"employee_id": "E-1042"})
    result = await ex.resume(paused.pending, "modify", override_args={"employee_id": "E-9"})

    assert result.state["result"]["employee_id"] == "E-9"


async def test_resume_reject_stops_rather_than_reporting_success():
    # The normal route assumes the action happened, so a rejection must not follow it.
    ex = WorkflowExecutor(Workflow.from_dict(_gated_flow()), connectors=_connectors())
    paused = await ex.run({"employee_id": "E-1042"})
    result = await ex.resume(paused.pending, "reject")

    assert result.status == "success"
    assert result.state["result"] is None
    assert "rejected" in result.output
    assert result.nodes_traversed == ["lookup", "check", "grant"]


async def test_on_reject_routes_to_an_explicit_recovery_path():
    nodes = [dict(n) for n in VPN_FLOW["nodes"]]
    nodes[2] = {**nodes[2], "requires_approval": True, "on_reject": "deny"}
    flow = {**VPN_FLOW, "nodes": nodes}

    ex = WorkflowExecutor(
        Workflow.from_dict(flow), connectors=_connectors(), llm=FakeLLM("Refused.")
    )
    paused = await ex.run({"employee_id": "E-1042"})
    result = await ex.resume(paused.pending, "reject")

    assert result.state["message"] == "Refused."
    assert result.nodes_traversed[-1] == "deny"


async def test_resume_rejects_an_unknown_decision():
    ex = WorkflowExecutor(Workflow.from_dict(_gated_flow()), connectors=_connectors())
    paused = await ex.run({"employee_id": "E-1042"})
    with pytest.raises(ValueError, match="approve"):
        await ex.resume(paused.pending, "maybe")


# --- failure handling ---


async def test_connector_failure_ends_the_run_naming_the_node():
    flow = {
        "nodes": [
            {"id": "kaboom", "type": "connector_action", "connector": "vpn",
             "method": "explode", "inputs": {"employee_id": "E-1"}},
        ]
    }
    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors())
    result = await ex.run()

    assert result.status == "error"
    assert result.failed_node == "kaboom"
    assert "connector exploded" in result.error


async def test_a_node_without_a_connector_resolves_a_plain_tool():
    # A project's own @tool functions are callable without pretending to be a connector.
    flow = {
        "nodes": [
            {"id": "a", "type": "connector_action", "method": "lookup",
             "inputs": {"employee_id": "E-1"}, "output": "who"},
        ]
    }
    tools = [_tool(_get_employee, "lookup")]
    ex = WorkflowExecutor(Workflow.from_dict(flow), tools=tools)
    result = await ex.run()

    assert result.status == "success"
    assert result.state["who"]["name"] == "Rhea"


async def test_a_connector_less_node_falls_back_to_connector_methods():
    flow = {
        "nodes": [
            {"id": "a", "type": "connector_action", "method": "get_employee",
             "inputs": {"employee_id": "E-1"}, "output": "who"},
        ]
    }
    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors())
    result = await ex.run()

    assert result.state["who"]["name"] == "Rhea"


async def test_an_unresolvable_method_lists_what_is_available():
    flow = {"nodes": [{"id": "a", "type": "connector_action", "method": "ghost", "inputs": {}}]}
    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors())
    result = await ex.run()

    assert result.status == "error"
    assert "not a known tool or connector method" in result.error
    assert "get_employee" in result.error


async def test_unknown_connector_is_reported_with_what_is_available():
    flow = {
        "nodes": [
            {"id": "a", "type": "connector_action", "connector": "ghost",
             "method": "x", "inputs": {}},
        ]
    }
    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors())
    result = await ex.run()

    assert result.status == "error"
    assert "not configured" in result.error and "hr" in result.error


async def test_unknown_method_is_reported_with_what_is_available():
    flow = {
        "nodes": [
            {"id": "a", "type": "connector_action", "connector": "hr",
             "method": "nope", "inputs": {}},
        ]
    }
    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors())
    result = await ex.run()

    assert "has no method 'nope'" in result.error


async def test_bad_expression_ends_the_run_instead_of_raising():
    flow = {
        "nodes": [
            {"id": "a", "type": "condition", "when": "missing.field", "then": "a"},
        ]
    }
    ex = WorkflowExecutor(Workflow.from_dict(flow))
    result = await ex.run()

    assert result.status == "error"
    assert result.failed_node == "a"
    assert "Unknown name 'missing'" in result.error


async def test_llm_step_without_a_model_is_a_clear_error():
    flow = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "hi"}]}
    ex = WorkflowExecutor(Workflow.from_dict(flow))
    result = await ex.run()

    assert "no model was configured" in result.error


async def test_routing_cycle_is_bounded_by_max_steps():
    flow = {
        "max_steps": 5,
        "nodes": [
            {"id": "a", "type": "condition", "when": "true", "then": "b"},
            {"id": "b", "type": "condition", "when": "true", "then": "a"},
        ],
    }
    ex = WorkflowExecutor(Workflow.from_dict(flow))
    result = await ex.run()

    assert result.status == "error"
    assert "max_steps" in result.error
    assert len(result.nodes_traversed) == 5
