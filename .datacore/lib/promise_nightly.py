#!/usr/bin/env python3
"""The promise scoreboard, every night, and one message to The Firm when it moves.

Owner decision 2026-09-30: run the scoreboard nightly and alert The Firm when a
promise that was green turns red. Every machine runs it for itself (owner,
2026-09-30): an eval that needs another machine over ssh (need `fleet`, met
only on the roster's console) or a live agent session (need `agent`, never on
here) is "could not run here" on a machine that cannot do that, never red.

    promise_nightly.py [--no-send] [--weekly-day mon]

What it does, in order:
  1. Runs `promise_evals.py --json` (deterministic and production evals; never
     --agents, never --write-baseline: agent evals cost model runs, and the
     baseline stays the owner's). Red evals are run once more to keep each
     failing test's first line, which promise_evals.py does not report.
  2. Judges what THIS host could not run. A red whose every failing test lacks a
     need this host does not have (.datacore/config/test-needs.yaml, checked by
     needs_gate), or failed only because a host was unreachable over ssh, or
     has no eval file here, is "could-not-run" -- never a regression.
  3. Writes the board to ~/.datacore/state/promise-scoreboard/board-<date>.json
     (and latest.json), keeping the last KEEP nights.
  4. Compares with the history: a promise whose last judged state (green/red,
     skipping could-not-run nights) was green and is red tonight "turned red";
     red to green "recovered". "Was green" means green on THIS host: the first
     night on a host only starts its history and sends nothing (it prints which
     promises green in the owner's baseline -- .datacore/registry/
     promise-baseline.json, read only -- are red here). Every red is rerun for its
     failure line on the first night; after that only reds that could be news
     (not already red here) and reds whose eval file lacks a need here.
  5. Sends ONE message to The Firm group (ALERT_CHAT_ID, this host's bot token --
     the route the overnight host's own alerts take) listing what turned red -- plain promise text, id in
     brackets, first failure line -- what recovered, and what newly could not run.
     Nothing moved: nothing sent. A red that stays red is not repeated; on the
     weekly day (Monday) a summary lists every regression still red.

The last line printed is the job's contract (jobs/manifest.yaml,
nightshift-promise-scoreboard):
    promise-scoreboard: G green, R red, C could not run here; T turned red, V recovered; alert sent|none
An alert that could not be delivered ends the line "alert NOT delivered (...)"
and exits 1; it is also recorded as undelivered (tg_format, MSG-10).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date as _date, datetime, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parent
ROOT = LIB.parents[1]
sys.path.insert(0, str(LIB))

STATE_DIR = Path(os.environ.get("PROMISE_NIGHTLY_DIR") or Path.home() / ".datacore" / "state" / "promise-scoreboard")
BASELINE = ROOT / ".datacore" / "registry" / "promise-baseline.json"
KEEP = 14                       # nights of history kept on disk
RUN_TIMEOUT_S = 3 * 3600        # the whole scoreboard; promise_evals bounds each chunk itself
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: A failure line that says a host could not be reached: the eval needed a
#: machine this host cannot see (the Mac is a visitor and sleeps). A need this
#: host lacks, not a broken promise.
UNREACHABLE = re.compile(
    r"ssh: connect to host|Could not resolve hostname|No route to host|Connection timed out"
    r"|Connection refused|Host key verification failed|Permission denied \(publickey"
    r"|ssh: Could not|Operation timed out"
    # the evals' own words for a host they could not reach (seen on the first
    # nightshift run, 2026-09-30): an ssh call that timed out, "could not check
    # (UNREACHABLE ...)", "could not tell (unreachable)", "could not read the crontab (timeout)"
    r"|TimeoutExpired: Command '\['ssh'|\bUNREACHABLE\b|could not tell \(unreachable\)"
    r"|could not read the crontab \(timeout\)", re.I)

#: agent_eval.require_enabled()'s own words: an agent eval the nightly did not
#: run (it never turns DATACORE_AGENT_EVALS on -- real model runs cost money).
AGENT_NOT_RUN = re.compile(r"agent eval not run \(set DATACORE_AGENT_EVALS=1\)")

GREEN, RED, CNR = "green", "red", "could-not-run"


# ---- running the scoreboard ------------------------------------------------------

def has_pytest() -> bool:
    """Can this interpreter run the evals at all? Without pytest every eval
    'crashes' and the board would be all red (box, 2026-09-30: 0 green, 188 red)."""
    import importlib.util
    return importlib.util.find_spec("pytest") is not None


def runner_command() -> list[str]:
    return [sys.executable, str(LIB / "promise_evals.py"), "--json"]


def runner_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "DATACORE_AGENT_EVALS"}
    env["DATACORE_ROOT"] = str(ROOT)
    return env


def failure_details(cwd: Path, files: list[Path], env_extra: dict) -> dict[str, list[tuple[str, str]]]:
    """{file path: [(test name, first failure line)]} from one more pytest run."""
    import promise_evals
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as fh:
        xml = fh.name
    env = {**runner_env(), **env_extra, "PROMISE_EVALS_ALL": "1"}
    out: dict[str, list[tuple[str, str]]] = {}
    try:
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--continue-on-collection-errors", f"--junitxml={xml}",
                        *[str(f) for f in files]], cwd=cwd, env=env, capture_output=True, text=True,
                       timeout=promise_evals.SUITE_TIMEOUT_S)
        tree = ET.parse(xml)
    except subprocess.TimeoutExpired:
        return {str(f): [("", f"timed out after {promise_evals.SUITE_TIMEOUT_S}s")] for f in files}
    except (ET.ParseError, OSError):
        return {str(f): [("", "pytest wrote no report (collection error or crash)")] for f in files}
    finally:
        Path(xml).unlink(missing_ok=True)
    by_name = {f.name: f for f in files}
    reported: set[str] = set()
    for case in tree.iter("testcase"):
        parts = (case.get("classname") or "").split(".")
        fname = next((p + ".py" for p in parts if p + ".py" in by_name), None)
        if case.get("file"):
            fname = case.get("file").split("/")[-1]
        if fname not in by_name:
            continue
        reported.add(fname)
        bad = [c for c in case if c.tag in ("failure", "error", "skipped")]
        if not bad:
            continue
        stem = fname[:-3]
        classes = parts[parts.index(stem) + 1:] if stem in parts else []
        test = "::".join([*classes, case.get("name", "")])
        out.setdefault(str(by_name[fname]), []).append((test, _first_line(bad[0])))
    for f in files:
        if str(f) not in out:
            out[str(f)] = [("", "passed when run again for its failure line (flaky?)" if f.name in reported
                            else "pytest reported no test from this file (collection error or crash)")]
    return out


def _first_line(el) -> str:
    prefix = "skipped: " if el.tag == "skipped" else ""
    for text in (el.get("message") or "", el.text or ""):
        for line in text.splitlines():
            if line.strip():
                return (prefix + " ".join(line.split()))[:300]
    return el.tag


def run_board(want=None) -> dict:
    """promise_evals.py --json, plus {"failures": {pid: [{file, test, line}]}} for the reds
    that `want(pid)` says could be news (all reds when None), and {"eval_files": {pid: [file]}}."""
    r = subprocess.run(runner_command(), cwd=ROOT, env=runner_env(), capture_output=True, text=True,
                       timeout=RUN_TIMEOUT_S)
    start = r.stdout.find("{")
    if start < 0:
        raise RuntimeError(f"promise_evals.py printed no JSON (exit {r.returncode}): "
                           f"{(r.stderr or r.stdout).strip()[-200:]}")
    raw = json.loads(r.stdout[start:])
    import promise_evals
    files = promise_evals.eval_files()
    reds = [pid for pid, st in raw.get("promises", {}).items() if st == "red" and (want is None or want(pid))]
    wanted: dict[str, set[Path]] = {}
    for pid in reds:
        for suite, f in files.get(promise_evals.norm(pid), []):
            wanted.setdefault(suite, set()).add(f)
    details: dict[str, list[tuple[str, str]]] = {}
    for name, cwd, _tdir, env in promise_evals.SUITES:
        batch = sorted(wanted.get(name, ()))
        for i in range(0, len(batch), promise_evals.CHUNK_FILES):
            details.update(failure_details(cwd, batch[i:i + promise_evals.CHUNK_FILES], env))
    raw["failures"] = {pid: [{"file": _rel(f), "test": t, "line": line}
                             for _s, f in files.get(promise_evals.norm(pid), [])
                             for t, line in details.get(str(f), [])]
                       for pid in reds}
    raw["eval_files"] = {pid: [_rel(f) for _s, f in files.get(promise_evals.norm(pid), [])]
                         for pid in raw.get("promises", {})}
    return raw


def _rel(f: Path) -> str:
    try:
        return str(Path(f).relative_to(ROOT))
    except ValueError:
        return str(f)


def unmet_needs() -> dict[str, list[str]]:
    """{'<file>[::<test>]': [needs this host lacks]} -- as an ordinary run sees them."""
    import needs_gate
    env = {k: v for k, v in os.environ.items() if k not in ("PROMISE_EVALS_ALL", "DATACORE_AGENT_EVALS")}
    return needs_gate.unmet_by_test(root=ROOT, env=env)


def promise_eval_files() -> dict[str, list[str]]:
    """{normalized promise id: [repo-relative eval file]}"""
    import promise_evals
    return {pid: [_rel(f) for _s, f in fs] for pid, fs in promise_evals.eval_files().items()}


def promise_texts() -> dict[str, str]:
    import promise_evals
    return promise_evals.promises()


# ---- the board -------------------------------------------------------------------

def _norm(pid: str) -> str:
    m = re.match(r"^([A-Z]+)[^A-Z0-9]*0*(\d+)", str(pid).upper())
    return m.group(1) + m.group(2) if m else re.sub(r"[^A-Z0-9]", "", str(pid).upper())


def _covered(failure: dict, unmet: dict[str, list[str]]) -> list[str] | None:
    """The needs that explain this failure, or None when nothing on this host does."""
    import needs_gate
    node = f"{failure.get('file', '')}::{failure['test']}" if failure.get("test") else failure.get("file", "")
    for key, needs in unmet.items():
        if needs_gate.matches(node, key) or key == failure.get("file"):
            return list(needs)
    if UNREACHABLE.search(failure.get("line") or ""):
        return ["a host reachable over ssh from here"]
    if AGENT_NOT_RUN.search(failure.get("line") or ""):
        return ["agent"]
    return None


def build_board(raw: dict, unmet: dict[str, list[str]], texts: dict[str, str], *, date: str,
                host: str | None = None) -> dict:
    promises = {}
    for pid, st in (raw.get("promises") or {}).items():
        entry = {"state": st, "text": texts.get(pid, "")}
        fails = (raw.get("failures") or {}).get(pid) or []
        if st == "no-eval":
            entry.update(state=CNR, why="no eval file for it on this host")
        elif st == RED and not fails:
            # Not rerun (already red here), or no detail: could-not-run only when
            # every eval file behind it lacks a need this host does not have.
            efs = (raw.get("eval_files") or {}).get(pid) or []
            missing = [unmet.get(f) for f in efs]
            if efs and all(missing):
                entry.update(state=CNR, why="needs " + ", ".join(sorted({x for m in missing for x in m})))
        elif st == RED:
            needs = [_covered(f, unmet) for f in fails]
            # The first failure nothing on this host explains is the one worth reading.
            first = next((f for f, n in zip(fails, needs) if n is None), fails[0] if fails else {})
            entry["why"] = f"{first.get('test') or first.get('file', '')}: {first.get('line', '')}".strip(": ")
            if fails and all(n is not None for n in needs):
                missing = sorted({x for n in needs for x in n})
                entry.update(state=CNR, why="needs " + ", ".join(missing))
        promises[pid] = entry
    counts: dict[str, int] = {}
    for e in promises.values():
        counts[e["state"]] = counts.get(e["state"], 0) + 1
    return {"date": date, "host": host or socket.gethostname().split(".")[0],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "counts": counts, "promises": promises}


def load_history(state: Path, before: str) -> list[dict]:
    """Boards from nights before `before`, newest first."""
    out = []
    for p in sorted(Path(state).glob("board-*.json"), reverse=True):
        d = p.stem[len("board-"):]
        if d >= before:
            continue
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out


def baseline_green() -> set[str]:
    try:
        return {_norm(x) for x in json.loads(Path(BASELINE).read_text()).get("green", [])}
    except (OSError, ValueError, AttributeError):
        return set()


def changes(history: list[dict], board: dict, base_green: set[str]) -> dict[str, list[str]]:
    """turned_red, recovered, newly_cnr, still_red, still_cnr -- lists of promise ids."""
    out = {k: [] for k in ("turned_red", "recovered", "newly_cnr", "still_red", "still_cnr")}
    for pid, e in board["promises"].items():
        past = [h["promises"].get(pid, {}).get("state") for h in history]
        judged = next((s for s in past if s in (GREEN, RED)), None)
        if judged is None and not history:
            judged = GREEN if _norm(pid) in base_green else None
        ever_green = GREEN in past or _norm(pid) in base_green
        last = past[0] if past else None
        st = e["state"]
        if st == RED and judged == GREEN:
            out["turned_red"].append(pid)
        elif st == GREEN and judged == RED:
            out["recovered"].append(pid)
        elif st == RED and ever_green:
            out["still_red"].append(pid)
        if st == CNR and ever_green:
            (out["newly_cnr"] if last != CNR else out["still_cnr"]).append(pid)
    return out


# ---- the message -------------------------------------------------------------------

def _clip(s: str, n: int) -> str:
    try:
        from tg_format import clip
        return clip(s, n)
    except ImportError:
        s = " ".join(str(s).split())
        return s if len(s) <= n else s[: n - 1] + "…"


def _name(board: dict, pid: str) -> str:
    return f"{_clip(board['promises'][pid].get('text') or pid, 90)} ({pid})"


def message(ch: dict[str, list[str]], board: dict, *, weekly: bool, pointer: str) -> str | None:
    lines: list[str] = []
    p = board["promises"]
    if ch["turned_red"]:
        lines.append(f"🔴 {len(ch['turned_red'])} promise(s) turned red")
        lines += [f"• {_name(board, pid)}: {_clip(p[pid].get('why', ''), 140)}" for pid in ch["turned_red"]]
    if ch["recovered"]:
        lines.append("✅ Recovered: " + "; ".join(_name(board, pid) for pid in ch["recovered"]))
    if ch["newly_cnr"]:
        lines.append(f"⚪ Could not run here ({board['host']}), not a regression:")
        lines += [f"• {_name(board, pid)}: {_clip(p[pid].get('why', ''), 100)}" for pid in ch["newly_cnr"]]
    if weekly and (ch["still_red"] or ch["still_cnr"]):
        if ch["still_red"]:
            lines.append(f"📋 Weekly: {len(ch['still_red'])} promise(s) that were green are still red")
            lines += [f"• {_name(board, pid)}" for pid in ch["still_red"]]
        if ch["still_cnr"]:
            lines.append(f"📋 Weekly: {len(ch['still_cnr'])} still could not run here: "
                         + ", ".join(ch["still_cnr"]))
    if not lines:
        return None
    return f"Promise scoreboard, {board['date']} on {board['host']}\n\n" + "\n".join(lines) + f"\n\nBoard: {pointer}"


#: The install's shared env files: TELEGRAM_BOT_TOKEN and ALERT_CHAT_ID (The Firm).
ENV_FILES = [ROOT / ".datacore" / "env" / ".env", ROOT / ".datacore" / "env" / "local.env"]


def _undelivered(reason: str, text: str) -> None:
    try:
        from tg_format import record_undelivered
        record_undelivered("promise_nightly", reason, text)
    except Exception:  # noqa: BLE001 -- the contract line still says NOT delivered
        pass


def _settings() -> dict[str, str]:
    """The two settings, from the env files (os.environ wins when it has them)."""
    from env_utils import parse_env_file
    merged: dict[str, str] = {}
    for f in ENV_FILES:
        try:
            if Path(f).exists():
                merged.update(parse_env_file(Path(f), inline_comments=True))
        except (OSError, ValueError):
            continue
    for key in ("TELEGRAM_BOT_TOKEN", "ALERT_CHAT_ID"):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    return merged


def alert_command() -> str:
    """This host's own alert command, or "": job_verify's lookup, reused --
    $DATACORE_ALERT_COMMAND, else `command:` in ~/.datacore/alerts.yaml."""
    try:
        from job_verify import _alert_command
        return _alert_command()
    except Exception:  # noqa: BLE001 -- job_verify not importable here: the variable alone
        return os.environ.get("DATACORE_ALERT_COMMAND", "").strip()


def send_to_firm(text: str) -> tuple[bool, str]:
    """One message to The Firm group -- the route this host's own alerts take.
    An alert command (DATACORE_ALERT_COMMAND or ~/.datacore/alerts.yaml, as
    job_verify reads it), when set, is that route (the text on stdin). Else
    (job_verify_notify.sh's direct route, nightshift run.py, fleet_sync_alert.sh):
    TELEGRAM_BOT_TOKEN posts to ALERT_CHAT_ID. Only the group, never a fallback
    to a 1:1 chat (MSG-1). One phone screen with a pointer to the full text
    (MSG-4). A send that cannot be made or is refused is recorded as undelivered
    (MSG-10). winston_send.py is not used: on the overnight host its loader
    refuses to start (a root-owned ~/.config/cos.env, found 2026-09-30).
    """
    command = alert_command()
    if command:
        # The host's own alert route (box: Winston's sender; the workstation:
        # the same, over ssh to the always-on host).
        try:
            r = subprocess.run(["bash", "-c", command], input=text, capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                return True, "sent"
            why = f"alert command exit {r.returncode}"
        except (OSError, subprocess.SubprocessError) as e:
            why = f"alert command {type(e).__name__}"
        _undelivered(why, text)
        return False, why
    cfg = _settings()
    token, chat = cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("ALERT_CHAT_ID", "")
    if not chat:
        why = "ALERT_CHAT_ID unset: alerts go only to The Firm group, never a 1:1 chat"
        _undelivered(why, text)
        return False, why
    if not token:
        why = "no bot token (TELEGRAM_BOT_TOKEN)"
        _undelivered(why, text)
        return False, why
    body = text
    try:
        import tg_format
        body = tg_format.normalize(text)
        short = tg_format.fit(body)
        if short != body:
            short = tg_format.fit(body, more=tg_format.keep_full(body, "promise_nightly") or None)
        body = short
    except Exception:  # noqa: BLE001 -- a missing formatter must not stop the alert
        pass
    data = urllib.parse.urlencode({"chat_id": chat, "text": body}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                    timeout=15) as r:
            if r.status == 200:
                return True, "sent"
            why = f"http {r.status}"
    except urllib.error.HTTPError as e:
        why = f"http {e.code}"
    except Exception as e:  # noqa: BLE001
        why = f"{type(e).__name__}"
    _undelivered(why, text)
    return False, why


# ---- main --------------------------------------------------------------------------

def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _write(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-send", action="store_true", help="print the message instead of sending it")
    ap.add_argument("--weekly-day", default="mon", choices=WEEKDAYS)
    args = ap.parse_args(argv)
    night = today()
    state = Path(STATE_DIR)
    history = load_history(state, night) if state.exists() else []
    base = baseline_green()

    if not has_pytest():
        print(f"promise-scoreboard: FAILED to run (no pytest for {sys.executable}: this host cannot run "
              f"the evals, so there is no board)")
        return 1
    try:
        unmet = unmet_needs()
        lacking = {k.split("::", 1)[0] for k in unmet}
        files = promise_eval_files()
    except Exception as exc:  # noqa: BLE001 -- the contract line must say it failed
        print(f"promise-scoreboard: FAILED to run ({type(exc).__name__}: {str(exc)[:200]})")
        return 1

    def rerun(pid: str) -> bool:
        """Which reds are run once more for their failure lines: every red on the
        first night here (a red recorded without its reason is never rerun later);
        after that a red that could be news (not already red here last time it was
        judged), and any red whose eval file lacks a need here -- a need declared
        for single tests is judged per failing test, so it needs the failures."""
        if not history:
            return True
        if any(f in lacking for f in files.get(_norm(pid), [])):
            return True
        past = [h["promises"].get(pid, {}).get("state") for h in history]
        judged = next((x for x in past if x in (GREEN, RED)), None)
        return judged != RED

    try:
        raw = run_board(rerun)
        board = build_board(raw, unmet, promise_texts(), date=night)
    except Exception as exc:  # noqa: BLE001 -- the contract line must say it failed
        print(f"promise-scoreboard: FAILED to run ({type(exc).__name__}: {str(exc)[:200]})")
        return 1
    state.mkdir(parents=True, exist_ok=True)
    path = state / f"board-{night}.json"
    _write(path, board)
    _write(state / "latest.json", board)
    for old in sorted(state.glob("board-*.json"))[:-KEEP]:
        old.unlink(missing_ok=True)

    ch = changes(history, board, base)
    weekly = WEEKDAYS[_date.fromisoformat(night).weekday()] == args.weekly_day
    text = message(ch, board, weekly=weekly, pointer=f"{path} on {board['host']}")
    if not history:
        # "Was green" means green on THIS host. The baseline is the workstation's;
        # compared with it, the overnight host's first run found 27 promises red
        # that it simply cannot run the way the workstation does (2026-09-30).
        print(f"first night on this host: history started, nothing sent; {len(ch['turned_red'])} promise(s) "
              f"green in the owner's baseline are red here: {', '.join(ch['turned_red']) or 'none'}")
        text = None
        ch = {**ch, "turned_red": [], "recovered": [], "newly_cnr": []}
    rc, outcome = 0, "none"
    if text and args.no_send:
        print(text)
        outcome = "none (--no-send)"
    elif text:
        ok, why = send_to_firm(text)
        outcome = "sent" if ok else f"NOT delivered ({why})"
        rc = 0 if ok else 1
    c = board["counts"]
    for key in ("turned_red", "recovered", "newly_cnr"):
        if ch[key]:
            print(f"{key}: {', '.join(ch[key])}")
    print(f"promise-scoreboard: {c.get(GREEN, 0)} green, {c.get(RED, 0)} red, {c.get(CNR, 0)} could not run here; "
          f"{len(ch['turned_red'])} turned red, {len(ch['recovered'])} recovered; "
          f"alert {outcome}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
