#!/usr/bin/env python3
"""One formatter for every script-built Telegram message.

The rules are the telegram-output-formatting skill's (Telegram is the memo,
the disk is the dossier): bullets are "•", titles are short, nothing is cut
mid-word. Scripts built their messages by hand and cut with `s[:80]`, so the
owner read "…(plur-ai/ent" and lines broken where a column count fell
(2026-09-26). Every sender passes its text through here instead.

    clip(s, n)        shorten at a word boundary, ending in "…"
    normalize(text)   bullets to "•", no markdown emphasis or headings, no runs
                      of blank lines; code blocks are left alone
    html_safe(text)   escape for parse_mode=HTML, keeping <b> <i> <code> <pre>
    fit(text, ...)    one phone screen (15 lines, 1200 chars): the first lines,
                      then "… full report: <pointer>"; never cut mid-word
    keep_full(text, sender)          save the unshortened text, return its path
    record_undelivered(sender, reason, text)
                      one JSON line in ~/.datacore/state/undelivered-alerts.jsonl
                      ($DATACORE_UNDELIVERED_LOG), which the morning sweep reads

Nothing fails silently (MSG-10): a sender that cannot deliver -- no group, no
token, a non-200 answer, an exception -- records it here instead of printing
to a log nobody reads, and morning_repair.undelivered() reports it.

CLI, for shell senders (text on stdin):
    tg_format.py [--html]                           normalized text
    tg_format.py --fit [--more P] [--save-as S] [--html]
                                                    normalized and fitted; with
                                                    --save-as the full text is kept
                                                    and the pointer names its path
    tg_format.py --undelivered SENDER REASON        record a failed delivery
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BULLET = re.compile(r"^(\s*)[-*+]\s+(?=\S)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1")
_KEPT_TAGS = re.compile(r"&lt;(/?)(b|i|u|s|code|pre)&gt;")


def clip(s: str, n: int) -> str:
    """`s` in at most `n` characters, cut at a word boundary, ending in "…"."""
    s = " ".join(str(s).split())
    if len(s) <= n:
        return s
    cut = s[: max(1, n - 1)]
    space = cut.rfind(" ")
    if space >= n // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-–—(/") + "…"


def normalize(text: str) -> str:
    out: list[str] = []
    in_code = False
    for line in str(text).splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if not in_code:
            line = _BULLET.sub(r"\1• ", line)
            line = _HEADING.sub("", line)
            line = _EMPHASIS.sub(r"\2", line)
            line = line.rstrip()
        if not line.strip() and out and not out[-1].strip():
            continue                      # one blank line between blocks, never more
        out.append(line)
    return "\n".join(out).strip("\n")


def html_safe(text: str) -> str:
    """Escape for Telegram's HTML mode. A bare "<" or "&" in a title made the
    whole send fail, with only a printed warning."""
    return _KEPT_TAGS.sub(r"<\1\2>", html.escape(str(text), quote=False))


MAX_LINES, MAX_CHARS = 15, 1200


def fit(text: str, max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS, more: str | None = None) -> str:
    """`text` as one phone screen: the first whole lines, then a pointer to the rest.

    A message that already fits comes back unchanged. Otherwise the last line is
    "… full report: <more>" (or "… N more line(s)" without a pointer), and every
    kept line is whole -- only a first line too long for the screen on its own is
    shortened, at a word boundary, by clip().
    """
    text = str(text).strip("\n")
    lines = text.splitlines()
    if len(lines) <= max_lines and len(text) <= max_chars:
        return text
    kept: list[str] = []
    used = 0

    def pointer(n_left: int) -> str:
        return f"… full report: {more}" if more else f"… {n_left} more line(s)"

    budget = max_chars - len(pointer(len(lines))) - 1
    for line in lines:
        if len(kept) >= max_lines - 1:
            break
        cost = len(line) + (1 if kept else 0)
        if used + cost > budget:
            if not kept:                      # one line longer than the screen: shorten it
                kept.append(clip(line, max(budget, 2)))
            break
        kept.append(line)
        used += cost
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept + [pointer(len(lines) - len(kept))])


def undelivered_log() -> Path:
    return Path(os.environ.get("DATACORE_UNDELIVERED_LOG")
                or Path.home() / ".datacore" / "state" / "undelivered-alerts.jsonl")


def keep_full(text: str, sender: str) -> str:
    """Save the unshortened text beside the undelivered log; '' when it cannot be saved."""
    try:
        d = undelivered_log().parent / "alerts"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{re.sub(r'[^A-Za-z0-9_.-]+', '-', sender)}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.txt"
        p.write_text(str(text), encoding="utf-8")
        return str(p)
    except Exception:  # noqa: BLE001 -- a pointer is a convenience, never a reason to fail the send
        return ""


def record_undelivered(sender: str, reason: str, text: str = "") -> bool:
    """Append one line {at, host, sender, reason, text_head}. Never raises: the
    sender calling it is already failing and must not crash its job as well."""
    try:
        p = undelivered_log()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "host": socket.gethostname().split(".")[0], "sender": str(sender),
                           "reason": clip(reason, 300), "text_head": clip(text, 200)}, ensure_ascii=False)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="format Telegram text from stdin")
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--more", default=None)
    ap.add_argument("--save-as", default=None)
    ap.add_argument("--undelivered", nargs=2, metavar=("SENDER", "REASON"))
    a = ap.parse_args()
    raw = sys.stdin.read()
    if a.undelivered:
        return 0 if record_undelivered(a.undelivered[0], a.undelivered[1], raw) else 1
    text = normalize(raw)
    if a.fit:
        short = fit(text, more=a.more)
        if short != text and a.save_as:
            path = keep_full(text, a.save_as)
            if path:
                short = fit(text, more=path)
        text = short
    print(html_safe(text) if a.html else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
