"""No test run sends a real alert.

2026-09-30: the promise board's test called send_to_firm() with the direct
Telegram call stubbed, but on the Mac and the chief-of-staff host the alert
command in ~/.datacore/alerts.yaml (installed that day) is the first route, so
running the suite posted "A capture made twice lands once (CAP-4) turned red"
to The Firm, twice. The suite now points the alert settings at a file that does
not exist; a test that needs an alert command sets DATACORE_ALERT_COMMAND itself.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_the_suite_reads_no_real_alert_settings():
    import job_verify
    assert os.environ.get("DATACORE_ALERTS_FILE"), "the test setup must point the alert settings away"
    assert not Path(os.environ["DATACORE_ALERTS_FILE"]).exists()
    assert job_verify._alert_command() == os.environ.get("DATACORE_ALERT_COMMAND", "").strip()


def test_the_alert_settings_file_is_read_from_where_it_is_pointed(tmp_path, monkeypatch):
    import job_verify
    f = tmp_path / "alerts.yaml"
    f.write_text("command: cat > /dev/null\n")
    monkeypatch.delenv("DATACORE_ALERT_COMMAND", raising=False)
    monkeypatch.setenv("DATACORE_ALERTS_FILE", str(f))
    assert job_verify._alert_command() == "cat > /dev/null"
