"""Tests for config_plane.py -- the canonical env-file loader.

`load()` is pure file parsing: it must never read or write `os.environ` --
merging a loaded dict with the process environment is left to callers.
Every parse-variant test therefore also gets a companion assertion (see
`test_load_never_touches_os_environ`) proving that discipline holds.

Malformed lines (no `=`, or a key that doesn't match
`[A-Za-z_][A-Za-z0-9_]*`) are collected into a single `ConfigError` --
never raised one at a time -- so a caller sees every problem in one pass.

The `doctor()` tests below (Task 3.2) cover the manifest-required-vars +
legacy-env audit. The binding rule there: the `DoctorReport` and its
`table` never carry variable VALUES -- only names and source names.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

import config_plane
from config_plane import (
    LEGACY_SOURCES,
    ConfigError,
    DoctorReport,
    check_permissions,
    doctor,
    load,
)
from jobs.manifest import ManifestError


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "env"
    path.write_text(text)
    return path


# --- parse variants ----------------------------------------------------------


def test_parses_plain_key_value_line(tmp_path):
    path = _write(tmp_path, "FOO=bar\n")
    assert load(path) == {"FOO": "bar"}


def test_parses_multiple_lines(tmp_path):
    path = _write(tmp_path, "FOO=bar\nBAZ=qux\n")
    assert load(path) == {"FOO": "bar", "BAZ": "qux"}


def test_tolerates_export_prefix(tmp_path):
    path = _write(tmp_path, "export FOO=bar\n")
    assert load(path) == {"FOO": "bar"}


def test_export_prefix_only_matches_exact_string_with_space(tmp_path):
    # "exported" is not "export " -- the key is the literal token before '='.
    path = _write(tmp_path, "exported=bar\n")
    assert load(path) == {"exported": "bar"}


def test_export_prefix_tolerated_after_leading_whitespace(tmp_path):
    path = _write(tmp_path, "   export FOO=bar\n")
    assert load(path) == {"FOO": "bar"}


def test_double_quoted_value_is_unwrapped(tmp_path):
    path = _write(tmp_path, 'FOO="bar"\n')
    assert load(path) == {"FOO": "bar"}


def test_single_quoted_value_is_unwrapped(tmp_path):
    path = _write(tmp_path, "FOO='bar'\n")
    assert load(path) == {"FOO": "bar"}


def test_mismatched_quotes_are_not_stripped(tmp_path):
    # Starts with " ends with ' -- not a matching pair, left untouched.
    path = _write(tmp_path, "FOO=\"bar'\n")
    assert load(path) == {"FOO": "\"bar'"}


def test_single_quote_char_alone_is_not_stripped(tmp_path):
    # A lone quote character is not a matching pair of length >= 2.
    path = _write(tmp_path, 'FOO="\n')
    assert load(path) == {"FOO": '"'}


def test_value_containing_equals_sign_splits_on_first_only(tmp_path):
    path = _write(tmp_path, "FOO=bar=baz=qux\n")
    assert load(path) == {"FOO": "bar=baz=qux"}


def test_value_containing_hash_is_not_treated_as_comment(tmp_path):
    # Inline comments are NOT stripped -- a '#' inside an unquoted value
    # is part of the value, since env files don't reliably support them.
    path = _write(tmp_path, "FOO=bar#not-a-comment\n")
    assert load(path) == {"FOO": "bar#not-a-comment"}


def test_quoted_value_containing_hash_preserved(tmp_path):
    path = _write(tmp_path, 'FOO="bar#baz"\n')
    assert load(path) == {"FOO": "bar#baz"}


def test_blank_lines_are_skipped(tmp_path):
    path = _write(tmp_path, "FOO=bar\n\nBAZ=qux\n")
    assert load(path) == {"FOO": "bar", "BAZ": "qux"}


def test_whitespace_only_lines_are_skipped(tmp_path):
    path = _write(tmp_path, "FOO=bar\n   \t  \nBAZ=qux\n")
    assert load(path) == {"FOO": "bar", "BAZ": "qux"}


def test_comment_lines_are_skipped(tmp_path):
    path = _write(tmp_path, "# a comment\nFOO=bar\n")
    assert load(path) == {"FOO": "bar"}


def test_comment_lines_with_leading_whitespace_are_skipped(tmp_path):
    path = _write(tmp_path, "   # indented comment\nFOO=bar\n")
    assert load(path) == {"FOO": "bar"}


def test_trailing_newline_is_stripped_not_part_of_value(tmp_path):
    path = _write(tmp_path, "FOO=bar\n")
    assert load(path)["FOO"] == "bar"
    assert "\n" not in load(path)["FOO"]


def test_file_without_trailing_newline_on_last_line(tmp_path):
    path = _write(tmp_path, "FOO=bar\nBAZ=qux")
    assert load(path) == {"FOO": "bar", "BAZ": "qux"}


def test_underscore_and_digit_key_is_valid(tmp_path):
    path = _write(tmp_path, "_FOO_1=bar\n")
    assert load(path) == {"_FOO_1": "bar"}


def test_empty_value_is_valid(tmp_path):
    path = _write(tmp_path, "FOO=\n")
    assert load(path) == {"FOO": ""}


# --- missing file -------------------------------------------------------------


def test_missing_file_returns_empty_dict(tmp_path):
    path = tmp_path / "does-not-exist"
    assert load(path) == {}


# --- malformed lines: collected into one ConfigError ---------------------------


def test_line_without_equals_is_malformed(tmp_path):
    path = _write(tmp_path, "FOO=bar\nNOTAKEYVALUE\n")
    with pytest.raises(ConfigError) as excinfo:
        load(path)
    message = str(excinfo.value)
    assert "2" in message


def test_bad_key_leading_digit_is_malformed(tmp_path):
    path = _write(tmp_path, "1FOO=bar\n")
    with pytest.raises(ConfigError) as excinfo:
        load(path)
    assert "1" in str(excinfo.value)


def test_bad_key_with_illegal_character_is_malformed(tmp_path):
    path = _write(tmp_path, "FOO-BAR=baz\n")
    with pytest.raises(ConfigError):
        load(path)


def test_config_error_is_a_value_error(tmp_path):
    path = _write(tmp_path, "NOTAKEYVALUE\n")
    with pytest.raises(ValueError):
        load(path)


def test_multiple_malformed_lines_all_collected_in_one_error(tmp_path):
    path = _write(tmp_path, "GOOD=1\nNOTAKEYVALUE\n1BAD=2\nOK=3\n9BAD=4\n")
    with pytest.raises(ConfigError) as excinfo:
        load(path)
    message = str(excinfo.value)
    # line 2 (no '='), line 3 (bad key), line 5 (bad key) -- all present.
    assert "2" in message
    assert "3" in message
    assert "5" in message


def test_malformed_error_names_line_numbers_and_reasons(tmp_path):
    path = _write(tmp_path, "NOTAKEYVALUE\n1BAD=2\n")
    with pytest.raises(ConfigError) as excinfo:
        load(path)
    message = str(excinfo.value).lower()
    # Each malformed line's message should distinguish "no equals" from
    # "bad key" -- not just repeat a generic "malformed line" string.
    assert "1" in message  # line number for NOTAKEYVALUE
    assert "2" in message  # line number for 1BAD=2


# --- CANONICAL_PATH / default path resolution ----------------------------------


def test_canonical_path_constant():
    # `datacore.env`, not a bare `env` -- `~/.datacore/env` was found by a
    # real-machine audit (Task 3.3 close) to already be a pre-existing
    # directory (older per-service-credential-file convention) on at least
    # one real machine; the self-describing filename sidesteps that
    # collision rather than relying on the directory guard alone.
    assert config_plane.CANONICAL_PATH == Path.home() / ".datacore" / "datacore.env"


def test_load_with_no_path_uses_canonical_path(tmp_path, monkeypatch):
    fake_canonical = tmp_path / "env"
    fake_canonical.write_text("FOO=bar\n")
    monkeypatch.setattr(config_plane, "CANONICAL_PATH", fake_canonical)
    assert load() == {"FOO": "bar"}


def test_load_with_no_path_and_missing_canonical_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config_plane, "CANONICAL_PATH", tmp_path / "nope")
    assert load() == {}


# --- never touches os.environ --------------------------------------------------


def test_load_never_touches_os_environ(tmp_path, monkeypatch):
    sentinel_key = "CONFIG_PLANE_TEST_SENTINEL_VAR"
    monkeypatch.delenv(sentinel_key, raising=False)
    path = _write(tmp_path, f"{sentinel_key}=some-value\n")
    result = load(path)
    assert result[sentinel_key] == "some-value"
    assert sentinel_key not in os.environ


def test_check_permissions_never_touches_os_environ(tmp_path, monkeypatch):
    sentinel_key = "CONFIG_PLANE_TEST_SENTINEL_VAR_2"
    monkeypatch.delenv(sentinel_key, raising=False)
    path = _write(tmp_path, "FOO=bar\n")
    path.chmod(0o644)
    check_permissions(path)
    assert sentinel_key not in os.environ


# --- check_permissions ----------------------------------------------------------


def test_check_permissions_warns_on_loose_mode_0644(tmp_path):
    path = _write(tmp_path, "FOO=bar\n")
    path.chmod(0o644)
    warnings = check_permissions(path)
    assert len(warnings) == 1
    assert str(path) in warnings[0]
    assert "644" in warnings[0]


def test_check_permissions_clean_on_0600(tmp_path):
    path = _write(tmp_path, "FOO=bar\n")
    path.chmod(0o600)
    assert check_permissions(path) == []


def test_check_permissions_clean_on_stricter_than_0600(tmp_path):
    path = _write(tmp_path, "FOO=bar\n")
    path.chmod(0o400)
    assert check_permissions(path) == []


def test_check_permissions_missing_file_returns_empty_list(tmp_path):
    path = tmp_path / "does-not-exist"
    assert check_permissions(path) == []


def test_check_permissions_warns_on_group_readable_only(tmp_path):
    # 0o640: owner rw, group r, other none -- group bit (0o040) trips the
    # (mode & 0o077) != 0 check even though "other" has no access.
    path = _write(tmp_path, "FOO=bar\n")
    path.chmod(0o640)
    warnings = check_permissions(path)
    assert len(warnings) == 1


def test_check_permissions_with_no_path_uses_canonical_path(tmp_path, monkeypatch):
    fake_canonical = tmp_path / "env"
    fake_canonical.write_text("FOO=bar\n")
    fake_canonical.chmod(0o644)
    monkeypatch.setattr(config_plane, "CANONICAL_PATH", fake_canonical)
    warnings = check_permissions()
    assert len(warnings) == 1
    assert str(fake_canonical) in warnings[0]


def test_check_permissions_returns_list_of_strings(tmp_path):
    path = _write(tmp_path, "FOO=bar\n")
    path.chmod(0o644)
    warnings = check_permissions(path)
    assert isinstance(warnings, list)
    assert all(isinstance(w, str) for w in warnings)


# --- non-file guard (Task 3.3 fix round) --------------------------------------
#
# A real-machine audit (Task 3.3 close) found `~/.datacore/env` -- the
# pre-fix canonical path -- already occupied by a pre-existing directory
# (an older per-service-credential-file convention). `load()`/
# `check_permissions()` had no guard against their target being a
# directory: `Path.read_text()` on a directory raises `IsADirectoryError`
# (an unguarded `OSError`). These tests pin the fix: a directory (or any
# non-regular-file path) is reported as a clean, named finding -- a
# `ConfigError` from `load()`, a warning string from `check_permissions()`
# -- never an uncaught `OSError`.


def test_load_raises_config_error_when_path_is_directory(tmp_path):
    path = tmp_path / "canonical-is-a-dir"
    path.mkdir()
    with pytest.raises(ConfigError) as excinfo:
        load(path)
    message = str(excinfo.value)
    assert "not a regular file" in message
    assert str(path) in message


def test_check_permissions_warns_when_path_is_directory(tmp_path):
    path = tmp_path / "canonical-is-a-dir"
    path.mkdir()
    warnings = check_permissions(path)
    assert len(warnings) == 1
    assert "not a regular file" in warnings[0]
    assert str(path) in warnings[0]


def test_load_directory_guard_never_touches_os_environ(tmp_path, monkeypatch):
    sentinel_key = "CONFIG_PLANE_DIR_GUARD_TEST_SENTINEL_VAR"
    monkeypatch.delenv(sentinel_key, raising=False)
    path = tmp_path / "canonical-is-a-dir"
    path.mkdir()
    with pytest.raises(ConfigError):
        load(path)
    assert sentinel_key not in os.environ


# --- doctor() -- manifest-required vars + legacy env audit (Task 3.2) --------


def _write_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _job(name: str, machine: str, required_env=None) -> dict:
    return {
        "name": name,
        "machine": machine,
        "schedule": "0 3 * * *",
        "cmd": "true",
        "artifacts": [{"path": "/tmp/x"}],
        "required_env": required_env or [],
    }


def _write_manifest(tmp_path: Path, jobs: list) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "jobs": jobs}, sort_keys=False))
    return path


def test_legacy_sources_constant_shape():
    assert LEGACY_SOURCES == {
        "cos.env": Path.home() / ".config" / "cos.env",
        "datacored.env": Path("/etc/datacored.env"),
        "hermes.env": Path.home() / ".hermes" / ".env",
    }


def test_doctor_report_is_dataclass_with_expected_fields():
    report = DoctorReport(missing=["A"], conflicts=[("A", "cos.env", "differs from canonical")], legacy_only={"cos.env": ["B"]}, table="# x")
    assert report.missing == ["A"]
    assert report.conflicts == [("A", "cos.env", "differs from canonical")]
    assert report.legacy_only == {"cos.env": ["B"]}
    assert report.table == "# x"


def test_doctor_missing_is_union_of_required_env_for_machines_jobs(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [
            _job("mac-a", "mac", ["FOO", "BAR"]),
            _job("mac-b", "mac", ["BAR", "BAZ"]),
        ],
    )
    canonical = _write_file(tmp_path / "canonical-env", "")
    report = doctor("mac", manifest_path=manifest, canonical_path=canonical, legacy_sources={})
    assert report.missing == ["BAR", "BAZ", "FOO"]


def test_doctor_missing_excludes_vars_present_in_canonical(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac", ["FOO", "BAR"])])
    canonical = _write_file(tmp_path / "canonical-env", "FOO=set\n")
    report = doctor("mac", manifest_path=manifest, canonical_path=canonical, legacy_sources={})
    assert report.missing == ["BAR"]


def test_doctor_missing_filtered_by_machine_ignores_other_machines_jobs(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [
            _job("mac-a", "mac", ["MAC_ONLY"]),
            _job("box-a", "box", ["BOX_ONLY"]),
        ],
    )
    canonical = _write_file(tmp_path / "canonical-env", "")
    report = doctor("mac", manifest_path=manifest, canonical_path=canonical, legacy_sources={})
    assert report.missing == ["MAC_ONLY"]
    assert "BOX_ONLY" not in report.missing


def test_doctor_missing_empty_when_all_required_vars_present(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac", ["FOO"])])
    canonical = _write_file(tmp_path / "canonical-env", "FOO=set\n")
    report = doctor("mac", manifest_path=manifest, canonical_path=canonical, legacy_sources={})
    assert report.missing == []


def test_doctor_conflict_detected_and_values_never_reported(tmp_path):
    secret = "sk-test-XYZ123"
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "API_KEY=sk-canonical-VALUE1\n")
    legacy_path = _write_file(tmp_path / "cos.env", f"API_KEY={secret}\n")
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    assert report.conflicts == [("API_KEY", "cos.env", "differs from canonical")]
    # SECRETS RULE: values never appear anywhere in the report -- names only.
    assert secret not in repr(report)
    assert "sk-canonical-VALUE1" not in repr(report)
    assert secret not in report.table
    assert "sk-canonical-VALUE1" not in report.table


def test_doctor_no_conflict_when_legacy_value_matches_canonical(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "API_KEY=same-value\n")
    legacy_path = _write_file(tmp_path / "cos.env", "API_KEY=same-value\n")
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    assert report.conflicts == []


def test_doctor_conflicts_only_compare_keys_present_in_both(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "ONLY_CANONICAL=x\n")
    legacy_path = _write_file(tmp_path / "cos.env", "ONLY_LEGACY=y\n")
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    assert report.conflicts == []


def test_doctor_conflicts_are_sorted(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "ZVAR=c1\nAVAR=c2\n")
    legacy_path = _write_file(tmp_path / "cos.env", "ZVAR=l1\nAVAR=l2\n")
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    assert report.conflicts == [
        ("AVAR", "cos.env", "differs from canonical"),
        ("ZVAR", "cos.env", "differs from canonical"),
    ]


def test_doctor_legacy_only_mapping_per_source(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "")
    legacy_path = _write_file(tmp_path / "cos.env", "LEGACY_VAR_B=1\nLEGACY_VAR_A=2\n")
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    assert report.legacy_only == {"cos.env": ["LEGACY_VAR_A", "LEGACY_VAR_B"]}


def test_doctor_legacy_only_across_multiple_sources(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "")
    cos_path = _write_file(tmp_path / "cos.env", "FROM_COS=1\n")
    hermes_path = _write_file(tmp_path / "hermes.env", "FROM_HERMES=1\n")
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": cos_path, "hermes.env": hermes_path},
    )
    assert report.legacy_only == {"cos.env": ["FROM_COS"], "hermes.env": ["FROM_HERMES"]}


def test_doctor_legacy_only_omits_source_with_no_extra_vars(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "SHARED=1\n")
    legacy_path = _write_file(tmp_path / "cos.env", "SHARED=1\n")
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    assert report.legacy_only == {}


def test_doctor_absent_legacy_file_skipped_silently(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "")
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": tmp_path / "does-not-exist.env"},
    )
    assert report.legacy_only == {}
    assert report.conflicts == []


def test_doctor_unparseable_legacy_file_is_finding_not_crash(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "")
    legacy_path = _write_file(tmp_path / "cos.env", "NOTAKEYVALUE\n")
    # Must not raise ConfigError -- an unparseable legacy source is a
    # finding surfaced in the table, not a crash.
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    assert "cos.env" not in report.legacy_only
    assert all(source != "cos.env" for _, source, _ in report.conflicts)
    assert "cos.env" in report.table
    assert "unparseable" in report.table.lower()


def test_doctor_malformed_legacy_line_never_leaks_fragment_into_report(tmp_path):
    # Regression test: doctor()'s `except ConfigError:` around the legacy
    # `load()` call must stay unbound (`except ConfigError:`, never
    # `except ConfigError as e:` with `str(e)` folded into the report) --
    # ConfigError's message embeds the raw malformed line verbatim, so
    # binding it and stringifying it anywhere in the report would leak
    # secret fragments straight out of a malformed legacy line. Plant a
    # distinctive secret fragment inside a MALFORMED (no '=') line so the
    # legacy file fails to parse, and pin that it can never resurface.
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "")
    legacy_path = _write_file(
        tmp_path / "cos.env",
        "sk-live-LEAKME123 not a kv pair\nGOOD_VAR=fine\n",
    )
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    # No crash, and the source is surfaced as unparseable...
    assert "cos.env" in report.table
    assert "unparseable" in report.table.lower()
    # ...but the secret fragment from the malformed line must appear
    # NOWHERE -- not in the table, not anywhere in the report at all.
    assert "LEAKME123" not in report.table
    assert "LEAKME123" not in repr(report)


def test_doctor_table_names_machine_and_canonical_path(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "")
    report = doctor("mac", manifest_path=manifest, canonical_path=canonical, legacy_sources={})
    assert "mac" in report.table
    assert str(canonical) in report.table


def test_doctor_table_renders_none_placeholders_when_all_clean(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "")
    report = doctor("mac", manifest_path=manifest, canonical_path=canonical, legacy_sources={})
    assert report.missing == []
    assert report.conflicts == []
    assert report.legacy_only == {}
    # Exactly 4 sections (missing, conflicts, legacy-only, unparseable),
    # all empty -- each renders its own "(none)" placeholder.
    assert report.table.lower().count("(none)") == 4


def test_doctor_table_lists_missing_conflicts_and_legacy_only_sections(tmp_path):
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac", ["A_MISSING_VAR"])])
    canonical = _write_file(tmp_path / "canonical-env", "SHARED=canon\n")
    legacy_path = _write_file(tmp_path / "cos.env", "SHARED=legacy\nEXTRA_VAR=1\n")
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    assert "A_MISSING_VAR" in report.table
    assert "SHARED" in report.table
    assert "EXTRA_VAR" in report.table
    assert "cos.env" in report.table


def test_doctor_propagates_manifest_error_for_invalid_manifest(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"jobs": []}))  # missing required 'version'
    canonical = _write_file(tmp_path / "canonical-env", "")
    with pytest.raises(ManifestError):
        doctor("mac", manifest_path=path, canonical_path=canonical, legacy_sources={})


def test_doctor_propagates_oserror_for_missing_manifest_file(tmp_path):
    canonical = _write_file(tmp_path / "canonical-env", "")
    with pytest.raises(OSError):
        doctor(
            "mac",
            manifest_path=tmp_path / "does-not-exist.yaml",
            canonical_path=canonical,
            legacy_sources={},
        )


def test_doctor_propagates_config_error_for_directory_canonical_path(tmp_path):
    # The non-file guard in load() means a directory-shaped canonical path
    # (the exact real-machine finding this fix round addresses) surfaces as
    # a ConfigError, not an uncaught IsADirectoryError/OSError. doctor()
    # does not catch it itself -- callers (job_verify.py --doctor) decide
    # how to report it.
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical_dir = tmp_path / "canonical-is-a-dir"
    canonical_dir.mkdir()
    with pytest.raises(ConfigError):
        doctor("mac", manifest_path=manifest, canonical_path=canonical_dir, legacy_sources={})


def test_doctor_legacy_source_that_is_a_directory_reported_as_unparseable(tmp_path):
    # A legacy source hitting the same non-file guard is a different story:
    # doctor()'s per-source loop already catches ConfigError and surfaces it
    # as an "unparseable" finding -- so a directory-shaped legacy source is
    # now gracefully handled too, a free side effect of the guard living in
    # the shared load() rather than being special-cased per caller.
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "")
    legacy_dir = tmp_path / "cos-env-as-dir"
    legacy_dir.mkdir()
    report = doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_dir},
    )
    assert "cos.env" in report.table
    assert "unparseable" in report.table.lower()


def test_doctor_never_touches_os_environ(tmp_path, monkeypatch):
    sentinel_key = "CONFIG_PLANE_DOCTOR_TEST_SENTINEL_VAR"
    monkeypatch.delenv(sentinel_key, raising=False)
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac", ["FOO"])])
    canonical = _write_file(tmp_path / "canonical-env", "")
    legacy_path = _write_file(tmp_path / "cos.env", "FOO=bar\n")
    doctor(
        "mac",
        manifest_path=manifest,
        canonical_path=canonical,
        legacy_sources={"cos.env": legacy_path},
    )
    assert sentinel_key not in os.environ


def test_doctor_default_legacy_sources_is_module_constant(tmp_path):
    # legacy_sources=None (the default) means "use LEGACY_SOURCES" -- since
    # those real paths won't exist in a test sandbox, they're silently
    # skipped, but the call must not raise.
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    canonical = _write_file(tmp_path / "canonical-env", "")
    report = doctor("mac", manifest_path=manifest, canonical_path=canonical)
    assert isinstance(report, DoctorReport)


def test_doctor_default_canonical_path_is_canonical_path_constant(tmp_path, monkeypatch):
    fake_canonical = tmp_path / "env"
    fake_canonical.write_text("")
    monkeypatch.setattr(config_plane, "CANONICAL_PATH", fake_canonical)
    manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    report = doctor("mac", manifest_path=manifest, legacy_sources={})
    assert str(fake_canonical) in report.table


def test_doctor_default_manifest_path_used_when_none(tmp_path, monkeypatch):
    fake_manifest = _write_manifest(tmp_path, [_job("mac-a", "mac")])
    monkeypatch.setattr(config_plane, "DEFAULT_MANIFEST_PATH", fake_manifest)
    canonical = _write_file(tmp_path / "canonical-env", "")
    report = doctor("mac", canonical_path=canonical, legacy_sources={})
    assert isinstance(report, DoctorReport)
