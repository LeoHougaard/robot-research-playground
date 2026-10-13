"""Loopback-only, read-only status page. No robot controls or arbitrary files."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import sqlite3


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Robot playground</title>
<style>body{font:16px system-ui;margin:40px auto;max-width:920px;padding:0 20px;background:#111820;color:#e6edf3}h1{font-size:28px}small{color:#a3b1bd}section{margin:22px 0;padding:20px;background:#1b2531;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}li{margin:12px 0}a{color:#9bc7fa}.metrics{display:flex;gap:30px;flex-wrap:wrap}.metric strong{display:block;font-size:32px;margin-bottom:4px}.mode{color:#d6ba83}</style>
<h1>Robot playground</h1><small>Read-only status. This page has no arming or movement controls.</small><section id="status">Loading...</section><section><h2>Recent findings</h2><ul id="findings"></ul></section>
<script>async function refresh(){try{let r=await fetch('/api/status');let s=await r.json();let grid=document.createElement('div');grid.className='metrics';for(let [label,key] of [['Agent generations','generations'],['Recorded trials','experiments'],['Prediction checks','evaluations']]){let box=document.createElement('div');box.className='metric';let n=document.createElement('strong');n.textContent=s[key]||0;box.append(n,document.createTextNode(label));grid.append(box)}let mode=document.createElement('p');mode.className='mode';let latest=(s.recent_runs||[])[0];mode.textContent=latest?(latest.mode==='simulation'?'Simulation evidence':'Physical evidence')+' · Latest run '+latest.status:'Waiting for first run';let note=document.createElement('small');note.textContent=latest?.mode==='simulation'?'These results describe the simulator. Physical findings require real trials.':'Physical operation requires accepted hardware and operator arming.';document.querySelector('#status').replaceChildren(grid,mode,note);let items=(s.recent_findings||[]).map(f=>{let x=document.createElement('li');x.textContent=f.claim+' '+(f.status==='hypothesis'?'Hypothesis awaiting reproduction.':'Checked by the prediction evaluator.')+' '+f.conditions;return x});if(!items.length){let x=document.createElement('li');x.textContent='No findings recorded yet.';items.push(x)}document.querySelector('#findings').replaceChildren(...items)}catch(e){document.querySelector('#status').textContent='Waiting for evidence archive.'}}refresh();setInterval(refresh,5000)</script></html>"""


def read_status(state_dir):
    path = Path(state_dir).resolve() / "archive" / "evidence.sqlite3"
    if not path.exists():
        return {"status": "waiting", "runs": 0, "experiments": 0, "generations": 0, "evaluations": 0, "recent_findings": []}
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        result = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("runs", "generations", "experiments", "findings", "evaluations")}
        result["recent_findings"] = [dict(x) for x in db.execute("SELECT id,claim,status,conditions FROM findings ORDER BY created DESC LIMIT 10")]
        result["recent_runs"] = [dict(x) for x in db.execute("SELECT id,mode,status,started,ended FROM runs ORDER BY started DESC LIMIT 5")]
        row = db.execute("SELECT AVG(error),MIN(error),MAX(error) FROM experiments WHERE status='completed'").fetchone()
        result["prediction_error"] = {"mean": row[0], "min": row[1], "max": row[2]}
        return result
    finally:
        db.close()


def serve(state_dir, host="127.0.0.1", port=8765):
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("Status viewer only binds loopback")
    except ValueError as exc:
        raise ValueError("Use a loopback IP address for the status viewer") from exc
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body, kind, status = PAGE.encode(), "text/html; charset=utf-8", 200
            elif self.path == "/api/status":
                try:
                    body = json.dumps(read_status(state_dir), allow_nan=False).encode()
                    status = 200
                except (OSError, sqlite3.Error):
                    body, status = b'{"error":"archive unavailable"}', 503
                kind = "application/json"
            else:
                body, kind, status = b"Not found", "text/plain", 404
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, format, *args):
            pass
    with ThreadingHTTPServer((host, port), Handler) as server:
        print(f"Read-only viewer: http://{host}:{port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
