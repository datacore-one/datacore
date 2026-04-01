#!/usr/bin/env python3
"""Extract X auth session from local browser and transfer to nightshift.

Reads cookies directly from your Chrome/Brave profile — no login required.
Requires you to already be logged in to X as @FairDataSociety in Chrome or Brave.

Usage:
    python3 extract_x_auth.py
"""

import json
import subprocess
from pathlib import Path

DATA_DIR = Path.home() / "Data"
LOCAL_AUTH = DATA_DIR / ".datacore" / "state" / "x-auth-state.json"
NIGHTSHIFT_AUTH = "nightshift:~/Data/.datacore/state/x-auth-state.json"

REQUIRED_COOKIES = {"auth_token", "ct0"}


def get_x_cookies_from_browser(browser: str) -> list:
    """Extract x.com cookies from Chrome or Brave."""
    import browser_cookie3
    try:
        if browser == "chrome":
            cj = browser_cookie3.chrome(domain_name=".x.com")
        else:
            cj = browser_cookie3.brave(domain_name=".x.com")
    except Exception as e:
        print(f"  Could not read {browser} cookies: {e}")
        return []

    cookies = []
    for c in cj:
        if not c.domain or "x.com" not in c.domain:
            continue
        cookies.append({
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path or "/",
            "expires": int(c.expires) if c.expires else -1,
            "httpOnly": False,
            "secure": bool(c.secure),
            "sameSite": "None",
        })
    return cookies


def main():
    LOCAL_AUTH.parent.mkdir(parents=True, exist_ok=True)

    cookies = []
    for browser in ("brave", "chrome"):
        print(f"Trying {browser}...")
        cookies = get_x_cookies_from_browser(browser)
        names = {c["name"] for c in cookies}
        if REQUIRED_COOKIES.issubset(names):
            print(f"  Found auth_token + ct0 in {browser} — good.")
            break
        else:
            missing = REQUIRED_COOKIES - names
            print(f"  Missing {missing} in {browser}, trying next...")
            cookies = []

    if not cookies:
        print("\nERROR: Could not find X auth cookies in Chrome or Brave.")
        print("Make sure you are logged in to X as @FairDataSociety in Chrome or Brave.")
        return 1

    state = {"cookies": cookies, "origins": []}
    LOCAL_AUTH.write_text(json.dumps(state, indent=2))
    print(f"Auth saved locally: {LOCAL_AUTH} ({len(cookies)} cookies)")

    print("Transferring to nightshift...")
    result = subprocess.run(
        ["rsync", "-avz", str(LOCAL_AUTH), NIGHTSHIFT_AUTH],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("Done. Nightshift can now post via Chrome.")
    else:
        print(f"rsync failed: {result.stderr}")
        print(f"Manual transfer: rsync {LOCAL_AUTH} {NIGHTSHIFT_AUTH}")

    return 0


if __name__ == "__main__":
    exit(main())
