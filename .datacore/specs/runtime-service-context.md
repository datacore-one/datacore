# Linux runtime security contexts

The `datacore-runtime@.service` template is a deployment building block for
separating an executor from an operator and from the execution-admission
controller. One context represents one set of permitted data and credentials;
it does not represent an agent personality. Distinct personalities can share a
context when their access policy is the same.

The template requires a system service manager with DynamicUser, LoadCredential,
ProtectProc and the other declared sandbox settings. Qualify it on the actual
host; an ignored or unsupported directive is not a successful deployment.
The launcher refuses root, ordinary login accounts, missing privilege limits,
nonprivate state, malformed profiles and credential scope mismatches.
Each instance explicitly uses `User=dc-%i`; the launcher checks that the OS
identity matches its context. Omitting this directive lets template instances
share systemd's default user and permits access to peer process environments.

## Files

Install the template in `/etc/systemd/system/datacore-runtime@.service` and the
launcher in `/opt/datacore/lib/runtime_context.py`. Both and their parent
directories must be administrator owned and unavailable for worker writes.
Each administrator-controlled `/etc/datacore/runtime/CONTEXT.json` contains:

```json
{
  "version": 1,
  "command": ["/opt/datacore/providers/example/venv/bin/python", "-I", "-m", "example"],
  "environment": {},
  "credential_names": ["EXAMPLE_API_KEY"]
}
```

Use a lowercase context name of at most 24 characters, beginning with a letter,
with only letters, digits and hyphens. Store the corresponding credential JSON
in `/etc/datacore/runtime/CONTEXT.secrets.json`, root owned, mode 0600:

```json
{"EXAMPLE_API_KEY": "replace-with-this-contexts-assigned-credential"}
```

The credential keys must match the declared names exactly. Do not include
operator SSH keys, a raw credential repository, controller credentials, or
credentials assigned exclusively to another context. Credentials must not be
embedded in command arguments or the nonsecret profile. The launcher supplies a
fresh environment; shell startup, interpreter injection and identity overrides
are refused. It does not inherit operator or service-manager secrets.

## Code and data binding

`DATACORE_ROOT` selects data, not a policy implementation. A bundled Hermes
plugin and the bundled hook/job entrypoints use helpers from their own code
installation. A partial installation must fail before executing protected work.
An explicit `DATACORE_LIB` is exclusive: invalid or missing overrides cannot
fall back to data-checkout code. The plugin and stream tailer refuse to combine
already-imported identity, policy or persistence modules from another release.
Restart the process when changing its installed code directory.

Standalone copies of the stream tailer require an explicit `DATACORE_LIB` when
their helpers are not adjacent. Legacy standalone Hermes plugins can discover
a complete data/runner checkout, but managed deployments must bind them
explicitly to the administrator-controlled release. A symlink to the bundled
plugin resolves back to that release. Code-path binding is not OS isolation;
the service boundary and immutable release permissions remain necessary.

## Persistent state and cutover

Systemd owns identity allocation and the persistent directory
`/var/lib/datacore-workers/CONTEXT`. The worker receives this private HOME,
`HOME/Data` as DATACORE_ROOT and `HOME/state` as DATACORE_STATE. The template does
not bind the operator home, synchronized controller state, or arbitrary host
data into this directory. Stage only the authorized context's data and provider
state here. Install provider code and a verified dependency environment below
`/opt/datacore/providers/`; workers must not own these installation directories.

This template does not migrate a running provider. Before cutover, quiesce its
writers, save a private verified backup including SQLite WAL state, and preserve
original data and permissions. Transfer the intended state and scoped
credentials, configure integrations for the new paths, then verify startup,
normal operation, restart and data preservation. Never run old and replacement
gateways concurrently against the same live work or bot identity. A rollback
must reconcile writes made after cutover before resuming the previous instance.

Restart is deliberately manual by default. A deployment enabling automatic
restart must first establish the provider's retry and recovery semantics.

## Required negative verification

From the running worker and a child terminal process, verify that operator
credentials, peer-context credentials, admission-controller state and host
management sockets cannot be accessed; administrator privilege escalation must
fail. Verify that assigned credentials remain available, permitted data remains
writable, and acknowledged local state survives restart. Use disposable data
for destructive and interruption tests. Process/credential isolation does not
supply network service authentication, cross-host fencing or external-effect
idempotency; these require their own controls and verification.

Keep two distinct contexts running simultaneously and verify different UIDs,
denial of peer process-environment reads and signal permission, and denial of
peer state and credentials in both directions. Testing only against an operator
account does not establish isolation between workers.

The profile is not a declaration that a pre-existing gateway has been isolated.
Only an installed, negatively tested service establishes that deployment fact.
