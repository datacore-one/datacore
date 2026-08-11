#!/usr/bin/env python3
"""Resolve a conflicted cadence-log.yaml by merging git stages 2 and 3.

The file is a map of cadence-name -> {last_run, result, notes}. A textual
merge of it is meaningless and, done with a regex, actively dangerous: it
produced nested markers that were then committed. Merge the DATA instead.

Union of keys; for a key on both sides the entry with the later last_run wins.
Refuses to write if either stage already carries markers, or if the result
would.
"""
import subprocess, sys, yaml

p = sys.argv[1]

def stage(n):
    r = subprocess.run(['git', 'show', f':{n}:{p}'], capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    if '<' * 7 in r.stdout:
        sys.exit(f"REFUSING: stage {n} of {p} already contains conflict markers")
    return yaml.safe_load(r.stdout) or {}

ours, theirs = stage(2), stage(3)
lr = lambda e: str((e or {}).get('last_run') or '')

merged, added, bumped = dict(ours), 0, 0
for k, v in theirs.items():
    if k not in merged:
        merged[k] = v; added += 1
    elif lr(v) > lr(merged[k]):
        merged[k] = v; bumped += 1

text = yaml.safe_dump(merged, default_flow_style=False, sort_keys=True,
                      allow_unicode=True, width=100)
if '<' * 7 in text:
    sys.exit("REFUSING: merged result contains conflict markers")
open(p, 'w').write(text)
yaml.safe_load(open(p))   # must reparse
print(f"    resolved {p}: {len(merged)} cadences (+{added} new, {bumped} bumped)")
