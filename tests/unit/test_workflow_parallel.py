"""The `parallel` node — run several existing nodes at once and merge results.

Each branch names another node already in the graph, run concurrently via
asyncio.gather rather than one `next` at a time.
"""

import asyncio
import time

import pytest
from langchain_core.tools import StructuredTool

from roscoe.workflow.executor import WorkflowExecutor
from roscoe.workflow.schema import Parallel, Workflow, WorkflowError
from roscoe.workflow.validate import validate_workflow


async def _slow_flights(origin: str) -> dict:
    """Check flights."""
    await asyncio.sleep(0.2)
    return {"origin": origin, "price": 200}


async def _slow_hotels(city: str) -> dict:
    """Check hotels."""
    await asyncio.sleep(0.2)
    return {"city": city, "price": 150}


def _tool(fn, name):
    return StructuredTool.from_function(fn, name=name, description=fn.__doc__ or name, coroutine=fn)


def _connectors():
    return {"travel": [_tool(_slow_flights, "flights"), _tool(_slow_hotels, "hotels")]}


FLOW = {
    "entry": "fan",
    "output": "{{ combined }}",
    "nodes": [
        {"id": "fan", "type": "parallel",
         "branches": {"flight": "check_flights", "hotel": "check_hotels"},
         "output": "combined", "next": "END"},
        {"id": "check_flights", "type": "connector_action", "connector": "travel",
         "method": "flights", "inputs": {"origin": "{{ input.origin }}"}, "output": "flight_info"},
        {"id": "check_hotels", "type": "connector_action", "connector": "travel",
         "method": "hotels", "inputs": {"city": "{{ input.city }}"}, "output": "hotel_info"},
    ],
}


def _flow(**overrides):
    data = {**FLOW, "nodes": [dict(n) for n in FLOW["nodes"]]}
    data.update(overrides)
    return data


# --- schema ---


def test_a_parallel_node_round_trips_through_yaml():
    wf = Workflow.from_dict(_flow())
    node = wf.to_dict()["nodes"][0]

    assert node["branches"] == {"flight": "check_flights", "hotel": "check_hotels"}
    assert isinstance(wf.get("fan"), Parallel)


def test_a_parallel_node_needs_at_least_one_branch():
    with pytest.raises(WorkflowError) as exc:
        Workflow.from_dict(_flow(nodes=[
            {"id": "fan", "type": "parallel", "branches": {}, "next": "END"},
        ]))
    assert "branches" in str(exc.value)


def test_a_branch_pointing_nowhere_is_rejected_at_parse_time():
    with pytest.raises(WorkflowError) as exc:
        Workflow.from_dict(_flow(nodes=[
            {"id": "fan", "type": "parallel", "branches": {"x": "missing"}, "next": "END"},
        ]))
    assert "missing" in str(exc.value)


def test_branch_targets_do_not_count_as_unreachable():
    """check_flights/check_hotels are reached only through the parallel node's
    branches, not a plain `next` — reachability has to know that counts."""
    assert validate_workflow(Workflow.from_dict(_flow())) == []


# --- execution ---


def test_branches_run_concurrently_not_one_after_another():
    executor = WorkflowExecutor(
        Workflow.from_dict(_flow()), connectors=_connectors(), llm=None,
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    start = time.monotonic()
    result = asyncio.run(executor.run({"origin": "LHR", "city": "Paris"}))
    elapsed = time.monotonic() - start

    assert result.status == "success"
    # Two 0.2s branches: ~0.2s if concurrent, ~0.4s if sequential.
    assert elapsed < 0.35, f"branches did not run concurrently ({elapsed:.2f}s)"


def test_outputs_are_merged_by_branch_name():
    executor = WorkflowExecutor(
        Workflow.from_dict(_flow()), connectors=_connectors(), llm=None,
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    result = asyncio.run(executor.run({"origin": "LHR", "city": "Paris"}))

    assert result.state["combined"] == {
        "flight": {"origin": "LHR", "price": 200},
        "hotel": {"city": "Paris", "price": 150},
    }


def test_each_branchs_own_output_also_lands_in_shared_state():
    """A branch node runs exactly like it would sequentially — its own
    `output:` key is still readable downstream, not just inside the merge."""
    executor = WorkflowExecutor(
        Workflow.from_dict(_flow()), connectors=_connectors(), llm=None,
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    result = asyncio.run(executor.run({"origin": "LHR", "city": "Paris"}))

    assert result.state["flight_info"] == {"origin": "LHR", "price": 200}
    assert result.state["hotel_info"] == {"city": "Paris", "price": 150}


def test_a_branch_pointing_at_a_condition_is_refused_at_run_time():
    flow = _flow(nodes=[
        {"id": "fan", "type": "parallel", "branches": {"x": "decide"}, "next": "END"},
        {"id": "decide", "type": "condition", "when": "true", "then": "END"},
    ])
    executor = WorkflowExecutor(
        Workflow.from_dict(flow), connectors={}, llm=None,
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    result = asyncio.run(executor.run({}))

    assert result.status == "error"
    assert "condition" in result.error


def test_a_branch_that_needs_approval_is_refused_rather_than_left_half_paused():
    flow = _flow(nodes=[
        {"id": "fan", "type": "parallel", "branches": {"x": "act"}, "next": "END"},
        {"id": "act", "type": "connector_action", "connector": "travel", "method": "flights",
         "inputs": {"origin": "LHR"}, "requires_approval": True, "output": "o"},
    ])
    executor = WorkflowExecutor(
        Workflow.from_dict(flow), connectors=_connectors(), llm=None,
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    result = asyncio.run(executor.run({}))

    assert result.status == "error"
    assert "approval" in result.error
