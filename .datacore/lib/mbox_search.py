#!/usr/bin/env python3
"""
Search locally cached Thunderbird mail.

Thunderbird keeps every IMAP account it has synced as plain mbox files under the
profile directory. That is a complete, offline, greppable archive — no network,
no credentials, no provider API. This script makes it queryable.

Why not `mailbox.mbox`: it builds a full in-memory index before returning
anything, and these stores run to 1.9 GB per file. This streams instead, so
memory stays flat regardless of store size.

Usage
-----
    # Which accounts exist, and where do they live?
    python3 mbox_search.py accounts

    # Messages from a domain, newest first
    python3 mbox_search.py search --account gregor@plur.si --from maha.si

    # Narrow by body keyword, and show the matching lines
    python3 mbox_search.py search --account gregor@plur.si \
        --from maha.si --body 'geslo|password' --context 2

    # Everything mentioning a term, across every account
    python3 mbox_search.py search --body 'izvid' --limit 40

Matching is case-insensitive regex throughout. Bodies are decoded from
base64/quoted-printable and charset-normalised before matching, because the
interesting content is frequently neither ASCII nor plain text.

PRIVACY: reads only local files, sends nothing anywhere. Output can contain
message bodies — treat it like the mailbox itself.
"""

from __future__ import annotations

import argparse
import email
import email.header
import email.policy
import quopri
import base64
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

PROFILES_ROOT = Path.home() / "Library" / "Thunderbird" / "Profiles"
LINUX_PROFILES_ROOT = Path.home() / ".thunderbird"

# Thunderbird sidecar files that are not mail.
SKIP_SUFFIXES = {".msf", ".dat", ".html", ".sqlite", ".json", ".log"}
SKIP_NAMES = {"nstmp", "filterlog.html", "msgFilterRules.dat"}


def profile_dir() -> Path:
    root = PROFILES_ROOT if PROFILES_ROOT.exists() else LINUX_PROFILES_ROOT
    if not root.exists():
        raise SystemExit(f"no Thunderbird profile directory at {PROFILES_ROOT} or {LINUX_PROFILES_ROOT}")
    candidates = [p for p in root.iterdir() if p.is_dir()]
    if not candidates:
        raise SystemExit(f"no profiles under {root}")
    # Prefer the default-release profile when several exist.
    for p in candidates:
        if "default-release" in p.name:
            return p
    return candidates[0]


def accounts(profile: Path) -> List[dict]:
    """Map each configured address to the directory holding its mbox files."""
    prefs = (profile / "prefs.js")
    if not prefs.exists():
        return []
    text = prefs.read_text(errors="replace")

    def pref(name: str) -> Optional[str]:
        m = re.search(rf'user_pref\("{re.escape(name)}",\s*"([^"]*)"\)', text)
        return m.group(1) if m else None

    found = []
    for server in sorted(set(re.findall(r'mail\.server\.(server\d+)\.', text))):
        user = pref(f"mail.server.{server}.userName")
        rel = pref(f"mail.server.{server}.directory-rel")
        host = pref(f"mail.server.{server}.hostname")
        if not user or not rel:
            continue
        path = profile / rel.replace("[ProfD]", "").lstrip("/")
        found.append({"server": server, "address": user, "hostname": host,
                      "path": path, "exists": path.exists()})
    return found


def mbox_files(root: Path) -> Iterator[Path]:
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix in SKIP_SUFFIXES or p.name in SKIP_NAMES:
            continue
        if p.stat().st_size == 0:
            continue
        yield p


# A message starts at a line beginning "From " at position 0 of the line.
_FROM_LINE = re.compile(rb"^From (?:\s|\S)*?\n", re.MULTILINE)


def iter_messages(path: Path, chunk_size: int = 8 << 20) -> Iterator[bytes]:
    """
    Stream raw RFC-822 messages out of an mbox file.

    Splits on lines starting with "From " at column 0. That is the mbox
    convention and it is imperfect — a body line reading "From here to there"
    can false-split — but Thunderbird escapes those as ">From " on write, so in
    practice this is reliable for its own stores.
    """
    buf = b""
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"\n(?=From )", buf)
            # Last part may be incomplete; hold it back.
            buf = parts.pop() if parts else b""
            for part in parts:
                if part.strip():
                    yield part
    if buf.strip():
        yield buf


def _decode_header(raw: Optional[str]) -> str:
    if not raw:
        return ""
    try:
        parts = email.header.decode_header(raw)
        out = []
        for text, charset in parts:
            if isinstance(text, bytes):
                out.append(text.decode(charset or "utf-8", errors="replace"))
            else:
                out.append(text)
        decoded = "".join(out)
    except Exception:
        decoded = raw
    # Collapse folded whitespace. Long filenames are split across physical lines
    # by RFC 2231 continuation, so a decoded filename can contain a literal
    # newline: "Zavcer Gregor Lipoprotein A\n 27.7.23.pdf". That silently defeats
    # any caller regex using `.` or anchoring with `$` — `.` does not cross a
    # newline — so an extension filter drops exactly the documents with the
    # longest, most descriptive names. It reported "22 attachments" and looked
    # complete while omitting the omega-3 and Lipoprotein(a) reports.
    return re.sub(r"\s+", " ", decoded).strip()


def body_text(msg: email.message.Message, max_bytes: int = 400_000) -> str:
    """
    Best-effort plain text for a message, decoded and charset-normalised.

    Transfer encodings matter here: a password sent in a base64 HTML part is
    invisible to a raw grep of the mbox, which is the whole reason this exists.
    """
    chunks: List[str] = []
    try:
        parts = msg.walk() if msg.is_multipart() else [msg]
    except Exception:
        return ""
    for part in parts:
        ctype = (part.get_content_type() or "").lower()
        if not ctype.startswith("text/"):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if payload is None:
            raw = part.get_payload()
            if not isinstance(raw, str):
                continue
            enc = (part.get("Content-Transfer-Encoding") or "").lower()
            try:
                if enc == "base64":
                    payload = base64.b64decode(raw + "===")
                elif enc == "quoted-printable":
                    payload = quopri.decodestring(raw)
                else:
                    payload = raw.encode("utf-8", errors="replace")
            except Exception:
                continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, AttributeError):
            text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
        if ctype == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
        chunks.append(text)
        if sum(len(c) for c in chunks) > max_bytes:
            break
    return "\n".join(chunks)


@dataclass
class Hit:
    folder: str
    date: str
    sender: str
    to: str
    subject: str
    snippets: List[str]
    attachments: List[str]


def search(root: Path, from_re=None, to_re=None, subject_re=None, body_re=None,
           context: int = 0, limit: int = 50, folder_re=None) -> List[Hit]:
    hits: List[Hit] = []
    for path in mbox_files(root):
        folder = str(path.relative_to(root))
        if folder_re and not folder_re.search(folder):
            continue
        for raw in iter_messages(path):
            if len(hits) >= limit:
                return hits
            # Cheap pre-filter on the raw bytes before the expensive parse.
            if from_re or subject_re or body_re or to_re:
                head = raw[:4000]
                if from_re and not from_re.search(head.decode("utf-8", "replace")):
                    # Sender may be encoded; fall through to a full parse only
                    # if the domain appears anywhere in the message at all.
                    if not from_re.search(raw.decode("utf-8", "replace")):
                        continue
            try:
                msg = email.message_from_bytes(raw, policy=email.policy.compat32)
            except Exception:
                continue

            sender = _decode_header(msg.get("From"))
            to = _decode_header(msg.get("To"))
            subject = _decode_header(msg.get("Subject"))
            date = _decode_header(msg.get("Date"))

            if from_re and not from_re.search(sender):
                continue
            if to_re and not to_re.search(to):
                continue
            if subject_re and not subject_re.search(subject):
                continue

            snippets: List[str] = []
            if body_re:
                text = body_text(msg)
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    if body_re.search(line):
                        lo = max(0, i - context)
                        hi = min(len(lines), i + context + 1)
                        snippets.append("\n".join(l.strip() for l in lines[lo:hi] if l.strip()))
                    if len(snippets) >= 6:
                        break
                if not snippets:
                    continue

            attachments = []
            try:
                for part in (msg.walk() if msg.is_multipart() else []):
                    fn = part.get_filename()
                    if fn:
                        attachments.append(_decode_header(fn))
            except Exception:
                pass

            hits.append(Hit(folder, date, sender, to, subject, snippets, attachments))
    return hits


def extract(args, accs) -> int:
    """
    Save attachments out of the local mbox stores.

    Medical reports arrive as email attachments and stay there. Recovering them
    from the mailbox is more reliable than hunting for whatever copy was saved to
    disk years ago — and unlike a password-protected archive assembled later, the
    original attachments are exactly what the lab sent.

    Filenames are sanitised and de-duplicated rather than overwritten: two labs
    both sending "izvid.pdf" must not collapse into one file.
    """
    out = Path(args.out).expanduser()
    targets = [a for a in accs if a["exists"]]
    if args.account:
        targets = [a for a in targets if args.account.lower() in a["address"].lower()]
    if not targets:
        print("no matching cached account", file=sys.stderr)
        return 1

    def rx(v):
        return re.compile(v, re.IGNORECASE) if v else None

    from_re, subject_re, name_re = rx(args.from_), rx(args.subject), rx(args.name)
    written, seen, skipped = 0, set(), 0

    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)

    for a in targets:
        for path in mbox_files(a["path"]):
            for raw in iter_messages(path):
                if written >= args.limit:
                    break
                if from_re and not from_re.search(raw[:4000].decode("utf-8", "replace")):
                    if not from_re.search(raw.decode("utf-8", "replace")):
                        continue
                try:
                    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
                except Exception:
                    continue
                if from_re and not from_re.search(_decode_header(msg.get("From"))):
                    continue
                if subject_re and not subject_re.search(_decode_header(msg.get("Subject"))):
                    continue
                if not msg.is_multipart():
                    continue

                date = _decode_header(msg.get("Date"))
                for part in msg.walk():
                    fn = _decode_header(part.get_filename() or "")
                    if not fn:
                        continue
                    if name_re and not name_re.search(fn):
                        continue
                    try:
                        payload = part.get_payload(decode=True)
                    except Exception:
                        payload = None
                    if not payload:
                        continue

                    safe = re.sub(r"[^\w.\-() ]+", "_", fn).strip() or "attachment.bin"
                    # Prefix with the send date so ordering is chronological and
                    # two labs sending "izvid.pdf" cannot collide.
                    stamp = ""
                    m = re.search(r"(\d{1,2}) (\w{3}) (\d{4})", date)
                    if m:
                        months = dict(Jan="01", Feb="02", Mar="03", Apr="04", May="05", Jun="06",
                                      Jul="07", Aug="08", Sep="09", Oct="10", Nov="11", Dec="12")
                        stamp = f"{m.group(3)}-{months.get(m.group(2), '00')}-{int(m.group(1)):02d}_"
                    target = out / f"{stamp}{safe}"
                    n = 1
                    while str(target) in seen or (target.exists() and not args.dry_run):
                        target = out / f"{stamp}{Path(safe).stem}_{n}{Path(safe).suffix}"
                        n += 1
                    seen.add(str(target))

                    if args.dry_run:
                        print(f"  would write {target.name}  ({len(payload):,} bytes)")
                    else:
                        target.write_bytes(payload)
                        print(f"  {target.name}  ({len(payload):,} bytes)")
                    written += 1

    print(f"\n{written} attachment(s){' (dry run)' if args.dry_run else f' -> {out}'}",
          file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("accounts", help="list configured accounts and their mbox paths")

    s = sub.add_parser("search", help="search cached mail")
    s.add_argument("--account", help="address, or substring of one (default: all)")
    s.add_argument("--from", dest="from_", help="regex against the From header")
    s.add_argument("--to", help="regex against the To header")
    s.add_argument("--subject", help="regex against the Subject header")
    s.add_argument("--body", help="regex against decoded body text")
    s.add_argument("--folder", help="regex against the folder path")
    s.add_argument("--context", type=int, default=0, help="body lines of context around a match")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--show-attachments", action="store_true")

    x = sub.add_parser("extract", help="save matching attachments to a directory")
    x.add_argument("--account", help="address, or substring of one")
    x.add_argument("--from", dest="from_", help="regex against the From header")
    x.add_argument("--subject", help="regex against the Subject header")
    x.add_argument("--name", help="regex against the attachment filename")
    x.add_argument("--out", required=True, help="destination directory")
    x.add_argument("--limit", type=int, default=200)
    x.add_argument("--dry-run", action="store_true",
                   help="list what would be written without writing it")

    args = ap.parse_args()
    profile = profile_dir()
    accs = accounts(profile)

    if args.cmd == "accounts":
        for a in accs:
            mark = "" if a["exists"] else "   (no local cache)"
            print(f"{a['address']:32} {a['hostname'] or '?':24} {a['path']}{mark}")
        return 0

    if args.cmd == "extract":
        return extract(args, accs)

    targets = [a for a in accs if a["exists"]]
    if args.account:
        targets = [a for a in targets if args.account.lower() in a["address"].lower()]
        if not targets:
            print(f"no cached account matching {args.account!r}", file=sys.stderr)
            return 1

    def rx(v):
        return re.compile(v, re.IGNORECASE) if v else None

    total = 0
    for a in targets:
        hits = search(
            a["path"],
            from_re=rx(args.from_), to_re=rx(args.to),
            subject_re=rx(args.subject), body_re=rx(args.body),
            folder_re=rx(args.folder),
            context=args.context, limit=args.limit,
        )
        for h in hits:
            total += 1
            print("=" * 78)
            print(f"[{a['address']}] {h.folder}")
            print(f"date    : {h.date}")
            print(f"from    : {h.sender}")
            print(f"to      : {h.to}")
            print(f"subject : {h.subject}")
            if args.show_attachments and h.attachments:
                print(f"attach  : {', '.join(h.attachments)}")
            for sn in h.snippets:
                print("  ---")
                for line in sn.splitlines():
                    print(f"  {line}")
    print(f"\n{total} message(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
