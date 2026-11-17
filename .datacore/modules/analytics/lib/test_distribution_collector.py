"""Tests for distribution_collector: X date decoding, the ledger parse and the
Friday report diff. Fixtures only, no network.

Run: python3 -m pytest .datacore/modules/analytics/lib/test_distribution_collector.py -q
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import distribution_collector as dc  # noqa: E402

HEADER = "id,date,brand,channel,url,title,type,event,cost_eur,notes\n"


# --- X status-ID snowflake -------------------------------------------------

def test_snowflake_matches_a_time_recorded_independently():
    # The X work order of 22 Oct records the launch thread as starting
    # 17:39 UTC on Tue 20 Oct 2026 at this status.
    t = dc.x_status_datetime("https://x.com/DataFundProject/status/2112599521746452834")
    assert t.tzinfo is not None
    assert (t.year, t.month, t.day, t.hour, t.minute) == (2026, 10, 20, 17, 39)


def test_snowflake_accepts_bare_id_twitter_host_and_query():
    a = dc.x_status_datetime("2117211185074389193")
    b = dc.x_status_datetime("https://twitter.com/DataFundProject/status/2117211185074389193?s=20")
    assert a == b
    assert a.date() == dt.date(2026, 11, 2)


def test_snowflake_rejects_non_status_and_pre_snowflake_ids():
    with pytest.raises(ValueError):
        dc.x_status_datetime("https://x.com/DataFundProject")
    with pytest.raises(ValueError):
        dc.x_status_datetime("20")  # 2006-era sequential id, no timestamp in it


# --- ledger parse -----------------------------------------------------------

def _write(tmp_path, body):
    p = tmp_path / "ledger.csv"
    p.write_text(HEADER + body)
    return p


def test_ledger_parses_typed_rows(tmp_path):
    p = _write(tmp_path,
               "a1,2026-10-20,datafund,datafund.io,https://datafund.io/blog/x/,Lead article,article,no,,launch\n"
               "a2,2026-11-08,plur,glama,https://glama.ai/mcp/servers/plur-ai/plur,Glama,listing,yes,12.5,\n")
    rows = dc.parse_ledger(p)
    assert [r["id"] for r in rows] == ["a1", "a2"]
    assert rows[0]["date"] == dt.date(2026, 10, 20)
    assert rows[0]["event"] is False and rows[1]["event"] is True
    assert rows[0]["cost_eur"] is None and rows[1]["cost_eur"] == 12.5
    assert rows[0]["notes"] == "launch"


def test_ledger_reports_every_bad_row(tmp_path):
    p = _write(tmp_path,
               "b1,2026-13-01,datafund,x,https://x.com/a/status/1,T,post,no,,\n"   # bad date
               "b2,2026-10-20,acme,x,https://x.com/a/status/2,T,post,no,,\n"      # bad brand
               "b3,2026-10-20,plur,x,,T,post,no,,\n"                              # no url
               "b4,2026-10-20,plur,x,https://x.com/a/status/4,T,tweet,no,,\n"     # bad type
               "b5,2026-10-20,plur,x,https://x.com/a/status/5,T,post,maybe,,\n"   # bad event
               "b5,2026-10-20,plur,x,https://x.com/a/status/6,T,post,no,abc,\n")  # dup id, bad cost
    with pytest.raises(dc.LedgerError) as e:
        dc.parse_ledger(p)
    msg = str(e.value)
    for needle in ("b1", "b2", "b3", "b4", "b5", "duplicate", "cost_eur"):
        assert needle in msg


def test_ledger_rejects_wrong_columns(tmp_path):
    p = tmp_path / "ledger.csv"
    p.write_text("id,date,url\nx,2026-10-20,https://a\n")
    with pytest.raises(dc.LedgerError):
        dc.parse_ledger(p)


# --- report diff ------------------------------------------------------------

def _days(end, values):
    """Daily dict ending on `end` (inclusive), oldest value first."""
    end = dt.date.fromisoformat(end)
    n = len(values)
    return {(end - dt.timedelta(days=n - 1 - i)).isoformat(): v for i, v in enumerate(values)}


def _snapshot(date, stars, devto_reactions, yt_views, posthog_ok=True, gh_traffic=True):
    s = {
        "date": date,
        "sources": {
            "npm": {"status": "ok", "packages": {
                "@plur-ai/mcp": {"daily": _days(date, [10] * 7 + [20] * 7)}}},
            "github": {"status": "ok", "repos": {"plur-ai/plur": {
                "stars": stars,
                "traffic": ({"status": "ok",
                             "views": {"daily": _days(date, [1] * 7 + [3] * 7)},
                             "clones": {"daily": _days(date, [0] * 7 + [2] * 7)},
                             "referrers": [{"referrer": "news.ycombinator.com", "count": 9, "uniques": 7}],
                             "paths": []}
                            if gh_traffic else {"status": "no access"}),
            }}},
            "devto": {"status": "ok", "users": {"plur9": {"status": "ok", "articles": [
                {"url": "https://dev.to/plur9/post-1", "reactions": devto_reactions, "comments": 1}]}}},
            "youtube": {"status": "ok", "videos": {"vid00000001": {"status": "ok", "views": yt_views}}},
        },
    }
    if posthog_ok:
        s["sources"]["posthog"] = {"status": "ok", "window_start": (dt.date.fromisoformat(date) - dt.timedelta(days=13)).isoformat(),
                                   "urls": {"https://plur.ai/blog/post-1/": {"daily": _days(date, [2] * 7 + [5] * 7)}}}
    else:
        s["sources"]["posthog"] = {"status": "no access", "reason": "broker refused"}
    return s


LEDGER_ROWS = (
    "p1,2026-11-10,plur,plur.ai,https://plur.ai/blog/post-1/,Post one,article,no,,\n"
    "p2,2026-11-10,plur,devto,https://dev.to/plur9/post-1,Post one (dev.to),article,no,,\n"
    "p3,2026-11-02,datafund,youtube,https://youtu.be/vid00000001,Demo,video,no,,\n"
    "p4,2026-11-18,datafund,x,https://x.com/DataFundProject/status/2117211185074389193,Post,post,no,,\n"
    "p5,2026-11-19,plur,glama,https://glama.ai/mcp/servers/plur-ai/plur,Glama,listing,yes,25,\n"
)


def _row(report, first_cell, measure=None):
    for line in report.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == first_cell and (measure is None or measure in cells):
            return cells
    raise AssertionError(f"no row {first_cell!r} {measure!r} in:\n{report}")


@pytest.fixture
def ledger(tmp_path):
    return dc.parse_ledger(_write(tmp_path, LEDGER_ROWS))


def test_report_diffs_this_week_against_last_week(ledger):
    snaps = {
        "2026-11-20": _snapshot("2026-11-20", stars=330, devto_reactions=7, yt_views=40),
        "2026-11-13": _snapshot("2026-11-13", stars=325, devto_reactions=4, yt_views=30),
        "2026-11-06": _snapshot("2026-11-06", stars=324, devto_reactions=1, yt_views=13),
    }
    rep = dc.build_report(ledger, snaps, dt.date(2026, 11, 20))

    # page views: daily series in the newest snapshot, 5/day this week, 2/day before
    assert _row(rep, "p1")[-3:] == ["35", "14", "+21"]
    # dev.to reactions: cumulative 7 -> 4 -> 1, so +3 this week, +3 last week
    assert _row(rep, "p2", "dev.to reactions")[-3:] == ["3", "3", "0"]
    # YouTube views: cumulative 40 -> 30 -> 13
    assert _row(rep, "p3")[-3:] == ["10", "17", "-7"]
    # X has no metrics source: n/a, never a number
    assert _row(rep, "p4")[-3:] == ["n/a", "n/a", "n/a"]

    assert _row(rep, "npm @plur-ai/mcp downloads")[1:] == ["140", "70", "+70"]
    assert _row(rep, "GitHub plur-ai/plur stars gained")[1:] == ["5", "1", "+4"]
    assert _row(rep, "GitHub plur-ai/plur views")[1:] == ["21", "7", "+14"]
    assert _row(rep, "Pieces published")[1:] == ["2", "2", "0"]
    assert _row(rep, "Distribution events")[1:] == ["1", "0", "+1"]
    assert _row(rep, "Spend (EUR)")[1:] == ["25", "0", "+25"]
    assert "news.ycombinator.com" in rep


def test_report_shows_na_when_a_source_failed_or_a_snapshot_is_missing(ledger):
    snaps = {
        "2026-11-20": _snapshot("2026-11-20", stars=330, devto_reactions=7, yt_views=40,
                                posthog_ok=False, gh_traffic=False),
        # no snapshot a week earlier: cumulative measures cannot be diffed
    }
    rep = dc.build_report(ledger, snaps, dt.date(2026, 11, 20))
    assert _row(rep, "p1")[-3:] == ["n/a", "n/a", "n/a"]
    assert _row(rep, "p2", "dev.to reactions")[-3:] == ["n/a", "n/a", "n/a"]
    assert _row(rep, "p3")[-3:] == ["n/a", "n/a", "n/a"]
    assert _row(rep, "GitHub plur-ai/plur stars gained")[1:] == ["n/a", "n/a", "n/a"]
    assert _row(rep, "GitHub plur-ai/plur views")[1:] == ["n/a", "n/a", "n/a"]
    # npm comes from the newest snapshot's daily series, so it still works
    assert _row(rep, "npm @plur-ai/mcp downloads")[1:] == ["140", "70", "+70"]
    assert "no access" in rep


def test_piece_published_after_the_baseline_counts_from_zero(tmp_path):
    rows = dc.parse_ledger(_write(tmp_path,
        "n1,2026-11-16,plur,devto,https://dev.to/plur9/post-1,New,article,no,,\n"))
    s_new = _snapshot("2026-11-20", stars=1, devto_reactions=4, yt_views=0)
    s_old = _snapshot("2026-11-13", stars=1, devto_reactions=0, yt_views=0)
    s_old["sources"]["devto"]["users"]["plur9"]["articles"] = []  # not published yet
    rep = dc.build_report(rows, {"2026-11-20": s_new, "2026-11-13": s_old}, dt.date(2026, 11, 20))
    # it did not exist a week or two weeks ago, so both baselines are a real 0
    assert _row(rep, "n1", "dev.to reactions")[-3:] == ["4", "0", "+4"]


def test_report_without_any_snapshot_says_so(ledger):
    with pytest.raises(dc.NoSnapshotError):
        dc.build_report(ledger, {}, dt.date(2026, 11, 20))


def test_daily_series_are_merged_across_snapshots(ledger):
    """GitHub keeps 14 days of traffic. Older snapshots fill in what the newest lacks."""
    new = _snapshot("2026-11-20", stars=1, devto_reactions=0, yt_views=0)
    old = _snapshot("2026-11-13", stars=1, devto_reactions=0, yt_views=0)
    tr_new = new["sources"]["github"]["repos"]["plur-ai/plur"]["traffic"]
    tr_new["views"] = {"daily": _days("2026-11-20", [3] * 7)}
    tr_new["window_start"], tr_new["window_end"] = "2026-11-14", "2026-11-20"
    tr_old = old["sources"]["github"]["repos"]["plur-ai/plur"]["traffic"]
    tr_old["views"] = {"daily": _days("2026-11-13", [1] * 7)}
    tr_old["window_start"], tr_old["window_end"] = "2026-11-07", "2026-11-13"
    rep = dc.build_report(ledger, {"2026-11-20": new, "2026-11-13": old}, dt.date(2026, 11, 20))
    assert _row(rep, "GitHub plur-ai/plur views")[1:] == ["21", "7", "+14"]


def test_github_window_comes_from_the_days_github_returned(monkeypatch):
    days = [{"timestamp": f"2026-11-{d:02d}T00:00:00Z", "count": 1, "uniques": 1} for d in range(3, 17)]

    def fake_gh(path):
        if path.endswith("traffic/views"):
            return {"count": 14, "uniques": 14, "views": days}, None
        if path.endswith("traffic/clones"):
            return {"count": 0, "uniques": 0, "clones": []}, None
        if "traffic/popular" in path:
            return [], None
        return {"stargazers_count": 7}, None
    monkeypatch.setattr(dc, "_gh", fake_gh)
    out = dc.collect_github(["o/r"], dt.date(2026, 11, 17))["repos"]["o/r"]
    assert out["stars"] == 7
    views = out["traffic"]["views"]
    assert (views["window_start"], views["window_end"]) == ("2026-11-03", "2026-11-16")
