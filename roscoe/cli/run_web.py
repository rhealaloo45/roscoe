"""Built-in web UI for ``roscoe run`` — a landing page with a chat panel.

A stdlib ``http.server`` (no Flask) serves the page and a plain JSON chat
endpoint. No streaming of the reply itself: each message is a single
request/response. While that request is in flight, the page polls a small
``/api/progress`` endpoint for a workflow's current node, so a long run shows
*which step it's on* instead of an indefinite "..." — a workflow's, since only
:class:`~roscoe.workflow.runner.WorkflowRunner` exposes ``set_on_step``; a plain
agent still just shows the typing indicator, which is fine because a single
ReAct call has no graph to report progress through.

Human-in-the-loop pauses surface as approve/reject buttons inside the chat panel.

The page is configured from an optional ``ui:`` block in ``agent_config.yaml``
— title, greeting, accent colour — so a project gets a presentable front end
without writing one. Declaring ``ui.inputs`` switches the panel from a chat box
to a **form**, which is what a workflow actually wants: it takes named inputs
(``input.employee_id``), not a sentence.

The HTTP layer is threaded (``ThreadingHTTPServer``) so a progress poll can be
served while a chat request is still running — but every actual agent call
still funnels through one dedicated worker thread (``max_workers=1``), so the
agent's async primitives (the rate-limiter's lock, etc.) keep the single
persistent per-thread event loop they need. Concurrent *agent* calls were never
supported and still aren't; what changed is that the HTTP socket no longer
blocks on one.
"""

from __future__ import annotations

import json
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: Defaults for every ``ui:`` key, so the block is entirely optional.
_UI_DEFAULTS: dict[str, Any] = {
    "title": "roscoe",
    "subtitle": "agent",
    "heading": "Welcome",
    "intro": "Ask a question, or sign in for personalised help.",
    "greeting": "Hi — I'm your agent. Ask me anything.",
    "placeholder": "Type a message…",
    "submit": "Send",
    "accent": "#2563eb",
    "inputs": [],
}


def check_auth(headers: Any, api_key: str | None) -> bool:
    """Whether a request may call the API.

    No key configured means no check — a local ``roscoe run`` stays as
    frictionless as it has always been. Configure one and every ``/api/`` call
    must present it, which is what makes the server safe to point an existing
    application at.
    """
    if not api_key:
        return True
    return headers.get("Authorization", "") == f"Bearer {api_key}"


def cors_headers(allowed: str | None) -> dict[str, str]:
    """CORS headers when a browser on another origin is allowed to call this.

    Without these a ``fetch`` from an app's own domain fails before the agent
    is ever reached, with a console message and no server-side trace.
    """
    if not allowed:
        return {}
    return {
        "Access-Control-Allow-Origin": allowed,
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Max-Age": "86400",
    }


def serve_chat(agent: Any, *, host: str = "127.0.0.1", port: int = 5005,
               user_id: str = "web-user", session_id: str = "web-session",
               open_browser: bool = True, ui: dict[str, Any] | None = None,
               api_key: str | None = None, cors_origin: str | None = None) -> None:
    """Serve the browser UI for ``agent`` (blocking; Ctrl-C to stop)."""
    state: dict[str, Any] = {"pending_run_id": None}
    settings = {**_UI_DEFAULTS, **(ui or {})}
    # Only a workflow accepts named inputs; an agent takes a sentence.
    takes_inputs = hasattr(agent, "workflow")
    fields = _clean_fields(settings.get("inputs")) if takes_inputs else []
    page = _render_page(settings, fields)
    # A webhook trigger node opts the project into a public POST /webhook —
    # without one, an external service has no way to start this workflow, so
    # the endpoint stays 404 rather than silently accepting anything.
    webhook_enabled = takes_inputs and any(
        getattr(n, "type", None) == "trigger" and getattr(n, "kind", "schedule") == "webhook"
        for n in getattr(agent.workflow, "nodes", None) or []
    )

    # All actual agent.run()/resume() calls happen on this one worker thread,
    # never on whichever HTTP thread received the request — that's what keeps
    # the async primitives on a single persistent event loop even though the
    # HTTP layer itself is now threaded.
    work = ThreadPoolExecutor(max_workers=1)
    # `on_step` fires once per node, in order, so accumulating every id gives a
    # checklist for free: everything but the last entry is done, the last one
    # is whatever the run is on right now.
    progress: dict[str, list[str]] = {"steps": []}
    _last_step: dict[str, Any] = {"node": None, "t": 0.0}

    def _on_step(node_id: str) -> None:
        now = time.monotonic()
        if _last_step["node"] is not None:
            print(f"    ({_last_step['node']} took {now - _last_step['t']:.1f}s)")
        _last_step["node"], _last_step["t"] = node_id, now
        progress["steps"].append(node_id)
        print(f"  -> node '{node_id}'")

    def _on_tool_call(agent_name: str, event: dict[str, Any]) -> None:
        args = event.get("args", {})
        if event["phase"] == "start":
            print(f"      [{agent_name}] {event['name']}({args}) ...")
        else:
            mark = "ok" if event["phase"] == "done" else "FAILED"
            print(
                f"      [{agent_name}] {event['name']} {mark} "
                f"({event['seconds']:.1f}s): {event['summary']}"
            )

    if hasattr(agent, "set_on_step"):
        agent.set_on_step(_on_step)
    if hasattr(agent, "set_on_tool_call"):
        agent.set_on_tool_call(_on_tool_call)

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence request spam
            pass

        def handle_one_request(self) -> None:
            """Swallow the client hanging up — a reload aborts the socket
            mid-response, and the stdlib prints a traceback that reads like a
            crash when nothing has gone wrong."""
            try:
                super().handle_one_request()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                self.close_connection = True

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def _send_json(self, obj: dict[str, Any], code: int = 200) -> None:
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in cors_headers(cors_origin).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self) -> bool:
            """Gate ``/api/`` only. The page itself stays open — it is a
            convenience for local use, the API is the surface worth protecting."""
            if not self.path.startswith("/api/"):
                return True
            if check_auth(self.headers, api_key):
                return True
            self._send_json({"error": "Unauthorized"}, code=401)
            return False

        def do_OPTIONS(self) -> None:  # noqa: N802
            """CORS preflight. A cross-origin POST sends this first and never
            reaches the agent if it goes unanswered."""
            self.send_response(204)
            for name, value in cors_headers(cors_origin).items():
                self.send_header(name, value)
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if not self._allowed():
                return
            if self.path in ("/", "/index.html"):
                body = page.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/progress":
                self._send_json({"steps": progress["steps"]})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if not self._allowed():
                return
            if self.path == "/api/chat":
                self._chat(self._read_json())
            elif self.path == "/api/approve":
                self._approve(self._read_json().get("decision", "reject"))
            elif self.path == "/webhook" and webhook_enabled:
                self._webhook(self._read_json())
            else:
                self.send_response(404)
                self.end_headers()

        def _webhook(self, body: dict[str, Any]) -> None:
            # The request body *is* the workflow's input, same as an
            # {"inputs": {...}} chat call — no "message" fallback, since a
            # webhook caller is a service, not someone typing a sentence.
            progress["steps"] = []
            result = work.submit(agent.run, body, user_id=user_id, session_id=session_id).result()
            self._send_json(_result_payload(result, state))

        def _chat(self, body: dict[str, Any]) -> None:
            uid = body.get("user_id") or user_id
            sid = body.get("session_id") or session_id
            # A form posts named inputs; a chat box posts a sentence.
            payload = body.get("inputs") if takes_inputs and body.get("inputs") else body.get("message", "")
            progress["steps"] = []
            result = work.submit(agent.run, payload, user_id=uid, session_id=sid).result()
            self._send_json(_result_payload(result, state))

        def _approve(self, decision: str) -> None:
            run_id = state.get("pending_run_id")
            if not run_id:
                self._send_json({"type": "error", "error": "No pending action."})
                return
            state["pending_run_id"] = None
            progress["steps"] = []
            result = work.submit(agent.resume, run_id, decision).result()
            self._send_json(_result_payload(result, state))

    httpd = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"roscoe run — web UI at {url}")
    print(f"  agent={agent.agent_name}  provider={agent.provider}  model={agent.model}")
    if webhook_enabled:
        print(f"  webhook: POST {url}/webhook  (request body becomes the workflow's input)")
    print("  Ctrl-C to stop.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
        work.shutdown(wait=False, cancel_futures=True)


def _clean_fields(raw: Any) -> list[dict[str, Any]]:
    """Normalise ``ui.inputs`` into form fields, ignoring malformed entries.

    A bad field should not take the whole page down — the agent still works, the
    input just isn't offered.
    """
    fields: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        fields.append({
            "name": name,
            "label": str(item.get("label") or name.replace("_", " ").capitalize()),
            "type": str(item.get("type") or "text"),
            "placeholder": str(item.get("placeholder") or ""),
            "options": [str(o) for o in (item.get("options") or [])],
            "default": "" if item.get("default") is None else str(item["default"]),
            "required": bool(item.get("required", False)),
        })
    return fields


def _render_page(settings: dict[str, Any], fields: list[dict[str, Any]]) -> str:
    """Substitute the ``ui:`` settings into the page template."""
    page = _PAGE
    for key in ("title", "subtitle", "heading", "intro", "greeting", "placeholder", "submit"):
        page = page.replace(f"__{key.upper()}__", _escape(str(settings.get(key, ""))))
    page = page.replace("__ACCENT__", _css_colour(settings.get("accent")))
    return page.replace("__FIELDS__", json.dumps(fields))


def _css_colour(value: Any) -> str:
    """Only let a colour-shaped string through — it is interpolated into CSS."""
    text = str(value or "").strip()
    ok = text.startswith("#") and 4 <= len(text) <= 9 and all(
        c in "0123456789abcdefABCDEF" for c in text[1:]
    )
    return text if ok else _UI_DEFAULTS["accent"]


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _result_payload(result: Any, state: dict[str, Any]) -> dict[str, Any]:
    cost = f"${result.cost_usd:.4f}" if result.cost_usd else "free"
    if result.status == "paused":
        state["pending_run_id"] = result.run_id
        action = result.pending_action or {}
        return {"type": "paused", "run_id": result.run_id,
                "tool_calls": action.get("tool_calls", [])}
    if result.status == "error":
        return {"type": "error", "error": str(result.error)}
    # Some models occasionally stop with an empty final message (no tool_calls,
    # no content) — never render a literally blank bubble for that.
    output = result.output or "(no response from the model — try rephrasing)"
    return {"type": "final", "output": output,
            "tokens": result.total_tokens, "cost": cost, "tools": result.tool_calls}


_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  :root{--accent:__ACCENT__}
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#f7f8fa;color:#1e293b;height:100vh;overflow:hidden}
  /* form mode — shown instead of the chat box when ui.inputs is declared */
  .form{padding:14px 28px;border-top:1px solid #e2e8f0;background:#eef1f6;
    display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px 14px;align-items:end}
  .form label{display:block;font-size:11.5px;color:#64748b;margin-bottom:4px}
  .form input,.form select{width:100%;padding:9px 12px;background:#fff;border:1px solid #cbd5e1;
    border-radius:9px;color:#0f172a;font-size:14px;font-family:inherit;outline:none}
  .form input:focus,.form select:focus{border-color:var(--accent)}
  .form .go{padding:10px 20px;background:var(--accent);color:#fff;border:none;border-radius:9px;
    font-weight:600;cursor:pointer;font-size:14px;height:38px}
  .form .go:disabled{background:#cbd5e1;cursor:not-allowed}

  /* 12-col split: 3 cols sidebar, 9 cols chat */
  .layout{display:grid;grid-template-columns:repeat(12,1fr);height:100vh}
  .sidebar{grid-column:span 3;background:#eef1f6;border-right:1px solid #dbe1e8;
    padding:32px 24px;display:flex;flex-direction:column}
  .sidebar h1{font-size:19px;font-weight:700;margin-bottom:6px;color:#0f172a}
  .sidebar p{font-size:13px;color:#64748b;margin-bottom:24px;line-height:1.5}
  .field{margin-bottom:14px}
  .field label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
  .field input{width:100%;padding:10px 13px;background:#fff;border:1px solid #cbd5e1;
    border-radius:10px;color:#0f172a;font-size:14px;outline:none}
  .field input:focus{border-color:var(--accent)}
  .sidebar button{width:100%;margin-top:8px;padding:11px;background:var(--accent);color:#fff;border:none;
    border-radius:10px;font-weight:600;font-size:14px;cursor:pointer}
  .sidebar button:hover{background:#1d4ed8}
  .signedin{display:none;font-size:13px;color:#64748b}
  .signedin b{color:#0f172a}
  .meta-block{margin-top:auto;padding-top:20px;border-top:1px solid #dbe1e8;font-size:11px;color:#94a3b8}
  .meta-block div{margin-bottom:4px}

  /* chat column */
  .chat{grid-column:span 9;display:flex;flex-direction:column;min-height:0;background:#fff}
  .chead{padding:18px 28px;background:#eef1f6;border-bottom:1px solid #dbe1e8;display:flex;align-items:center;justify-content:space-between}
  .chead .t{font-size:15px;font-weight:600;color:#0f172a}
  .chead .s{font-size:11px;color:#94a3b8;margin-top:2px}
  .msgs{flex:1;overflow-y:auto;padding:24px 28px;display:flex;flex-direction:column;gap:12px;background:#f7f8fa}
  .msgs::-webkit-scrollbar{width:6px}.msgs::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:3px}
  .m{max-width:65%;padding:10px 15px;border-radius:14px;font-size:14px;line-height:1.55;word-wrap:break-word}
  .m.user{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px;white-space:pre-wrap}
  .m.bot{align-self:flex-start;background:#fff;color:#1e293b;border:1px solid #e2e8f0;border-bottom-left-radius:4px}
  .m.err{align-self:center;background:#fef2f2;color:#dc2626;font-size:13px;border:1px solid #fecaca}
  .m.bot p{margin:0 0 8px}.m.bot p:last-child{margin-bottom:0}
  .m.bot ul,.m.bot ol{margin:0 0 8px 20px}
  .m.bot code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12.5px;font-family:ui-monospace,monospace}
  .m.bot pre{background:#0f172a;color:#e2e8f0;padding:10px 12px;border-radius:8px;overflow-x:auto;margin:0 0 8px}
  .m.bot pre code{background:none;padding:0;color:inherit}
  .m.bot table{border-collapse:collapse;margin:0 0 8px;font-size:13px}
  .m.bot th,.m.bot td{border:1px solid #e2e8f0;padding:5px 9px;text-align:left}
  .m.bot th{background:#f8fafc}
  .m.bot a{color:var(--accent)}
  .typing{align-self:flex-start;background:#fff;border:1px solid #e2e8f0;border-radius:14px;border-bottom-left-radius:4px;
    padding:12px 16px;display:flex;flex-direction:column;gap:8px;min-width:120px}
  .typing .dots{display:flex;align-items:center;gap:4px}
  .typing .dots span{width:6px;height:6px;border-radius:50%;background:#94a3b8;animation:bounce 1.2s infinite}
  .typing .dots span:nth-child(2){animation-delay:.15s}.typing .dots span:nth-child(3){animation-delay:.3s}
  @keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:.5}30%{transform:translateY(-4px);opacity:1}}
  /* the checklist of nodes a workflow has entered so far — empty and invisible
     for a plain agent, which has no graph to report progress through */
  .steps{display:flex;flex-direction:column;gap:3px}
  .steps .step{font-size:11.5px;display:flex;align-items:center;gap:6px;color:#94a3b8}
  .steps .step.done{color:#16a34a}
  .steps .step.current{color:#0f172a;font-weight:600}
  .steps .spin{width:9px;height:9px;flex:0 0 auto;border-radius:50%;border:2px solid #cbd5e1;
    border-top-color:var(--accent);animation:spin .7s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .approve{align-self:flex-start;background:#fffbeb;border:1px solid #fcd34d;border-radius:12px;padding:12px 15px;font-size:13px;max-width:70%}
  .approve b{color:#b45309}
  .approve .tc{margin:8px 0}
  .approve .tc-name{font-family:ui-monospace,monospace;font-weight:600;color:#92400e;margin-bottom:4px}
  .approve table.tc-args{border-collapse:collapse;width:100%}
  .approve .tc-k{font-family:ui-monospace,monospace;color:#92400e;opacity:.75;padding:3px 8px 3px 0;
    vertical-align:top;white-space:nowrap;font-size:12px}
  .approve .tc-v{font-family:ui-monospace,monospace;color:#78350f;padding:3px 0;word-break:break-word;font-size:12px}
  .approve button{border:none;border-radius:8px;padding:7px 15px;font-weight:600;cursor:pointer;margin-right:8px;margin-top:6px;font-size:12.5px}
  .ok{background:#16a34a;color:#fff}.no{background:#dc2626;color:#fff}
  .bar{padding:8px 28px;font-size:11px;color:#94a3b8;background:#eef1f6;border-top:1px solid #dbe1e8}
  .in{display:flex;gap:10px;padding:16px 28px;background:#eef1f6;border-top:1px solid #dbe1e8}
  .in input{flex:1;padding:11px 15px;background:#fff;border:1px solid #cbd5e1;border-radius:10px;color:#0f172a;font-size:14px;outline:none}
  .in input:focus{border-color:var(--accent)}
  .in button{padding:11px 20px;background:var(--accent);color:#fff;border:none;border-radius:10px;font-weight:600;cursor:pointer;font-size:14px}
  .in button:disabled{background:#cbd5e1;cursor:not-allowed;color:#fff}

  @media (max-width:760px){
    .layout{grid-template-columns:1fr}
    .sidebar{grid-column:1;border-right:none;border-bottom:1px solid #dbe1e8}
    .chat{grid-column:1}
  }
</style></head><body>

<div class="layout">
  <div class="sidebar">
    <h1>__HEADING__</h1>
    <p>__INTRO__</p>
    <div id="loginForm">
      <div class="field"><label>Your name</label><input id="lname" placeholder="e.g. Rhea Laloo"></div>
      <div class="field"><label>Employee ID (optional)</label><input id="lid" placeholder="e.g. E-1042"></div>
      <button onclick="signIn()">Continue</button>
    </div>
    <div class="signedin" id="signedIn">Signed in as <b id="signedName"></b>.</div>
    <div class="meta-block" id="metaBlock"></div>
  </div>

  <div class="chat">
    <div class="chead">
      <div><div class="t">__TITLE__</div><div class="s">__SUBTITLE__</div></div>
    </div>
    <div class="msgs" id="msgs"><div class="m bot">__GREETING__</div></div>
    <div class="bar" id="bar">ready</div>
    <form class="form" id="form" style="display:none" onsubmit="event.preventDefault();submitForm()"></form>
    <div class="in" id="chatbar">
      <input id="q" placeholder="__PLACEHOLDER__" onkeydown="if(event.key==='Enter')send()">
      <button id="btn" onclick="send()">__SUBMIT__</button>
    </div>
  </div>
</div>

<script>
const FIELDS=__FIELDS__;
const msgs=document.getElementById('msgs'),q=document.getElementById('q'),btn=document.getElementById('btn'),bar=document.getElementById('bar');
let awaiting=false,userId='web-user';

// A workflow takes named inputs, so offer a form instead of a chat box.
function buildForm(){
  if(!FIELDS.length)return;
  document.getElementById('chatbar').style.display='none';
  // The sidebar sign-in belongs to the chat flow. With a real input form on the
  // right it just asks for the same details twice, so drop it.
  const login=document.getElementById('loginForm');
  if(login)login.style.display='none';
  const f=document.getElementById('form');
  f.style.display='grid';
  f.innerHTML=FIELDS.map(fd=>{
    const control = fd.type==='select'
      ? '<select name="'+esc(fd.name)+'">'+fd.options.map(o=>
          '<option'+(o===fd.default?' selected':'')+'>'+esc(o)+'</option>').join('')+'</select>'
      : '<input name="'+esc(fd.name)+'" type="'+esc(fd.type)+'" value="'+esc(fd.default)+'"'
        + ' placeholder="'+esc(fd.placeholder)+'"'+(fd.required?' required':'')+'>';
    return '<div><label>'+esc(fd.label)+'</label>'+control+'</div>';
  }).join('')+'<div><button class="go" id="go" type="submit">__SUBMIT__</button></div>';
}

async function submitForm(){
  if(awaiting)return;
  const f=document.getElementById('form');
  const inputs={};
  for(const el of f.querySelectorAll('input,select')) if(el.name) inputs[el.name]=el.value;
  const shown=FIELDS.map(fd=>fd.label+': '+(inputs[fd.name]||'—')).join('\n');
  add('user',shown);
  lockForm(true);bar.textContent='working…';
  const typing=showTyping();
  try{
    const resp=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({inputs:inputs,user_id:userId})});
    const ev=await resp.json();
    removeTyping(typing);handle(ev);
  }catch(e){removeTyping(typing);add('err','connection error: '+e.message)}
  lockForm(false);
}

function lockForm(on){
  const go=document.getElementById('go');
  if(go){go.disabled=on;go.textContent=on?'Working…':'__SUBMIT__';}
}

function signIn(){
  const name=document.getElementById('lname').value.trim();
  const id=document.getElementById('lid').value.trim();
  if(name)userId=id?(name+' ('+id+')'):name;
  document.getElementById('loginForm').style.display='none';
  const s=document.getElementById('signedIn');s.style.display='block';
  document.getElementById('signedName').textContent=name||'guest';
  q.focus();
}

function add(cls,txt){
  const d=document.createElement('div');d.className='m '+cls;
  if(cls==='bot'&&window.marked){d.innerHTML=marked.parse(txt||'')}else{d.textContent=txt}
  msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d
}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML}
function lock(on){btn.disabled=on;q.disabled=on}
function prettyNode(id){return id.replace(/[_-]+/g,' ').replace(/^./,c=>c.toUpperCase())}
function showTyping(){
  const d=document.createElement('div');d.className='typing';
  d.innerHTML='<div class="dots"><span></span><span></span><span></span></div><div class="steps"></div>';
  msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;
  const list=d.querySelector('.steps');
  let lastLen=-1;
  // Polls the checklist of nodes a workflow has entered so far — a plain agent
  // has no graph to report through, so `steps` just stays empty for one, and
  // this quietly renders nothing.
  d._poll=setInterval(async()=>{
    try{
      const r=await fetch('/api/progress');const j=await r.json();
      const steps=j.steps||[];
      if(steps.length===lastLen)return;   // avoid re-rendering every 600ms for nothing
      lastLen=steps.length;
      list.innerHTML=steps.map((s,i)=>
        '<div class="step'+(i===steps.length-1?' current':' done')+'">'
        + (i===steps.length-1?'<span class="spin"></span>':'&#10003;')
        + ' '+esc(prettyNode(s))+'</div>'
      ).join('');
      msgs.scrollTop=msgs.scrollHeight;
    }catch(e){}
  },600);
  return d;
}
function removeTyping(d){clearInterval(d._poll);d.remove()}

async function send(){
  if(awaiting)return;
  const t=q.value.trim();if(!t)return;q.value='';add('user',t);lock(true);bar.textContent='working…';
  const typing=showTyping();
  try{
    const resp=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:t,user_id:userId})});
    const ev=await resp.json();
    removeTyping(typing);
    handle(ev);
  }catch(e){removeTyping(typing);add('err','connection error: '+e.message)}
  lock(false);q.focus();
}
function handle(ev){
  if(ev.type==='final'){add('bot',ev.output);bar.textContent='tokens='+ev.tokens+' · cost='+ev.cost+(ev.tools&&ev.tools.length?' · tools: '+ev.tools.join(' → '):'')}
  else if(ev.type==='error'){add('err',ev.error);bar.textContent='error'}
  else if(ev.type==='paused'){showApprove(ev)}
}
function fmtVal(v){
  const s=typeof v==='string'?v:JSON.stringify(v);
  return s.length>160?s.slice(0,160)+'…':s;
}
function showApprove(ev){
  awaiting=true;bar.textContent='awaiting approval';
  const box=document.createElement('div');box.className='approve';
  let html='<b>&#9208; approval required</b>';
  (ev.tool_calls||[]).forEach(tc=>{
    html+='<div class="tc"><div class="tc-name">'+esc(tc.name)+'</div>';
    const args=tc.args||{};
    const keys=Object.keys(args);
    if(keys.length){
      html+='<table class="tc-args">'+keys.map(k=>
        '<tr><td class="tc-k">'+esc(k)+'</td><td class="tc-v">'+esc(fmtVal(args[k]))+'</td></tr>'
      ).join('')+'</table>';
    }
    html+='</div>';
  });
  html+='<button class="ok">Approve</button><button class="no">Reject</button>';
  box.innerHTML=html;msgs.appendChild(box);msgs.scrollTop=msgs.scrollHeight;
  box.querySelector('.ok').onclick=()=>decide('approve',box);
  box.querySelector('.no').onclick=()=>decide('reject',box);
}
async function decide(d,box){
  awaiting=false;box.querySelectorAll('button').forEach(b=>b.disabled=true);lock(true);bar.textContent='working…';
  const typing=showTyping();
  const resp=await fetch('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:d})});
  const ev=await resp.json();
  removeTyping(typing);
  handle(ev);
  lock(false);lockForm(false);
  if(!FIELDS.length)q.focus();
}

buildForm();
</script>
</body></html>"""
