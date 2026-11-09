# Audit behavior options

Status: audit-branch implementation, not a deployment or merge approval. The
instance-bound execution proposal remains parked under
[issue #192](https://github.com/datacore-one/datacore/issues/192). Existing deployed
services have not been reconfigured by this change.

## Defaults and explicit opt-ins

All switches below accept only `0` or `1`. Omitted means `0`. Invalid values are
configuration errors. Set them in the environment of the relevant service and
restart that service deliberately; they do not migrate persisted data. Keep
producer and consumer settings consistent when enabling a workflow across hosts.

| Setting | Default `0` | Explicit `1` |
| --- | --- | --- |
| `DATACORE_REVIEW_BEFORE_EXECUTION` | Existing approved machine tasks do not acquire a new freshness/contract requirement. Unapproved machine tasks retain the existing review gate. CoS briefing status remains independent of optional review failure; component failures remain recorded. | Current successful review and execution contract required for approved machine work; incomplete review makes the daily orchestration fail. |
| `DATACORE_CADENCE_PROPOSALS` | Cadence generator retains AI tags and writes to `org/next_actions.org`; heartbeat uses its existing direct Claude CLI execution mode. | Cadences/signals enter `org/inbox.org` without an AI dispatch tag or fabricated approval; heartbeat captures work instead of launching the model. |
| `DATACORE_INSTANCE_BOUND_EXECUTION` | No protocol-3 allocation requirement, installation UUID provisioning, or CoS publication of the parked allocation. | Experimental instance-bound allocation admission and receipts. Requires separately provisioned, protected controller state and matching producer/executor configuration. This is not approved for deployment. |
| `DATACORE_SCOPED_MODULE_NAMES` (MCP) | Existing `datacore_<module>_<tool>` names. Select one module context using `DATACORE_SPACE`, otherwise the unique personal space. Code precedence: selected space, personal, global. | Advertise the accessible scopes separately using `datacore_<space>_<module>_<tool>` for scoped modules. Global names remain unqualified. |

Compatibility configuration (these are also the code defaults):

```sh
DATACORE_REVIEW_BEFORE_EXECUTION=0
DATACORE_CADENCE_PROPOSALS=0
DATACORE_INSTANCE_BOUND_EXECUTION=0
DATACORE_SCOPED_MODULE_NAMES=0
```

`DATACORE_SPACE` is an optional canonical space name, not an ordinal folder label.
An invalid selector, duplicate best-precedence module, or failed selected module
refuses that registration; another module must not silently receive the call.
The selector affects module tools; existing core tool space arguments remain.
Names and data routing are not authorization or operating-system isolation.

## Existing module data

Retain `[space]/.datacore/modules/<module>/data` when this is private user-space
storage separate from installed code. Retain already separated
`[space]/.datacore/module-data/<module>/data` when it contains the selected state.
No automatic migration or empty replacement store is introduced. Use the latter
layout for copied/linked scoped code that would otherwise overlap private data.
Private files already inside code, conflicting stores, aliases, or incomplete
migration receipts require preservation and explicit reconciliation. Existing
settings and workflow state remain beside their existing data directory.

## Safety fixes that remain active

These are corrections to data/authority handling, not optional workflow policies:

- Safe paths, atomic preservation, serialization, exact scope selection and stale
  registration checks remain enforced. Disabling a policy cannot disable them.
- Machine-generated tasks retain their true ORIGIN and do not manufacture
  APPROVED_BY. Restoring the AI tag does not forge human authorization.
- A successful process or model string is not durable cadence completion. Actual
  task/output evidence remains necessary before advancing the cadence record.
  A legacy inline heartbeat can run, but its text alone does not advance history.
- Combined intent reports remain owner-private rather than being written into a
  shared team space. Old reports are retained, not silently moved or removed.
- Automatic Git publication still requires authority for all outgoing history,
  not only the latest selected file. Existing unverified history may hold a push;
  local work is retained. Normal hooks and secret checks remain active.
- Approval content binding, operator pause controls, and credential-safe errors
  remain active. Previously incomplete approval data can require reapproval.
- UTC remains the accepted internal time basis. No host timezone is changed here.

## Known compatibility qualifications

The source audit branches remain mixed and are **not merge-ready**. Legacy
advisory allocation interoperability is not verified by disabling protocol 3;
do not roll this runner out over a service that depends on those allocations.
The wider instance/takeover proposal and its normative DIP changes remain parked.
The owner's separate decision about automatic retry after an uncertain external
effect remains pending. The current duplicate-effect protections are unchanged.

Full MCP mode and the audited orchestration modules require a compatible installed
Datacore core and explicit dependency environment. This audit-added runtime
coupling is still present; a naming option does not remove it. Core-only MCP
mode remains separate. Matched source tests do not prove an older deployed core
is compatible. Runtime isolation, legacy allocation compatibility and supported
installed versions still require qualification before release.

Turning an option back off does not delete captured inbox tasks, data stores,
proofs, or allocation receipts. Pending proposals remain for normal review;
uncertain receipts remain for reconciliation. No automatic recovery migration
runs as a side effect of changing a setting.
