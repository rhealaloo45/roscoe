"""``roscoe build`` — a visual editor for workflows.

Drag nodes onto a canvas, connect them, fill in a form per node, and save: the
project's ``workflow.yaml`` is written from the graph. Editing it by hand still works
— the canvas loads whatever is on disk, so neither way of working locks you out of
the other.

Node positions live in a sibling ``.workflow-layout.json`` rather than in the
workflow itself, so the config stays about behaviour and a hand-edited file never
fills up with pixel coordinates.

**Saving rewrites the file, so YAML comments in the ``workflow:`` block are lost.**
That is inherent to round-tripping through a graph; the command says so before it
overwrites anything.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import click
import yaml

from roscoe.config.loader import ConfigError, load_config
from roscoe.workflow.loader import DEFAULT_WORKFLOW_FILE, find_workflow_file
from roscoe.workflow.registry import build_connectors
from roscoe.workflow.schema import Workflow, WorkflowError
from roscoe.workflow.validate import validate_workflow

#: Where node positions are kept — beside the workflow, out of the config.
LAYOUT_FILE = ".workflow-layout.json"


@click.command("build")
@click.option("--config", default="agent_config.yaml", show_default=True,
              help="Path to the agent config YAML.")
@click.option("--workflow", "workflow_path", default=None,
              help="Workflow file to edit. Defaults to workflow.yaml beside the config.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Editor host.")
@click.option("--port", default=8099, show_default=True, help="Editor port.")
@click.option("--no-browser", is_flag=True, help="Serve without opening a browser.")
def build_command(
    config: str, workflow_path: str | None, host: str, port: int, no_browser: bool
) -> None:
    """Open the visual workflow editor."""
    config_file = Path(config)
    target = Path(workflow_path) if workflow_path else (
        find_workflow_file(config_file) or config_file.parent / DEFAULT_WORKFLOW_FILE
    )

    state = _EditorState(config_file, target)
    httpd = HTTPServer((host, port), _handler_for(state))
    url = f"http://{host}:{port}"

    click.secho(f"roscoe build — editing {target}", fg="blue", bold=True)
    if target.is_file():
        click.secho("  Saving rewrites this file; comments in it will be lost.", fg="yellow")
    else:
        click.secho("  New workflow — it will be created when you save.", dim=True)
    click.secho(f"  {url}   (Ctrl-C to stop)", dim=True)

    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nstopped.")
    finally:
        httpd.server_close()


class _EditorState:
    """Reads and writes the workflow file the editor is pointed at."""

    def __init__(self, config_file: Path, workflow_file: Path) -> None:
        self.config_file = config_file
        self.workflow_file = workflow_file
        self.layout_file = workflow_file.parent / LAYOUT_FILE

    # --- loading ---

    def load(self) -> dict[str, Any]:
        """Current workflow + saved positions + what connectors offer."""
        block = self._read_block()
        try:
            workflow = Workflow.from_dict(block, self._agents()) if block else None
            error = None
        except WorkflowError as exc:
            workflow, error = None, str(exc)

        return {
            "file": str(self.workflow_file),
            "workflow": workflow.to_dict() if workflow else _starter(),
            "layout": self._read_layout(),
            "methods": self._methods(),
            "agents": sorted(self._agents()),
            "error": error,
        }

    def _read_block(self) -> dict[str, Any]:
        """The workflow mapping, from its own file or the config's `workflow:` key."""
        if self.workflow_file.is_file():
            raw = yaml.safe_load(self.workflow_file.read_text(encoding="utf-8")) or {}
            return raw.get("workflow", raw)
        if self.config_file.is_file():
            try:
                return (load_config(self.config_file) or {}).get("workflow") or {}
            except ConfigError:
                return {}
        return {}

    def _agents(self) -> dict[str, Any]:
        for source in (self.workflow_file, self.config_file):
            if not source.is_file():
                continue
            try:
                raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            if raw.get("agents"):
                return raw["agents"]
        return {}

    def _methods(self) -> dict[str, list[str]]:
        """Method names per connector, so the editor offers real choices."""
        connectors = self._connectors()
        if not connectors:
            return {}
        try:
            return {
                name: sorted(tool.name for tool in connector.tools)
                for name, connector in connectors.items()
            }
        finally:
            _close(connectors)

    def _existing_document(self) -> dict[str, Any]:
        """The workflow file's other top-level keys, so a save preserves them."""
        if not self.workflow_file.is_file():
            return {}
        try:
            raw = yaml.safe_load(self.workflow_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return {}
        if not isinstance(raw, dict) or "workflow" not in raw:
            return {}  # bare file: nodes were at the top level, nothing to keep
        return {key: value for key, value in raw.items() if key != "workflow"}

    def _read_layout(self) -> dict[str, Any]:
        if not self.layout_file.is_file():
            return {}
        try:
            return json.loads(self.layout_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    # --- saving ---

    def check(self, block: dict[str, Any]) -> dict[str, Any]:
        """Parse and validate a graph without writing it."""
        try:
            workflow = Workflow.from_dict(block, self._agents())
        except WorkflowError as exc:
            return {"ok": False, "issues": [{"level": "error", "message": str(exc)}]}

        connectors = self._connectors()
        try:
            issues = validate_workflow(workflow, connectors=connectors)
        finally:
            _close(connectors)
        return {
            "ok": not any(i.level == "error" for i in issues),
            "issues": [{"level": i.level, "message": i.message, "node": i.node} for i in issues],
        }

    def _connectors(self) -> dict[str, Any] | None:
        try:
            config = load_config(self.config_file) if self.config_file.is_file() else {}
            block = config.get("connectors") or {}
            return build_connectors(block) if block else None
        except Exception:  # noqa: BLE001
            return None

    def save(self, block: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
        """Write the workflow, refusing anything that would not parse."""
        result = self.check(block)
        if not result["ok"]:
            return {"saved": False, **result}

        # Round-trip through the parser so what lands on disk is normalised, not
        # whatever shape the browser happened to send.
        workflow = Workflow.from_dict(block, self._agents())

        # Keep every other top-level key the file already had — `agents:` above all.
        # Writing only the workflow would quietly delete the rest of the user's file.
        document = self._existing_document()
        document["workflow"] = workflow.to_dict()
        body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=88)
        self.workflow_file.write_text(
            "# Generated by `roscoe build`. Editing by hand is fine — the editor\n"
            "# reloads whatever is here.\n\n" + body,
            encoding="utf-8",
        )
        self.layout_file.write_text(json.dumps(layout, indent=2), encoding="utf-8")
        return {"saved": True, "file": str(self.workflow_file), **result}


def _close(connectors: dict[str, Any] | None) -> None:
    """Release connections opened just to inspect a connector.

    The editor is long-lived, so leaving these open holds a sqlite file locked for
    as long as it runs — on Windows that blocks the agent from touching its own
    database.
    """
    for connector in (connectors or {}).values():
        try:
            connector.close()
        except Exception:  # noqa: BLE001 — best effort, never break the editor
            pass


def _starter() -> dict[str, Any]:
    """A one-node workflow, so a new project opens with something on the canvas."""
    return {
        "entry": "start",
        "nodes": [{"id": "start", "type": "llm_step", "prompt": "Say hello.", "output": "reply"}],
    }


def _handler_for(state: _EditorState) -> type[BaseHTTPRequestHandler]:
    from roscoe.cli.build_ui import PAGE

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _json(self, payload: dict[str, Any], code: int = 200) -> None:
            body = json.dumps(payload, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/api/workflow"):
                self._json(state.load())
                return
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            payload = self._read()
            if self.path.startswith("/api/validate"):
                self._json(state.check(payload.get("workflow") or {}))
            elif self.path.startswith("/api/save"):
                self._json(
                    state.save(payload.get("workflow") or {}, payload.get("layout") or {})
                )
            else:
                self.send_response(404)
                self.end_headers()

    return _Handler
