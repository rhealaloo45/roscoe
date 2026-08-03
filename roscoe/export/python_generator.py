"""Generate a standalone Python script from a workflow.

The point is a file that runs somewhere roscoe cannot be installed — an org that
won't approve the dependency, an app that wants the agent inline rather than as
a service. So the output imports ``httpx`` and nothing else: no roscoe, no
langchain.

Two things make that cheap rather than a rewrite. The templating engine is
already pure stdlib, so it is vendored verbatim instead of reimplemented —
identical behaviour, no second grammar to keep in step. And an ``llm_step`` is
one POST to a chat-completions endpoint; the provider adapters only earn their
keep for tool-calling, which the supported node types don't do.

What is *not* carried over is deliberate: retries, approval gates, audit logging
and cost tracking all stay behind. An export is the workflow's logic, not
roscoe's runtime around it, and the generated header says so plainly rather than
letting someone assume feature parity.

Anything that can't be translated faithfully is refused by name (see
:class:`ExportError`) — a half-working script that fails at runtime, in someone
else's codebase, would be far worse than a clear no here.
"""

from __future__ import annotations

import pprint
from pathlib import Path
from typing import Any

from roscoe.workflow.schema import (
    AgentStep,
    Condition,
    ConnectorAction,
    LLMStep,
    Trigger,
    Workflow,
)

#: Providers whose chat API is OpenAI-shaped (``POST {base_url}/chat/completions``
#: with a bearer token), which is the only wire format the generated file speaks.
_OPENAI_WIRE = {
    "openai": "https://api.openai.com/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "ollama": "http://localhost:11434/v1",
}

#: An agent connector exports too: calling another agent is an HTTP POST, and
#: the generated file can make it as easily as roscoe can.
_SUPPORTED_CONNECTORS = {"rest_api", "agent", "agent_api"}


class ExportError(ValueError):
    """Raised when a workflow uses something the generated file could not do.

    Always names the specific node, connector or provider, and says what to do
    instead — the message is shown to someone in the builder, not in a terminal.
    """


def _vendored_expressions() -> str:
    """The templating engine's source, to inline into the generated file.

    Vendored rather than reimplemented: it is pure stdlib and self-contained, so
    the copy behaves exactly like roscoe's own — ``{{ }}`` in an exported file
    resolves the way it did in the editor, including the error messages.
    """
    source = Path(__file__).with_name("..").resolve() / "workflow" / "expressions.py"
    text = source.read_text(encoding="utf-8")
    # Start after the __future__ import, not at it: those are only legal at the
    # very top of a file, and this lands in the middle of the generated one.
    # Nothing here needs it — roscoe is 3.11+, where the builtin generics in
    # these signatures are native.
    marker = "from __future__ import annotations\n"
    return text[text.index(marker) + len(marker):].lstrip("\n")


def _check_supported(workflow: Workflow, connectors: dict[str, Any], model: dict[str, Any]) -> None:
    """Refuse anything the generated file could not reproduce faithfully."""
    for node in workflow.nodes:
        if isinstance(node, AgentStep):
            raise ExportError(
                f"'{node.id}' is an Agent node. Those pick their own tools as they "
                f"go, which needs roscoe's agent loop — export can't reproduce it. "
                f"Replace it with Action and Prompt steps, or run this workflow "
                f"with roscoe instead of exporting it."
            )
        if isinstance(node, ConnectorAction) and node.connector:
            spec = connectors.get(node.connector) or {}
            kind = spec.get("type", node.connector)
            if kind not in _SUPPORTED_CONNECTORS:
                raise ExportError(
                    f"'{node.id}' uses the '{node.connector}' connector ({kind}), which "
                    f"needs roscoe's own client to sign its requests. Exporting "
                    f"currently supports REST connectors only."
                )
        if isinstance(node, ConnectorAction) and not node.connector:
            raise ExportError(
                f"'{node.id}' calls '{node.method}' without naming a connector, so it "
                f"resolves to a tool defined in this project's Python. Exported files "
                f"have no access to those."
            )

    provider = (model.get("provider") or "").strip()
    needs_llm = any(isinstance(n, LLMStep) for n in workflow.nodes)
    if needs_llm and provider not in _OPENAI_WIRE:
        supported = ", ".join(sorted(_OPENAI_WIRE))
        raise ExportError(
            f"The model provider '{provider or '(not set)'}' doesn't use the same API "
            f"shape as the exported file. Switch to one of: {supported}."
        )


def _env_ref(value: Any) -> str:
    """Render a config value as Python, turning ``${VAR}`` into an env lookup.

    Secrets are never baked into the output: the placeholder stays a placeholder
    and resolves wherever the file ends up running, so an exported script is safe
    to commit.
    """
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return f"os.environ.get({value[2:-1]!r}, '')"
    return repr(value)


def _connector_block(connectors: dict[str, Any]) -> str:
    lines = []
    for name, spec in connectors.items():
        spec = spec or {}
        kind = spec.get("type", name)
        settings = ", ".join(
            f"{key!r}: {_env_ref(val)}"
            for key, val in spec.items()
            if key in ("base_url", "auth", "token", "api_key", "header", "username", "password")
        )
        # Carried so the generated caller knows to unwrap /api/chat's envelope
        # rather than handing a workflow the whole {type, output, tokens} dict.
        kind_entry = "'kind': 'agent', " if kind in ("agent", "agent_api") else ""
        lines.append(f"    {name!r}: {{{kind_entry}{settings}}},")
    return "\n".join(lines) or "    # (no connectors)"


def generate_python(
    workflow: Workflow,
    config: dict[str, Any],
    *,
    name: str = "agent",
) -> str:
    """Render ``workflow`` as a standalone Python module.

    Raises:
        ExportError: if the workflow uses a node, connector or provider the
            generated file could not reproduce.
    """
    connectors = config.get("connectors") or {}
    model = config.get("model") or {}
    _check_supported(workflow, connectors, model)

    base_url = model.get("base_url") or _OPENAI_WIRE.get(model.get("provider"), "")
    nodes = {n.id: _node_spec(n) for n in workflow.nodes}

    return _TEMPLATE.format(
        name=name,
        expressions=_vendored_expressions(),
        connectors=_connector_block(connectors),
        # pformat, not json.dumps: the output is Python source, and JSON's
        # null/true/false are NameErrors there.
        nodes=pprint.pformat(nodes, indent=1, width=88, sort_dicts=False),
        entry=repr(workflow.entry),
        output=repr(workflow.output),
        system=repr(workflow.system),
        model=repr(model.get("model", "")),
        base_url=repr(base_url),
        api_key=_env_ref(model.get("api_key", "")),
        temperature=repr(model.get("temperature", 0.1)),
    )


def _node_spec(node: Any) -> dict[str, Any]:
    """One node as the plain dict the generated walker interprets."""
    spec: dict[str, Any] = {"type": node.type, "next": node.next, "output": node.output}
    if isinstance(node, Trigger):
        # Carried through so the file documents its own cadence, even though the
        # OS scheduler is what actually fires it.
        spec.update(every=node.every, at=node.at)
    elif isinstance(node, ConnectorAction):
        spec.update(connector=node.connector, method=node.method, inputs=node.inputs,
                    output_message=node.output_message)
    elif isinstance(node, Condition):
        spec.update(when=node.when, then=node.then, otherwise=node.otherwise)
    elif isinstance(node, LLMStep):
        spec.update(prompt=node.prompt, system=node.system, parse=node.parse)
    return spec


_TEMPLATE = '''"""{name} — generated by roscoe, runs without it.

Only dependency:  pip install httpx

Secrets are read from the environment, not baked in, so this file is safe to
commit. Set whatever ${{VARS}} your connectors and model used.

Not carried over from roscoe: retries, human approval gates, audit logging and
cost tracking. This is the workflow's logic on its own — if you need those,
run it with roscoe instead.

Use it:
    from {name} import run
    print(run({{"message": "hello"}})["output"])
"""

import json
import os
import sys

import httpx

# --------------------------------------------------------------------------
# Templating — vendored from roscoe so {{{{ }}}} behaves identically here.
# --------------------------------------------------------------------------

{expressions}

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

CONNECTORS = {{
{connectors}
}}

MODEL = {model}
BASE_URL = {base_url}
API_KEY = {api_key}
TEMPERATURE = {temperature}
SYSTEM = {system}

NODES = {nodes}

ENTRY = {entry}
OUTPUT = {output}


# --------------------------------------------------------------------------
# Calling things
# --------------------------------------------------------------------------

def _headers(cfg):
    """Auth headers for a REST connector, matching roscoe's own modes."""
    mode = cfg.get("auth", "none")
    if mode == "bearer":
        return {{"Authorization": "Bearer " + cfg.get("token", "")}}
    if mode == "api_key":
        return {{cfg.get("header", "X-API-Key"): cfg.get("api_key", "")}}
    if mode == "basic":
        import base64
        raw = (cfg.get("username", "") + ":" + cfg.get("password", "")).encode()
        return {{"Authorization": "Basic " + base64.b64encode(raw).decode()}}
    return {{}}


def call_agent(cfg, message):
    """Ask another roscoe agent and return its answer, not its envelope."""
    headers = {{"Content-Type": "application/json"}}
    if cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + cfg["api_key"]

    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            cfg.get("base_url", "").rstrip("/") + "/api/chat",
            headers=headers, json={{"message": message}},
        )
        response.raise_for_status()
        reply = response.json()

    if reply.get("type") == "error":
        raise RuntimeError("The agent at " + cfg.get("base_url", "") + " failed: "
                           + str(reply.get("error")))
    if reply.get("type") == "paused":
        raise RuntimeError("The agent at " + cfg.get("base_url", "")
                           + " is waiting for a human decision, so it cannot answer.")
    return reply.get("output", reply)


def call_connector(name, method, args):
    """One REST call. `method` is rest_get / rest_post / rest_put / rest_delete."""
    cfg = CONNECTORS.get(name) or {{}}
    if cfg.get("kind") == "agent":
        return call_agent(cfg, (args or {{}}).get("message", ""))
    verb = method.replace("rest_", "").upper()
    if verb not in ("GET", "POST", "PUT", "DELETE"):
        raise RuntimeError("Unsupported method: " + method)

    args = dict(args or {{}})
    path = args.pop("path", "")
    url = cfg.get("base_url", "").rstrip("/") + "/" + str(path).lstrip("/")

    with httpx.Client(timeout=60.0) as client:
        response = client.request(
            verb, url, headers=_headers(cfg),
            params=args.get("params"),
            json=args.get("body"),
        )
        response.raise_for_status()
        if "application/json" in response.headers.get("content-type", ""):
            return response.json()
        return {{"status_code": response.status_code, "text": response.text}}


def call_model(prompt, system=None):
    """One chat completion. No tools, no loop — same as roscoe's Prompt step."""
    messages = []
    if system or SYSTEM:
        messages.append({{"role": "system", "content": system or SYSTEM}})
    messages.append({{"role": "user", "content": prompt}})

    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            BASE_URL.rstrip("/") + "/chat/completions",
            headers={{"Authorization": "Bearer " + API_KEY}},
            json={{"model": MODEL, "messages": messages, "temperature": TEMPERATURE}},
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------
# The workflow
# --------------------------------------------------------------------------

def run(inputs=None):
    """Run the workflow once. Returns {{"status", "output", "state", "steps"}}."""
    state = {{"input": dict(inputs or {{}})}}
    steps = []
    node_id = ENTRY

    # Bounded so a mis-wired loop stops rather than running forever.
    for _ in range(200):
        if not node_id or node_id == "END":
            break
        node = NODES.get(node_id)
        if node is None:
            return {{"status": "error", "output": None, "state": state,
                    "steps": steps, "error": "No node named " + repr(node_id)}}
        steps.append(node_id)

        try:
            node_id = _step(node, node_id, state)
        except Exception as exc:
            return {{"status": "error", "output": None, "state": state, "steps": steps,
                    "error": type(exc).__name__ + ": " + str(exc)}}
    else:
        return {{"status": "error", "output": None, "state": state, "steps": steps,
                "error": "Stopped after 200 steps — the workflow may loop."}}

    output = render(OUTPUT, state) if OUTPUT else None
    return {{"status": "success", "output": output, "state": state, "steps": steps}}


def _step(node, node_id, state):
    """Run one node, write its result into state, return the next node's id."""
    kind = node["type"]

    if kind == "trigger":
        # Nothing to do — it only records how often this should be run.
        return node.get("next") or _fallthrough(node_id)

    if kind == "condition":
        branch = node["then"] if truthy(node["when"], state) else node.get("otherwise")
        return branch or node.get("next") or _fallthrough(node_id)

    if kind == "connector_action":
        args = render(node.get("inputs") or {{}}, state)
        value = call_connector(node["connector"], node["method"], args)
        if node.get("output_message"):
            scope = dict(state)
            if node.get("output"):
                scope[node["output"]] = value
            value = render(node["output_message"], scope)
    elif kind == "llm_step":
        value = call_model(render(node["prompt"], state), node.get("system"))
        if node.get("parse") == "json":
            value = json.loads(value)
    else:
        raise RuntimeError("Unsupported node type: " + kind)

    if node.get("output"):
        state[node["output"]] = value
    return node.get("next") or _fallthrough(node_id)


def _fallthrough(node_id):
    """A node with no explicit `next` falls through to the following one."""
    ids = list(NODES)
    i = ids.index(node_id)
    return ids[i + 1] if i + 1 < len(ids) else "END"


if __name__ == "__main__":
    result = run({{"message": " ".join(sys.argv[1:])}})
    if result["status"] != "success":
        print("error:", result.get("error"), file=sys.stderr)
        raise SystemExit(1)
    print(result["output"])
'''
