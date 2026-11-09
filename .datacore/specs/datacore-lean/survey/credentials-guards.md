# Survey: credentials, security, guards (2026-09-23)

Read-only subagent survey; no secret values read. Suspicions cite lines and are UNVERIFIED until modelled or replayed.
CLAUDE.md broker claims: "stdout only" HOLDS (creds.py:692 vs 668/681/690); "verifies before returning" and "n-a never a pass" FAIL in `creds get` (#1); "prefers this host's own value" holds in credential_access.resolve but is reversed by env_utils.load_env_files (#2).

1. **creds get/doctor verdicts** (creds.py:642-753, credential_access.py:724-919) — n-a served with exit 0 (689-693); `get` calls verify_value without `entry=` (675) so `disabled` (:863), api_base verifiers (:876) and OAuth1 skipped in get but applied in doctor; doctor passes `c.extra` which excludes `provider`/`id` (225-228, 255) so gitea/gitlab verifiers (741-748) never fire and any 2xx is "ok" (914); per-credential lock keyed on raw arg (661); doctor crashes when index is None (707-708). Model S.
2. **credential precedence / env parsing** (credential_access.py:144-494, env_utils.py:64-132, config_plane.py:103-158) — load_env_files first-wins over [.env, local.env] (118-130) so fleet beats host (pinned by tests/test_env_configuration.py:48-54; 14 comms callers); four parsers disagree on duplicate keys (first / last / error / last); path stores not `~`-expanded (167 vs 360); ConfigError echoes raw_line (141). Model M.
3. **model_routing privacy floor** (model_routing.py:46-137) — "Sensitive tasks NEVER escalate to frontier models" (10) but escalation (98-105) overwrites the floor without re-check: pick_model("contract-review", venture="dmcc") → high-stakes → claude-opus; unknown class ranks 0 (48-50). Model S.
4. **hooks/restricted_hosts_guard** (166-275) — git only recognised as the first token of the whole command (194, 216): `cd repo && git push`, `FOO=1 git push`, `env git push` pass; `git -c k=v push` passes (200-204); exceptions → exit 0 (278-282, 242); git error = "not a repo" (146-147). No tests. Model M.
5. **tool_policy.decide** (114-269) — call_text matches only _TEXT_KEYS when any present (120-126): MultiEdit `edits[].new_string`, NotebookEdit `new_source` unmatched; docstring vs code disagree on unlisted principal (code safer). Model S.
6. **hooks/injection_integrity_guard** (69-131) — any mention of the path clears the gate (129-131): partial Read/head/ls clears; Bash allowed while armed (42); all failures exit 0. No tests. Model S.
7. **hooks/log_ownership_guard** (104-253) — `--no-merges` skips evil merges; author-email filter forgeable; failed rev-list allows push. Model S–M.
8. **egress_runtime_check / egress_scan** — all-n-a exits 0 (253); `--enforce` skips manifest parse errors (191-193) and syntax-error files (154). No tests. Model S.
9. **hooks.py retry/escalation** (946-1066) — retry counter keyed "default" (1016, task_id never present 958-963), never reset, persisted: after 3 lifetime transient errors never retries; unknown validate hook skipped → "Validation passed" (893-895). Model S.
10. **file_utils atomic write / lock / CAS** — well built; only the lock-domain mismatch from #1. Model M, low odds.
11. **pre_push_scan / git_privacy** — YAMLError exits 1 not 2 (202-210, 334); new-file gate only on status A (279); empty stdin = clean (213-215). Model M.
12. **commit_gate partition** (58-82) — no suspicion; easy proof.

Not candidates: secret_http, safe_move, process_run, bounded_dns, http_utils, yaml_safety, hook_state, git_publication, org_guard (advisory), space_policy_guard (fails open by design; MultiEdit/NotebookEdit allowed :135), actor_identity, principals_check, plur_secret_exposure_scan, hooks_composer.
