"""The pre-push scanner is the only gate between this repo and a public secret
leak, and it had no tests (independent review 2026-09-03). These pin the
matching helpers and the category lists the new-file gate is built from."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("pps", ROOT / ".datacore" / "lib" / "pre_push_scan.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


S = _load()


def test_new_file_allowlist_admits_bin_and_lib_but_not_secrets():
    allow = S.DATACORE_NEW_FILE_ALLOW
    assert any(S.single_level_match(".datacore/bin/creds", a) for a in allow)
    assert any(S.single_level_match(".datacore/lib/jobs/run.py", a) for a in allow)
    assert not any(S.single_level_match(".datacore/secrets/credential-index.yaml", a) for a in allow)
    assert not any(S.single_level_match(".datacore/private/notes.md", a) for a in allow)
    assert not any(S.single_level_match(".datacore/env/.env", a) for a in allow)


def test_reject_categories_never_belong_on_a_public_repo():
    reject = S.DATACORE_NEW_FILE_REJECT if hasattr(S, "DATACORE_NEW_FILE_REJECT") else []
    assert reject, "the scanner must carry an explicit reject list"
    for p in (".datacore/secrets/x.yaml", ".datacore/private/x.md", ".datacore/cos/priorities.yaml"):
        assert S.match_any(p, reject), f"{p} must be rejected outright"


def test_single_level_match_does_not_let_a_star_swallow_a_slash():
    assert S.single_level_match(".datacore/x", ".datacore/*")
    assert not S.single_level_match(".datacore/x/y", ".datacore/*")
    assert S.single_level_match(".datacore/x/y/z", ".datacore/x/**")


def test_glob_match_prefix_and_suffix_semantics():
    assert S.glob_match("1-datafund/.datacore/state/heartbeat.json", "**/.datacore/state/**")
    assert S.glob_match("docs/enterprise/deal.md", "docs/enterprise/**")
    assert S.glob_match("a/b/id_ed25519", "**/id_ed25519")
    assert not S.glob_match("docs/public/x.md", "docs/enterprise/**")
