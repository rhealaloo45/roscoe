"""``roscoe run`` — the single command that runs your agent.

The Flask-style entry point: in a project directory (``agent_config.yaml`` +
``tools/my_tools.py``), ``roscoe run`` boots the agent and drops you into an
interactive chat. Pass ``-m`` for a one-shot message instead. Responses stream
token-by-token; human-in-the-loop pauses are handled inline at the prompt.
"""

from __future__ import annotations

import click

from roscoe.cli.eval_command import _load_tools


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
def run_command(
    config: str, tools_ref: str, message: str | None, as_terminal: bool,
    host: str, port: int, no_browser: bool,
    user_id: str, session_id: str, no_stream: bool,
) -> None:
    """Run the agent in this project.

    Opens a browser chat by default. Use ``--terminal`` for an in-terminal chat,
    or ``-m "..."`` for a one-shot message.
    """
    from roscoe import AgentRunner

    try:
        tools = _load_tools(tools_ref)
        agent = AgentRunner.from_config(config, tools=tools)
    except (FileNotFoundError, ValueError, ImportError) as exc:
        raise click.ClickException(str(exc)) from exc

    # One-shot: run in the terminal and exit.
    if message is not None:
        click.secho(f"roscoe run — {agent.agent_name}", fg="blue", bold=True)
        click.secho(f"  provider={agent.provider}  model={agent.model}  tools={len(tools)}", dim=True)
        _turn(agent, message, user_id, session_id, stream=not no_stream)
        return

    # Interactive terminal chat.
    if as_terminal:
        click.secho(f"roscoe run — {agent.agent_name}", fg="blue", bold=True)
        click.secho(f"  provider={agent.provider}  model={agent.model}  tools={len(tools)}", dim=True)
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


def _turn(agent, text: str, user_id: str, session_id: str, *, stream: bool) -> None:
    """Run one turn, print the reply (streamed or not), and handle a HITL pause."""
    click.secho("agent", fg="blue", nl=False)
    click.echo(" › ", nl=False)

    result = _stream_turn(agent, text, user_id, session_id) if stream else _plain_turn(
        agent, text, user_id, session_id)

    if result is None:
        return
    if result.status == "paused":
        _handle_pause(agent, result, user_id, session_id, stream=stream)
        return
    _print_stats(result)


def _plain_turn(agent, text: str, user_id: str, session_id: str):
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
