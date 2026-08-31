#!/usr/bin/env python3
"""Analyse a keyword-defined corpus of engrams across PLUR stores.

Produces the verifiable numbers behind an engagement "data story":
count, date range, capture sources, domains, types, scopes, capture rate
over time, and recall usage (injections + feedback).

Usage:
    python3 .datacore/lib/engram_corpus_analysis.py --keyword igea
    python3 .datacore/lib/engram_corpus_analysis.py --keyword igea --domain plur.enterprise
    python3 .datacore/lib/engram_corpus_analysis.py --keyword igea --json out.json

Tiers:
    T1 domain   — engram.domain starts with <domain-prefix built from keyword>
    T2 tag      — keyword appears in engram.tags
    T3 text     — keyword appears anywhere in statement/rationale/summary/source/domain/tags

Written 2026-08-31 for the IGEA engagement analysis. Kept generic so any
engagement (customer, project, venture) can be measured the same way.
"""
import argparse
import collections
import json
import os
import re
import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

PLUR_ROOT = os.path.expanduser("~/.plur")
DEFAULT_STORES = [
    (os.path.join(PLUR_ROOT, "engrams.yaml"), "global-store"),
    (os.path.expanduser("~/Data/.plur/engrams.yaml"), "project:Data"),
    (os.path.expanduser("~/Data/5-plur/.plur/engrams.yaml"), "project:5-plur"),
    (os.path.expanduser("~/Data/5-plur/2-projects/plur/.plur/engrams.yaml"), "project:plur"),
]
HISTORY_DIR = os.path.join(PLUR_ROOT, "history")

# PLUR used ENG-YYYY-MMDD-NNN until ~2026-08, then ENG-YYYY-MM-DD-NNN. Match both.
ID_DATE = re.compile(r"^ENG-(\d{4})-(\d{2})-?(\d{2})-")


def load_stores(stores):
    out = []
    for path, label in stores:
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        for e in data.get("engrams", []) or []:
            e["_store"] = label
            out.append(e)
    return out


def haystack(e):
    parts = [
        str(e.get("statement") or ""),
        str(e.get("rationale") or ""),
        str(e.get("summary") or ""),
        str(e.get("source") or ""),
        str(e.get("domain") or ""),
        " ".join(e.get("tags") or []),
    ]
    return " ".join(parts).lower()


def classify(engrams, keyword):
    kw = keyword.lower()
    t1, t2, t3 = [], [], []
    for e in engrams:
        dom = (e.get("domain") or "").lower()
        tags = [str(t).lower() for t in (e.get("tags") or [])]
        hay = haystack(e)
        in_text = kw in hay
        in_tag = kw in tags
        in_dom = kw in dom
        if in_dom:
            t1.append(e)
        if in_tag:
            t2.append(e)
        if in_text:
            t3.append(e)
    return {"T1_domain": t1, "T2_tag": t2, "T3_text": t3}


def id_month(e):
    m = ID_DATE.match(e.get("id", ""))
    return f"{m.group(1)}-{m.group(2)}" if m else None


def id_date(e):
    m = ID_DATE.match(e.get("id", ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def load_history():
    events = []
    if not os.path.isdir(HISTORY_DIR):
        return events
    for fn in sorted(os.listdir(HISTORY_DIR)):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(HISTORY_DIR, fn)) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def counter_block(items, key, top=None):
    c = collections.Counter(items)
    rows = c.most_common(top) if top else sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return {"label": key, "rows": rows, "distinct": len(c), "total": sum(c.values())}


def analyse(corpus, events, keyword):
    ids = {e["id"] for e in corpus}
    res = {}
    res["count"] = len(corpus)

    dates = sorted(d for d in (id_date(e) for e in corpus) if d)
    res["first_id_date"] = dates[0] if dates else None
    res["last_id_date"] = dates[-1] if dates else None
    res["undated_ids"] = sum(1 for e in corpus if not id_date(e))

    res["by_month_id"] = counter_block([id_month(e) for e in corpus if id_month(e)], "month")
    res["by_domain"] = counter_block([e.get("domain") or "<none>" for e in corpus], "domain")
    res["by_type"] = counter_block([e.get("type") or "<none>" for e in corpus], "type")
    res["by_scope"] = counter_block([e.get("scope") or "<none>" for e in corpus], "scope")
    res["by_store"] = counter_block([e.get("_store") for e in corpus], "store")
    res["by_status"] = counter_block([e.get("status") or "<none>" for e in corpus], "status")
    res["by_commitment"] = counter_block([e.get("commitment") or "<none>" for e in corpus], "commitment")
    res["by_visibility"] = counter_block([e.get("visibility") or "<none>" for e in corpus], "visibility")
    res["by_source"] = counter_block([e.get("source") or "<none>" for e in corpus], "source", top=40)
    res["by_pack"] = counter_block([e.get("pack") or "<none>" for e in corpus], "pack")

    tags = collections.Counter()
    for e in corpus:
        for t in e.get("tags") or []:
            tags[str(t)] += 1
    res["top_tags"] = {"label": "tag", "rows": tags.most_common(30), "distinct": len(tags),
                       "total": sum(tags.values())}

    # recall usage from engram activation
    freqs = [(e.get("activation") or {}).get("frequency", 0) or 0 for e in corpus]
    res["activation_frequency_sum"] = sum(freqs)
    res["activation_frequency_max"] = max(freqs) if freqs else 0
    res["activation_frequency_zero"] = sum(1 for f in freqs if f == 0)
    res["activation_frequency_nonzero"] = sum(1 for f in freqs if f > 0)
    res["top_recalled"] = sorted(
        [(e["id"], (e.get("activation") or {}).get("frequency", 0) or 0,
          (e.get("activation") or {}).get("last_accessed"),
          (e.get("summary") or str(e.get("statement"))[:90]))
         for e in corpus], key=lambda r: -r[1])[:20]

    la = [ (e.get("activation") or {}).get("last_accessed") for e in corpus ]
    la = sorted([x for x in la if x])
    res["last_accessed_min"] = la[0] if la else None
    res["last_accessed_max"] = la[-1] if la else None
    res["last_accessed_by_month"] = counter_block([x[:7] for x in la], "month")

    pos = sum((e.get("feedback_signals") or {}).get("positive", 0) or 0 for e in corpus)
    neg = sum((e.get("feedback_signals") or {}).get("negative", 0) or 0 for e in corpus)
    neu = sum((e.get("feedback_signals") or {}).get("neutral", 0) or 0 for e in corpus)
    res["feedback"] = {"positive": pos, "negative": neg, "neutral": neu}
    res["injection_count_sum"] = sum(e.get("injection_count", 0) or 0 for e in corpus)
    res["reference_count_sum"] = sum(e.get("reference_count", 0) or 0 for e in corpus)
    res["write_count_sum"] = sum(e.get("write_count", 0) or 0 for e in corpus)
    res["locked"] = sum(1 for e in corpus if e.get("locked_at"))
    res["pinned"] = sum(1 for e in corpus if e.get("pinned"))
    res["with_rationale"] = sum(1 for e in corpus if e.get("rationale"))
    res["with_source"] = sum(1 for e in corpus if e.get("source"))

    # statement volume
    chars = [len(str(e.get("statement") or "")) for e in corpus]
    res["statement_chars_total"] = sum(chars)
    res["statement_chars_mean"] = round(sum(chars) / len(chars)) if chars else 0
    rat = [len(str(e.get("rationale") or "")) for e in corpus]
    res["rationale_chars_total"] = sum(rat)

    # history-derived
    created = [ev for ev in events if ev.get("event") == "engram_created" and ev.get("engram_id") in ids]
    res["history_created_events"] = len(created)
    res["history_created_by_month"] = counter_block([ev["timestamp"][:7] for ev in created], "month")
    res["history_created_by_day"] = counter_block([ev["timestamp"][:10] for ev in created], "day")
    res["history_routed_to"] = counter_block(
        [(ev.get("data") or {}).get("routed_to") for ev in created if (ev.get("data") or {}).get("routed_to")],
        "routed_to")

    injections = []
    for ev in events:
        if ev.get("event") != "co_injection":
            continue
        d = ev.get("data") or {}
        hit = [i for i in (d.get("ids") or []) if i in ids]
        if hit:
            injections.append((ev["timestamp"], d.get("source"), d.get("session_id"), hit))
    res["injection_events"] = len(injections)
    res["injection_engram_hits"] = sum(len(h) for _, _, _, h in injections)
    res["injection_by_month"] = counter_block([t[:7] for t, _, _, _ in injections], "month")
    res["injection_by_source"] = counter_block([s or "<none>" for _, s, _, _ in injections], "source")
    # NOTE: in PLUR <=0.19 `session_id` on co_injection is effectively unique per
    # injection event (verified 2026-08-31: 3446 distinct ids across 3451 events,
    # max 2 events per id). It is NOT a conversation identifier — do not report
    # this as "distinct sessions/conversations".
    res["injection_session_id_values"] = len({s for _, _, s, _ in injections if s})
    res["injection_first"] = injections[0][0] if injections else None
    res["injection_last"] = max((t for t, _, _, _ in injections), default=None)
    inj_ids = collections.Counter()
    for _, _, _, hit in injections:
        for i in hit:
            inj_ids[i] += 1
    res["injection_distinct_engrams"] = len(inj_ids)
    res["injection_top_engrams"] = inj_ids.most_common(15)

    outcomes = collections.Counter()
    for ev in events:
        if ev.get("event") == "injection_outcome" and ev.get("engram_id") in ids:
            outcomes[(ev.get("data") or {}).get("signal")] += 1
    res["injection_outcomes"] = dict(outcomes)

    fb = collections.Counter()
    for ev in events:
        if ev.get("event") == "feedback_received" and ev.get("engram_id") in ids:
            fb[(ev.get("data") or {}).get("signal")] += 1
    res["history_feedback"] = dict(fb)

    retired = [ev for ev in events if ev.get("event") == "engram_retired" and ev.get("engram_id") in ids]
    res["history_retired"] = len(retired)
    dedup = [ev for ev in events if ev.get("event") == "dedup_near_duplicate" and ev.get("engram_id") in ids]
    res["history_dedup_near_duplicate"] = len(dedup)

    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--tier", default="T3_text", choices=["T1_domain", "T2_tag", "T3_text"])
    ap.add_argument("--json", help="write full result JSON here")
    ap.add_argument("--all-tiers", action="store_true")
    args = ap.parse_args()

    engrams = load_stores(DEFAULT_STORES)
    events = load_history()
    tiers = classify(engrams, args.keyword)

    out = {
        "keyword": args.keyword,
        "stores_loaded": [lbl for p, lbl in DEFAULT_STORES if os.path.exists(p)],
        "total_engrams_scanned": len(engrams),
        "total_history_events": len(events),
        "tier_counts": {k: len(v) for k, v in tiers.items()},
    }
    targets = tiers.keys() if args.all_tiers else [args.tier]
    for t in targets:
        out[t] = analyse(tiers[t], events, args.keyword)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2, default=str)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
