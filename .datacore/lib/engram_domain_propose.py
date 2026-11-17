#!/usr/bin/env python3
"""Propose a `domain` for engrams that have none (plur-ai/plur#671).

An engram without a domain cannot auto-route to a team scope — it falls to
global regardless of content. This is the propose half of propose-then-apply:
it writes a reviewable proposal file and changes nothing.

Rules are lexical and deliberately conservative. An engram that matches no
rule is left unproposed rather than given a plausible-looking domain, because
a wrong domain routes knowledge to the wrong team, which is worse than none.
"""
import os, re, sys, yaml, collections, json

RULES = [
 (r'\bplur[_ -]?(learn|recall|inject|forget|pin|scope|pack)|engram schema|activation|'
  r'spreading activation|rerank|bm25|embedding|hybrid search|dedup|tension',  'plur.engineering.core'),
 (r'\bmcp\b|model context protocol|tools?\.ts|server\.ts|stdio',              'plur.engineering.mcp'),
 (r'\bpack\b|hub\b|marketplace|capsule|\.plur\b',                             'plur.engineering.packs'),
 (r'provenance|prov-o|signed|signature|attest|tamper|audit chain|scitt',      'plur.engineering.provenance'),
 (r'scope|permission|acl|tenant|review polic|promote|team store',             'plur.engineering.scopes'),
 (r'\bci\b|release|publish|npm|pypi|version bump|changelog|workflow',         'plur.engineering.release'),
 (r'benchmark|longmemeval|locomo|recall@|hit@|plur-bench',                    'plur.research.benchmarks'),
 (r'competitor|mem0|letta|\bzep\b|weaviate|cognee|caura|origintrail',         'plur.research.competitors'),
 (r'positioning|messaging|tagline|share of voice|\bgeo\b|blog|dev\.to|tweet|'
  r'linkedin|content calendar',                                              'plur.comms.positioning'),
 (r'pricing|discount|list price|seat|invoice|contract|mfn|licence|license',   'plur.leadership.pricing'),
 (r'roadmap|milestone|intent graph|strategy|fundrais|investor|cap table',     'plur.leadership.strategy'),
 (r'enterprise|igea|\bsrc\b|customer|deploy|onboard|integrator',              'plur.enterprise.delivery'),
 (r'server|ssh|dns|systemd|nginx|caddy|backup|cron|host\b|infra',             'plur.infra'),
 (r'org-mode|org file|gtd|next_actions|inbox\.org|nightshift|datacore',       'datacore.gtd'),
]

def main():
    path = os.path.expanduser('~/.plur/engrams.yaml')
    d = yaml.safe_load(open(path))
    engrams = d['engrams'] if isinstance(d, dict) else d
    nodom = [e for e in engrams
             if not e.get('domain') and e.get('status') == 'active']
    props, unmatched = [], []
    for e in nodom:
        text = f"{e.get('statement','')} {e.get('rationale','')}".lower()
        for pat, dom in RULES:
            if re.search(pat, text):
                props.append({'id': e['id'], 'scope': e.get('scope'),
                              'domain': dom,
                              'statement': str(e.get('statement',''))[:110]})
                break
        else:
            unmatched.append({'id': e['id'], 'scope': e.get('scope'),
                              'statement': str(e.get('statement',''))[:110]})
    out = os.path.join(os.path.dirname(path), 'domain-proposal.yaml')
    yaml.safe_dump({'generated': '2026-09-02',
                    'total_active_without_domain': len(nodom),
                    'proposed': len(props),
                    'unmatched': len(unmatched),
                    'proposals': props, 'unmatched_engrams': unmatched},
                   open(out, 'w'), sort_keys=False, allow_unicode=True, width=100)
    c = collections.Counter(p['domain'] for p in props)
    print(f"{len(engrams)} engrams total")
    print(f"{len(nodom)} active without a domain")
    print(f"{len(props)} proposed ({100*len(props)//max(len(nodom),1)}%), "
          f"{len(unmatched)} left unproposed on purpose\n")
    for k, v in c.most_common():
        print(f"  {v:5}  {k}")
    print(f"\nwrote {out} — review, then apply. Nothing was changed.")

if __name__ == '__main__':
    sys.exit(main())
