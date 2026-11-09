"""Promise CAP-6:

    Saving my browser tabs captures every open tab except internal browser
    pages, and closes them only if I say yes.

Kind: deterministic.
  - host (`lib/host.py capture_tabs`): given every open tab across windows,
    every web/file page lands in the inbox and no internal browser page
    (brave://, chrome://, about:, chrome-extension://, devtools://, chrome-untrusted://,
    view-source:) does;
  - extension (`extension/popup.js`, run under node with a fake DOM and a fake
    `chrome` API): the save queries tabs across ALL windows, sends every
    non-internal tab to the host, and calls chrome.tabs.remove only after the
    Yes button — never after No, never on its own;
  - the extension and the host filter the same internal prefixes.

Seeded failure: popup.js closes the captured tabs straight after a successful
save, or an internal prefix is dropped from the filter.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1]
HOST = MODULE / "lib" / "host.py"
POPUP = MODULE / "extension" / "popup.js"
BACKGROUND = MODULE / "extension" / "background.js"

WEB = ["https://example.org/a", "http://intranet.local/b", "file:///tmp/notes.html"]
INTERNAL = ["brave://settings", "chrome://newtab/", "about:blank", "chrome-extension://abc/p.html",
            "devtools://devtools/bundled/x.html", "chrome-untrusted://new-tab-page/", "view-source:https://example.org/a"]


def _host():
    spec = importlib.util.spec_from_file_location("tab_host_cap6", HOST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_host_captures_every_page_but_no_internal_one(tmp_path):
    host = _host()
    inbox = tmp_path / "inbox.org"
    inbox.write_text("#+TITLE: Inbox\n\n* Inbox\n", encoding="utf-8")
    cfg = json.loads((MODULE / "lib" / "config.json").read_text(encoding="utf-8"))
    cfg["inbox_path"] = str(inbox)
    tabs = [{"title": f"T{i}", "url": u} for i, u in enumerate(WEB + INTERNAL)]
    res = host.capture_tabs(tabs, cfg)
    assert res["success"] and res["count"] == len(WEB), res
    text = inbox.read_text(encoding="utf-8")
    for u in WEB:
        assert f":SOURCE: {u}" in text, f"{u} was not captured"
    for u in INTERNAL:
        assert f":SOURCE: {u}" not in text, f"internal page {u} was captured"


def _prefixes_js(path: Path) -> set[str]:
    m = re.search(r"FILTERED_PREFIXES\s*=\s*\[(.*?)\]", path.read_text(encoding="utf-8"), re.S)
    return set(re.findall(r"\"([^\"]+)\"", m.group(1)))


def test_extension_and_host_filter_the_same_internal_pages():
    cfg = set(json.loads((MODULE / "lib" / "config.json").read_text(encoding="utf-8"))["filtered_prefixes"])
    assert _prefixes_js(POPUP) == cfg and _prefixes_js(BACKGROUND) == cfg


HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, textContent: '', className: '', disabled: false, handlers: {},
    classList: { s: new Set(['hidden']), add(c) { this.s.add(c); }, remove(c) { this.s.delete(c); } },
    addEventListener(ev, fn) { this.handlers[ev] = fn; } };
  return els[id];
}
const TABS = JSON.parse(process.argv[3]);
const log = { queries: [], sent: null, removed: [] };
const chrome = {
  runtime: { lastError: null,
    sendNativeMessage(host, msg, cb) { log.sent = msg;
      setTimeout(() => cb({ success: true, count: msg.tabs.length, duplicates_skipped: 0 }), 0); } },
  tabs: {
    async query(q) { log.queries.push(q); return q.active ? [TABS[0]] : TABS; },
    async remove(ids) { log.removed.push(...ids); } } };
const ctx = { document: { getElementById: el }, chrome, window: { close() {} }, setTimeout, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const answer = process.argv[4];
(async () => {
  await el('capture-btn').handlers.click();
  await new Promise(r => setTimeout(r, 20));
  log.asked = !el('phase-close').classList.s.has('hidden');
  log.removedBeforeAnswer = log.removed.length;
  if (answer === 'yes') await el('close-yes').handlers.click();
  if (answer === 'no') await el('close-no').handlers.click();
  await new Promise(r => setTimeout(r, 20));
  process.stdout.write(JSON.stringify(log));
})();
"""


def _run_popup(tmp_path: Path, answer: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to run the extension popup under test")
    h = tmp_path / "harness.js"
    h.write_text(HARNESS, encoding="utf-8")
    tabs = [{"id": i + 1, "url": u, "title": f"T{i}", "active": i == 0} for i, u in enumerate(WEB + INTERNAL)]
    r = subprocess.run([node, str(h), str(POPUP), json.dumps(tabs), answer],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_popup_sends_every_page_and_asks_before_closing(tmp_path):
    log = _run_popup(tmp_path, "none")
    assert {} in log["queries"], "the save must query tabs across all windows"
    assert sorted(t["url"] for t in log["sent"]["tabs"]) == sorted(WEB)
    assert log["asked"], "the popup did not ask whether to close the captured tabs"
    assert log["removedBeforeAnswer"] == 0 and log["removed"] == [], "tabs closed without a yes"


def test_popup_closes_nothing_on_no(tmp_path):
    log = _run_popup(tmp_path, "no")
    assert log["removed"] == [], f"tabs closed after No: {log['removed']}"


def test_popup_closes_the_captured_tabs_on_yes(tmp_path):
    log = _run_popup(tmp_path, "yes")
    web_ids = {i + 1 for i, _ in enumerate(WEB)}
    removed = set(log["removed"])
    assert removed, "Yes closed nothing"
    assert removed <= web_ids, "Yes closed an internal page that was never captured"
    assert web_ids - removed <= {1}, "Yes left captured tabs open (only the active tab may stay)"
