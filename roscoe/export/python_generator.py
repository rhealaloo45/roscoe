"""Generate a standalone Python script from a workflow.

The point is a file that runs somewhere roscoe cannot be installed — an org that
won't approve the dependency, an app that wants the agent inline rather than as
a service. So the output imports ``httpx`` and nothing else: no roscoe, no
langchain.

Three things make that cheap rather than a rewrite. The templating engine is
already pure stdlib, so it is vendored verbatim instead of reimplemented —
identical behaviour, no second grammar to keep in step. An ``llm_step`` is one
POST to a chat-completions endpoint; the provider adapters only earn their keep
for tool-calling, which the supported node types don't do. And most connectors
are a base URL, some auth headers and a path, which restates directly — see
:mod:`roscoe.export.connector_snippets`, where each one contributes the source
of its own tools, emitted only if the workflow actually uses them.

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

from roscoe.export.connector_snippets import SNIPPETS, labels, settings_for, tools_for
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
_PLAIN_CONNECTORS = {"rest_api", "agent"}

#: The spellings ``registry.py`` accepts, collapsed to the one name used here.
_ALIASES = {
    "rest": "rest_api",
    "sqlite": "database",
    "sql": "database",
    "search": "web_search",
    "email": "smtp",
    "sms": "twilio",
    "agent_api": "agent",
}


def _canonical(type_name: str) -> str:
    """One connector type name, whichever alias the config spelled it with."""
    return _ALIASES.get(type_name, type_name)


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


def _kind_of(connectors: dict[str, Any], name: str) -> str:
    """The canonical type of the named connector, as the config declared it."""
    spec = connectors.get(name) or {}
    return _canonical(spec.get("type", name))


def _check_supported(
    workflow: Workflow, connectors: dict[str, Any], model: dict[str, Any]
) -> None:
    """Refuse anything the generated file could not reproduce faithfully."""
    for node in workflow.nodes:
        if isinstance(node, AgentStep):
            raise ExportError(
                f"'{node.id}' is an Agent node. Those pick their own tools as they "
                f"go, which needs roscoe's agent loop — export can't reproduce it. "
                f"Replace it with Action and Prompt steps, or run this workflow "
                f"with roscoe instead of exporting it."
            )
        if not isinstance(node, ConnectorAction):
            continue
        if not node.connector:
            raise ExportError(
                f"'{node.id}' calls '{node.method}' without naming a connector, so it "
                f"resolves to a tool defined in this project's Python. Exported files "
                f"have no access to those."
            )

        kind = _kind_of(connectors, node.connector)
        if kind in _PLAIN_CONNECTORS:
            continue
        if kind not in SNIPPETS:
            raise ExportError(
                f"'{node.id}' uses the '{node.connector}' connector ({kind}), which "
                f"signs its requests through roscoe's own client — usually an OAuth "
                f"exchange or a database driver an exported file can't assume is "
                f"installed. Run this workflow with roscoe, or swap it for one export "
                f"supports: {labels()}, your own REST API, another agent."
            )

        known = tools_for(kind)
        if node.method not in known:
            raise ExportError(
                f"'{node.id}' calls '{node.method}', which the '{node.connector}' "
                f"connector ({kind}) has no tool for. It offers: {', '.join(known)}."
            )

    # Settings are built here as well as at render time so a bad one (an unknown
    # search provider, a database driver that needs a package) is reported as a
    # refusal alongside the others, not as a traceback halfway through writing.
    for name, spec in connectors.items():
        kind = _canonical((spec or {}).get("type", name))
        if kind in SNIPPETS:
            try:
                settings_for(kind, spec or {})
            except ValueError as exc:
                raise ExportError(f"Connector '{name}': {exc}") from exc

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
    to commit. Containers are walked so a ``${VAR}`` nested in a headers dict is
    caught too.
    """
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return f"os.environ.get({value[2:-1]!r}, '')"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k!r}: {_env_ref(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_env_ref(v) for v in value) + "]"
    return repr(value)


#: Config keys a plain REST connector carries into the generated file.
_REST_KEYS = ("base_url", "auth", "token", "api_key", "header", "username", "password")


def _settings_of(kind: str, spec: dict[str, Any]) -> dict[str, Any]:
    """The settings one connector contributes to the generated CONNECTORS dict."""
    if kind == "agent":
        return {k: v for k, v in spec.items() if k in ("base_url", "api_key")}
    if kind in SNIPPETS:
        return settings_for(kind, spec)
    return {k: v for k, v in spec.items() if k in _REST_KEYS}


def _connector_block(connectors: dict[str, Any]) -> str:
    lines = []
    for name, spec in connectors.items():
        spec = spec or {}
        kind = _canonical(spec.get("type", name))
        if kind not in SNIPPETS and kind != "agent":
            kind = "rest_api"
        settings = _settings_of(kind, spec)
        rendered = "".join(f", {k!r}: {_env_ref(v)}" for k, v in settings.items())
        # `kind` is what the generated dispatch keys on, so it is always present.
        lines.append(f"    {name!r}: {{'kind': {kind!r}{rendered}}},")
    return "\n".join(lines) or "    # (no connectors)"


def _used_kinds(workflow: Workflow, connectors: dict[str, Any]) -> list[str]:
    """Catalogued connector types this workflow actually calls, in a stable order.

    Only these get their code emitted — an agent that searches the web has no
    reason to carry a Jira client it never reaches.
    """
    used = {
        _kind_of(connectors, node.connector)
        for node in workflow.nodes
        if isinstance(node, ConnectorAction) and node.connector
    }
    return [kind for kind in SNIPPETS if kind in used]


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

    kinds = _used_kinds(workflow, connectors)
    imports = sorted({
        module for kind in kinds for module in SNIPPETS[kind].get("imports", ())
    })
    snippets = "".join(SNIPPETS[kind]["code"] for kind in kinds)
    table = "\n".join(
        f"    ({kind!r}, {tool!r}): _{kind}_{tool},"
        for kind in kinds
        for tool in tools_for(kind)
    )

    base_url = model.get("base_url") or _OPENAI_WIRE.get(model.get("provider"), "")
    nodes = {n.id: _node_spec(n) for n in workflow.nodes}

    return _TEMPLATE.format(
        name=name,
        extra_imports="".join(f"import {module}\n" for module in imports),
        expressions=_vendored_expressions(),
        connectors=_connector_block(connectors),
        snippets=snippets or "\n# (this workflow calls no built-in connectors)\n",
        tool_table=table or "    # (none)",
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
commit. Put them in a .env beside this file, or set them for real — actual
environment variables always win.

Not carried over from roscoe: retries, human approval gates, audit logging and
cost tracking. This is the workflow's logic on its own — if you need those,
run it with roscoe.

Use it:
    from {name} import run
    print(run({{"message": "hello"}})["output"])
"""

import base64
import json
import os
import sys
import time
{extra_imports}
import httpx


def _load_dotenv(path=None):
    """Read a .env sitting beside this file, without needing python-dotenv.

    Real environment variables win, so a container's own config is never
    overridden by a file someone left in the directory.
    """
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Before the settings below read it.
_load_dotenv()

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

#: Access tokens, by token endpoint + client, with the moment they go stale.
_TOKEN_CACHE = {{}}


def _oauth_token(cfg):
    """A bearer token for an OAuth connector, refreshed when it expires.

    Both flows this file supports — Google's refresh_token and Microsoft's
    client_credentials — are a form-encoded POST returning an access token and
    a lifetime, so one helper covers them by carrying the form in the config.
    """
    url = cfg.get("token_url", "").replace("{{tenant}}", cfg.get("tenant_id", ""))
    form = dict(cfg.get("token_form") or {{}})
    key = url + "|" + str(form.get("client_id", ""))

    cached = _TOKEN_CACHE.get(key)
    if cached and time.monotonic() < cached[1]:
        return cached[0]

    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            url, data=form,
            # These endpoints reject a form body whose header claims JSON.
            headers={{"Content-Type": "application/x-www-form-urlencoded"}},
        )
        response.raise_for_status()
        payload = response.json()

    token = payload["access_token"]
    # A minute of margin, so a token can't expire mid-request.
    _TOKEN_CACHE[key] = (
        token, time.monotonic() + int(payload.get("expires_in", 3600)) - 60)
    return token


def _headers(cfg):
    """Auth headers for a connector, matching roscoe's own modes."""
    out = dict(cfg.get("headers") or {{}})
    mode = cfg.get("auth", "none")
    if mode == "bearer":
        out["Authorization"] = "Bearer " + cfg.get("token", "")
    elif mode == "api_key":
        out[cfg.get("header", "X-API-Key")] = cfg.get("api_key", "")
    elif mode == "basic":
        raw = (cfg.get("username", "") + ":" + cfg.get("password", "")).encode()
        out["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    elif mode == "oauth":
        out["Authorization"] = "Bearer " + _oauth_token(cfg)
    return out


def _http(cfg, verb, path, params=None, json_body=None, data=None, content=None,
          extra_headers=None):
    """One HTTP call. An absolute `path` is used as-is, for APIs like Google's
    that spread their endpoints across several hosts."""
    path = str(path)
    url = path if path.startswith("http") else (
        cfg.get("base_url", "").rstrip("/") + "/" + path.lstrip("/"))

    headers = _headers(cfg)
    if extra_headers:
        headers.update(extra_headers)

    with httpx.Client(timeout=60.0) as client:
        response = client.request(
            verb, url, headers=headers,
            params=params, json=json_body, data=data, content=content,
        )
        response.raise_for_status()
        if "application/json" in response.headers.get("content-type", ""):
            return response.json()
        return {{"status_code": response.status_code, "text": response.text}}


def _rest(cfg, method, args):
    """A raw REST call: `method` is rest_get / rest_post / rest_put / rest_delete."""
    verb = method.replace("rest_", "").upper()
    if verb not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise RuntimeError("Unsupported method: " + method)
    args = dict(args or {{}})
    path = args.pop("path", "")
    return _http(cfg, verb, path, params=args.get("params"), json_body=args.get("body"))


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

{snippets}
#: (connector type, tool name) -> the function that runs it.
CONNECTOR_TOOLS = {{
{tool_table}
}}


def call_connector(name, method, args):
    """Run one connector tool, whichever kind of connector it belongs to."""
    cfg = CONNECTORS.get(name) or {{}}
    kind = cfg.get("kind", "rest_api")

    if kind == "agent":
        return call_agent(cfg, (args or {{}}).get("message", ""))
    if kind == "rest_api":
        return _rest(cfg, method, args)

    tool = CONNECTOR_TOOLS.get((kind, method))
    if tool is None:
        raise RuntimeError(
            "Connector " + repr(name) + " (" + kind + ") has no tool called "
            + repr(method) + ".")
    return tool(cfg, dict(args or {{}}))


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
    # A model's answer can carry a dash or quote outside Windows' legacy console
    # codepage (cp1252) — reconfigure rather than let a routine reply crash the
    # print with UnicodeEncodeError.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    result = run({{"message": " ".join(sys.argv[1:])}})
    if result["status"] != "success":
        print("error:", result.get("error"), file=sys.stderr)
        raise SystemExit(1)
    print(result["output"])
'''
