"""Harness for the MSG-* promise evals: see every Telegram request a sender makes, with no network.

Two captures, so an eval does not care HOW a sender posts:
  - a fake `curl` first on PATH (shell senders);
  - a sitecustomize on PYTHONPATH that intercepts urllib's OpenerDirector.open for
    api.telegram.org (Python senders, including secret_http's own opener).
Both append one JSON line per request to $FAKE_TG_LOG: {via, url, method, token, fields}.
The answer is HTTP $FAKE_HTTP (default 200); getChat answers $FAKE_GETCHAT_HTTP (default 400,
"not a member"). Undelivered alerts are expected in $DATACORE_UNDELIVERED_LOG.

Each repo that owns a sender carries a copy (root lib, chief-of-staff, nightshift).
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_CAPTURE = r'''
import io, json, os, urllib.error, urllib.request
from urllib.parse import parse_qsl, parse_qs, urlsplit


def _record(url, fields, via):
    method = urlsplit(url).path.rsplit("/", 1)[-1]
    token = url.split("/bot", 1)[1].split("/", 1)[0] if "/bot" in url else ""
    with open(os.environ["FAKE_TG_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"via": via, "url": url, "method": method, "token": token, "fields": fields}) + "\n")


def _code(url):
    if "getChat" in url:
        return int(os.environ.get("FAKE_GETCHAT_HTTP", "400"))
    return int(os.environ.get("FAKE_HTTP", "200"))


def _body(code):
    return json.dumps({"ok": True, "result": {}} if code == 200 else
                      {"ok": False, "error_code": code, "description": "fake"}).encode()
'''

_SITE = _CAPTURE + r'''

class _Resp:
    def __init__(self, code, body):
        self.status = code; self._b = io.BytesIO(body)
    def read(self, size=-1):
        return self._b.read(size)
    def getcode(self):
        return self.status
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


_orig = urllib.request.OpenerDirector.open


def _open(self, fullurl, data=None, *a, **k):
    url = fullurl.full_url if isinstance(fullurl, urllib.request.Request) else fullurl
    if "api.telegram.org" not in url:
        return _orig(self, fullurl, data, *a, **k)
    raw = data if data is not None else getattr(fullurl, "data", None)
    fields = dict(parse_qsl(raw.decode() if raw else "", keep_blank_values=True))
    fields.update({key: v[0] for key, v in parse_qs(urlsplit(url).query).items()})
    _record(url, fields, "urllib")
    code = _code(url)
    if code != 200:
        raise urllib.error.HTTPError(url, code, "fake", {}, io.BytesIO(_body(code)))
    return _Resp(code, _body(code))


urllib.request.OpenerDirector.open = _open
'''

_CURL = _CAPTURE + r'''
import sys
args, url, fields, wfmt, out, fail, i = sys.argv[1:], "", {}, None, None, False, 0
while i < len(args):
    a = args[i]
    if a in ("-d", "--data", "--data-urlencode", "--data-raw", "-F", "--form"):
        k, _, v = args[i + 1].partition("="); fields[k] = v; i += 2; continue
    if a in ("-w", "--write-out"):
        wfmt = args[i + 1]; i += 2; continue
    if a in ("-o", "--output"):
        out = args[i + 1]; i += 2; continue
    if a in ("-m", "--max-time", "--connect-timeout", "-X", "--request", "-H", "--header"):
        i += 2; continue
    if a == "--fail" or (a.startswith("-") and not a.startswith("--") and a[1:].isalpha() and "f" in a[1:]):
        fail = True
    if a.startswith("http"):
        url = a
    i += 1
fields.update({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})
_record(url, fields, "curl")
code = _code(url)
body = _body(code)
if out and out != "/dev/null":
    open(out, "wb").write(body)
elif not out:
    sys.stdout.write(body.decode())
if wfmt:
    sys.stdout.write(wfmt.replace("%{http_code}", str(code)))
sys.exit(22 if fail and code >= 400 else 0)
'''

_JOURNALCTL = "#!/bin/bash\nprintf '%s\\n' \"${FAKE_JOURNAL:-}\"\n"

SCRUB = ("ALERT_CHAT_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WINSTON_BOT_TOKEN", "WINSTON_CHAT_ID",
         "JOB_VERIFY_ENV_FILE", "https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY")


def fake_env(tmp_path: Path, **extra: str) -> dict:
    """An environment with no Telegram settings of its own, the fakes first on the paths."""
    bindir, site = tmp_path / "fakebin", tmp_path / "fakesite"
    bindir.mkdir(exist_ok=True); site.mkdir(exist_ok=True)
    (site / "sitecustomize.py").write_text(_SITE)
    curl = bindir / "curl"
    curl.write_text(f"#!{sys.executable}\n" + _CURL); curl.chmod(0o755)
    j = bindir / "journalctl"; j.write_text(_JOURNALCTL); j.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env.update({
        "PATH": f"{bindir}:{env.get('PATH', '')}",
        "PYTHONPATH": os.pathsep.join(p for p in (str(site), env.get("PYTHONPATH", "")) if p),
        "FAKE_TG_LOG": str(tmp_path / "telegram-requests.jsonl"),
        "DATACORE_UNDELIVERED_LOG": str(tmp_path / "undelivered-alerts.jsonl"),
    })
    env.update(extra)
    return env


def requests(tmp_path: Path) -> list[dict]:
    p = tmp_path / "telegram-requests.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def posts(tmp_path: Path) -> list[dict]:
    """Only sendMessage counts as a post; getChat is a probe."""
    return [r for r in requests(tmp_path) if r["method"] == "sendMessage"]


def undelivered(tmp_path: Path) -> list[dict]:
    p = tmp_path / "undelivered-alerts.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def visible(text: str) -> str:
    """What the phone shows: HTML tags removed, entities decoded."""
    return html.unescape(re.sub(r"</?(b|i|u|s|code|pre)>", "", text))


def run_py(code: str, env: dict, cwd: str | None = None, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=cwd, input=stdin, capture_output=True,
                          text=True, timeout=120)


def run_sh(script: str | Path, *args: str, env: dict, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(script), *args], env=env, input=stdin, capture_output=True, text=True,
                          timeout=120)


def whole_words(text: str, source: str) -> list[str]:
    """Tokens of `text` that are not whole tokens of `source` (a cut word shows up here).
    A trailing "…" marks a deliberate shortening and is stripped before the check."""
    src = set(source.split())
    bad = []
    for tok in text.split():
        t = tok.rstrip("…")
        if t and t not in src and tok != "…":
            bad.append(tok)
    return bad


# ---- root senders ---------------------------------------------------------------------

LIB = Path(__file__).resolve().parents[1]
_FAKE_JOB_VERIFY = "import os, sys\nsys.stdout.write(os.environ.get('FAKE_JV_OUT', ''))\nsys.exit(1)\n"


def alert_block(job: str) -> str:
    """One operator-facing job_verify failure block (the filter relays these)."""
    return (f"job '{job}' FAILED:\n  - ~/.datacore/state/{job}.log: last line does not match 'ok'\n"
            f"could NOT delegate {job} (autofix unavailable); escalating to the operator\n"
            f"alert: job.verify FAILED: {job} (1 failure(s))\n")


def job_verify_notify_env(tmp_path: Path, jv_out: str, host_env: str, **extra: str) -> dict:
    """job_verify_notify.sh on its direct route: a fake job_verify that fails with `jv_out`,
    the real alert filter and formatter, and a host env file with `host_env` in it."""
    lib = tmp_path / "runner" / ".datacore" / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "job_verify.py").write_text(_FAKE_JOB_VERIFY)
    for name in ("job_verify_alert_filter.py", "tg_format.py"):
        if not (lib / name).exists():
            (lib / name).symlink_to(LIB / name)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "host.env").write_text(host_env)
    return fake_env(tmp_path, JOB_VERIFY_RUNNER=str(tmp_path / "runner"), JOB_VERIFY_LOG=str(tmp_path / "jv.log"),
                    JOB_VERIFY_PYTHON=sys.executable, JOB_VERIFY_ENV_FILE=str(tmp_path / "host.env"),
                    DATACORE_ROOT=str(tmp_path / "data"), FAKE_JV_OUT=jv_out, **extra)
