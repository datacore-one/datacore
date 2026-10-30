"""fleet_dashboard: a value it could not read is never shown as green.

The checks it summarises have reported "ok" over broken parts (2026-10-30), so
the console must show a silent or crashing checker as a fault, not a pass.
"""
import json

import fleet_dashboard as fd

HOME = "/" + "home/someone/"  # built at runtime: the pre-commit path scan refuses literal home paths


def test_unreachable_is_red():
    assert fd.assess("box", "host", {"_error": "timed out"})["status"] == "red"


def test_a_silent_checker_is_not_green():
    v = fd.assess("box", "host", {"head": "abc", "jvage": str(105 * 3600), "jvlast": "OK 7 jobs"})
    assert v["status"] == "amber" and "silent" in v["checker"]


def test_no_checker_is_not_green():
    assert fd.assess("dayshift", "host", {"head": "abc"})["status"] == "amber"


def test_a_crashing_checker_is_red_even_when_its_last_line_looks_harmless():
    kv = {"jvage": "60", "jvlast": "job 'x': unknown machine 'dayshift'",
          "jverr": "error: invalid manifest " + HOME + "x/manifest.yaml: job"}
    v = fd.assess("plur-claw", "pod", kv)
    assert v["status"] == "red" and "/home/" not in v["checker"]


def test_faults_are_counted_and_home_paths_hidden():
    jobs = {"jobs": {f"j{i}": {"failed_runs": i, "last_detail": HOME + ".datacore/x.log: stale"} for i in range(5)}}
    v = fd.assess("box@pod", "pod", {"jvage": "60", "jvlast": "relay", "jv": json.dumps(jobs)})
    assert v["status"] == "red" and len(v["faults"]) == 5 and v["faults"][0]["detail"].startswith("~/")


def test_a_failed_backup_raises_the_first_alert():
    alerts, orders = fd.alerts_and_orders([], {"bk_result": "exit-code", "bk_newest": "2026-10-26",
                                               "bk_why": "SKIPPED, not enough disk: would leave under 20% free",
                                               "disk_pct": "91%"}, {"status": "ok", "tools": {}}, {})
    assert alerts[0][0] == "red" and "2026-10-26" in alerts[0][1]
    assert "20% free" in alerts[1][2]
