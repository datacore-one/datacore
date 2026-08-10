#!/usr/bin/env python3
"""Cross-platform installer for Datacore Tab Capture Native Messaging host.

Detects OS and browser, writes the Native Messaging manifest to the correct
location, and makes the host script executable.

Usage:
    python3 install.py [--browser brave|chrome|chromium] [--uninstall]
"""

import argparse
import json
import os
import platform
import stat
import sys

HOST_NAME = "com.datacore.tab_capture"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOST_SCRIPT = os.path.join(SCRIPT_DIR, "host.py")

# Native Messaging host manifest directories per OS and browser
MANIFEST_DIRS = {
    "Darwin": {
        "brave": "~/Library/Application Support/Google/Chrome/NativeMessagingHosts",
        "chrome": "~/Library/Application Support/Google/Chrome/NativeMessagingHosts",
        "chromium": "~/Library/Application Support/Chromium/NativeMessagingHosts",
    },
    "Linux": {
        "brave": "~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts",
        "chrome": "~/.config/google-chrome/NativeMessagingHosts",
        "chromium": "~/.config/chromium/NativeMessagingHosts",
    },
}


def detect_browsers():
    """Detect which supported browsers are installed."""
    system = platform.system()
    if system not in MANIFEST_DIRS:
        return []

    # Check which browsers are actually installed
    found = []
    browser_apps = {
        "Darwin": {
            "brave": "/Applications/Brave Browser.app",
            "chrome": "/Applications/Google Chrome.app",
            "chromium": "/Applications/Chromium.app",
        },
        "Linux": {
            "brave": "/usr/bin/brave-browser",
            "chrome": "/usr/bin/google-chrome",
            "chromium": "/usr/bin/chromium-browser",
        },
    }
    apps = browser_apps.get(system, {})
    for browser in MANIFEST_DIRS[system]:
        if browser in apps and os.path.exists(apps[browser]):
            found.append(browser)
    return found


def get_manifest_path(browser):
    """Get the manifest file path for a browser."""
    system = platform.system()
    if system not in MANIFEST_DIRS:
        print(f"Error: Unsupported OS: {system}")
        sys.exit(1)

    if browser not in MANIFEST_DIRS[system]:
        print(f"Error: Unknown browser: {browser}")
        sys.exit(1)

    manifest_dir = os.path.expanduser(MANIFEST_DIRS[system][browser])
    return os.path.join(manifest_dir, f"{HOST_NAME}.json")


def create_manifest(extension_id=None):
    """Create the Native Messaging host manifest."""
    manifest = {
        "name": HOST_NAME,
        "description": "Datacore Tab Capture - browser tabs to GTD inbox",
        "path": HOST_SCRIPT,
        "type": "stdio",
    }

    if extension_id:
        manifest["allowed_origins"] = [f"chrome-extension://{extension_id}/"]
    else:
        # During development, we can't restrict by extension ID since it
        # changes on each load. The browser enforces origin checks anyway
        # for extensions loaded from the store. For unpacked extensions,
        # the ID is stable per directory path.
        manifest["allowed_origins"] = ["chrome-extension://*/"]

    return manifest


def install(browser, extension_id=None):
    """Install the Native Messaging host for a browser."""
    manifest_path = get_manifest_path(browser)
    manifest_dir = os.path.dirname(manifest_path)

    # Create directory if needed
    os.makedirs(manifest_dir, exist_ok=True)

    # Write manifest
    manifest = create_manifest(extension_id)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest written to: {manifest_path}")

    # Make host script executable
    st = os.stat(HOST_SCRIPT)
    os.chmod(HOST_SCRIPT, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Made executable: {HOST_SCRIPT}")

    # Create default config if missing
    config_path = os.path.join(SCRIPT_DIR, "config.json")
    if not os.path.exists(config_path):
        default_config = {
            "inbox_path": "~/Data/0-personal/org/inbox.org",
            "filtered_prefixes": [
                "brave://", "chrome://", "about:", "chrome-extension://", "devtools://"
            ]
        }
        with open(config_path, "w") as f:
            json.dump(default_config, f, indent=2)
        print(f"Created default config: {config_path}")

    print()
    print("Next steps:")
    print(f"  1. Open {browser}://extensions")
    print("  2. Enable 'Developer mode' (toggle in top-right)")
    print("  3. Click 'Load unpacked'")
    ext_dir = os.path.join(os.path.dirname(SCRIPT_DIR), "extension")
    print(f"  4. Select: {ext_dir}")
    print("  5. Pin the extension to your toolbar")
    print()
    print("After loading, note the extension ID and re-run with:")
    print(f"  python3 {__file__} --browser {browser} --extension-id <ID>")
    print("This locks the manifest to your specific extension.")


def uninstall(browser):
    """Remove the Native Messaging host manifest for a browser."""
    manifest_path = get_manifest_path(browser)
    if os.path.exists(manifest_path):
        os.remove(manifest_path)
        print(f"Removed: {manifest_path}")
    else:
        print(f"Not found: {manifest_path}")


def main():
    parser = argparse.ArgumentParser(description="Install Datacore Tab Capture Native Messaging host")
    parser.add_argument("--browser", choices=["brave", "chrome", "chromium"],
                        help="Target browser (auto-detected if omitted)")
    parser.add_argument("--extension-id", help="Lock manifest to specific extension ID")
    parser.add_argument("--uninstall", action="store_true", help="Remove the manifest")
    args = parser.parse_args()

    if args.browser:
        browsers = [args.browser]
    else:
        browsers = detect_browsers()
        if not browsers:
            print("No supported browsers detected.")
            sys.exit(1)
        print(f"Detected browsers: {', '.join(browsers)}")

    for browser in browsers:
        print(f"\n--- {browser.title()} ---")
        if args.uninstall:
            uninstall(browser)
        else:
            install(browser, args.extension_id)


if __name__ == "__main__":
    main()
