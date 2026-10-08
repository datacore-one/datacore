# Local MCP / CoS / Nightshift installation

Status: implementation verified with a disposable three-replica drill and focused regression tests. Actual workstation/server qualification remains a deployment step. App UI work follows this work.

## DONE_WHEN

- Cursor captures rich tasks through its local MCP and the existing structured Org adapter; capture and explicit delegation stay distinct.
- Nightshift records artifact references and commit evidence consistently; failed outcome appends are retained for retry and reported, never silently treated as a complete audit trail.
- The existing ledger remains the handoff and history. Completion stays REVIEW until another actor verifies it. No shared MCP or second task queue is introduced.
- macOS scheduling selects launchd, invokes the actual installed entry points with a usable interpreter, preserves paths with spaces, and rejects unsupported schedules rather than widening them.
- A documented, repeatable new-user setup uses declared per-writer identities, optional signing with explicit verification status, a private Git remote, and curated CoS/Nightshift schedules without personal fleet defaults.
- Windows setup has an explicit supported POSIX/WSL route for local MCP instead of claiming native support while importing fcntl.
- A disposable multi-replica acceptance test exercises rich capture, ledger replication, Nightshift claim/outcome, artifact delivery, separate-actor verification and convergence, including offline/retry evidence.
- Platform or authenticated-provider checks unavailable on this development host are named explicitly; fixture results are not presented as proof of a real Windows or fresh Mini deployment.

## Boundaries

Reuse the adapter, ledger, transport, scheduler adapters and CoS workflows. Do not change DIPs, install production schedules, send chat messages, rotate credentials, copy private user data, or modify the datacore-app in this work. Chat is optional; choosing and authenticating a channel is installation configuration. Keep existing working deployments compatible.

## Verification

Targeted regression tests plus a disposable installation/handoff drill. Real Cursor approval, model authentication, Windows/WSL runtime, launchd activation and reboot/sleep behavior need an actual deployment check.

Verified: 127 core/adapter/transport/installation tests, 64 Nightshift lifecycle/claim/delivery/scheduler tests, and 69 CoS tests (260 passed). The replica drill invokes the real MCP task handler and structured adapter on two independent roots, converges through a private bare Git remote, records Nightshift claims and deterministic output, checks REVIEW retention, rejects self-verification, then converges human verification back to all replicas. Fault tests cover a partial outcome append and a lost acknowledgement without duplicate events. macOS plutil accepts the generated schedule. This is not a GUI or authenticated model execution test.

Implementation entry points: `server_setup.py plan|doctor|apply|activate`, Cursor installer `--wsl-distribution`, Nightshift `audit-reconcile`. Installation guide: `.datacore/docs/cos-nightshift-install.md`.
