"""``roscoe run`` — the single command that runs your agent.

The Flask-style entry point: in a project directory (``agent_config.yaml`` +
``tools/my_tools.py``), ``roscoe run`` opens a browser UI by default. Pass
``--terminal`` for an interactive console chat (token-by-token streaming
there), or ``-m`` for a one-shot message. Human-in-the-loop pauses are
handled inline either way.

If the project has its own web UI script (a bespoke Flask/etc. app that
builds the agent itself — e.g. a custom login page, dashboard, branded
chat), ``roscoe run`` defers to it instead of the generic built-in browser
widget: by default it looks for ``app.py`` in the project directory and
just runs it as a subprocess. To use a different filename, set ``ui_script:
your_file.py`` in ``agent_config.yaml`` (or pass ``--ui-script`` to override
for a single run). This only applies to the default web mode — ``--terminal``
and ``-m`` never touch the UI script; they always build the agent directly
from ``--config``/``--tools`` and talk to it in-process (no Flask, no
subprocess), since a console session doesn't need any web app.

Some templates (hr_agent, it_support_agent, legal_agent, exec_assistant_agent)
wire tools through a ``build_tools(connector)`` factory bound to a real,
credentialed connector in ``main.py``, rather than a plain ``TOOLS`` list the
default ``--tools tools.my_tools:TOOLS`` can resolve generically. If that
default doesn't resolve and the project has its own ``main.py``, ``roscoe
run``/``--terminal``/``-m`` fall back to importing it and reusing the
top-level ``agent`` it already builds, instead of failing outright.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import click
import yaml

from roscoe.cli.eval_command import _load_tools

_DEFAULT_TOOLS_REF = "tools.my_tools:TOOLS"


def _configured_ui_script(config_path: str) -> str | None:
    """Read an optional top-level ``ui_script:`` key from the agent config."""
    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None
    value = data.get("ui_script")
    return str(value) if value else None


def _agent_from_main_py():
    """Import ``main.py`` in the cwd and return its top-level ``agent``, or None."""
    if not os.path.isfile("main.py"):
        return None
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    spec = importlib.util.spec_from_file_location("__roscoe_main__", "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "agent", None)


def _load_agent(config: str, tools_ref: str):
    """Build the agent: the normal ``--tools`` path, falling back to a
    project's own ``main.py`` (see module docstring) if the default tools
    module doesn't resolve."""
    from roscoe import AgentRunner

    try:
        tools = _load_tools(tools_ref)
        return AgentRunner.from_config(config, tools=tools)
    except (ImportError, AttributeError) as exc:
        if tools_ref != _DEFAULT_TOOLS_REF:
            raise click.ClickException(str(exc)) from exc
        agent = _agent_from_main_py()
        if agent is None:
            raise click.ClickException(
                f"{exc} — and main.py doesn't define a top-level `agent` to "
                "fall back to. Pass --tools module:attribute explicitly."
            ) from exc
        return agent


@click.command("run")
@click.option("--config", default="agent_config.yaml", show_default=True,
              help="Path to the agent config YAML.")
@click.option("--tools", "tools_ref", default="tools.my_tools:TOOLS", show_default=True,
              help="module:attribute resolving to a tool list.")
@click.option("--message", "-m", default=None, help="Send one message and exit (terminal, no server).")
@click.option("--terminal", "as_terminal", is_flag=True, help="Interactive chat in the terminal instead of the browser.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Web chat host.")
@click.option("--port", default=5005, show_default=True, help="Web chat port.")
@click.option("--no-browser", is_flag=True, help="Don't auto-open the browser.")
@click.option("--user", "user_id", default="web-user", show_default=True, help="user_id for memory/audit.")
@click.option("--session", "session_id", default="web-session", show_default=True, help="session_id for memory.")
@click.option("--no-stream", is_flag=True, help="Terminal mode: wait for the full reply instead of streaming.")
@click.option("--ui-script", default=None,
              help="Custom web UI entry point to run instead of the built-in browser widget. "
                   "Defaults to 'app.py', or the 'ui_script:' key in agent_config.yaml, if either exists.")
@click.option("--no-ui-script", is_flag=True,
              help="Ignore any custom UI script and always use roscoe's built-in browser widget.")
@click.option("--set", "set_values", multiple=True, metavar="KEY=VALUE",
              help="Workflow input, repeatable — e.g. --set topic=vpn. Ignored for agents.")
def run_command(
    config: str, tools_ref: str, message: str | None, as_terminal: bool,
    host: str, port: int, no_browser: bool,
    user_id: str, session_id: str, no_stream: bool,
    ui_script: str | None, no_ui_script: bool, set_values: tuple[str, ...],
) -> None:
    """Run the agent in this project.

    Opens a browser chat by default. Use ``--terminal`` for an in-terminal chat,
    or ``-m "..."`` for a one-shot message.
    """
    # Default web mode: if the project ships its own UI entry point, run that
    # instead of building an agent here — the script builds its own.
    resolved_ui_script = ui_script or _configured_ui_script(config) or "app.py"
    if message is None and not as_terminal and not no_ui_script and os.path.isfile(resolved_ui_script):
        click.secho(f"roscoe run — launching custom UI: {resolved_ui_script}", fg="blue", bold=True)
        raise SystemExit(subprocess.call([sys.executable, resolved_ui_script]))

    # A project defining a workflow runs the graph instead of an autonomous agent.
    # WorkflowRunner returns the same AgentResult, so everything below is shared.
    from roscoe.workflow.loader import has_workflow

    is_workflow = has_workflow(config)
    inputs = _parse_set_values(set_values)

    try:
        if is_workflow:
            from roscoe.workflow.runner import WorkflowRunner

            agent = WorkflowRunner.from_config(config, tools=_optional_tools(tools_ref))
        else:
            agent = _load_agent(config, tools_ref)
    except click.ClickException:
        raise
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc

    if is_workflow:
        summary = f"  provider={agent.provider}  model={agent.model}  nodes={len(agent.workflow.nodes)}"
        # `--set` means the caller supplied the workflow's inputs, so run it once and
        # print the result. With neither --set nor -m, fall through to the browser
        # chat, where a message arrives as input.message.
        if inputs and message is None and not as_terminal:
            message = ""
    else:
        summary = (
            f"  provider={agent.provider}  model={agent.model}  "
            f"tools={len(agent._executor._tools)}"  # noqa: SLF001 — CLI display only
        )

    # One-shot: run in the terminal and exit. Workflows take their inputs from
    # --set, so a bare `roscoe run` on a workflow project lands here too.
    if message is not None:
        click.secho(f"roscoe run — {agent.agent_name}", fg="blue", bold=True)
        click.secho(summary, dim=True)
        _turn(agent, inputs or message, user_id, session_id, stream=not no_stream)
        return

    # Interactive terminal chat.
    if as_terminal:
        click.secho(f"roscoe run — {agent.agent_name}", fg="blue", bold=True)
        click.secho(summary, dim=True)
        click.secho("  Type your message. Commands: 'exit' / 'quit' to leave.\n", dim=True)
        while True:
            try:
                text = click.prompt(click.style("you", fg="green"), prompt_suffix=" › ")
            except (click.Abort, EOFError):
                click.echo()
                break
            if text.strip().lower() in ("exit", "quit"):
                break
            _turn(agent, text, user_id, session_id, stream=not no_stream)
        return

    # Default: browser chat.
    from roscoe.cli.run_web import serve_chat

    serve_chat(agent, host=host, port=port, user_id=user_id, session_id=session_id,
               open_browser=not no_browser)


def _parse_set_values(values: tuple[str, ...]) -> dict[str, str]:
    """Turn repeated ``--set key=value`` options into a workflow input dict."""
    inputs: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise click.ClickException(f"--set expects KEY=VALUE, got '{item}'.")
        key, value = item.split("=", 1)
        inputs[key.strip()] = value
    return inputs


def _optional_tools(tools_ref: str) -> list:
    """Load ``--tools`` if it resolves; a workflow project usually has none."""
    try:
        return _load_tools(tools_ref)
    except (ImportError, AttributeError, ValueError):
        return []


def _turn(agent, text, user_id: str, session_id: str, *, stream: bool) -> None:
    """Run one turn, print the reply (streamed or not), and handle a HITL pause."""
    click.secho("agent", fg="blue", nl=False)
    click.echo(" › ", nl=False)

    # Workflows run node by node rather than token by token, so there is nothing
    # to stream — fall back to waiting for the result.
    can_stream = stream and hasattr(agent, "stream")
    result = _stream_turn(agent, text, user_id, session_id) if can_stream else _plain_turn(
        agent, text, user_id, session_id)

    if result is None:
        return
    if result.status == "paused":
        _handle_pause(agent, result, user_id, session_id, stream=stream)
        return
    _print_stats(result)


def _plain_turn(agent, text, user_id: str, session_id: str):
    result = agent.run(text, user_id=user_id, session_id=session_id)
    if result.status == "error":
        click.secho(f"[error] {result.error}", fg="red")
    elif result.status == "rate_limited":
        click.secho("[rate limited] try again shortly.", fg="yellow")
    elif result.status != "paused":
        click.echo(result.output)
    return result


def _stream_turn(agent, text: str, user_id: str, session_id: str):
    """Stream tokens to the terminal; return the terminal AgentResult."""
    result = None
    printed_any = False
    for event in agent.stream(text, user_id=user_id, session_id=session_id):
        etype = event["type"]
        if etype == "token":
            click.echo(event["text"], nl=False)
            printed_any = True
        elif etype == "tool":
            click.secho(f"\n  ⟳ running tool: {event['name']}", fg="cyan")
        elif etype in ("final", "paused", "error"):
            result = event["result"]
    if result is not None and result.status == "error":
        click.secho(f"\n[error] {result.error}", fg="red")
    elif result is not None and result.status == "rate_limited":
        click.secho("\n[rate limited] try again shortly.", fg="yellow")
    elif printed_any:
        click.echo()  # newline after streamed answer
    elif result is not None and result.status != "paused":
        click.echo(result.output)
    return result


def _handle_pause(agent, result, user_id: str, session_id: str, *, stream: bool) -> None:
    """Show the gated tool call(s) and prompt for approval, then resume."""
    action = result.pending_action or {}
    click.secho("\n  ⏸  approval required:", fg="yellow", bold=True)
    for tc in action.get("tool_calls", []):
        click.secho(f"     {tc.get('name')}({tc.get('args', {})})", fg="yellow")
    decision = "approve" if click.confirm(click.style("  approve?", fg="yellow"), default=False) else "reject"

    resumed = agent.resume(result.run_id, decision)
    click.secho("agent", fg="blue", nl=False)
    click.echo(" › ", nl=False)
    if resumed.status == "error":
        click.secho(f"[error] {resumed.error}", fg="red")
    else:
        click.echo(resumed.output)
    _print_stats(resumed)


def _print_stats(result) -> None:
    cost = f"${result.cost_usd:.4f}" if result.cost_usd else "free"
    tools = f"  tools={' → '.join(result.tool_calls)}" if result.tool_calls else ""
    click.secho(f"  [tokens={result.total_tokens}  cost={cost}{tools}]\n", dim=True)
