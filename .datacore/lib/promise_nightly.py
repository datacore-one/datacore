#!/usr/bin/env python3
"""The promise scoreboard, every night, and one message to The Firm when it moves.

Owner decision 2026-09-30: run the scoreboard nightly on the overnight host and
alert The Firm when a promise that was green turns red.

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
     red to green "recovered". With no history, the owner's baseline
     (.datacore/registry/promise-baseline.json, read only) is the prior.
  5. Sends ONE message to The Firm (winston_send.py --alert, the route this
     host's job_verify uses) listing what turned red -- plain promise text, id in
     brackets, first failure line -- what recovered, and what newly could not run.
     Nothing moved: nothing sent. A red that stays red is not repeated; on the
     weekly day (Monday) a summary lists every regression still red.

The last line printed is the job's contract (jobs/manifest.yaml,
nightshift-promise-scoreboard):
    promise-scoreboard: G green, R red, C could not run here; T turned red, V recovered; alert sent|none
An alert that could not be delivered ends the line "alert NOT delivered (...)"
and exits 1; it is also recorded as undelivered by winston_send (MSG-10).
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
    r"|ssh: Could not|Operation timed out", re.I)

GREEN, RED, CNR = "green", "red", "could-not-run"


# ---- running the scoreboard ------------------------------------------------------

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
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}",
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


def run_board() -> dict:
    """promise_evals.py --json, plus {"failures": {pid: [{file, test, line}]}} for the reds."""
    r = subprocess.run(runner_command(), cwd=ROOT, env=runner_env(), capture_output=True, text=True,
                       timeout=RUN_TIMEOUT_S)
    start = r.stdout.find("{")
    if start < 0:
        raise RuntimeError(f"promise_evals.py printed no JSON (exit {r.returncode}): "
                           f"{(r.stderr or r.stdout).strip()[-200:]}")
    raw = json.loads(r.stdout[start:])
    import promise_evals
    files = promise_evals.eval_files()
    reds = [pid for pid, st in raw.get("promises", {}).items() if st == "red"]
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
    return None


def build_board(raw: dict, unmet: dict[str, list[str]], texts: dict[str, str], *, date: str,
                host: str | None = None) -> dict:
    promises = {}
    for pid, st in (raw.get("promises") or {}).items():
        entry = {"state": st, "text": texts.get(pid, "")}
        fails = (raw.get("failures") or {}).get(pid) or []
        if st == "no-eval":
            entry.update(state=CNR, why="no eval file for it on this host")
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


def send_to_firm(text: str) -> tuple[bool, str]:
    """One alert to The Firm group through winston_send.py --alert.

    On the overnight host the bot token is TELEGRAM_BOT_TOKEN in the install's
    env files; winston_send reads WINSTON_BOT_TOKEN, so it is mapped exactly as
    this host's job_verify cron line maps it. winston_send records a failed send
    as undelivered (MSG-10) and exits non-zero.
    """
    env = dict(os.environ)
    if not env.get("WINSTON_BOT_TOKEN"):
        try:
            import cos_env
            merged = cos_env.read()
            if merged.get("TELEGRAM_BOT_TOKEN"):
                env["WINSTON_BOT_TOKEN"] = merged["TELEGRAM_BOT_TOKEN"]
        except Exception:  # noqa: BLE001 -- winston_send then says which setting is missing
            pass
    try:
        r = subprocess.run([sys.executable, str(LIB / "winston_send.py"), "--alert"], input=text, text=True,
                           capture_output=True, timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}"
    if r.returncode == 0:
        return True, "sent"
    return False, ((r.stderr or r.stdout).strip().splitlines() or [f"exit {r.returncode}"])[-1][:200]


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
    try:
        raw = run_board()
        board = build_board(raw, unmet_needs(), promise_texts(), date=night)
    except Exception as exc:  # noqa: BLE001 -- the contract line must say it failed
        print(f"promise-scoreboard: FAILED to run ({type(exc).__name__}: {str(exc)[:200]})")
        return 1
    state = Path(STATE_DIR)
    state.mkdir(parents=True, exist_ok=True)
    history = load_history(state, night)
    path = state / f"board-{night}.json"
    _write(path, board)
    _write(state / "latest.json", board)
    for old in sorted(state.glob("board-*.json"))[:-KEEP]:
        old.unlink(missing_ok=True)

    ch = changes(history, board, baseline_green())
    weekly = WEEKDAYS[_date.fromisoformat(night).weekday()] == args.weekly_day
    text = message(ch, board, weekly=weekly, pointer=f"{path} on {board['host']}")
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
