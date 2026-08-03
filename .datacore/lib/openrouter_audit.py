#!/usr/bin/env python3
"""
Adversarial code audit via OpenRouter.

Sends a diff (plus optional whole files for context) to a chosen model with an
adversarial prompt: find what is WRONG, do not validate. Prints the model's
findings verbatim so they can be checked rather than trusted.

Usage:
  openrouter_audit.py --diff <file> [--file <path> ...] [--model <id>]
                      [--focus "..."] [--out <path>]

The API key is read from OPENROUTER_API_KEY, or --key-file (a file containing
one `sk-or-...` token). The key is never printed.
"""
import argparse, json, os, re, sys, urllib.request

DEFAULT_MODEL = "openai/gpt-5.6-sol-pro"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = """You are a hostile code reviewer performing a pre-release audit.

Your job is to find what is WRONG. You are not here to summarise, praise, or
confirm. A review that concludes "this looks correct" has failed unless you can
show you tried hard to break it and could not.

Rules:
- Prefer ONE demonstrated defect over ten speculative ones. For each finding,
  state the concrete input or interleaving that triggers it and the resulting
  wrong behaviour.
- Attack the reasoning, not just the syntax. If a comment claims an invariant,
  ask whether the code actually establishes it, and whether the invariant is
  even the right one.
- Look specifically for: guards that can be bypassed by a path the author did
  not enumerate; error handling that converts a loud failure into a silent one;
  concurrency interleavings; changes whose tests assert the outcome rather than
  the mechanism; and any place a claimed measurement would not actually hold.
- Rank findings by severity, and say plainly when something is a nit.
- If you believe a specific area is under-tested, name the test that is missing
  and what it would assert.
- Be concise. No preamble."""


def build_prompt(diff, files, focus):
    parts = []
    if focus:
        parts.append(f"## Focus\n\n{focus}\n")
    if files:
        parts.append("## Full files (post-change), for context beyond the diff\n")
        for path, body in files:
            parts.append(f"### {path}\n```typescript\n{body}\n```\n")
    parts.append("## The change under review (unified diff)\n")
    parts.append(f"```diff\n{diff}\n```\n")
    parts.append("Report the defects you found, most severe first.")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", required=True)
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--focus", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--key-file", default="")
    ap.add_argument("--max-tokens", type=int, default=16000)
    a = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key and a.key_file:
        m = re.search(r"sk-or-[A-Za-z0-9_-]+", open(a.key_file).read())
        if m:
            key = m.group(0)
    if not key:
        sys.exit("No API key: set OPENROUTER_API_KEY or pass --key-file")

    diff = open(a.diff, encoding="utf8", errors="replace").read()
    files = [(p, open(p, encoding="utf8", errors="replace").read()) for p in a.file]
    prompt = build_prompt(diff, files, a.focus)

    approx = (len(SYSTEM) + len(prompt)) // 4
    print(f"model={a.model}  approx input tokens={approx:,}", file=sys.stderr)

    body = json.dumps({
        "model": a.model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": a.max_tokens,
    }).encode()

    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "plur pre-release adversarial audit",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:600]}")

    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or "(empty response)"
    usage = data.get("usage") or {}
    print(f"usage: {usage}", file=sys.stderr)

    if a.out:
        open(a.out, "w", encoding="utf8").write(text)
        print(f"written: {a.out}", file=sys.stderr)
    print(text)


if __name__ == "__main__":
    main()
