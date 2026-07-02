"""Live web dashboard for the audit log — stdlib-only, zero extra deps.

``roscoe monitor --web`` serves this. It reloads the JSONL audit log on every
request (cheap, local read) so the page reflects new runs the moment they land.
Two routes: ``/`` returns the HTML shell, ``/api/metrics`` returns the aggregated
metrics plus recent runs as JSON. The page polls the JSON endpoint on a timer, so
the dashboard is "live" without websockets or a background process.

Built on ``http.server`` deliberately: the SDK must not pull Flask/FastAPI in just
to show a dashboard. The aggregation logic is reused from ``metrics.py`` unchanged.
"""

from __future__ import annotations

import dataclasses
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from roscoe.monitoring.metrics import aggregate, load_audit

#: How many of the most recent runs to send to the dashboard table.
_RECENT_LIMIT = 50


def _metrics_payload(audit_path: str | Path) -> dict[str, Any]:
    """Build the JSON payload: aggregated metrics + the most recent runs."""
    records = load_audit(audit_path)
    m = aggregate(records)
    metrics = dataclasses.asdict(m)
    # dataclasses.asdict drops @property values — add the derived headline numbers.
    metrics["total_cost_usd"] = m.total_cost_usd
    metrics["max_daily_cost_usd"] = m.max_daily_cost_usd
    metrics["max_p95_latency_ms"] = m.max_p95_latency_ms

    recent = list(reversed(records[-_RECENT_LIMIT:]))
    return {"metrics": metrics, "recent": recent, "record_count": len(records)}


class _Handler(BaseHTTPRequestHandler):
    audit_path: str = "logs/audit.jsonl"  # set on the class before serving

    def log_message(self, *args: Any) -> None:  # noqa: D401 — silence request spam
        pass

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        if self.path.startswith("/api/metrics"):
            body = json.dumps(_metrics_payload(self.audit_path), default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ("/", "/index.html"):
            body = _PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def serve(audit_path: str | Path, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start the blocking web dashboard server (Ctrl-C to stop)."""
    _Handler.audit_path = str(audit_path)
    httpd = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"roscoe monitor — live dashboard at {url}")
    print(f"  audit log: {audit_path}")
    print("  Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()


# --- the single-page dashboard (vanilla JS, polls /api/metrics) ---------------

_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>roscoe monitor</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#0f1117;color:#e2e8f0;padding:24px;line-height:1.5}
  h1{font-size:20px;color:#60a5fa;display:flex;align-items:center;gap:10px}
  h1 .dot{width:9px;height:9px;border-radius:50%;background:#22c55e;
    box-shadow:0 0 8px #22c55e;animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .sub{color:#64748b;font-size:12px;margin:4px 0 20px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:24px}
  .card{background:#1a1d27;border:1px solid #262b38;border-radius:12px;padding:16px 18px}
  .card .label{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#64748b}
  .card .value{font-size:26px;font-weight:700;margin-top:6px}
  .card .value.ok{color:#22c55e}.card .value.warn{color:#f59e0b}.card .value.bad{color:#ef4444}
  h2{font-size:14px;color:#a78bfa;margin:22px 0 10px;text-transform:uppercase;letter-spacing:.5px}
  table{width:100%;border-collapse:collapse;background:#1a1d27;border-radius:10px;overflow:hidden}
  th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#64748b;
    padding:10px 14px;background:#151822;border-bottom:1px solid #262b38}
  td{padding:9px 14px;font-size:13px;border-bottom:1px solid #20242f}
  tr:last-child td{border-bottom:none}
  .pill{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:600}
  .pill.success{background:#052e1a;color:#4ade80}
  .pill.error{background:#3b0d0d;color:#f87171}
  .pill.paused{background:#3a2e07;color:#fbbf24}
  .pill.rate_limited{background:#2e1a3b;color:#c084fc}
  .bars{display:flex;align-items:flex-end;gap:6px;height:120px;padding:10px 0}
  .bar{flex:1;background:linear-gradient(#60a5fa,#3b82f6);border-radius:4px 4px 0 0;
    min-height:2px;position:relative}
  .bar span{position:absolute;bottom:-18px;left:0;right:0;text-align:center;font-size:10px;color:#64748b}
  .bar b{position:absolute;top:-16px;left:0;right:0;text-align:center;font-size:10px;color:#93c5fd}
  .mono{font-family:ui-monospace,'Cascadia Code',monospace;font-size:12px;color:#94a3b8}
  .empty{color:#475569;font-size:13px;padding:14px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  @media(max-width:720px){.grid2{grid-template-columns:1fr}}
</style></head><body>
<h1><span class="dot"></span>roscoe monitor</h1>
<div class="sub" id="sub">loading…</div>

<div class="cards" id="cards"></div>

<h2>cost per day</h2>
<div class="bars" id="costbars"></div>

<div class="grid2">
  <div>
    <h2>latency by agent (ms)</h2>
    <table id="lat"><thead><tr><th>agent</th><th>p50</th><th>p95</th><th>p99</th><th>n</th></tr></thead><tbody></tbody></table>
  </div>
  <div>
    <h2>errors by type</h2>
    <table id="err"><thead><tr><th>type</th><th>count</th></tr></thead><tbody></tbody></table>
  </div>
</div>

<h2>recent runs</h2>
<table id="runs"><thead><tr><th>time</th><th>agent</th><th>user</th><th>status</th><th>tokens</th><th>cost</th></tr></thead><tbody></tbody></table>

<script>
const $=id=>document.getElementById(id);
function card(label,value,cls){return `<div class="card"><div class="label">${label}</div><div class="value ${cls||''}">${value}</div></div>`}
function fmtTime(iso){if(!iso)return '—';const d=new Date(iso);return isNaN(d)?iso:d.toLocaleTimeString()}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML}

async function tick(){
  let d;
  try{d=await (await fetch('/api/metrics')).json()}catch(e){$('sub').textContent='connection lost — retrying…';return}
  const m=d.metrics;
  const errCls=m.error_rate_pct>10?'bad':m.error_rate_pct>0?'warn':'ok';
  $('cards').innerHTML=
    card('total runs',m.total_runs)+
    card('error rate',m.error_rate_pct+'%',errCls)+
    card('total cost','$'+(m.total_cost_usd||0).toFixed(4))+
    card('max p95',Math.round(m.max_p95_latency_ms||0)+' ms')+
    card('records',d.record_count);
  $('sub').textContent='updated '+new Date().toLocaleTimeString()+' · auto-refresh 3s';

  // cost per day bars
  const days=Object.entries(m.cost_by_day||{}).sort((a,b)=>a[0]<b[0]?-1:1);
  const max=Math.max(...days.map(x=>x[1]),0.000001);
  $('costbars').innerHTML=days.length?days.map(([day,c])=>
    `<div class="bar" style="height:${Math.max(2,c/max*100)}%"><b>$${c.toFixed(3)}</b><span>${day.slice(5)}</span></div>`
  ).join(''):'<div class="empty">no cost data yet</div>';

  // latency table
  const lat=Object.entries(m.latency_ms_by_agent||{});
  $('lat').querySelector('tbody').innerHTML=lat.length?lat.map(([a,l])=>
    `<tr><td>${esc(a)}</td><td class="mono">${l.p50}</td><td class="mono">${l.p95}</td><td class="mono">${l.p99}</td><td class="mono">${l.count}</td></tr>`
  ).join(''):'<tr><td colspan="5" class="empty">no data</td></tr>';

  // errors table
  const errs=Object.entries(m.errors_by_type||{}).sort((a,b)=>b[1]-a[1]);
  $('err').querySelector('tbody').innerHTML=errs.length?errs.map(([t,c])=>
    `<tr><td>${esc(t)}</td><td class="mono">${c}</td></tr>`
  ).join(''):'<tr><td colspan="2" class="empty">no errors 🎉</td></tr>';

  // recent runs
  $('runs').querySelector('tbody').innerHTML=(d.recent||[]).length?d.recent.map(r=>
    `<tr><td class="mono">${fmtTime(r.start_time)}</td><td>${esc(r.agent_name)}</td>`+
    `<td>${esc(r.user_id||'—')}</td><td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td>`+
    `<td class="mono">${r.total_tokens||0}</td><td class="mono">$${(r.cost_usd||0).toFixed(4)}</td></tr>`
  ).join(''):'<tr><td colspan="6" class="empty">no runs yet</td></tr>';
}
tick();setInterval(tick,3000);
</script>
</body></html>"""
