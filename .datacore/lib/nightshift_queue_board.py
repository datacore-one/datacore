#!/usr/bin/env python3
"""Decision board for :AI: tasks nightshift cannot run (missing SURFACE/DONE_WHEN).

Written 2026-09-23. Nightshift did no work for 4.5 days: 25 of its 45 open :AI:
tasks lacked SURFACE and/or DONE_WHEN (and ROADMAP, where the space has a
roadmap), so the executor could select nothing, and actor-presence said so.
The fix is the owner's decision per task, not a beacon: make it runnable,
make it a human task, bench it, or drop it. This renders those decisions as a
decision-board page (skill: decision-board) and applies the saved choices.

    nightshift_queue_board.py build   --rows rows.json [--out PATH]
    nightshift_queue_board.py apply   --rows rows.json --decisions <slug>.decisions.json [--dry-run]

rows.json: [{"id", "section", "title", "context", "orgId", "file",
             "run": {"SURFACE": ..., "DONE_WHEN": ..., "ROADMAP": ...} | null,
             "suggested": "run|human|bench|drop|done"}]
Apply goes through org_workspace_adapter (the ledger path), one task at a time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

LIB = Path(__file__).resolve().parent
STATE = Path(os.environ.get("DATACORE_STATE", Path.home() / ".datacore" / "state")) / "decision-boards"
SLUG = "nightshift-queue-2026-09-23"
WAKE = (date.today() + timedelta(days=30)).isoformat()

OPTIONS = {
    "run":   ("Make it runnable", "Sets SURFACE and DONE_WHEN (and ROADMAP) as shown; nightshift can pick it up tonight."),
    "human": ("A person's task", "Removes the :AI: tag. It stays TODO, on your list, not the agent's."),
    "bench": ("Bench it", f"DEFERRED, waking {WAKE}. Off both lists until then."),
    "drop":  ("Drop it", "CANCELLED, with the reason recorded on the task."),
    "done":  ("Already done", "Marked DONE."),
}
SECTIONS = [("dupes", "Duplicates and expired"), ("human", "Tasks a person has to do"),
            ("agent", "Agent work that needs a definition")]


def build(rows: list[dict]) -> str:
    items = []
    for r in rows:
        keys = ["run", "human", "bench", "drop"] if r.get("run") else ["human", "bench", "drop"]
        if "done" not in keys:
            keys.append("done")
        if r["suggested"] not in keys:
            raise SystemExit(f"{r['id']}: suggestion {r['suggested']} is not an option")
        preview = ""
        if r.get("run"):
            preview = "\n".join(f"{k}: {v}" for k, v in r["run"].items())
        items.append({"id": r["id"], "section": r["section"], "title": r["title"], "context": r["context"],
                      "meta": [r["space"], r["orgId"] or "no id"], "preview": preview, "suggested": r["suggested"],
                      "options": [{"value": k, "label": OPTIONS[k][0], "consequence": OPTIONS[k][1]} for k in keys]})
    build_id = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:16]
    data = {"board": SLUG, "build": build_id, "sections": SECTIONS, "items": items}
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c") \
        .replace(chr(8232), "\\u2028").replace(chr(8233), "\\u2029")
    return PAGE.replace("__DATA__", blob).replace("__N__", str(len(items)))


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">
<title>Nightshift queue</title>
<style>
:root{--bg:#fafaf9;--surface:#ffffff;--text:#1a1a1a;--muted:#6b6b70;--rule:#e6e6e3;--cyan:#22d3ee;--amber:#f0a050;--violet:#a78bfa;--green:#34d399;--status:#e8695f;--primary:#7c3aed;
--sans:"Outfit",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme: dark){:root{--bg:#0e0f14;--surface:#15171d;--text:#f0f0f2;--muted:#9a9aa3;--rule:#262833}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:300 16px/1.55 var(--sans);padding:0 16px}
main{max-width:860px;margin:0 auto;padding:32px 0 96px}
.eyebrow{font:400 12px var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
h1{font-weight:200;font-size:34px;line-height:1.15;margin:6px 0 10px;text-wrap:balance}
.lede{max-width:62ch;margin:0 0 6px}.asof{font:12px var(--mono);color:var(--muted)}
.path{display:flex;gap:18px;flex-wrap:wrap;margin:18px 0 8px;font:400 12px var(--sans);letter-spacing:.08em;text-transform:uppercase}
.path span{padding-bottom:4px;border-bottom:2px solid var(--rule);color:var(--muted)}
.path span.on{color:var(--text);border-color:var(--violet)}
.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:2;background:var(--bg);border-bottom:1px solid var(--rule);display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:12px 0;margin:12px 0 8px}
.bar .count{font:13px var(--mono)}.bar a{color:var(--primary);cursor:pointer;font-size:14px}
.bar .status{font:12px var(--mono);color:var(--muted);flex:1;min-width:160px}
button{font:400 14px var(--sans);background:var(--primary);color:#fff;border:0;border-radius:6px;padding:9px 18px;cursor:pointer}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
h2{font:400 13px var(--sans);letter-spacing:.12em;text-transform:uppercase;margin:36px 0 4px;color:var(--muted)}
h2 .n{font-family:var(--mono);margin-left:8px}
.row{padding:18px 0;border-top:1px solid var(--rule)}
.meta{font:12px var(--mono);color:var(--muted);display:flex;gap:10px;flex-wrap:wrap;overflow-wrap:anywhere}
.row h3{font-weight:400;font-size:17px;margin:4px 0 4px}.ctx{margin:0 0 8px;max-width:70ch}
pre{font:12px/1.5 var(--mono);background:var(--surface);border-left:2px solid var(--violet);padding:8px 10px;margin:6px 0 10px;white-space:pre-wrap;overflow-wrap:anywhere}
.opts{display:grid;gap:6px;margin:6px 0 8px}
label.opt{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;padding:8px 10px;border:1px solid var(--rule);border-radius:6px;cursor:pointer;background:var(--surface)}
label.opt input{grid-row:span 2;margin-top:4px}label.opt .l{font-weight:400}label.opt .c{font-size:14px;color:var(--muted)}
label.opt:has(input:checked){border-color:var(--primary)}
.tag{font:11px var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--green);margin-left:8px}
textarea{width:100%;min-height:38px;font:300 14px var(--sans);background:var(--surface);color:var(--text);border:1px solid var(--rule);border-radius:6px;padding:6px 8px}
.notelabel{font:400 11px var(--sans);letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
@media (prefers-reduced-motion: reduce){*{transition:none!important}}
</style></head><body><main>
<div class="eyebrow">Nightshift · queue review</div>
<h1>Nightshift cannot run __N__ of its tasks</h1>
<p class="lede">Each task below lacks the SURFACE, DONE_WHEN or ROADMAP the executor needs, so nightshift has run nothing since 2026-09-19. Decide each one: runnable, a person's task, benched, or dropped.</p>
<div class="asof">as of 2026-09-23 · read from the task files on the mac</div>
<div class="path"><span>Found</span><span class="on">Decide</span><span>Apply</span><span>Nightshift runs</span></div>
<div class="bar"><span class="count" id="count"></span><a id="rest">Use the suggestions for the rest</a><span class="status" id="status">Not saved yet</span><button id="save">Save</button></div>
<div id="board"></div>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
var D=JSON.parse(document.getElementById('data').textContent);
var KEY='decision-board:'+D.board+':'+D.build, st={};
try{st=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){st={}}
function persist(){try{localStorage.setItem(KEY,JSON.stringify(st))}catch(e){}}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var board=document.getElementById('board');
D.sections.forEach(function(s){
  var rows=D.items.filter(function(i){return i.section===s[0]});if(!rows.length)return;
  var h=el('h2',null,s[1]);h.appendChild(el('span','n',String(rows.length)));board.appendChild(h);
  rows.forEach(function(it){
    var r=el('div','row');var m=el('div','meta');m.appendChild(el('span',null,it.id));
    it.meta.forEach(function(x){m.appendChild(el('span',null,x))});r.appendChild(m);
    r.appendChild(el('h3',null,it.title));r.appendChild(el('p','ctx',it.context));
    if(it.preview)r.appendChild(el('pre',null,it.preview));
    var o=el('div','opts');
    it.options.forEach(function(op){
      var lab=el('label','opt');var inp=document.createElement('input');inp.type='radio';inp.name=it.id;inp.value=op.value;inp.id=it.id+'-'+op.value;
      if(st[it.id]&&st[it.id].choice===op.value)inp.checked=true;
      inp.addEventListener('change',function(){st[it.id]=st[it.id]||{};st[it.id].choice=op.value;persist();count()});
      lab.appendChild(inp);var l=el('span','l',op.label);if(op.value===it.suggested)l.appendChild(el('span','tag','suggested'));
      lab.appendChild(l);lab.appendChild(el('span','c',op.consequence));o.appendChild(lab)});
    r.appendChild(o);r.appendChild(el('div','notelabel','Note for Claude'));
    var ta=document.createElement('textarea');ta.id=it.id+'-note';ta.value=(st[it.id]&&st[it.id].note)||'';
    ta.addEventListener('input',function(){st[it.id]=st[it.id]||{};st[it.id].note=ta.value;persist()});
    r.appendChild(ta);board.appendChild(r)})});
function count(){var n=D.items.filter(function(i){return st[i.id]&&st[i.id].choice}).length;
  document.getElementById('count').textContent=n+' of '+D.items.length+' decided'}
document.getElementById('rest').addEventListener('click',function(){
  D.items.forEach(function(i){if(!(st[i.id]&&st[i.id].choice)){st[i.id]=st[i.id]||{};st[i.id].choice=i.suggested;
    var x=document.getElementById(i.id+'-'+i.suggested);if(x)x.checked=true}});persist();count()});
document.getElementById('save').addEventListener('click',function(){
  var dec={};D.items.forEach(function(i){if(st[i.id]&&(st[i.id].choice||st[i.id].note))dec[i.id]={choice:st[i.id].choice||'',note:st[i.id].note||''}});
  var out={board:D.board,build:D.build,savedAt:new Date().toISOString(),decisions:dec};
  var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:'application/json'}));
  a.download=D.board+'.decisions.json';document.body.appendChild(a);a.click();a.remove();
  document.getElementById('status').textContent='Saved to Downloads as '+D.board+'.decisions.json'});
count();
})();
</script></body></html>"""


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def apply(rows: list[dict], decisions: dict, dry_run: bool) -> int:
    page = build(rows)
    expected = json.loads(page.split('id="data">', 1)[1].split("</script>", 1)[0])["build"]
    if decisions.get("board") != SLUG or decisions.get("build") != expected:
        raise SystemExit("decisions are for a different board or build; regenerate and review again")
    adapter = [sys.executable, str(LIB / "org_workspace_adapter.py")]
    by_id = {r["id"]: r for r in rows}
    for rid, d in decisions["decisions"].items():
        r, choice, note = by_id[rid], d.get("choice"), (d.get("note") or "").strip()
        if not choice:
            print(f"{rid}: no choice, note only: {note}")
            continue
        base = adapter + ["update", "--file", r["file"]] + (["--id", r["orgId"]] if r["orgId"] else ["--title", r["title"][:60]])
        reason = f"2026-09-23 nightshift queue review: {OPTIONS[choice][0].lower()}" + (f" -- {note}" if note else "")
        if choice == "run":
            cmd = base + sum((["--property", f"{k}={v}"] for k, v in r["run"].items()), [])
        elif choice == "human":
            cmd = base + ["--tags", ":" + ":".join(t for t in r["tags"] if t != "AI") + ":", "--property", f"REVIEW_NOTE={reason}"]
        elif choice == "bench":
            cmd = base + ["--state", "DEFERRED", "--scheduled", WAKE, "--property", f"REVIEW_NOTE={reason}"]
        elif choice == "drop":
            cmd = base + ["--state", "CANCELLED", "--property", f"REVIEW_NOTE={reason}"]
        else:
            cmd = base + ["--state", "DONE", "--property", f"REVIEW_NOTE={reason}"]
        print(f"{rid} {choice:5} {r['title'][:60]}")
        if dry_run:
            print("   would run:", " ".join(cmd[2:])[:200])
            continue
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode:
            print(f"   FAILED: {(res.stderr or res.stdout).strip()[:300]}")
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--rows", type=Path, required=True)
    b.add_argument("--out", type=Path, default=STATE / f"{SLUG}.html")
    a = sub.add_parser("apply"); a.add_argument("--rows", type=Path, required=True)
    a.add_argument("--decisions", type=Path, required=True); a.add_argument("--dry-run", action="store_true")
    x = ap.parse_args()
    rows = json.loads(x.rows.read_text())
    if x.cmd == "build":
        write_private(x.out, build(rows))
        print(x.out)
        return 0
    return apply(rows, json.loads(x.decisions.read_text()), x.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
