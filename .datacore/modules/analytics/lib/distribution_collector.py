#!/usr/bin/env python3
"""Daily distribution metrics collector and the Friday results table.

Collect (default): reads the content ledger and writes one dated snapshot,
<out_dir>/YYYY-MM-DD.json, with what each source reported today:
  - npm downloads per package (last day, last week, and the daily series
    npm returns for the last month);
  - GitHub stars, and traffic (views, clones, top referrers, top paths) per
    repository through `gh api`; traffic needs push access, else "no access";
  - dev.to public reactions and comments per article, per user (public API);
  - YouTube view counts for the ledger's videos, read from the public watch
    page (no API key);
  - PostHog page views per ledger URL on the configured sites, daily for the
    last 14 days, through the analytics module's query_hogql helper.
Every source records a status ("ok", "partial", "no access", "error ...").
A failed source is recorded as failed; nothing is estimated.

Report (--report [--date YYYY-MM-DD]): prints the results table as markdown,
this week against last week, per ledger piece plus totals. The week is the
seven days ending on the report date (default: yesterday, the last full day).
Daily series come from the newest snapshot; cumulative counts (stars,
reactions, views) are diffed against the snapshots taken one and two weeks
earlier. A missing snapshot or a failed source shows "n/a", never a guess.

Read-only towards every platform. Credentials come only from the broker
(`creds get ... --consumer distribution-collector`); a refusal is recorded as
"no access". User data (the ledger, the snapshots, which packages and repos
to track) lives in the space, named by a JSON config:

  --config PATH or $DISTRIBUTION_CONFIG, e.g.
  {"ledger": "../campaigns/x/content-ledger.csv",   # relative to the config
   "out_dir": ".",
   "npm_packages": ["@scope/pkg"], "github_repos": ["org/repo"],
   "devto_users": ["user"], "posthog_sites": {"example.com": "PostHog project name"}}

Ledger columns: id, date, brand (plur|datafund), channel, url, title,
type (article|post|video|reference|release|listing|ad), event (yes|no:
placed in an audience we don't own), cost_eur (blank = no spend recorded), notes.

Also: --decode-x URL_OR_ID ... prints the UTC time encoded in an X status ID.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

USER_AGENT = "datacore-distribution-collector/0.1"
CONSUMER = "distribution-collector"
TIMEOUT = 20

# --- X status IDs -----------------------------------------------------------

X_EPOCH_MS = 1288834974657  # snowflake epoch, 2010-11-04 01:42:54.657 UTC


def x_status_datetime(url_or_id: str) -> dt.datetime:
    """UTC time encoded in an X (Twitter) status ID, from a URL or a bare ID."""
    s = str(url_or_id).strip()
    m = re.search(r"/status(?:es)?/(\d+)", s)
    if m:
        sid = int(m.group(1))
    elif re.fullmatch(r"\d+", s):
        sid = int(s)
    else:
        raise ValueError(f"not an X status URL or ID: {s!r}")
    ms = sid >> 22
    if ms <= 0:
        raise ValueError(f"ID {sid} predates snowflake IDs and carries no timestamp")
    return dt.datetime.fromtimestamp((ms + X_EPOCH_MS) / 1000, tz=dt.timezone.utc)


# --- ledger -----------------------------------------------------------------

LEDGER_COLUMNS = ["id", "date", "brand", "channel", "url", "title", "type", "event", "cost_eur", "notes"]
BRANDS = {"plur", "datafund"}
TYPES = {"article", "post", "video", "reference", "release", "listing", "ad"}


class LedgerError(ValueError):
    pass


def parse_ledger(path) -> list[dict]:
    """Parse and validate the content ledger. Raises LedgerError naming every bad row."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != LEDGER_COLUMNS:
            raise LedgerError(f"{path}: columns must be {','.join(LEDGER_COLUMNS)}; found {reader.fieldnames}")
        rows, errors, seen = [], [], set()
        for n, raw in enumerate(reader, start=2):
            r = {k: (raw.get(k) or "").strip() for k in LEDGER_COLUMNS}
            rid = r["id"] or f"line {n}"
            problems = []
            if not r["id"]:
                problems.append("missing id")
            elif r["id"] in seen:
                problems.append("duplicate id")
            seen.add(r["id"])
            try:
                date = dt.date.fromisoformat(r["date"])
            except ValueError:
                date = None
                problems.append(f"bad date {r['date']!r}")
            if r["brand"] not in BRANDS:
                problems.append(f"brand {r['brand']!r} not in {sorted(BRANDS)}")
            if not r["url"].startswith(("https://", "http://")):
                problems.append("missing url")
            if r["type"] not in TYPES:
                problems.append(f"type {r['type']!r} not in {sorted(TYPES)}")
            if r["event"] not in ("yes", "no"):
                problems.append(f"event {r['event']!r} must be yes or no")
            cost = None
            if r["cost_eur"]:
                try:
                    cost = float(r["cost_eur"])
                except ValueError:
                    problems.append(f"cost_eur {r['cost_eur']!r} is not a number")
            if not r["channel"]:
                problems.append("missing channel")
            if problems:
                errors.append(f"{rid} (line {n}): " + "; ".join(problems))
                continue
            rows.append({**r, "date": date, "event": r["event"] == "yes", "cost_eur": cost})
    if errors:
        raise LedgerError(f"{path}: {len(errors)} bad row(s):\n  " + "\n  ".join(errors))
    return rows


# --- URL helpers ------------------------------------------------------------

def url_host(url: str) -> str:
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def norm_url(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    path = p.path.rstrip("/") or "/"
    return f"{url_host(url)}{path}"


YT_ID = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/))([A-Za-z0-9_-]{11})")


def youtube_id(url: str) -> str | None:
    m = YT_ID.search(url)
    return m.group(1) if m else None


# --- collection -------------------------------------------------------------

def _get(url: str, accept="application/json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                               "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _get_json(url: str):
    return json.loads(_get(url))


def _status(parts: list[str]) -> str:
    ok = sum(1 for p in parts if p == "ok")
    if parts and ok == len(parts):
        return "ok"
    if ok:
        return "partial"
    if parts and all(p == "no access" for p in parts):
        return "no access"
    return "error"


def collect_npm(packages: list[str]) -> dict:
    out = {}
    for pkg in packages:
        try:
            d = _get_json("https://api.npmjs.org/downloads/range/last-month/" + urllib.parse.quote(pkg, safe="@"))
            daily = {x["day"]: x["downloads"] for x in d.get("downloads", [])}
            days = sorted(daily)
            out[pkg] = {"status": "ok", "window_start": d.get("start"), "window_end": d.get("end"),
                        "last_day": daily[days[-1]] if days else 0,
                        "last_week": sum(daily[k] for k in days[-7:]),
                        "daily": daily}
        except (urllib.error.URLError, ValueError, KeyError) as e:
            out[pkg] = {"status": f"error: {type(e).__name__} {getattr(e, 'code', '')}".strip()}
    return {"status": _status([v["status"] for v in out.values()]), "packages": out}


def _gh(path: str):
    """Read-only `gh api` GET. Returns (json, None) or (None, status)."""
    try:
        p = subprocess.run(["gh", "api", "-X", "GET", path], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"error: {type(e).__name__}"
    if p.returncode != 0:
        err = (p.stderr or "") + (p.stdout or "")
        if "403" in err or "push access" in err.lower() or "Resource not accessible" in err:
            return None, "no access"
        if "404" in err:
            return None, "error: not found"
        return None, "error: gh exit %d" % p.returncode
    try:
        return json.loads(p.stdout), None
    except json.JSONDecodeError:
        return None, "error: bad json"


def _gh_series(d: dict, key: str) -> dict:
    daily = {x["timestamp"][:10]: x["count"] for x in d.get(key, [])}
    uniques = {x["timestamp"][:10]: x["uniques"] for x in d.get(key, [])}
    out = {"count": d.get("count"), "uniques": d.get("uniques"), "daily": daily, "daily_uniques": uniques}
    if daily:  # the days GitHub actually returned (14 full days, ending yesterday as observed)
        out["window_start"], out["window_end"] = min(daily), max(daily)
    return out


def collect_github(repos: list[str], today: dt.date) -> dict:
    out = {}
    for repo in repos:
        info, err = _gh(f"repos/{repo}")
        entry = {"status": "ok" if info else err, "stars": info.get("stargazers_count") if info else None}
        # fallback window for a series that came back empty: GitHub's 14 days ending yesterday
        traffic = {"window_start": (today - dt.timedelta(days=14)).isoformat(),
                   "window_end": (today - dt.timedelta(days=1)).isoformat()}
        parts = []
        for name, path in (("views", "traffic/views"), ("clones", "traffic/clones"),
                           ("referrers", "traffic/popular/referrers"), ("paths", "traffic/popular/paths")):
            d, e = _gh(f"repos/{repo}/{path}")
            parts.append("ok" if e is None else e)
            if e is None:
                traffic[name] = _gh_series(d, name) if name in ("views", "clones") else d
        traffic["status"] = _status(parts)
        entry["traffic"] = traffic
        out[repo] = entry
    parts = [v["status"] for v in out.values()] + [v["traffic"]["status"] for v in out.values()]
    return {"status": _status(parts), "repos": out}


def collect_devto(users: list[str]) -> dict:
    out = {}
    for user in users:
        try:
            arts = _get_json(f"https://dev.to/api/articles?username={urllib.parse.quote(user)}&per_page=1000")
            out[user] = {"status": "ok", "articles": [
                {"url": a.get("url"), "canonical_url": a.get("canonical_url"), "title": a.get("title"),
                 "published_at": a.get("published_at"), "reactions": a.get("public_reactions_count"),
                 "comments": a.get("comments_count")} for a in arts]}
        except (urllib.error.URLError, ValueError) as e:
            out[user] = {"status": f"error: {type(e).__name__} {getattr(e, 'code', '')}".strip()}
    return {"status": _status([v["status"] for v in out.values()]), "users": out}


def collect_youtube(video_ids: list[str]) -> dict:
    out = {}
    for vid in video_ids:
        try:
            html = _get(f"https://www.youtube.com/watch?v={vid}", accept="text/html").decode("utf-8", "ignore")
            m = re.search(r'"videoDetails":\{.*?"viewCount":"(\d+)"', html, re.S) or \
                re.search(r'"viewCount":"(\d+)"', html)
            if m:
                out[vid] = {"status": "ok", "views": int(m.group(1)), "source": "public watch page"}
            else:
                out[vid] = {"status": "n/a", "reason": "no view count on the public page (private, removed, or page changed)"}
        except urllib.error.URLError as e:
            out[vid] = {"status": f"error: {type(e).__name__} {getattr(e, 'code', '')}".strip()}
    return {"status": _status([v["status"] for v in out.values()]) if out else "ok", "videos": out}


def broker_get(var: str) -> tuple[str | None, str]:
    """Ask the credential broker. Returns (value, note). The value is never printed."""
    try:
        p = subprocess.run(["creds", "get", var, "--consumer", CONSUMER],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"broker unavailable ({type(e).__name__})"
    value = p.stdout.strip()
    if p.returncode != 0 or not value:
        return None, f"broker refused (exit {p.returncode})"
    return value, "ok"


def collect_posthog(sites: dict, ledger: list[dict], today: dt.date) -> dict:
    start = today - dt.timedelta(days=14)
    base = {"sites": sorted(sites), "window_start": start.isoformat(),
            "window_end": (today - dt.timedelta(days=1)).isoformat()}
    key, note = broker_get("POSTHOG_API_KEY")
    if not key:
        return {**base, "status": "no access", "reason": note}
    try:
        import posthog_daily  # the analytics module's helper
    except Exception as e:  # noqa: BLE001 - report, do not crash the run
        return {**base, "status": f"error: helper import failed ({type(e).__name__})"}
    projects = posthog_daily.load_projects()
    urls, site_status = {}, {}
    for host, project_name in sites.items():
        pid = projects.get(project_name)
        pieces = [r for r in ledger if url_host(r["url"]) == host]
        if pid is None:
            site_status[host] = f"error: no PostHog project named {project_name!r} configured"
            continue
        if not pieces:
            site_status[host] = "ok"
            continue
        paths = set()
        for r in pieces:
            p = urllib.parse.urlsplit(r["url"]).path or "/"
            paths.update({p, p.rstrip("/") or "/", (p.rstrip("/") + "/")})
        in_list = ", ".join("'" + p.replace("\\", "\\\\").replace("'", "\\'") + "'" for p in sorted(paths))
        q = f"""
            SELECT properties.$host, properties.$pathname, toString(toDate(timestamp)), count()
            FROM events
            WHERE event = '$pageview'
              AND timestamp >= toDateTime('{start.isoformat()} 00:00:00')
              AND timestamp < toDateTime('{today.isoformat()} 00:00:00')
              AND properties.$pathname IN ({in_list})
            GROUP BY 1, 2, 3
        """
        try:
            rows = posthog_daily.query_hogql(key, pid, q, raise_errors=True)
        except Exception as e:  # noqa: BLE001
            site_status[host] = f"error: query failed ({type(e).__name__} {getattr(e, 'code', '')})".strip()
            continue
        by_norm = {norm_url(r["url"]): r["url"] for r in pieces}
        for r in pieces:
            urls.setdefault(r["url"], {"daily": {}, "last_7d": 0})
        for h, path, day, n in rows:
            target = by_norm.get(norm_url(f"https://{(h or host)}{path or '/'}"))
            if not target:
                continue
            d = urls[target]["daily"]
            d[day] = d.get(day, 0) + int(n)
        site_status[host] = "ok"
    week_start = (today - dt.timedelta(days=7)).isoformat()
    for v in urls.values():
        v["last_7d"] = sum(n for day, n in v["daily"].items() if day >= week_start)
    return {**base, "status": _status(list(site_status.values())), "site_status": site_status, "urls": urls}


def collect(config: dict, ledger: list[dict], today: dt.date) -> dict:
    videos = []
    for r in ledger:
        vid = youtube_id(r["url"])
        if vid and vid not in videos:
            videos.append(vid)
    return {
        "date": today.isoformat(),
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ledger_rows": len(ledger),
        "sources": {
            "npm": collect_npm(config.get("npm_packages", [])),
            "github": collect_github(config.get("github_repos", []), today),
            "devto": collect_devto(config.get("devto_users", [])),
            "youtube": collect_youtube(videos),
            "posthog": collect_posthog(config.get("posthog_sites", {}), ledger, today),
        },
    }


# --- report -----------------------------------------------------------------

class NoSnapshotError(RuntimeError):
    pass


TOLERANCE_DAYS = 2


def _pick(snapshots: dict, target: dt.date):
    """Newest snapshot dated on or before target, at most TOLERANCE_DAYS older."""
    best = None
    for k in snapshots:
        d = dt.date.fromisoformat(k)
        if target - dt.timedelta(days=TOLERANCE_DAYS) <= d <= target and (best is None or d > best):
            best = d
    return (best, snapshots[best.isoformat()]) if best else (None, None)


def _days_between(a: str, b: str) -> list[str]:
    d, e = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _merged_series(snapshots: dict, upto: dt.date, extract):
    """Merge one daily series across every snapshot dated on or before `upto`.

    extract(snapshot) -> {"daily": {...}, "window_start", "window_end"} or None
    (source failed or did not measure it). Inside a snapshot's window a day
    without an entry is a real 0; newer snapshots override older ones.
    Returns {"daily", "covered"} or None when no snapshot measured it.
    """
    daily, covered = {}, set()
    for key in sorted(snapshots):
        if dt.date.fromisoformat(key) > upto:
            continue
        ser = extract(snapshots[key])
        if not ser or ser.get("daily") is None:
            continue
        d = ser["daily"]
        ws = ser.get("window_start") or (min(d) if d else None)
        we = ser.get("window_end") or (max(d) if d else None)
        if not ws or not we:
            continue
        for day in _days_between(ws, we):
            covered.add(day)
            daily[day] = d.get(day, 0)
    return {"daily": daily, "covered": covered} if covered else None


def _window_sum(merged: dict | None, start: dt.date, end: dt.date):
    """Sum over [start, end], or None when any day of it was not measured."""
    if not merged:
        return None
    days = _days_between(start.isoformat(), end.isoformat())
    if any(d not in merged["covered"] for d in days):
        return None
    return sum(merged["daily"].get(d, 0) for d in days)


def _ok(src: dict | None) -> bool:
    return bool(src) and src.get("status") in ("ok", "partial")


def _devto_article(snap, url):
    src = (snap or {}).get("sources", {}).get("devto")
    if not _ok(src):
        return None, False
    key = norm_url(url)
    for user in src.get("users", {}).values():
        if user.get("status") != "ok":
            continue
        for a in user.get("articles", []):
            if a.get("url") and norm_url(a["url"]) == key:
                return a, True
    return None, all(u.get("status") == "ok" for u in src.get("users", {}).values())


def _cumulative(snapshots, target: dt.date, piece_date: dt.date | None, getter):
    """Value of a cumulative count as of the snapshot nearest target.

    getter(snapshot) -> (value or None, source_ok). A piece published on or
    after the snapshot date (or after the target when there is no snapshot)
    did not exist yet, so its count was a real 0.
    """
    d, snap = _pick(snapshots, target)
    if snap is None:
        return 0 if piece_date and piece_date >= target else None
    value, source_ok = getter(snap)
    if value is not None:
        return value
    if source_ok and piece_date and piece_date >= d:
        return 0
    return None


def _diff(a, b):
    return None if a is None or b is None else a - b


def _fmt(v):
    if v is None:
        return "n/a"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(round(v, 2) if isinstance(v, float) else v)


def _fmt_change(v):
    if v is None:
        return "n/a"
    return ("+" if v > 0 else "") + _fmt(v)


def build_report(ledger: list[dict], snapshots: dict, report_date: dt.date) -> str:
    D = report_date
    this_start, this_end = D - dt.timedelta(days=6), D
    last_start, last_end = D - dt.timedelta(days=13), D - dt.timedelta(days=7)
    # A snapshot dated S describes the end of day S-1, so "as of D" is S = D+1.
    now_d, now = _pick(snapshots, D + dt.timedelta(days=1))
    if now is None:
        raise NoSnapshotError(f"no snapshot within {TOLERANCE_DAYS} days before {D + dt.timedelta(days=1)}")
    t_now, t_wk, t_2wk = (D + dt.timedelta(days=1), D - dt.timedelta(days=6), D - dt.timedelta(days=13))
    wk_d, _ = _pick(snapshots, t_wk)
    wk2_d, _ = _pick(snapshots, t_2wk)
    src = now.get("sources", {})
    posthog = src.get("posthog") or {}
    site_hosts = set(posthog.get("sites") or [url_host(k) for k in posthog.get("urls", {})])

    def merged(extract):
        return _merged_series(snapshots, now_d, extract)

    def ph_extract(url):
        key = norm_url(url)

        def ex(snap):
            ph = snap.get("sources", {}).get("posthog") or {}
            if not _ok(ph):
                return None
            for k, v in (ph.get("urls") or {}).items():
                if norm_url(k) == key:  # a URL the snapshot did not query is not a 0
                    return {"daily": v.get("daily", {}), "window_start": ph.get("window_start"),
                            "window_end": ph.get("window_end")}
            return None
        return ex

    def weekly_cum(piece, getter):
        vals = [_cumulative(snapshots, t, piece["date"], getter) for t in (t_now, t_wk, t_2wk)]
        return _diff(vals[0], vals[1]), _diff(vals[1], vals[2])

    rows, totals = [], {}
    for p in ledger:
        host = url_host(p["url"])
        measures = []
        if host in site_hosts:
            series = merged(ph_extract(p["url"]))
            measures.append(("page views", _window_sum(series, this_start, this_end),
                             _window_sum(series, last_start, last_end), f"Page views on ledger pieces, {host}"))
        elif host == "dev.to":
            for field, label in (("reactions", "dev.to reactions"), ("comments", "dev.to comments")):
                def g(snap, field=field):
                    a, ok = _devto_article(snap, p["url"])
                    return (a.get(field) if a else None), ok
                tw, lw = weekly_cum(p, g)
                measures.append((label, tw, lw, f"{label} on ledger pieces"))
        elif youtube_id(p["url"]):
            vid = youtube_id(p["url"])

            def g(snap, vid=vid):
                y = snap.get("sources", {}).get("youtube") or {}
                v = (y.get("videos") or {}).get(vid) or {}
                return (v.get("views") if v.get("status") == "ok" else None), False
            tw, lw = weekly_cum(p, g)
            measures.append(("YouTube views", tw, lw, "YouTube views on ledger pieces"))
        else:
            measures.append(("not measured", None, None, None))
        for name, tw, lw, total_key in measures:
            rows.append([p["id"], f"[{p['title']}]({p['url']})", p["date"].isoformat(), p["brand"], p["channel"],
                         name, _fmt(tw), _fmt(lw), _fmt_change(_diff(tw, lw))])
            if total_key:
                t = totals.setdefault(total_key, {"tw": 0, "lw": 0, "n": 0, "k_tw": 0, "k_lw": 0})
                t["n"] += 1
                if tw is not None:
                    t["tw"] += tw
                    t["k_tw"] += 1
                if lw is not None:
                    t["lw"] += lw
                    t["k_lw"] += 1

    trows = []

    def add(label, tw, lw):
        trows.append([label, _fmt(tw), _fmt(lw), _fmt_change(_diff(tw, lw))])

    npm = src.get("npm") or {}
    for pkg in (npm.get("packages") or {}):
        def npm_ex(snap, pkg=pkg):
            src_npm = snap.get("sources", {}).get("npm") or {}
            s = (src_npm.get("packages") or {}).get(pkg) or {}
            return s if s.get("status", src_npm.get("status")) == "ok" else None
        series = merged(npm_ex)
        add(f"npm {pkg} downloads", _window_sum(series, this_start, this_end),
            _window_sum(series, last_start, last_end))
    gh = src.get("github") or {}
    for repo, s in (gh.get("repos") or {}).items():
        def stars(snap, repo=repo):
            r = ((snap.get("sources", {}).get("github") or {}).get("repos") or {}).get(repo) or {}
            return r.get("stars"), False
        vals = [_cumulative(snapshots, t, None, stars) for t in (t_now, t_wk, t_2wk)]
        add(f"GitHub {repo} stars gained", _diff(vals[0], vals[1]), _diff(vals[1], vals[2]))
        for kind in ("views", "clones"):
            def gh_ex(snap, repo=repo, kind=kind):
                r = ((snap.get("sources", {}).get("github") or {}).get("repos") or {}).get(repo) or {}
                tr = r.get("traffic") or {}
                ser = tr.get(kind)
                if ser is None or tr.get("status") not in ("ok", "partial"):
                    return None
                return {"window_start": tr.get("window_start"), "window_end": tr.get("window_end"),
                        **{k: v for k, v in ser.items() if v is not None}}
            series = merged(gh_ex)
            add(f"GitHub {repo} {kind}", _window_sum(series, this_start, this_end),
                _window_sum(series, last_start, last_end))
    for key in sorted(totals):
        t = totals[key]
        label = key if t["k_tw"] == t["n"] else f"{key} ({t['k_tw']} of {t['n']} measured)"
        add(label, t["tw"] if t["k_tw"] else None, t["lw"] if t["k_lw"] else None)

    def in_week(r, a, b):
        return a <= r["date"] <= b
    add("Pieces published", sum(in_week(r, this_start, this_end) for r in ledger),
        sum(in_week(r, last_start, last_end) for r in ledger))
    add("Distribution events", sum(r["event"] and in_week(r, this_start, this_end) for r in ledger),
        sum(r["event"] and in_week(r, last_start, last_end) for r in ledger))
    add("Spend (EUR)", sum((r["cost_eur"] or 0) for r in ledger if in_week(r, this_start, this_end)),
        sum((r["cost_eur"] or 0) for r in ledger if in_week(r, last_start, last_end)))

    def fmt_d(d):
        return f"{d.strftime('%a')} {d.day} {d.strftime('%b %Y')}"

    out = [f"# Results table: week ending {fmt_d(D)}", "",
           f"- This week: {this_start.isoformat()} to {this_end.isoformat()}. Last week: {last_start.isoformat()} to {last_end.isoformat()}.",
           f"- Snapshots used: now {now_d}, a week earlier {wk_d or 'missing'}, two weeks earlier {wk2_d or 'missing'}.",
           "- Daily series (page views, npm, GitHub traffic) come from the newest snapshot; cumulative counts "
           "(stars, dev.to, YouTube) are differences between snapshots. n/a means the source failed or a snapshot is missing.",
           "", "## Totals", "", "| Measure | This week | Last week | Change |", "|---|---|---|---|"]
    out += ["| " + " | ".join(r) + " |" for r in trows]
    out += ["", "## Per piece", "",
            "| Id | Piece | Date | Brand | Channel | Measure | This week | Last week | Change |",
            "|---|---|---|---|---|---|---|---|---|"]
    out += ["| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in rows]
    refs = []
    for repo, s in (gh.get("repos") or {}).items():
        tr = s.get("traffic") or {}
        if tr.get("referrers"):
            refs.append(f"- {repo} referrers: " + ", ".join(f"{x['referrer']} {x['count']}" for x in tr["referrers"][:10]))
        if tr.get("paths"):
            refs.append(f"- {repo} paths: " + ", ".join(f"{x['path']} {x['count']}" for x in tr["paths"][:10]))
    if refs:
        out += ["", "## GitHub top referrers and paths (last 14 days, as GitHub reports them)", ""] + refs
    out += ["", f"## Sources in the {now_d} snapshot", ""] + source_lines(now)
    return "\n".join(out) + "\n"


def source_lines(snap: dict) -> list[str]:
    s = snap.get("sources", {})
    lines = []
    npm = s.get("npm") or {}
    lines.append(f"- npm: {npm.get('status', 'missing')}" + "".join(
        f"; {k} {v.get('status')}" for k, v in (npm.get("packages") or {}).items() if v.get("status") != "ok"))
    gh = s.get("github") or {}
    lines.append(f"- GitHub: {gh.get('status', 'missing')}" + "".join(
        f"; {k} stars {v.get('status')}, traffic {(v.get('traffic') or {}).get('status')}"
        for k, v in (gh.get("repos") or {}).items()))
    dv = s.get("devto") or {}
    lines.append(f"- dev.to: {dv.get('status', 'missing')}" + "".join(
        f"; {k} {v.get('status')} ({len(v.get('articles', []))} articles)" for k, v in (dv.get("users") or {}).items()))
    yt = s.get("youtube") or {}
    lines.append(f"- YouTube (public watch page): {yt.get('status', 'missing')}" + "".join(
        f"; {k} {v.get('status')}" + (f" ({v.get('reason')})" if v.get("reason") else "")
        for k, v in (yt.get("videos") or {}).items()))
    ph = s.get("posthog") or {}
    detail = ph.get("reason") or "; ".join(f"{k} {v}" for k, v in (ph.get("site_status") or {}).items())
    lines.append(f"- PostHog: {ph.get('status', 'missing')}" + (f" ({detail})" if detail else ""))
    lines.append("- X and LinkedIn: not measured (no metrics source).")
    return lines


# --- CLI --------------------------------------------------------------------

SNAPSHOT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def load_snapshots(out_dir: Path) -> dict:
    snaps = {}
    for p in sorted(Path(out_dir).glob("*.json")):
        if SNAPSHOT_NAME.match(p.name):
            snaps[p.stem] = json.loads(p.read_text())
    return snaps


def load_config(path: str | None) -> tuple[dict, Path]:
    path = path or os.environ.get("DISTRIBUTION_CONFIG")
    if not path:
        sys.exit("No config: pass --config PATH or set DISTRIBUTION_CONFIG (see the module docstring).")
    p = Path(path).expanduser().resolve()
    cfg = json.loads(p.read_text())
    base = p.parent
    cfg["ledger"] = (base / cfg["ledger"]).resolve()
    cfg["out_dir"] = (base / cfg.get("out_dir", ".")).resolve()
    return cfg, p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config")
    ap.add_argument("--report", action="store_true", help="print the weekly results table")
    ap.add_argument("--date", help="report date YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--decode-x", nargs="+", metavar="URL_OR_ID")
    args = ap.parse_args(argv)

    if args.decode_x:
        for s in args.decode_x:
            print(f"{x_status_datetime(s).isoformat()}  {s}")
        return 0

    cfg, _ = load_config(args.config)
    ledger = parse_ledger(cfg["ledger"])
    if args.report:
        d = dt.date.fromisoformat(args.date) if args.date else dt.date.today() - dt.timedelta(days=1)
        try:
            print(build_report(ledger, load_snapshots(cfg["out_dir"]), d), end="")
        except NoSnapshotError as e:
            print(f"No results table: {e}. Run the collector first.", file=sys.stderr)
            return 2
        return 0

    today = dt.date.today()
    snap = collect(cfg, ledger, today)
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    out = cfg["out_dir"] / f"{today.isoformat()}.json"
    out.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n")
    print(f"Wrote {out} ({len(ledger)} ledger rows)")
    print("\n".join(source_lines(snap)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
