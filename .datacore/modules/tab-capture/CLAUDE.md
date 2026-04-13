# Tab Capture Module

Browser tab capture to GTD inbox via Chromium Native Messaging.

## Setup

```bash
python3 .datacore/modules/tab-capture/lib/install.py
```

Then load the unpacked extension in your browser at `brave://extensions` or `chrome://extensions`.

## How It Works

1. Click the toolbar icon in Brave/Chrome
2. All tabs across all windows are captured
3. Internal pages (brave://, chrome://, about:) are filtered out
4. Duplicate URLs already in inbox.org are skipped
5. New tabs appended as TODO entries to inbox.org
6. Popup asks "Close captured tabs?" — Yes closes them, No keeps them open

## Configuration

Edit `.datacore/modules/tab-capture/lib/config.json`:

- `inbox_path` — path to inbox.org (default: `~/Data/0-personal/org/inbox.org`)
- `filtered_prefixes` — URL prefixes to skip

## Files

- `extension/` — Chromium extension (manifest.json, background.js, popup)
- `lib/host.py` — Native Messaging host script
- `lib/install.py` — Cross-platform installer
- `lib/config.json` — Configuration
