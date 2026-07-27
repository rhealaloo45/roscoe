"""Example custom UI — a starting point for replacing roscoe's built-in widget.

Not active by default. To switch to it:

    1. pip install flask
    2. rename this file to app.py — or set `ui_script: custom_ui_example.py`
       in agent_config.yaml
    3. roscoe run

`roscoe run` execs whatever `ui_script:` names (or `app.py`, if one exists)
instead of its own browser widget. Your script owns the whole thing from
there — the port, the framework, the page. Swap Flask for FastAPI, Streamlit,
Django, plain http.server, whatever you're already comfortable with; the part
worth copying is how the runner gets built and called, not the web framework
around it.

Human-in-the-loop pauses, cost tracking, and audit logging all come for free:
WorkflowRunner and AgentRunner return the same AgentResult no matter who's
driving them, so this example works unmodified whether the project is a
declarative workflow or a Python `@tool`-based agent.
"""

from __future__ import annotations

from flask import Flask, jsonify, request

from roscoe.workflow.loader import has_workflow

CONFIG = "agent_config.yaml"

# A workflow project (workflow.yaml present) runs the graph; otherwise this is
# a plain agent whose tools are Python functions. Same branch `roscoe run`
# itself takes — see roscoe/cli/run_command.py if you want the full picture.
if has_workflow(CONFIG):
    from roscoe.workflow.runner import WorkflowRunner

    agent = WorkflowRunner.from_config(CONFIG)
else:
    from roscoe.core.agent_runner import AgentRunner

    agent = AgentRunner.from_config(CONFIG)

app = Flask(__name__)

# One pending run per process, same simplification roscoe's own built-in UI
# makes. A real multi-user app should key this by session instead.
_pending_run_id: str | None = None


@app.route("/")
def index() -> str:
    return """
    <!doctype html><title>Custom UI example</title>
    <h1>Build your own front end here</h1>
    <p>POST JSON to <code>/api/chat</code>:
       <code>{"message": "..."}</code> for a plain agent, or
       <code>{"inputs": {"field": "value"}}</code> for a workflow.</p>
    <p>If the response comes back <code>"status": "paused"</code>, approve or
       reject it with POST <code>/api/approve</code>:
       <code>{"decision": "approve"}</code>.</p>
    """


@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True) or {}
    # A form posts named inputs (a workflow); a chat box posts a sentence.
    payload = body.get("inputs") or body.get("message", "")
    result = agent.run(payload, user_id=body.get("user_id", "web-user"))
    return jsonify(_serialise(result))


@app.route("/api/approve", methods=["POST"])
def approve():
    if not _pending_run_id:
        return jsonify({"error": "No run is waiting for approval."}), 400
    decision = (request.get_json(force=True) or {}).get("decision", "reject")
    result = agent.resume(_pending_run_id, decision)
    return jsonify(_serialise(result))


def _serialise(result) -> dict:
    global _pending_run_id
    if result.status == "paused":
        _pending_run_id = result.run_id
        return {"status": "paused", "pending_action": result.pending_action}
    _pending_run_id = None
    return {
        "status": result.status,
        "output": result.output,
        "tokens": result.total_tokens,
        "cost_usd": result.cost_usd,
        "error": result.error,
    }


if __name__ == "__main__":
    app.run(port=5005, debug=True)
