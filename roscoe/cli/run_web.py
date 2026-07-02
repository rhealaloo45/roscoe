"""Built-in web chat for ``roscoe run`` — talk to your agent in the browser.

A stdlib ``http.server`` (no Flask) serves a chat page and a streaming endpoint.
Tokens are pushed to the browser as newline-delimited JSON as the agent produces
them, so replies appear live. Human-in-the-loop pauses surface as approve/reject
buttons that resume the run.

Single-threaded on purpose: the agent's async primitives (rate-limiter lock, etc.)
live on one per-thread event loop, so serving every request from one thread keeps
them consistent — fine for a local, single-user chat.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def serve_chat(agent: Any, *, host: str = "127.0.0.1", port: int = 5005,
               user_id: str = "web-user", session_id: str = "web-session",
               open_browser: bool = True) -> None:
    """Serve the browser chat UI for ``agent`` (blocking; Ctrl-C to stop)."""
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

        def _begin_stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

        def _emit(self, obj: dict[str, Any]) -> None:
            try:
                self.wfile.write((json.dumps(obj, default=str) + "\n").encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionError):
                pass

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
                self._chat(self._read_json().get("message", ""))
            elif self.path == "/api/approve":
                self._approve(self._read_json().get("decision", "reject"))
            else:
                self.send_response(404)
                self.end_headers()

        def _chat(self, message: str) -> None:
            self._begin_stream()
            for event in agent.stream(message, user_id=user_id, session_id=session_id):
                etype = event["type"]
                if etype == "token":
                    self._emit({"type": "token", "text": event["text"]})
                elif etype == "tool":
                    self._emit({"type": "tool", "name": event["name"]})
                elif etype in ("final", "paused", "error"):
                    self._emit_result(event["result"])

        def _approve(self, decision: str) -> None:
            self._begin_stream()
            run_id = state.get("pending_run_id")
            if not run_id:
                self._emit({"type": "error", "error": "No pending action."})
                return
            state["pending_run_id"] = None
            result = agent.resume(run_id, decision)
            self._emit_result(result)

        def _emit_result(self, result: Any) -> None:
            cost = f"${result.cost_usd:.4f}" if result.cost_usd else "free"
            if result.status == "paused":
                state["pending_run_id"] = result.run_id
                action = result.pending_action or {}
                self._emit({"type": "paused", "run_id": result.run_id,
                            "tool_calls": action.get("tool_calls", [])})
            elif result.status == "error":
                self._emit({"type": "error", "error": str(result.error)})
            else:
                self._emit({"type": "final", "output": result.output,
                            "tokens": result.total_tokens, "cost": cost,
                            "tools": result.tool_calls})

    httpd = HTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"roscoe run — web chat at {url}")
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


_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>roscoe run</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#0f1117;color:#e2e8f0;height:100vh;display:flex;justify-content:center;align-items:center}
  .chat{width:760px;max-width:96vw;height:94vh;background:#161923;border:1px solid #262b38;
    border-radius:16px;display:flex;flex-direction:column;overflow:hidden}
  .head{background:linear-gradient(135deg,#1e3a5f,#0f172a);padding:16px 22px}
  .head h1{font-size:16px;font-weight:600}
  .head p{font-size:11px;opacity:.6;margin-top:2px}
  .msgs{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}
  .msgs::-webkit-scrollbar{width:6px}.msgs::-webkit-scrollbar-thumb{background:#334155;border-radius:3px}
  .m{max-width:82%;padding:11px 15px;border-radius:14px;font-size:14px;line-height:1.55;white-space:pre-wrap;word-wrap:break-word}
  .m.user{align-self:flex-end;background:#2563eb;color:#fff;border-bottom-right-radius:4px}
  .m.bot{align-self:flex-start;background:#1e2430;color:#e2e8f0;border-bottom-left-radius:4px}
  .m.tool{align-self:flex-start;background:#0b2b2b;color:#5eead4;font-size:12px;font-family:ui-monospace,monospace;padding:6px 12px}
  .m.err{align-self:center;background:#3b0d0d;color:#f87171;font-size:13px}
  .approve{align-self:flex-start;background:#3a2e07;border:1px solid #a16207;border-radius:12px;padding:12px 15px;font-size:13px}
  .approve b{color:#fbbf24}.approve .tc{font-family:ui-monospace,monospace;color:#fde68a;margin:6px 0}
  .approve button{border:none;border-radius:8px;padding:6px 16px;font-weight:600;cursor:pointer;margin-right:8px;margin-top:6px}
  .ok{background:#16a34a;color:#fff}.no{background:#dc2626;color:#fff}
  .bar{padding:8px 20px;font-size:11px;color:#64748b;border-top:1px solid #262b38}
  .in{display:flex;gap:10px;padding:14px 20px;border-top:1px solid #262b38}
  .in input{flex:1;padding:11px 15px;background:#0f1117;border:1px solid #334155;border-radius:12px;color:#e2e8f0;font-size:14px;outline:none}
  .in input:focus{border-color:#2563eb}
  .in button{padding:11px 24px;background:#2563eb;color:#fff;border:none;border-radius:12px;font-weight:600;cursor:pointer}
  .in button:disabled{background:#334155;cursor:not-allowed}
</style></head><body>
<div class="chat">
  <div class="head"><h1>roscoe run</h1><p id="meta">loading…</p></div>
  <div class="msgs" id="msgs"><div class="m bot">Hi — I'm your agent. Ask me anything.</div></div>
  <div class="bar" id="bar">ready</div>
  <div class="in">
    <input id="q" placeholder="Type a message…" autofocus onkeydown="if(event.key==='Enter')send()">
    <button id="btn" onclick="send()">Send</button>
  </div>
</div>
<script>
const msgs=document.getElementById('msgs'),q=document.getElementById('q'),btn=document.getElementById('btn'),bar=document.getElementById('bar');
let awaiting=false;
function add(cls,txt){const d=document.createElement('div');d.className='m '+cls;d.textContent=txt;msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML}
async function readStream(resp,onEvent){
  const reader=resp.body.getReader(),dec=new TextDecoder();let buf='';
  while(true){const {value,done}=await reader.read();if(done)break;
    buf+=dec.decode(value,{stream:true});let i;
    while((i=buf.indexOf('\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+1);
      if(line.trim())onEvent(JSON.parse(line));}}
}
function lock(on){btn.disabled=on;q.disabled=on}
async function send(){
  if(awaiting){return}
  const t=q.value.trim();if(!t)return;q.value='';add('user',t);lock(true);bar.textContent='thinking…';
  let bot=null;
  try{
    const resp=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:t})});
    await readStream(resp,ev=>handle(ev,()=>{if(!bot)bot=add('bot','');return bot}));
  }catch(e){add('err','connection error: '+e.message)}
  lock(false);q.focus();
}
function handle(ev,getBot){
  if(ev.type==='token'){getBot().textContent+=ev.text;msgs.scrollTop=msgs.scrollHeight}
  else if(ev.type==='tool'){add('tool','⟳ '+ev.name)}
  else if(ev.type==='final'){bar.textContent='tokens='+ev.tokens+' · cost='+ev.cost+(ev.tools&&ev.tools.length?' · tools: '+ev.tools.join(' → '):'')}
  else if(ev.type==='error'){add('err',ev.error);bar.textContent='error'}
  else if(ev.type==='paused'){showApprove(ev)}
}
function showApprove(ev){
  awaiting=true;bar.textContent='awaiting approval';
  const box=document.createElement('div');box.className='approve';
  let html='<b>⏸ approval required</b>';
  (ev.tool_calls||[]).forEach(tc=>{html+='<div class="tc">'+esc(tc.name)+'('+esc(JSON.stringify(tc.args||{}))+')</div>'});
  html+='<button class="ok">Approve</button><button class="no">Reject</button>';
  box.innerHTML=html;msgs.appendChild(box);msgs.scrollTop=msgs.scrollHeight;
  box.querySelector('.ok').onclick=()=>decide('approve',box);
  box.querySelector('.no').onclick=()=>decide('reject',box);
}
async function decide(d,box){
  awaiting=false;box.querySelectorAll('button').forEach(b=>b.disabled=true);lock(true);bar.textContent='resuming…';
  let bot=null;
  const resp=await fetch('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:d})});
  await readStream(resp,ev=>handle(ev,()=>{if(!bot)bot=add('bot','');return bot}));
  lock(false);q.focus();
}
fetch('/api/meta').catch(()=>{});
document.getElementById('meta').textContent='streaming chat';
</script>
</body></html>"""
