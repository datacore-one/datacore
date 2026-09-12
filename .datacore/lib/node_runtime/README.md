# Qualified Node integration profile

This directory declares the Node release and PLUR MCP/CLI dependencies used by
Datacore's Linux x64 integration qualification. It complements the Hermes
Python profile in `../hermes_runtime/`. It does not move operator data, configure
credentials, replace a live service, or establish an OS security boundary.

`manifest.json` pins the official Node archive and executable hashes. The PLUR
package and lock files pin the complete npm graph; selected native inference,
image and SQLite packages are required even though the upstream application
marks them optional. Runtime installation must not silently omit these features.
The Datacore MCP source commit is recorded separately: build that repository
with its own committed lockfile and run its `npm run verify` and TypeScript check.

## Reproduce the PLUR profile

Use a disposable unprivileged Linux context with no operator HOME, credentials,
Git/npm configuration or host management sockets. Obtain the declared Node
archive from its recorded official URL and verify SHA-256 before extraction.
Keep the archive's bundled npm, node-gyp and Node headers together. Use separate
empty files for npm's user and global configuration; npm rejects one filename
used for both. Set the registry to `https://registry.npmjs.org` and a disposable
cache directory. Copy `plur.package.json` to `package.json` and
`plur.package-lock.json` to `package-lock.json` in the build directory, together
with `verify-plur.mjs` and `deny-archive.cjs`.

Run the pinned npm's `ci --ignore-scripts --no-audit --no-fund`. All acquisition
must come from the lockfile; do not use an unversioned registry runner or copy a
global installation. Then deny external networking. Explicitly run the pinned
npm's bundled `node-gyp/bin/node-gyp.js rebuild --release --nodedir=NODE_PREFIX`
in `node_modules/better-sqlite3` using the selected Node executable. No ONNX
postinstall download is needed for the qualified Linux CPU profile.

Run:

```
python3 verify_environment.py BUILD_DIRECTORY --node NODE_PREFIX/bin/node
NODE_PREFIX/bin/node --require ./deny-archive.cjs ./verify-plur.mjs DATACORE_MCP_SDK_DIRECTORY
```

The SDK argument identifies `node_modules/@modelcontextprotocol/sdk` in the
qualified Datacore MCP installation. This is an existing locked test client,
not a package fetched at verification time. The tests use fresh private storage:
MCP discovery, two-process writes, exact retry, malformed input, restart
retrieval, native ONNX CPU inference, transformer import and an image round trip.
The image check also requires a fixed libheif version. Toy inference and module
import do not prove that a deployment's configured embedding model loads; check
that separately against its model cache and actual configuration.

For Datacore MCP, use a fresh offline `npm ci --omit=dev --ignore-scripts`
from the populated cache to obtain production dependencies after building.
`npm prune` can rewrite lock metadata; disabling its lock can request uncached
registry metadata. Retain the committed lock exactly and rerun the protocol and
native persistence checks after the production installation.

Seal verified code and dependencies under an administrator-owned immutable
release directory. Record and verify a complete file/symlink/mode manifest.
Stop the build context before sealing. Repeat behavior checks from a fresh
context with a different UID and no shared writable build state; explicitly
verify denial of code writes. A reused DynamicUser UID is not evidence of a
different identity. Run the profile verifier on the final installed paths too.
Only then reconcile the authorized service's explicit Node/MCP/CLI paths.
Follow `../../specs/runtime-service-context.md` for data preservation, credential
assignment, quiescence and cutover requirements.

## Advisory evaluation

The pinned graph upgrades affected URI, HTTP/query parsing, YAML and image
libraries. The 2026-09-12 offline npm advisory snapshot reports
[GHSA-vwc7-r8mq-g2x9](https://github.com/advisories/GHSA-vwc7-r8mq-g2x9)
in `adm-zip` 0.6.0, for which that record lists no fixed version. The dependency
is used by ONNX's installation helper; this profile disables install scripts
and uses packaged Linux CPU binaries. `deny-archive.cjs` makes qualification fail
if text-memory runtime requests load that dependency. This is reachability
evidence, not a patch or a runtime security boundary. Any change that enables
archive installation/extraction requires a new assessment, including destination
symlinks and private temporary directories. Do not suppress the scanner match
or describe the library itself as fixed. Refresh advisory data for each release.
