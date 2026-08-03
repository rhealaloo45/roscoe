"""Package an exported workflow as a folder someone can actually drop into a project.

A single ``.py`` is the interesting part but not a usable handover: it reads
environment variables nobody has written down, needs a dependency nobody has
stated, and arrives with no explanation of what it is or how to call it. So the
download is a zip of four files — the script, a ``.env.example`` naming every
variable it will look for and where that variable came from, a
``requirements.txt``, and a README written for whoever opens the folder without
having been in the room when it was built.

The ``.env.example`` is generated from the config rather than written by hand,
which is the point: it can't drift from what the script actually reads, and a
connector added in the editor turns up in it automatically.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from roscoe.export.python_generator import generate_python
from roscoe.workflow.schema import Condition, ConnectorAction, LLMStep, Trigger, Workflow

#: ``${VAR}`` as it appears in a config value.
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_REQUIREMENTS = """# The only thing this agent needs.
httpx>=0.27,<1.0
"""


def _placeholders(value: Any) -> list[str]:
    """Every ``${VAR}`` inside a config value, however deeply nested."""
    if isinstance(value, str):
        return _PLACEHOLDER.findall(value)
    if isinstance(value, dict):
        return [name for item in value.values() for name in _placeholders(item)]
    if isinstance(value, list):
        return [name for item in value for name in _placeholders(item)]
    return []


def env_vars(config: dict[str, Any]) -> dict[str, str]:
    """Every environment variable the exported file will read, and what wants it.

    Ordered as encountered, model first, so the example file reads in roughly the
    order someone would fill it in.
    """
    found: dict[str, str] = {}

    model = config.get("model") or {}
    provider = model.get("provider") or "the model"
    for name in _placeholders(model):
        found.setdefault(name, f"{provider} API key")

    for connector, spec in (config.get("connectors") or {}).items():
        kind = (spec or {}).get("type", connector)
        for name in _placeholders(spec):
            found.setdefault(name, f"connector '{connector}' ({kind})")

    return found


def env_example(config: dict[str, Any]) -> str:
    """The ``.env.example`` file's text."""
    variables = env_vars(config)
    lines = [
        "# Copy this file to .env and fill in the values.",
        "# The agent reads a .env sitting beside it, and real environment",
        "# variables always take priority over anything written here.",
        "",
    ]
    if not variables:
        lines.append("# This agent needs no secrets.")
    for name, origin in variables.items():
        lines.append(f"# {origin}")
        lines.append(f"{name}=")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def describe_flow(workflow: Workflow) -> str:
    """The workflow's steps as a numbered list, for the README.

    Written from the parsed nodes rather than the YAML so it describes what the
    exported file will really do, in the order it will really do it.
    """
    lines = []
    for position, node in enumerate(workflow.nodes, start=1):
        if isinstance(node, Trigger):
            when = f" at {node.at}" if node.at else ""
            what = f"marks how often this should run (every {node.every}{when})"
        elif isinstance(node, ConnectorAction):
            what = f"calls `{node.method}` on the `{node.connector}` connector"
        elif isinstance(node, Condition):
            what = f"branches on `{node.when}`"
        elif isinstance(node, LLMStep):
            what = "asks the model"
        else:
            what = f"a {node.type} step"
        if getattr(node, "output", None):
            what += f", saving the result as `{node.output}`"
        lines.append(f"{position}. **`{node.id}`** — {what}")
    return "\n".join(lines) or "_(no steps)_"


def readme(workflow: Workflow, config: dict[str, Any], name: str) -> str:
    """The README that ships alongside the script."""
    return f"""# {name}

A standalone agent, built with [roscoe](https://pypi.org/project/roscoe/) and
exported to plain Python. **roscoe is not needed to run it** — it imports
`httpx` and nothing else.

## Install

```bash
pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

`{name}.py` reads that file on import. Real environment variables win over it,
so the same code works unchanged in a container where the values come from your
own secret store. Nothing is baked into the source, so `{name}.py` is safe to
commit; `.env` is not.

## Use it

From your own code:

```python
from {name} import run

result = run({{"message": "hello"}})
print(result["output"])
```

`run()` always returns a dict and never raises, so a failure inside the agent
can't take down the request handler calling it:

```python
{{
    "status": "success" | "error",
    "output": ...,      # the workflow's output, on success
    "state": {{...}},     # every step's result, by name
    "steps": [...],     # the nodes it visited, in order
    "error": "...",     # only when status is "error"
}}
```

From a terminal:

```bash
python {name}.py "your message here"
```

## What it does

{describe_flow(workflow)}

## What stayed behind

roscoe wraps a workflow in retries, human approval gates, audit logging and cost
tracking. None of that is in here — this is the workflow's logic on its own. If
you need those, run the original project with `roscoe run` instead.
"""


def build_bundle(
    workflow: Workflow,
    config: dict[str, Any],
    *,
    name: str = "agent",
) -> bytes:
    """The whole export as a zip, ready to hand to someone.

    Everything sits under a single top-level folder, so unzipping in a project
    root drops one tidy directory rather than scattering four loose files.

    Raises:
        ExportError: if the workflow uses something the generated file could not
            reproduce — same refusals as :func:`generate_python`.
    """
    source = generate_python(workflow, config, name=name)

    files = {
        f"{name}/{name}.py": source,
        f"{name}/.env.example": env_example(config),
        f"{name}/requirements.txt": _REQUIREMENTS,
        f"{name}/README.md": readme(workflow, config, name),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return buffer.getvalue()
