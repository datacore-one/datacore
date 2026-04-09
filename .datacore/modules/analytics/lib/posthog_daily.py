#!/usr/bin/env python3
"""Fetch daily PostHog metrics for all projects. Outputs markdown summary."""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta

API_HOST = "https://eu.posthog.com"
PROJECTS = {
    "Datacore": 156062,
    "PLUR": 156064,
    "Datafund": 156069,
}

def get_api_key():
    key = os.environ.get("POSTHOG_API_KEY")
    if key:
        return key
    env_file = os.path.join(os.path.dirname(__file__), "..", "..", "..", "env", ".env")
    env_file = os.path.normpath(env_file)
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("POSTHOG_API_KEY=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None

def query_hogql(api_key, project_id, hogql):
    url = f"{API_HOST}/api/projects/{project_id}/query/"
    payload = json.dumps({"query": {"kind": "HogQLQuery", "query": hogql}}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("results", [])
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  Error querying project {project_id}: {e}", file=sys.stderr)
        return []

def get_metrics(api_key, project_id):
    q = """
    SELECT
        uniq(properties.$session_id) as sessions,
        uniq(properties.distinct_id) as visitors,
        count() as pageviews
    FROM events
    WHERE event = '$pageview'
      AND timestamp > now() - interval 1 day
    """
    results = query_hogql(api_key, project_id, q)
    if results and len(results) > 0:
        row = results[0]
        return {"sessions": row[0], "visitors": row[1], "pageviews": row[2]}
    return {"sessions": 0, "visitors": 0, "pageviews": 0}

def get_previous_metrics(api_key, project_id):
    q = """
    SELECT
        uniq(properties.$session_id) as sessions,
        uniq(properties.distinct_id) as visitors,
        count() as pageviews
    FROM events
    WHERE event = '$pageview'
      AND timestamp > now() - interval 2 day
      AND timestamp <= now() - interval 1 day
    """
    results = query_hogql(api_key, project_id, q)
    if results and len(results) > 0:
        row = results[0]
        return {"sessions": row[0], "visitors": row[1], "pageviews": row[2]}
    return {"sessions": 0, "visitors": 0, "pageviews": 0}

def delta_str(current, previous):
    if previous == 0:
        return "new" if current > 0 else ""
    pct = ((current - previous) / previous) * 100
    if pct > 0:
        return f"+{pct:.0f}%"
    elif pct < 0:
        return f"{pct:.0f}%"
    return "="

def main():
    api_key = get_api_key()
    if not api_key:
        print("No POSTHOG_API_KEY found")
        sys.exit(1)

    print("| Project | Visitors | Pages | Sessions | vs Yesterday |")
    print("|---------|----------|-------|----------|--------------|")

    total_v, total_p = 0, 0
    for name, pid in PROJECTS.items():
        m = get_metrics(api_key, pid)
        prev = get_previous_metrics(api_key, pid)
        d = delta_str(m["visitors"], prev["visitors"])
        print(f"| {name} | {m['visitors']} | {m['pageviews']} | {m['sessions']} | {d} |")
        total_v += m["visitors"]
        total_p += m["pageviews"]

    print(f"\n**Total**: {total_v} visitors, {total_p} pageviews (last 24h)")

if __name__ == "__main__":
    main()
