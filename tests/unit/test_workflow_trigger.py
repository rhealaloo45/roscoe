"""Trigger nodes — the schedule a workflow carries, and the loop that reads it.

A trigger is metadata that happens to live on the canvas: it says *when* to run
and nothing else, so the graph stays an ordinary graph that `roscoe run` can
still execute on demand.
"""

import asyncio
from datetime import datetime

import pytest

from roscoe.cli.schedule_command import _seconds_until
from roscoe.workflow.executor import WorkflowExecutor
from roscoe.workflow.schema import Trigger, Workflow, WorkflowError, parse_every
from roscoe.workflow.validate import validate_workflow


def _scheduled(**trigger_fields):
    node = {"id": "daily", "type": "trigger", "next": "act"}
    node.update(trigger_fields)
    return Workflow.from_dict({
        "entry": "daily",
        "output": "{{ out }}",
        "nodes": [
            node,
            {"id": "act", "type": "connector_action", "method": "ping",
             "output": "out", "next": "END"},
        ],
    })


# --- intervals ---


@pytest.mark.parametrize("text,seconds", [
    ("30s", 30), ("15m", 900), ("2h", 7200), ("1d", 86400), ("7d", 604800),
    (" 45m ", 2700),   # tolerate stray whitespace from a hand-edited file
    ("2H", 7200),      # ...and casing
])
def test_every_parses_to_seconds(text, seconds):
    assert parse_every(text) == seconds


@pytest.mark.parametrize("bad", ["", "5", "1x", "every day", "-2h", None])
def test_a_bad_interval_is_rejected_with_an_example(bad):
    with pytest.raises(WorkflowError) as exc:
        parse_every(bad)
    assert "30m" in str(exc.value)  # the message shows what a good one looks like


def test_a_zero_interval_is_rejected_rather_than_spinning():
    """`every: 0m` would fire in a tight loop forever — reject it at parse time."""
    with pytest.raises(WorkflowError) as exc:
        parse_every("0m")
    assert "at least 1" in str(exc.value)


# --- parsing ---


def test_a_trigger_round_trips_through_yaml():
    wf = _scheduled(every="1d", at="06:00")
    node = wf.to_dict()["nodes"][0]

    assert node == {"id": "daily", "type": "trigger", "every": "1d",
                    "at": "06:00", "next": "act"}
    assert Workflow.from_dict(wf.to_dict()).trigger.at == "06:00"


def test_a_trigger_without_a_time_keeps_at_out_of_the_file():
    assert "at" not in _scheduled(every="2h").to_dict()["nodes"][0]


def test_the_workflow_exposes_its_trigger():
    wf = _scheduled(every="1d")
    assert isinstance(wf.trigger, Trigger)
    assert wf.trigger.every == "1d"


def test_a_workflow_without_a_trigger_reports_none():
    wf = Workflow.from_dict({
        "entry": "a",
        "nodes": [{"id": "a", "type": "llm_step", "prompt": "hi", "next": "END"}],
    })
    assert wf.trigger is None


def test_a_trigger_must_say_how_often():
    with pytest.raises(WorkflowError) as exc:
        _scheduled()
    assert "every" in str(exc.value)


@pytest.mark.parametrize("bad_time", ["6am", "25:00", "6:70", "0600"])
def test_a_malformed_time_is_rejected(bad_time):
    with pytest.raises(WorkflowError) as exc:
        _scheduled(every="1d", at=bad_time)
    assert "HH:MM" in str(exc.value)


def test_a_scheduled_workflow_still_validates_clean():
    assert validate_workflow(_scheduled(every="1d", at="06:00")) == []


# --- execution ---


def test_running_a_trigger_does_nothing_and_moves_on():
    """A scheduled workflow is still runnable on demand: the trigger is a
    pass-through, so `roscoe run` behaves as if it weren't there."""
    class _Ping:
        name = "ping"

        async def ainvoke(self, args):
            return "pong"

    executor = WorkflowExecutor(
        _scheduled(every="1d"), connectors={}, llm=None, tools=[_Ping()],
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    result = asyncio.run(executor.run({}))

    assert result.status == "success"
    assert result.output == "pong"
    assert result.nodes_traversed == ["daily", "act"]


# --- the schedule loop's clock ---


def test_a_daily_time_later_today_waits_until_today():
    now = datetime(2026, 8, 2, 14, 30)
    assert _seconds_until("18:00", now) == 3.5 * 3600


def test_a_daily_time_already_past_waits_for_tomorrow():
    now = datetime(2026, 8, 2, 14, 30)
    assert _seconds_until("06:00", now) == 15.5 * 3600


def test_a_daily_time_exactly_now_does_not_fire_twice():
    """Landing exactly on the target must roll to tomorrow, not return 0 and
    re-fire immediately for the rest of the minute."""
    now = datetime(2026, 8, 2, 14, 30)
    assert _seconds_until("14:30", now) == 24 * 3600


# --- the editor ---


def test_the_builder_offers_a_schedule_node():
    from roscoe.cli.build_ui import PAGE

    assert "addNode('trigger')" in PAGE
    assert "trigger:'schedule'" in PAGE
    # Intervals are offered in plain language, not as cron strings.
    assert "once a day" in PAGE


# --- webhook triggers ---


def test_a_webhook_trigger_needs_no_interval():
    wf = _scheduled(kind="webhook")
    assert wf.trigger.kind == "webhook"
    assert wf.trigger.every == ""


def test_a_webhook_trigger_round_trips_without_a_stray_every():
    wf = _scheduled(kind="webhook")
    node = wf.to_dict()["nodes"][0]

    assert node == {"id": "daily", "type": "trigger", "kind": "webhook", "next": "act"}
    assert Workflow.from_dict(wf.to_dict()).trigger.kind == "webhook"


def test_a_schedule_trigger_omits_kind_from_the_file():
    """The common case stays uncluttered — `kind` only appears when it isn't
    the default, same convention as every other optional field here."""
    assert "kind" not in _scheduled(every="1d").to_dict()["nodes"][0]


def test_an_unknown_trigger_kind_is_rejected():
    with pytest.raises(WorkflowError) as exc:
        _scheduled(kind="cron")
    assert "cron" in str(exc.value)


def test_a_webhook_trigger_still_validates_clean():
    assert validate_workflow(_scheduled(kind="webhook")) == []


def test_a_webhook_workflow_still_runs_on_demand():
    """Same pass-through behaviour as a schedule trigger — `roscoe run` (or a
    direct call) works the same whether or not anything ever POSTs to it."""
    class _Ping:
        name = "ping"

        async def ainvoke(self, args):
            return "pong"

    executor = WorkflowExecutor(
        _scheduled(kind="webhook"), connectors={}, llm=None, tools=[_Ping()],
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    result = asyncio.run(executor.run({}))

    assert result.status == "success"
    assert result.nodes_traversed == ["daily", "act"]
