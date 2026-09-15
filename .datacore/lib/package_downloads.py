#!/usr/bin/env python3
"""package_downloads.py — download history for our published packages, npm and PyPI.

Why this exists: the seed deck quoted "8,617 installs a month" for one package, which says
nothing about whether the number is rising, what the total is, or how many packages there
are. A monthly point figure is the least informative shape this data comes in.

npm serves daily downloads via api.npmjs.org (range endpoint, 365 days per request, 18
months of history). PyPI serves them via pypistats.org. Both are public and need no key.

    python3 package_downloads.py                 # summary table
    python3 package_downloads.py --json out.json # daily series for charting
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

NPM = ["@plur-ai/core", "@plur-ai/mcp", "@plur-ai/claw", "@plur-ai/cli"]
PYPI = ["org-workspace"]
UA = {"User-Agent": "plur-package-stats/1.0 (+https://plur.ai)"}


def get(url: str):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return json.load(r)
    except Exception as e:                                    # noqa: BLE001
        print(f"  ! {url.split('/')[-1]}: {e}", file=sys.stderr)
        return None


def npm_daily(pkg: str, start: date, end: date) -> dict[str, int]:
    """npm caps a range request at 365 days, so walk the window in year-long chunks."""
    out: dict[str, int] = {}
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=364), end)
        url = (f"https://api.npmjs.org/downloads/range/{cur}:{stop}/"
               f"{urllib.parse.quote(pkg, safe='')}")
        d = get(url)
        for row in (d or {}).get("downloads", []):
            out[row["day"]] = row["downloads"]
        cur = stop + timedelta(days=1)
    return out


def pypi_daily(pkg: str) -> dict[str, int]:
    d = get(f"https://pypistats.org/api/packages/{pkg}/overall?mirrors=false")
    out: dict[str, int] = {}
    for row in (d or {}).get("data", []):
        out[row["date"]] = out.get(row["date"], 0) + row["downloads"]
    return out


def month_totals(daily: dict[str, int]) -> dict[str, int]:
    m: dict[str, int] = {}
    for day, n in daily.items():
        m[day[:7]] = m.get(day[:7], 0) + n
    return dict(sorted(m.items()))


def main() -> int:
    end = date.today()
    start = end - timedelta(days=545)
    series = {}
    print(f"Fetching {len(NPM)} npm + {len(PYPI)} PyPI packages, {start} to {end}\n")
    for p in NPM:
        series[p] = npm_daily(p, start, end)
    for p in PYPI:
        series[p] = pypi_daily(p)

    print(f"{'package':22} {'total':>10} {'first seen':>12} {'last 30d':>10} {'prev 30d':>10} {'change':>8}")
    print("-" * 78)
    grand = 0
    for p, daily in series.items():
        if not daily:
            print(f"{p:22} {'no data':>10}")
            continue
        days = sorted(daily)
        last30 = sum(daily[d] for d in days[-30:])
        prev30 = sum(daily[d] for d in days[-60:-30]) if len(days) >= 60 else 0
        chg = f"{(last30 / prev30 - 1) * 100:+.0f}%" if prev30 else "—"
        tot = sum(daily.values())
        grand += tot
        print(f"{p:22} {tot:>10,} {days[0]:>12} {last30:>10,} {prev30:>10,} {chg:>8}")
    print("-" * 78)
    print(f"{'TOTAL':22} {grand:>10,}\n")

    print("Monthly totals, all packages combined")
    combined: dict[str, int] = {}
    for daily in series.values():
        for mth, n in month_totals(daily).items():
            combined[mth] = combined.get(mth, 0) + n
    for mth, n in sorted(combined.items()):
        bar = "█" * max(1, round(n / max(combined.values()) * 44))
        print(f"  {mth}  {n:>9,}  {bar}")

    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        with open(out, "w") as f:
            json.dump({"fetched": str(end), "daily": series,
                       "monthly_combined": combined}, f, indent=1)
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
