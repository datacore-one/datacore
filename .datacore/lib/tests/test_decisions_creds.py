"""Owner decisions C1-C5 (2026-09-23), credentials area.

C1  `creds get` keeps serving n-a (exit 0) with a stderr notice; the notice is
    unambiguous and documented in `creds get --help`. --strict stays opt-in.
C2  Every env parser takes the LAST value for a duplicate key; env_utils no
    longer raises on one; `creds doctor` lists keys duplicated inside one
    store (names only, never values).
C3  dmcc's client-report and contract-review may escalate past the venture's
    privacy floor (owner's explicit choice). Nothing else may.
C4/C5  A read-only overlap report (keys in both .env and local.env, with
    truncated sha256 per value). Tested here on FAKE files only.

Everything runs in tmp dirs with FAKE values. See
.datacore/specs/datacore-lean/DECISIONS.md and findings/credentials.md.
"""

import io
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import config_plane  # noqa: E402
import credential_access as ca  # noqa: E402
import creds  # noqa: E402
import env_utils  # noqa: E402
import model_routing as mr  # noqa: E402
from test_credentials_formal import _run, broker  # noqa: E402,F401

LIB = Path(__file__).parent.parent


# --- C1: the n-a notice ----------------------------------------------------------

def test_c1_na_notice_is_unambiguous(broker):
    rc, out, err = _run(broker.cmd_get, "gem")
    assert rc == 0 and out.strip() == "fake-gem"
    line = err.strip().splitlines()[-1]
    assert line.startswith(creds.NA_NOTICE_PREFIX)
    assert "gem" in line and "UNVERIFIED" in line and "exit 0" in line
    assert "--strict" in line
    assert "fake-gem" not in err


def test_c1_other_outcomes_never_carry_the_na_prefix(broker):
    # FAIL (dead value) -- not the n-a notice
    _, _, err = _run(broker.cmd_get, "forge-gitea")
    assert creds.NA_NOTICE_PREFIX not in err
    # --no-verify skipped the check on request: silent, not an n-a verdict
    rc, _, err = _run(broker.cmd_get, "gem", no_verify=True)
    assert rc == 0 and creds.NA_NOTICE_PREFIX not in err
    # --strict refuses n-a: its own line, exit 3
    rc, out, err = _run(broker.cmd_get, "gem", strict=True)
    assert rc == 3 and out == ""
    assert creds.NA_NOTICE_PREFIX not in err and "exit 3" in err


def test_c1_get_help_documents_the_na_line(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["creds.py", "get", "--help"])
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as ei:
        creds.main()
    assert ei.value.code == 0
    text = out.getvalue()
    assert creds.NA_NOTICE_PREFIX in text
    for code in ("0", "1", "3"):
        assert f"exit {code}" in text
    assert "--strict" in text


# --- C2: last wins everywhere ------------------------------------------------------

DUP = "K1=first\nOTHER=x\nK1=second\n"


def test_c2_every_parser_takes_the_last_value(tmp_path):
    p = tmp_path / "dup.env"
    p.write_text(DUP)
    assert ca._read_var(p, "K1") == "second"
    assert ca._vars_in(p)["K1"] == "second"
    assert config_plane.load(p)["K1"] == "second"
    assert env_utils.parse_env_file(p)["K1"] == "second"


def test_c2_parsers_agree_on_every_key(tmp_path):
    p = tmp_path / "dup.env"
    p.write_text("A=1\nB=2\nA=3\nexport B=4\nC=5\nA=6\n")
    via = [
        {k: ca._read_var(p, k) for k in "ABC"},
        ca._vars_in(p),
        config_plane.load(p),
        env_utils.parse_env_file(p),
    ]
    assert all(v == {"A": "6", "B": "4", "C": "5"} for v in via)


def test_c2_broker_serves_the_last_value(broker):
    (ca.ENV / ".env").write_text("GEMINI_API_KEY=fake-old\nGEMINI_API_KEY=fake-new\n")
    assert ca.get_value("gem") == "fake-new"


def test_c2_load_env_files_accepts_a_duplicate(tmp_path, monkeypatch):
    p = tmp_path / "dup.env"
    p.write_text(DUP)
    monkeypatch.delenv("K1", raising=False)
    try:
        loaded = env_utils.load_env_files([p], override=True)
        assert loaded["K1"] == "second"
    finally:
        import os
        os.environ.pop("K1", None)
        os.environ.pop("OTHER", None)


def test_c2_duplicate_keys_names_only(tmp_path):
    p = tmp_path / "dup.env"
    p.write_text("A=1\nB=2\nA=3\nexport B=4\nC=5\n# A=7\n")
    assert ca.duplicate_keys(p) == ["A", "B"]


def test_c2_doctor_lists_in_store_duplicates_without_values(broker, tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "DATA", tmp_path)
    (ca.ENV / ".env").write_text(
        "GEMINI_API_KEY=fake-dup-one\nGEMINI_API_KEY=fake-dup-two\n"
        "SOME_FLAG=fake-flag-a\nSOME_FLAG=fake-flag-b\n")
    rc, out, _ = _run(broker.cmd_doctor, "gem")
    assert "duplicated" in out.lower()
    assert "GEMINI_API_KEY" in out
    assert "fake-dup" not in out and "fake-flag" not in out
    # the warning does not change the verdict or the exit code
    assert rc == 0


def test_c2_doctor_is_quiet_without_duplicates(broker, tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "DATA", tmp_path)
    _, out, _ = _run(broker.cmd_doctor, "gem")
    assert "duplicated" not in out.lower()


# --- C3: dmcc's two floor exceptions ---------------------------------------------

@pytest.fixture(autouse=False)
def fresh_config(monkeypatch):
    monkeypatch.setattr(mr, "_CONFIG", {})
    yield
    mr._CONFIG = {}


def test_c3_dmcc_contract_review_escalates_to_frontier(fresh_config):
    provider, model, why = mr.pick_model("contract-review", venture="dmcc")
    assert (provider, model) == ("anthropic", "claude-opus-4-7")
    assert "floor exception" in why


def test_c3_dmcc_client_report_escalates_to_frontier(fresh_config):
    provider, model, why = mr.pick_model("client-report", venture="dmcc")
    assert (provider, model) == ("anthropic", "claude-sonnet-4-6")
    assert "floor exception" in why


@pytest.mark.parametrize("task", ["public-bulk", "reasoning", "high-stakes",
                                  "architecture", "smart-contract", "x"])
def test_c3_dmcc_floor_holds_for_everything_else(fresh_config, task):
    provider, _, _ = mr.pick_model(task, venture="dmcc")
    assert provider == "ollama"


@pytest.mark.parametrize("task", ["contract-review", "client-report"])
@pytest.mark.parametrize("pc", ["internal", "sensitive"])
def test_c3_exception_never_waives_the_callers_privacy_class(fresh_config, task, pc):
    provider, _, _ = mr.pick_model(task, venture="dmcc", privacy_class=pc)
    assert provider == "ollama"


def test_c3_exception_list_is_exactly_the_owners(fresh_config):
    cfg = mr._load_config()
    assert sorted(cfg["ventures"]["dmcc"]["floor_exceptions"]) == [
        "client-report", "contract-review"]
    others = [v for v, p in cfg["ventures"].items()
              if v != "dmcc" and p.get("floor_exceptions")]
    assert others == []


def test_c3_exception_is_recorded_in_the_config():
    text = (LIB / "routing.yaml").read_text()
    idx = text.index("floor_exceptions")
    block = text[max(0, idx - 800): idx + 200]
    assert "2026-09-23" in block and "owner" in block.lower()


# --- C4/C5: read-only overlap report (never run on real data here) -----------------

def test_c45_overlap_report_prints_digests_not_values(tmp_path):
    env = tmp_path / ".datacore" / "env"
    env.mkdir(parents=True)
    (env / ".env").write_text(
        "SHARED_KEY=fake-fleet\nSAME_KEY=fake-same\nONLY_FLEET=fake-f\n")
    (env / "local.env").write_text(
        "SHARED_KEY=fake-host\nSAME_KEY=fake-same\nONLY_HOST=fake-h\n")
    r = subprocess.run(
        [sys.executable, str(LIB / "env_overlap_report.py"), "--root", str(tmp_path)],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "SHARED_KEY" in out and "SAME_KEY" in out
    assert "ONLY_FLEET" not in out and "ONLY_HOST" not in out
    assert "fake-" not in out
    fp = ca.fingerprint
    assert fp("fake-fleet") in out and fp("fake-host") in out
    shared = [l for l in out.splitlines() if l.lstrip().startswith("SHARED_KEY")][0]
    same = [l for l in out.splitlines() if l.lstrip().startswith("SAME_KEY")][0]
    assert "DIFFER" in shared and "same" in same


def test_c45_overlap_report_with_no_local_env(tmp_path):
    env = tmp_path / ".datacore" / "env"
    env.mkdir(parents=True)
    (env / ".env").write_text("A_KEY=fake-a\n")
    r = subprocess.run(
        [sys.executable, str(LIB / "env_overlap_report.py"), "--root", str(tmp_path)],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert "fake-" not in r.stdout
