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
    # Some models occasionally stop with an empty final message (no tool_calls,
    # no content) — never render a literally blank bubble for that.
    output = result.output or "(no response from the model — try rephrasing)"
    return {"type": "final", "output": output,
            "tokens": result.total_tokens, "cost": cost, "tools": result.tool_calls}


_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>roscoe run</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#f7f8fa;color:#1e293b;height:100vh;overflow:hidden}

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
  .field input:focus{border-color:#2563eb}
  .sidebar button{width:100%;margin-top:8px;padding:11px;background:#2563eb;color:#fff;border:none;
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
  .m.user{align-self:flex-end;background:#2563eb;color:#fff;border-bottom-right-radius:4px;white-space:pre-wrap}
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
  .m.bot a{color:#2563eb}
  .typing{align-self:flex-start;background:#fff;border:1px solid #e2e8f0;border-radius:14px;border-bottom-left-radius:4px;
    padding:12px 16px;display:flex;gap:4px}
  .typing span{width:6px;height:6px;border-radius:50%;background:#94a3b8;animation:bounce 1.2s infinite}
  .typing span:nth-child(2){animation-delay:.15s}.typing span:nth-child(3){animation-delay:.3s}
  @keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:.5}30%{transform:translateY(-4px);opacity:1}}
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
  .in input:focus{border-color:#2563eb}
  .in button{padding:11px 20px;background:#2563eb;color:#fff;border:none;border-radius:10px;font-weight:600;cursor:pointer;font-size:14px}
  .in button:disabled{background:#cbd5e1;cursor:not-allowed;color:#fff}

  @media (max-width:760px){
    .layout{grid-template-columns:1fr}
    .sidebar{grid-column:1;border-right:none;border-bottom:1px solid #dbe1e8}
    .chat{grid-column:1}
  }
</style></head><body>

<div class="layout">
  <div class="sidebar">
    <h1>Welcome</h1>
    <p>Sign in to get personalized help, or just start typing on the right to ask a question.</p>
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
      <div><div class="t">roscoe run</div><div class="s">agent chat</div></div>
    </div>
    <div class="msgs" id="msgs"><div class="m bot">Hi — I'm your agent. Ask me anything.</div></div>
    <div class="bar" id="bar">ready</div>
    <div class="in">
      <input id="q" placeholder="Type a message…" onkeydown="if(event.key==='Enter')send()">
      <button id="btn" onclick="send()">Send</button>
    </div>
  </div>
</div>

<script>
const msgs=document.getElementById('msgs'),q=document.getElementById('q'),btn=document.getElementById('btn'),bar=document.getElementById('bar');
let awaiting=false,userId='web-user';

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
  typing.remove();
  handle(ev);
  lock(false);q.focus();
}
</script>
</body></html>"""
