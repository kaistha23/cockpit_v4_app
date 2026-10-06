#!/usr/bin/env python3
"""Standalone SQL console over the Cockpit V4 lake (Corporate or Retail).

Runs on its own port (default 8415), reads the parquet files directly and
shares nothing with the main app, so it works whether or not that is up.
Read-only: one SELECT-type statement per run, in-memory copy of the book.
Every submitted query is appended to saved_queries.sql beside this file.

    .venv/bin/python3 scripts/sql_console/sql_console.py [--port 8415]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LAKE = ROOT / "data" / "cockpit_v4_lake"
SAVED = HERE / "saved_queries.sql"
MAX_ROWS = 1000
TIMEOUT_S = 60

sys.path.insert(0, str(ROOT))
try:
    from backend.cockpit_v4.domains import DEFAULT_RELEASES
    RELEASES = dict(DEFAULT_RELEASES)
except Exception:  # noqa: BLE001
    RELEASES = {"corporate": "v4-saudi-corporate-20q-v4",
                "retail": "v4-saudi-retail-20m-v5"}

_conns: dict[str, duckdb.DuckDBPyConnection] = {}
_lock = threading.Lock()
_save_lock = threading.Lock()


def _domain_key(name: str) -> str:
    return "corporate" if name.lower().startswith("corp") else "retail"


def connection(domain: str) -> duckdb.DuckDBPyConnection:
    key = _domain_key(domain)
    with _lock:
        if key in _conns:
            return _conns[key]
        release = next((r for d, r in RELEASES.items()
                        if _domain_key(d) == key), None)
        folder = LAKE / str(release)
        files = sorted(folder.glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"No parquet files in {folder}")
        con = duckdb.connect(":memory:")
        con.execute("SET threads TO 2")
        con.execute("SET memory_limit = '1536MB'")
        for f in files:
            con.execute(f'CREATE TABLE "{f.stem}" AS '
                        f"SELECT * FROM read_parquet('{f}')")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        _conns[key] = con
        return con


def save(domain: str, sql: str, status: str) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    with _save_lock, SAVED.open("a", encoding="utf-8") as fh:
        fh.write(f"-- [{stamp}] domain={domain} status={status}\n"
                 f"{sql.strip().rstrip(';')};\n\n")


def run(domain: str, sql: str) -> dict:
    sql = sql.strip()
    if not sql:
        return {"error": "Empty query."}
    try:
        con = connection(domain)
        stmts = con.extract_statements(sql)
        if len(stmts) != 1:
            raise ValueError("Submit exactly one statement.")
        if stmts[0].type not in (duckdb.StatementType.SELECT,
                                 duckdb.StatementType.EXPLAIN):
            raise ValueError("Read-only console: only SELECT / WITH / "
                             "DESCRIBE-style queries are allowed.")
        cur = con.cursor()
        timer = threading.Timer(TIMEOUT_S, cur.interrupt)
        timer.start()
        started = time.monotonic()
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
        finally:
            timer.cancel()
        truncated = len(rows) > MAX_ROWS
        rows = [[None if v is None else
                 v if isinstance(v, (int, float, bool, str)) else str(v)
                 for v in r] for r in rows[:MAX_ROWS]]
        return {"columns": cols, "rows": rows, "truncated": truncated,
                "seconds": round(time.monotonic() - started, 3)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def schema(domain: str) -> dict:
    try:
        con = connection(domain)
        out = {}
        for (t,) in con.execute("SHOW TABLES").fetchall():
            out[t] = [[r[0], r[1]] for r in
                      con.execute(f'DESCRIBE "{t}"').fetchall()]
        return out
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


PAGE = """<!doctype html><meta charset=utf-8><title>V4 SQL Console</title>
<style>
body{font:14px system-ui;margin:0;background:#f6f7f9;color:#1b1f24}
header{padding:10px 16px;background:#1b2a41;color:#fff;display:flex;gap:12px;align-items:center}
main{display:grid;grid-template-columns:260px 1fr;gap:12px;padding:12px}
aside,section{background:#fff;border:1px solid #d8dce1;border-radius:6px;padding:10px}
aside{max-height:85vh;overflow:auto;font-size:12px}
textarea{width:100%;height:180px;font:13px ui-monospace,Menlo,monospace;box-sizing:border-box}
button{padding:7px 16px;border:1px solid #1b2a41;border-radius:4px;background:#fff;cursor:pointer;font-weight:600}
button.on{background:#1b2a41;color:#fff}button.run{background:#0a7d4f;color:#fff;border-color:#0a7d4f}
#out{overflow:auto;max-height:60vh;margin-top:10px}
table{border-collapse:collapse;font-size:12px}th,td{border:1px solid #d8dce1;padding:3px 8px;white-space:nowrap}
th{background:#eef0f3;position:sticky;top:0}.err{color:#b00020;white-space:pre-wrap}
.meta{color:#5b6570;margin-top:6px}details{margin-bottom:4px}summary{cursor:pointer;font-weight:600}
</style>
<header><b>Cockpit V4 · SQL console</b>
<button id=bc class=on onclick="pick('corporate')">Corp</button>
<button id=br onclick="pick('retail')">Retail</button>
<span id=rel style="opacity:.7"></span></header>
<main><aside id=schema>Loading…</aside>
<section><textarea id=sql placeholder="SELECT * FROM ... LIMIT 10"></textarea>
<div style="margin-top:8px"><button class=run onclick=go()>Run (⌘/Ctrl+Enter)</button>
<span class=meta>Each run is appended to saved_queries.sql</span></div>
<div class=meta id=meta></div><div id=out></div></section></main>
<script>
let domain='corporate';
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function pick(d){domain=d;$('bc').className=d=='corporate'?'on':'';$('br').className=d=='retail'?'on':'';
 $('schema').textContent='Loading (first load reads the book)…';
 const s=await (await fetch('/schema?domain='+d)).json();
 $('schema').innerHTML=s.error?'<span class=err>'+esc(s.error)+'</span>':
  Object.entries(s).map(([t,c])=>'<details><summary>'+esc(t)+'</summary>'+c.map(x=>esc(x[0])+' <i>'+esc(x[1])+'</i>').join('<br>')+'</details>').join('');}
async function go(){$('meta').textContent='Running…';$('out').innerHTML='';
 const r=await (await fetch('/run',{method:'POST',body:JSON.stringify({domain,sql:$('sql').value})})).json();
 if(r.error){$('meta').textContent='';$('out').innerHTML='<div class=err>'+esc(r.error)+'</div>';return}
 $('meta').textContent=r.rows.length+' rows'+(r.truncated?' (truncated at 1000)':'')+' · '+r.seconds+'s · '+domain;
 $('out').innerHTML='<table><tr>'+r.columns.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr>'+
  r.rows.map(x=>'<tr>'+x.map(v=>'<td>'+(v===null?'<i>null</i>':esc(v))+'</td>').join('')+'</tr>').join('')+'</table>';}
$('sql').addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key=='Enter')go()});
pick('corporate');
</script>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict) -> None:
        self._send(json.dumps(obj, default=str).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/schema"):
            d = "retail" if "domain=retail" in self.path else "corporate"
            return self._json(schema(d))
        self._send(PAGE.encode(), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            return self._send(b"{}", "application/json", 404)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        domain = "retail" if body.get("domain") == "retail" else "corporate"
        result = run(domain, str(body.get("sql", "")))
        if body.get("sql", "").strip():
            save(domain, body["sql"], "error" if "error" in result else "ok")
        self._json(result)

    def log_message(self, *a) -> None:  # quiet
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8415)
    port = ap.parse_args().port
    print(f"V4 SQL console: http://127.0.0.1:{port}  (saves to {SAVED})")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
