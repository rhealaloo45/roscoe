"""``roscoe graph`` — see the workflow as a diagram.

Opens the flowchart in a browser by default, so a workflow can be reviewed by
someone who will not read YAML. ``--terminal`` prints the Mermaid source (paste it
into a PR, a doc, or a wiki), and ``--output`` writes it to a file.

Read-only on purpose: the file stays the source of truth, and the picture is
generated from it, so the two cannot drift.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import click

from roscoe.config.loader import ConfigError
from roscoe.workflow.diagram import to_mermaid
from roscoe.workflow.loader import load_workflow
from roscoe.workflow.schema import WorkflowError


@click.command("graph")
@click.option("--config", default="agent_config.yaml", show_default=True,
              help="Path to the agent config YAML.")
@click.option("--workflow", "workflow_path", default=None,
              help="Workflow file to draw. Defaults to the config's 'workflow:' "
                   "block, or a sibling workflow.yaml.")
@click.option("--terminal", "as_text", is_flag=True, help="Print Mermaid source instead of opening a browser.")
@click.option("--output", "out_path", default=None, help="Write the Mermaid source to a file.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Preview host.")
@click.option("--port", default=8090, show_default=True, help="Preview port.")
@click.option("--no-browser", is_flag=True, help="Serve without opening a browser.")
def graph_command(
    config: str, workflow_path: str | None, as_text: bool,
    out_path: str | None, host: str, port: int, no_browser: bool,
) -> None:
    """Draw this project's workflow as a flowchart."""
    try:
        workflow, _ = load_workflow(config, workflow_path, strict=False)
    except (WorkflowError, ConfigError) as exc:
        raise click.ClickException(str(exc)) from exc

    mermaid = to_mermaid(workflow)
    name = workflow_path or config

    if out_path:
        Path(out_path).write_text(mermaid, encoding="utf-8")
        click.secho(f"Wrote {out_path}", fg="green")
        return

    if as_text:
        click.echo(mermaid)
        return

    _serve(mermaid, name, len(workflow.nodes), host, port, open_browser=not no_browser)


def _serve(mermaid: str, name: str, node_count: int, host: str, port: int, *, open_browser: bool) -> None:
    """Serve the rendered diagram until interrupted."""
    page = _PAGE.replace("__DIAGRAM__", json.dumps(mermaid)).replace(
        "__TITLE__", name
    ).replace("__COUNT__", str(node_count))

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = HTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    click.secho(f"roscoe graph — {name}", fg="blue", bold=True)
    click.secho(f"  {node_count} node(s) at {url}   (Ctrl-C to stop)", dim=True)
    if open_browser:
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


_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>roscoe graph</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#f7f8fa;color:#1e293b;min-height:100vh}
  header{background:#eef1f6;border-bottom:1px solid #dbe1e8;padding:14px 24px;
    display:flex;align-items:baseline;gap:12px}
  header h1{font-size:15px;font-weight:600}
  header span{font-size:12px;color:#64748b}
  .legend{margin-left:auto;font-size:11.5px;color:#64748b;display:flex;gap:14px}
  .chart{padding:28px;display:flex;justify-content:center;overflow:auto}
  .chart svg{max-width:100%;height:auto}
</style></head><body>
<header>
  <h1>__TITLE__</h1><span>__COUNT__ nodes</span>
  <div class="legend">
    <span>[ ] action</span><span>&lt; &gt; decision</span><span>( ) prompt</span>
    <span>&#128274; needs approval</span><span>&middot;&middot;&middot; falls through</span>
  </div>
</header>
<div class="chart"><pre class="mermaid" id="chart"></pre></div>
<script>
  document.getElementById('chart').textContent = __DIAGRAM__;
  mermaid.initialize({startOnLoad: true, theme: 'neutral', securityLevel: 'strict'});
</script>
</body></html>"""
