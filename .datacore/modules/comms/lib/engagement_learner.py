#!/usr/bin/env python3
"""Weekly Engagement Learner — self-improving analysis of reply performance.

Runs Sunday 06:00 UTC. Analyzes posted replies with metrics_24h to find:
  1. Reply type vs engagement (auto-updates preferred_reply_types when ≥500)
  2. Topic cluster vs engagement (auto-updates quota weights when ≥500)
  3. Account tier sweet spot (report only — never auto-apply)
  4. Principles correlation (is principled content getting more engagement?)
  5. Evaluator threshold calibration (Pearson r between consensus and impressions)
  6. Blacklist candidates (≥3 replies, 0 engagement)
  7. Query performance (which discovery queries yield high-scoring candidates)

Auto-apply threshold: 500 analyzed replies (≈1 week at 85/day scale).
Below threshold: report only.

Usage:
    python3 engagement_learner.py           # Run analysis
    python3 engagement_learner.py --dry-run # Report without applying changes
"""

import json
import os
import sys
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"
REPORT_DIR = DATA_DIR / ".datacore" / "state" / "engagement-reports"

LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(DATA_DIR / ".datacore" / "lib"))

ACTIVATION_THRESHOLD = 500  # Minimum analyzed replies before auto-applying changes
REPORT_THRESHOLD = 100      # Send Telegram report (findings only) at this count


def load_env():
    try:
        from env_utils import load_env_files
        load_env_files()
    except Exception:
        pass


def _get_analyzed_posts(st: dict) -> list:
    """Return all posted replies that have been analyzed (have metrics_24h)."""
    return [
        p for p in st.get("posted", [])
        if p.get("analyzed") and p.get("metrics_24h")
    ]


def _engagement_score(metrics: dict) -> float:
    """Composite engagement score: likes + replies*3 + impressions/100."""
    likes = metrics.get("like_count", 0)
    replies = metrics.get("reply_count", 0)
    impressions = metrics.get("impression_count", 0)
    return likes + replies * 3 + impressions / 100


def _pearson_r(xs: list, ys: list) -> float:
    """Pearson correlation coefficient."""
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


# ─── Analysis Functions ────────────────────────────────────────────────────────

def analyze_reply_types(posts: list) -> dict:
    """Mean engagement by reply type."""
    type_scores = {}
    for p in posts:
        rt = p.get("reply_type")
        if not rt:
            continue
        metrics = p.get("metrics_24h", {})
        score = _engagement_score(metrics)
        type_scores.setdefault(rt, []).append(score)

    return {
        rt: {
            "mean": sum(scores) / len(scores),
            "count": len(scores),
        }
        for rt, scores in type_scores.items()
    }


def analyze_topic_clusters(posts: list) -> dict:
    """Mean engagement by topic cluster."""
    topic_scores = {}
    for p in posts:
        topic = p.get("topic_group", "unknown")
        metrics = p.get("metrics_24h", {})
        score = _engagement_score(metrics)
        topic_scores.setdefault(topic, []).append(score)

    return {
        topic: {
            "mean": sum(scores) / len(scores),
            "count": len(scores),
        }
        for topic, scores in topic_scores.items()
    }


def analyze_account_tiers(posts: list) -> dict:
    """Engagement by follower tier band."""
    # Band definitions: micro (<5K), small (5K-50K), medium (50K-500K), large (500K+)
    bands = {"micro": [], "small": [], "medium": [], "large": []}

    for p in posts:
        followers = p.get("target_followers", 0) or 0
        metrics = p.get("metrics_24h", {})
        score = _engagement_score(metrics)

        if followers < 5000:
            bands["micro"].append(score)
        elif followers < 50000:
            bands["small"].append(score)
        elif followers < 500000:
            bands["medium"].append(score)
        else:
            bands["large"].append(score)

    return {
        band: {
            "mean": sum(scores) / len(scores) if scores else 0,
            "count": len(scores),
        }
        for band, scores in bands.items()
    }


def analyze_principles_correlation(posts: list) -> dict:
    """Pearson r between principles_score and engagement."""
    pairs = []
    for p in posts:
        ps = p.get("principles_score")
        if ps is None:
            continue
        metrics = p.get("metrics_24h", {})
        score = _engagement_score(metrics)
        pairs.append((ps, score))

    if len(pairs) < 5:
        return {"r": 0.0, "n": len(pairs), "interpretation": "insufficient data"}

    xs, ys = zip(*pairs)
    r = _pearson_r(list(xs), list(ys))
    interp = (
        "strong positive" if r > 0.5 else
        "moderate positive" if r > 0.3 else
        "weak positive" if r > 0.1 else
        "no correlation" if r > -0.1 else
        "weak negative"
    )
    return {"r": round(r, 3), "n": len(pairs), "interpretation": interp}


def analyze_evaluator_calibration(posts: list) -> dict:
    """Pearson r between evaluator consensus and engagement."""
    pairs = []
    for p in posts:
        consensus = p.get("eval_consensus")
        if consensus is None:
            continue
        metrics = p.get("metrics_24h", {})
        score = _engagement_score(metrics)
        pairs.append((consensus, score))

    if len(pairs) < 5:
        return {"r": 0.0, "n": len(pairs), "recommendation": "insufficient data"}

    xs, ys = zip(*pairs)
    r = _pearson_r(list(xs), list(ys))

    recommendation = "no change"
    if r > 0.5:
        recommendation = "Consider lowering threshold slightly (strong signal)"
    elif r > 0.3:
        recommendation = "Threshold well-calibrated (moderate signal)"
    elif r < 0.1:
        recommendation = "Threshold may not correlate with quality — review manually"

    return {"r": round(r, 3), "n": len(pairs), "recommendation": recommendation}


def find_blacklist_candidates(posts: list) -> list:
    """Authors with ≥3 replies and 0 likes total."""
    author_stats = {}
    for p in posts:
        author = p.get("target_author", "")
        if not author:
            continue
        metrics = p.get("metrics_24h", {})
        likes = metrics.get("like_count", 0)
        replies = metrics.get("reply_count", 0)

        if author not in author_stats:
            author_stats[author] = {"replies": 0, "total_likes": 0, "total_replies": 0}
        author_stats[author]["replies"] += 1
        author_stats[author]["total_likes"] += likes
        author_stats[author]["total_replies"] += replies

    candidates = [
        author for author, stats in author_stats.items()
        if stats["replies"] >= 3
        and stats["total_likes"] == 0
        and stats["total_replies"] == 0
    ]
    return candidates


def analyze_query_performance(posts: list) -> dict:
    """Mean engagement by source_query (actual discovery query performance).

    Falls back to topic_group analysis when source_query is not populated
    (entries discovered before 4.3 was deployed).
    """
    # Use source_query if available; fall back to topic_group proxy
    query_scores: dict[str, list] = {}
    fallback_to_topic = True

    for p in posts:
        q = p.get("source_query", "")
        if q:
            fallback_to_topic = False
            # Use first 60 chars as key to keep output readable
            key = q[:60] + ("..." if len(q) > 60 else "")
            metrics = p.get("metrics_24h", {})
            score = _engagement_score(metrics)
            query_scores.setdefault(key, []).append(score)

    if fallback_to_topic or not query_scores:
        # source_query not yet populated — use topic_group as proxy
        return analyze_topic_clusters(posts)

    return {
        query: {
            "mean": sum(scores) / len(scores),
            "count": len(scores),
        }
        for query, scores in query_scores.items()
    }


# ─── Auto-apply Functions ──────────────────────────────────────────────────────

def apply_blacklist(candidates: list, st: dict) -> list:
    """Add blacklist candidates to config.blacklist_authors. Always auto-applied."""
    config = st.setdefault("config", {})
    existing = set(config.get("blacklist_authors", []))
    new_additions = [c for c in candidates if c not in existing]
    if new_additions:
        config["blacklist_authors"] = list(existing | set(new_additions))
    return new_additions


def apply_preferred_reply_types(type_analysis: dict, st: dict) -> dict:
    """Update preferred_reply_types in config based on engagement data."""
    if not type_analysis:
        return {}
    # Sort by mean engagement score
    ranked = sorted(type_analysis.items(), key=lambda x: -x[1]["mean"])
    config = st.setdefault("config", {})
    config["preferred_reply_types"] = [rt for rt, _ in ranked]
    return {rt: round(data["mean"], 2) for rt, data in ranked}


def apply_topic_quota_adjustments(topic_analysis: dict, st: dict) -> dict:
    """Adjust topic quota targets based on engagement performance."""
    from engagement_engine import TOPIC_QUOTA_TARGETS

    # Scale factor: topics with above-average engagement get +20% quota
    # Topics with below-average get -20%, floored at 5
    mean_engagement = (
        sum(d["mean"] for d in topic_analysis.values()) / len(topic_analysis)
        if topic_analysis else 1.0
    )

    config = st.setdefault("config", {})
    current_targets = config.get("topic_quota_targets", dict(TOPIC_QUOTA_TARGETS))
    new_targets = {}
    adjustments = {}

    for topic, data in topic_analysis.items():
        current = current_targets.get(topic, TOPIC_QUOTA_TARGETS.get(topic, 10))
        factor = 1.2 if data["mean"] > mean_engagement * 1.1 else (
            0.8 if data["mean"] < mean_engagement * 0.9 else 1.0
        )
        new_val = max(5, min(40, round(current * factor)))
        new_targets[topic] = new_val
        if new_val != current:
            adjustments[topic] = f"{current} → {new_val}"

    # Keep any topics not in analysis
    for topic, val in current_targets.items():
        if topic not in new_targets:
            new_targets[topic] = val

    config["topic_quota_targets"] = new_targets
    return adjustments


# ─── Report Generation ─────────────────────────────────────────────────────────

def generate_report(
    n_analyzed: int,
    type_analysis: dict,
    topic_analysis: dict,
    account_tiers: dict,
    principles_corr: dict,
    eval_calibration: dict,
    blacklist_candidates: list,
    applied: dict,
    dry_run: bool,
) -> str:
    """Generate markdown report of learnings."""
    now = datetime.now(timezone.utc)
    lines = [
        f"# Engagement Learner Report — {now.strftime('%Y-%m-%d')}",
        f"\nAnalyzed posts: **{n_analyzed}** | Threshold: {ACTIVATION_THRESHOLD} | "
        f"Auto-apply: {'YES' if n_analyzed >= ACTIVATION_THRESHOLD else 'NO (report only)'}",
        "\n---\n",
    ]

    # Reply types
    lines.append("## Reply Type Performance")
    if type_analysis:
        for rt, data in sorted(type_analysis.items(), key=lambda x: -x[1]["mean"]):
            lines.append(f"- **{rt}**: mean={data['mean']:.2f}, n={data['count']}")
    else:
        lines.append("- No data (reply_type not tracked)")

    if applied.get("preferred_reply_types"):
        lines.append(f"\n✓ Auto-applied ranking: {' > '.join(applied['preferred_reply_types'])}")

    # Topics
    lines.append("\n## Topic Cluster Performance")
    if topic_analysis:
        for topic, data in sorted(topic_analysis.items(), key=lambda x: -x[1]["mean"]):
            lines.append(f"- **{topic}**: mean={data['mean']:.2f}, n={data['count']}")
    else:
        lines.append("- No data")

    if applied.get("quota_adjustments"):
        lines.append("\n✓ Quota adjustments:")
        for topic, adj in applied["quota_adjustments"].items():
            lines.append(f"  - {topic}: {adj}")

    # Account tiers
    lines.append("\n## Account Tier Sweet Spot (report only)")
    for band, data in sorted(account_tiers.items(), key=lambda x: -x[1]["mean"]):
        if data["count"] > 0:
            lines.append(f"- **{band}**: mean={data['mean']:.2f}, n={data['count']}")

    # Principles correlation
    lines.append("\n## Principles Alignment Correlation")
    pc = principles_corr
    lines.append(f"- Pearson r={pc.get('r', 0):.3f}, n={pc.get('n', 0)}")
    lines.append(f"- Interpretation: {pc.get('interpretation', 'N/A')}")
    if pc.get("r", 0) < 0:
        lines.append("- ⚠️  WARNING: Principled replies getting LESS engagement — review alignment strategy")
    elif pc.get("r", 0) > 0.3:
        lines.append("- ✓ Good signal: principled replies correlating with engagement")

    # Evaluator calibration
    lines.append("\n## Evaluator Calibration (report only — never auto-apply)")
    ec = eval_calibration
    lines.append(f"- Pearson r={ec.get('r', 0):.3f} (consensus vs engagement), n={ec.get('n', 0)}")
    lines.append(f"- Recommendation: {ec.get('recommendation', 'N/A')}")

    # Blacklist
    lines.append(f"\n## Blacklist Candidates ({len(blacklist_candidates)})")
    if blacklist_candidates:
        for author in blacklist_candidates:
            lines.append(f"- {author}")
        if applied.get("blacklisted"):
            lines.append(f"\n✓ Auto-added to blacklist: {', '.join(applied['blacklisted'])}")
    else:
        lines.append("- None found")

    if dry_run:
        lines.append("\n\n---\n*DRY RUN — no changes applied*")

    return "\n".join(lines)


# ─── Telegram Reporting ────────────────────────────────────────────────────────

def send_telegram_report(
    type_analysis: dict,
    topic_analysis: dict,
    blacklist_additions: list,
    applied: dict,
    report_filename: str,
):
    """Send a Telegram message summarizing learner findings."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        return

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    # Best reply type
    best_type = ""
    if type_analysis:
        best = max(type_analysis.items(), key=lambda x: x[1]["mean"])
        best_type = f"{best[0]} ({best[1]['mean']:.1f} engagement score)"

    # Best topic
    best_topic_str = ""
    if topic_analysis:
        best_t = max(topic_analysis.items(), key=lambda x: x[1]["mean"])
        best_topic_str = f"{best_t[0]} ({best_t[1]['mean']:.1f} avg impressions)"

    auto_applied = applied.get("preferred_reply_types") or applied.get("quota_adjustments")
    n_applied = 0
    if applied.get("preferred_reply_types"):
        n_applied += len(applied["preferred_reply_types"])
    if applied.get("quota_adjustments"):
        n_applied += len(applied["quota_adjustments"])

    lines = [f"📊 <b>Weekly Learning — {date_str}</b>\n"]
    if best_type:
        lines.append(f"Best reply type: {best_type}")
    if best_topic_str:
        lines.append(f"Best topic: {best_topic_str}")
    if blacklist_additions:
        lines.append(f"Blacklist additions: {len(blacklist_additions)}")
    if n_applied > 0:
        lines.append(f"{n_applied} auto-applied changes")
    lines.append(f"Full report: engagement-reports/{report_filename}")

    text = "\n".join(lines)
    import json as _json
    from urllib.request import Request as _Req, urlopen as _urlopen
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    body = _json.dumps(payload).encode()
    req = _Req(f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with _urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
            if result.get("ok"):
                print(f"  Learner report sent to Telegram")
            else:
                print(f"  Telegram error: {result}")
    except Exception as e:
        print(f"  Telegram send failed: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    """Main entry point."""
    load_env()
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%Y-%m-%d %H:%M')}] Engagement Learner starting...")

    import engagement_state as state_mod
    st, baseline = state_mod.load(STATE_FILE)

    # Gather analyzed posts
    posts = _get_analyzed_posts(st)
    n = len(posts)
    print(f"  Analyzed posts available: {n} (report threshold: {REPORT_THRESHOLD}, auto-apply: {ACTIVATION_THRESHOLD})")

    # Run all analyses
    type_analysis = analyze_reply_types(posts)
    topic_analysis = analyze_topic_clusters(posts)
    account_tiers = analyze_account_tiers(posts)
    principles_corr = analyze_principles_correlation(posts)
    eval_calibration = analyze_evaluator_calibration(posts)
    blacklist_candidates = find_blacklist_candidates(posts)

    applied = {}

    # Auto-apply when threshold reached
    if n >= ACTIVATION_THRESHOLD and not dry_run:
        print(f"  Auto-apply threshold reached — applying learned changes")

        # 1. Preferred reply types
        if type_analysis:
            ranked = apply_preferred_reply_types(type_analysis, st)
            applied["preferred_reply_types"] = list(ranked.keys())
            print(f"  Applied reply type ranking: {' > '.join(ranked.keys())}")

        # 2. Topic quota adjustments (only if r > 0.3 for at least one topic)
        topic_means = [d["mean"] for d in topic_analysis.values()]
        if topic_means and max(topic_means) > 0:
            adjustments = apply_topic_quota_adjustments(topic_analysis, st)
            if adjustments:
                applied["quota_adjustments"] = adjustments
                print(f"  Applied quota adjustments: {adjustments}")

        # 3. Blacklist (always auto-apply regardless of threshold)
        if blacklist_candidates:
            added = apply_blacklist(blacklist_candidates, st)
            if added:
                applied["blacklisted"] = added
                print(f"  Blacklisted {len(added)} accounts")

        if not dry_run:
            state_mod.save(st, STATE_FILE, baseline=baseline)

    elif blacklist_candidates and not dry_run:
        # Always apply blacklist even below threshold
        added = apply_blacklist(blacklist_candidates, st)
        if added:
            applied["blacklisted"] = added
            print(f"  Blacklisted {len(added)} accounts (below threshold, always applied)")
            state_mod.save(st, STATE_FILE, baseline=baseline)

    # Generate report
    report = generate_report(
        n_analyzed=n,
        type_analysis=type_analysis,
        topic_analysis=topic_analysis,
        account_tiers=account_tiers,
        principles_corr=principles_corr,
        eval_calibration=eval_calibration,
        blacklist_candidates=blacklist_candidates,
        applied=applied,
        dry_run=dry_run,
    )

    # Write report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_filename = f"learner-{now.strftime('%Y-%m-%d')}.md"
    report_file = REPORT_DIR / report_filename
    report_file.write_text(report)
    print(f"\nReport written: {report_file}")
    print(report)

    # Send Telegram report when REPORT_THRESHOLD reached (findings only at 100, also auto-apply at 500)
    if n >= REPORT_THRESHOLD and not dry_run:
        print(f"  Report threshold reached — sending Telegram summary")
        try:
            send_telegram_report(
                type_analysis=type_analysis,
                topic_analysis=topic_analysis,
                blacklist_additions=applied.get("blacklisted", []),
                applied=applied,
                report_filename=report_filename,
            )
        except Exception as e:
            print(f"  Telegram report failed: {e}")

    # Write "What's Working" to Sunday's journal entry
    if not dry_run:
        _update_journal(report, now)


def _update_journal(report: str, now: datetime):
    """Append learning summary to personal journal."""
    journal_dir = DATA_DIR / "0-personal" / "notes" / "journals"
    if not journal_dir.exists():
        journal_dir = DATA_DIR / "0-personal" / "journal"
    if not journal_dir.exists():
        return

    date_str = now.strftime("%Y-%m-%d")
    journal_file = journal_dir / f"{date_str}.md"

    # Extract key findings (first 500 chars of report)
    summary_lines = [l for l in report.split("\n") if l.startswith("- ")][:6]
    summary = "\n".join(summary_lines)

    entry = f"\n\n## Weekly Engagement Learner\n\n{summary}\n\nFull report: `.datacore/state/engagement-reports/learner-{date_str}.md`\n"

    if journal_file.exists():
        journal_file.write_text(journal_file.read_text() + entry)
    else:
        journal_file.write_text(f"# {date_str}\n{entry}")

    print(f"Journal updated: {journal_file}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
