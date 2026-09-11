# Core and OCR runtime profile

`requirements-runtime.in` declares the core library, feed reader, optional
cloud speech formatter and OCR service dependencies. `requirements-runtime.txt`
locks their transitive versions and artifact hashes. It is separate from the
test-tool environment in `requirements-audit.txt`.

Install into a new virtual environment without `--system-site-packages`:

```sh
python3 -m venv /path/to/new-runtime
/path/to/new-runtime/bin/python -m pip --isolated install \
  --index-url https://pypi.org/simple --require-hashes --only-binary=:all: \
  -r .datacore/lib/requirements-runtime.txt
/path/to/new-runtime/bin/python -m pip check
/path/to/new-runtime/bin/python .datacore/lib/runtime_smoke.py
```

Install Tesseract and Poppler through the host package manager before the
smoke check. Keep the previous environment available until the new environment
passes qualification, then configure services to use its exact interpreter.
Do not move a virtual environment after creation: its entry-point paths may
refer to its original location.

The smoke check uses disposable synthetic images and mocked speech delivery.
It starts and stops the actual OCR MCP server twice, exercises image extraction,
checks a custom MCP lifespan, and exercises the installed gTTS formatter with
its unsafe transport and Click editor/pager functions disabled. It makes no
provider speech request. This check does not establish OS isolation, validate
fleet coordination, or qualify other modules' separate dependency profiles.

CI checks the locked profile on Linux with Python 3.10, 3.12 and 3.14. A host
still needs its own service, native-library and installed-module qualification.

## Evaluated dependency behavior

gTTS 2.5.4 restricts Click to versions below 8.2. Those versions are affected
by [CVE-2026-7246 / PYSEC-2026-2132](https://github.com/pypa/advisory-database/blob/main/vulns/click/PYSEC-2026-2132.yaml),
which concerns editor/pager shell execution. The fix is described in the
[Click 8.3.3 release](https://github.com/pallets/click/releases/tag/8.3.3).
The inspected Datacore speech and OCR paths do not call these functions;
the runtime smoke check also rejects their use in speech generation. This is
a bounded reachability assessment, not a waiver for unrelated Click consumers.
Re-evaluate it when adding a CLI path or changing these dependencies. Do not
force an incompatible Click version past gTTS's declared requirements.

gTTS's own `stream()` and `save()` disable certificate verification and write
directly to the destination. Datacore uses only its request-body formatter.
`speech_transport.py` owns explicit cloud consent, bounded verified HTTPS,
redirect refusal and atomic publication. `public_download.post` enforces a
socket deadline even if the peer trickles response bytes.

MCP 1.28.1 with pydantic-settings 2.15 reports an unresolved `lifespan` annotation
warning while defining its settings class. Explicit lifespan callbacks and the
OCR stdio lifecycle passed the smoke check. Environment-derived callable
lifespans are not part of this profile; the warning is recorded, not suppressed.

Regenerate the lock using the command in its header with uv 0.12.13, then scan
the resulting package set and run compatibility checks before adoption. A
successful dependency resolver or zero scanner matches alone does not qualify
an installation.
