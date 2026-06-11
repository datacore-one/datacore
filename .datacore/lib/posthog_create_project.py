#!/usr/bin/env python3
"""Create (or reuse) a PostHog project and wire its client key into a .env file.

Reads POSTHOG_API_KEY (personal phx_ key) from .datacore/env/.env, creates a
project in the current organization (idempotent by name), and writes the new
project's client ingestion key (phc_) as VITE_PUBLIC_POSTHOG_KEY into a target
.env file. NEVER prints the key values.

Usage:
  python3 .datacore/lib/posthog_create_project.py "Millions Online" \
      --env-out /path/to/app/.env.local --host https://eu.posthog.com
"""
import argparse
import json
import os
import re
import sys
import urllib.request

ENV_FILE = os.path.expanduser("~/Data/.datacore/env/.env")


def load_key():
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith("POSTHOG_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("POSTHOG_API_KEY not found in .env")


def api(host, path, key, method="GET", body=None):
    url = f"{host}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def write_env(path, token, host):
    ingest = host.replace("https://eu.posthog.com", "https://eu.i.posthog.com")
    lines = []
    if os.path.exists(path):
        with open(path) as f:
            lines = f.read().splitlines()
    def setkv(lines, k, v):
        pat = re.compile(rf"^{re.escape(k)}=")
        for i, ln in enumerate(lines):
            if pat.match(ln):
                lines[i] = f"{k}={v}"
                return lines
        lines.append(f"{k}={v}")
        return lines
    lines = setkv(lines, "VITE_PUBLIC_POSTHOG_KEY", token)
    lines = setkv(lines, "VITE_PUBLIC_POSTHOG_HOST", ingest)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--env-out", required=True)
    ap.add_argument("--host", default="https://eu.posthog.com")
    args = ap.parse_args()
    key = load_key()

    st, org = api(args.host, "/api/organizations/@current/", key)
    if st != 200:
        sys.exit(f"org lookup failed ({st}): {org}")
    org_id = org.get("id")
    print(f"org: {org.get('name')} ({org_id})")

    st, projs = api(args.host, f"/api/organizations/{org_id}/projects/", key)
    existing = None
    if st == 200:
        for p in (projs.get("results") or projs if isinstance(projs, dict) else projs):
            if isinstance(p, dict) and p.get("name", "").lower() == args.name.lower():
                existing = p
                break

    if existing:
        proj = existing
        print(f"reusing existing project: {proj.get('name')} (id {proj.get('id')})")
    else:
        st, proj = api(args.host, f"/api/organizations/{org_id}/projects/", key,
                       method="POST", body={"name": args.name})
        if st not in (200, 201):
            sys.exit(f"project create failed ({st}): {proj}")
        print(f"created project: {proj.get('name')} (id {proj.get('id')})")

    token = proj.get("api_token", "")
    if not token.startswith("phc_"):
        sys.exit(f"no client api_token returned: keys={list(proj.keys())}")
    write_env(args.env_out, token, args.host)
    print(f"wired client key -> {args.env_out} (VITE_PUBLIC_POSTHOG_KEY set; "
          f"token {token[:7]}…{token[-3:]} masked)")
    print(f"project_id={proj.get('id')}")


if __name__ == "__main__":
    main()
