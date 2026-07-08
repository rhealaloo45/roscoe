"""Built-in web UI for ``roscoe run`` — a login/landing page with a floating
support-style chat widget in the bottom-right corner.

A stdlib ``http.server`` (no Flask) serves the page and a plain JSON chat
endpoint. No streaming: each message is a single request/response, with a
"typing" indicator while the agent works. Human-in-the-loop pauses surface
as approve/reject buttons inside the chat panel.

Single-threaded on purpose: the agent's async primitives (rate-limiter lock,
etc.) live on one per-thread event loop, so serving every request from one
thread keeps them consistent — fine for a local, single-user demo.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def serve_chat(agent: Any, *, host: str = "127.0.0.1", port: int = 5005,
               user_id: str = "web-user", session_id: str = "web-session",
               open_browser: bool = True) -> None:
    """Serve the browser UI for ``agent`` (blocking; Ctrl-C to stop)."""
    state: dict[str, Any] = {"pending_run_id": None}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence request spam
            pass

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def _send_json(self, obj: dict[str, Any]) -> None:
            body = json.dumps(obj, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                body = _PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/api/chat":
                self._chat(self._read_json())
            elif self.path == "/api/approve":
                self._approve(self._read_json().get("decision", "reject"))
            else:
                self.send_response(404)
                self.end_headers()

        def _chat(self, body: dict[str, Any]) -> None:
            message = body.get("message", "")
            uid = body.get("user_id") or user_id
            sid = body.get("session_id") or session_id
            result = agent.run(message, user_id=uid, session_id=sid)
            self._send_json(_result_payload(result, state))

        def _approve(self, decision: str) -> None:
            run_id = state.get("pending_run_id")
            if not run_id:
                self._send_json({"type": "error", "error": "No pending action."})
                return
            state["pending_run_id"] = None
            result = agent.resume(run_id, decision)
            self._send_json(_result_payload(result, state))

    httpd = HTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"roscoe run — web UI at {url}")
    print(f"  agent={agent.agent_name}  provider={agent.provider}  model={agent.model}")
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


def _result_payload(result: Any, state: dict[str, Any]) -> dict[str, Any]:
    cost = f"${result.cost_usd:.4f}" if result.cost_usd else "free"
    if result.status == "paused":
        state["pending_run_id"] = result.run_id
        action = result.pending_action or {}
        return {"type": "paused", "run_id": result.run_id,
                "tool_calls": action.get("tool_calls", [])}
    if result.status == "error":
        return {"type": "error", "error": str(result.error)}
    return {"type": "final", "output": result.output,
            "tokens": result.total_tokens, "cost": cost, "tools": result.tool_calls}


_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>roscoe run</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#0b0d12;color:#e2e8f0;height:100vh;overflow:hidden}

  /* landing page behind the widget */
  .landing{height:100vh;display:flex;align-items:center;justify-content:center;
    background:radial-gradient(circle at 30% 20%,#182338,#0b0d12 60%)}
  .card{width:380px;max-width:90vw;background:#161923;border:1px solid #262b38;border-radius:16px;padding:32px}
  .card h1{font-size:20px;font-weight:700;margin-bottom:6px}
  .card p{font-size:13px;color:#8b93a3;margin-bottom:24px}
  .field{margin-bottom:14px}
  .field label{display:block;font-size:12px;color:#8b93a3;margin-bottom:6px}
  .field input{width:100%;padding:10px 13px;background:#0f1117;border:1px solid #334155;
    border-radius:10px;color:#e2e8f0;font-size:14px;outline:none}
  .field input:focus{border-color:#2563eb}
  .card button{width:100%;margin-top:8px;padding:11px;background:#2563eb;color:#fff;border:none;
    border-radius:10px;font-weight:600;font-size:14px;cursor:pointer}
  .card button:hover{background:#1d4ed8}
  .signedin{display:none;text-align:center;font-size:13px;color:#8b93a3}
  .signedin b{color:#e2e8f0}

  /* floating launcher */
  .launcher{position:fixed;right:24px;bottom:24px;width:58px;height:58px;border-radius:50%;
    background:#2563eb;display:flex;align-items:center;justify-content:center;cursor:pointer;
    box-shadow:0 6px 20px rgba(37,99,235,.45);z-index:20;transition:transform .15s}
  .launcher:hover{transform:scale(1.06)}
  .launcher svg{width:26px;height:26px;fill:#fff}
  .dot{position:absolute;top:2px;right:2px;width:12px;height:12px;background:#22c55e;border:2px solid #0b0d12;border-radius:50%}

  /* chat panel */
  .panel{position:fixed;right:24px;bottom:96px;width:360px;height:520px;max-height:76vh;
    background:#161923;border:1px solid #262b38;border-radius:16px;display:flex;flex-direction:column;
    overflow:hidden;box-shadow:0 20px 50px rgba(0,0,0,.5);z-index:21;
    opacity:0;transform:translateY(16px) scale(.98);pointer-events:none;transition:opacity .16s,transform .16s}
  .panel.open{opacity:1;transform:translateY(0) scale(1);pointer-events:auto}
  .phead{background:linear-gradient(135deg,#1e3a5f,#0f172a);padding:14px 16px;display:flex;align-items:center;justify-content:space-between}
  .phead .t{font-size:14px;font-weight:600}
  .phead .s{font-size:10.5px;opacity:.65;margin-top:1px}
  .pclose{cursor:pointer;opacity:.7;font-size:18px;line-height:1;padding:2px 4px}
  .pclose:hover{opacity:1}
  .msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
  .msgs::-webkit-scrollbar{width:5px}.msgs::-webkit-scrollbar-thumb{background:#334155;border-radius:3px}
  .m{max-width:84%;padding:9px 13px;border-radius:13px;font-size:13.5px;line-height:1.5;white-space:pre-wrap;word-wrap:break-word}
  .m.user{align-self:flex-end;background:#2563eb;color:#fff;border-bottom-right-radius:4px}
  .m.bot{align-self:flex-start;background:#1e2430;color:#e2e8f0;border-bottom-left-radius:4px}
  .m.err{align-self:center;background:#3b0d0d;color:#f87171;font-size:12.5px}
  .typing{align-self:flex-start;background:#1e2430;border-radius:13px;border-bottom-left-radius:4px;
    padding:11px 15px;display:flex;gap:4px}
  .typing span{width:6px;height:6px;border-radius:50%;background:#64748b;animation:bounce 1.2s infinite}
  .typing span:nth-child(2){animation-delay:.15s}.typing span:nth-child(3){animation-delay:.3s}
  @keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:.5}30%{transform:translateY(-4px);opacity:1}}
  .approve{align-self:flex-start;background:#3a2e07;border:1px solid #a16207;border-radius:12px;padding:11px 14px;font-size:12.5px}
  .approve b{color:#fbbf24}.approve .tc{font-family:ui-monospace,monospace;color:#fde68a;margin:5px 0;word-break:break-all}
  .approve button{border:none;border-radius:8px;padding:6px 14px;font-weight:600;cursor:pointer;margin-right:8px;margin-top:6px;font-size:12px}
  .ok{background:#16a34a;color:#fff}.no{background:#dc2626;color:#fff}
  .bar{padding:6px 14px;font-size:10.5px;color:#64748b;border-top:1px solid #262b38}
  .in{display:flex;gap:8px;padding:10px 12px;border-top:1px solid #262b38}
  .in input{flex:1;padding:9px 13px;background:#0f1117;border:1px solid #334155;border-radius:10px;color:#e2e8f0;font-size:13.5px;outline:none}
  .in input:focus{border-color:#2563eb}
  .in button{padding:9px 16px;background:#2563eb;color:#fff;border:none;border-radius:10px;font-weight:600;cursor:pointer;font-size:13px}
  .in button:disabled{background:#334155;cursor:not-allowed}
</style></head><body>

<div class="landing">
  <div class="card">
    <h1>Welcome</h1>
    <p>Sign in to get personalized help, or just open the chat to ask a question.</p>
    <div id="loginForm">
      <div class="field"><label>Your name</label><input id="lname" placeholder="e.g. Rhea Laloo"></div>
      <div class="field"><label>Employee ID (optional)</label><input id="lid" placeholder="e.g. E-1042"></div>
      <button onclick="signIn()">Continue</button>
    </div>
    <div class="signedin" id="signedIn">Signed in as <b id="signedName"></b>. Use the chat button to start.</div>
  </div>
</div>

<div class="launcher" id="launcher" onclick="togglePanel()">
  <svg viewBox="0 0 24 24"><path d="M4 4h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H8l-4.4 3.3A1 1 0 0 1 2 19.5V5a1 1 0 0 1 1-1h1z"/></svg>
  <div class="dot"></div>
</div>

<div class="panel" id="panel">
  <div class="phead">
    <div><div class="t">roscoe run</div><div class="s" id="meta">agent chat</div></div>
    <div class="pclose" onclick="togglePanel()">&times;</div>
  </div>
  <div class="msgs" id="msgs"><div class="m bot">Hi — I'm your agent. Ask me anything.</div></div>
  <div class="bar" id="bar">ready</div>
  <div class="in">
    <input id="q" placeholder="Type a message…" onkeydown="if(event.key==='Enter')send()">
    <button id="btn" onclick="send()">Send</button>
  </div>
</div>

<script>
const msgs=document.getElementById('msgs'),q=document.getElementById('q'),btn=document.getElementById('btn'),bar=document.getElementById('bar');
const panel=document.getElementById('panel');
let awaiting=false,userId='web-user',opened=false;

function signIn(){
  const name=document.getElementById('lname').value.trim();
  const id=document.getElementById('lid').value.trim();
  if(name)userId=id?(name+' ('+id+')'):name;
  document.getElementById('loginForm').style.display='none';
  const s=document.getElementById('signedIn');s.style.display='block';
  document.getElementById('signedName').textContent=name||'guest';
  if(!opened)togglePanel();
}
function togglePanel(){opened=!opened;panel.classList.toggle('open',opened);if(opened)q.focus()}

function add(cls,txt){const d=document.createElement('div');d.className='m '+cls;d.textContent=txt;msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML}
function lock(on){btn.disabled=on;q.disabled=on}
function showTyping(){const d=document.createElement('div');d.className='typing';d.innerHTML='<span></span><span></span><span></span>';msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d}

async function send(){
  if(awaiting)return;
  const t=q.value.trim();if(!t)return;q.value='';add('user',t);lock(true);bar.textContent='working…';
  const typing=showTyping();
  try{
    const resp=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:t,user_id:userId})});
    const ev=await resp.json();
    typing.remove();
    handle(ev);
  }catch(e){typing.remove();add('err','connection error: '+e.message)}
  lock(false);q.focus();
}
function handle(ev){
  if(ev.type==='final'){add('bot',ev.output);bar.textContent='tokens='+ev.tokens+' · cost='+ev.cost+(ev.tools&&ev.tools.length?' · tools: '+ev.tools.join(' → '):'')}
  else if(ev.type==='error'){add('err',ev.error);bar.textContent='error'}
  else if(ev.type==='paused'){showApprove(ev)}
}
function showApprove(ev){
  awaiting=true;bar.textContent='awaiting approval';
  const box=document.createElement('div');box.className='approve';
  let html='<b>&#9208; approval required</b>';
  (ev.tool_calls||[]).forEach(tc=>{html+='<div class="tc">'+esc(tc.name)+'('+esc(JSON.stringify(tc.args||{}))+')</div>'});
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
  typing.remove();
  handle(ev);
  lock(false);q.focus();
}
</script>
</body></html>"""
