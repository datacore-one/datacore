"""KNW-5: A podcast is made from research whenever there are enough sources, saved where
I can find it, and a failure is reported with its reason.

Kind: deterministic. The real orchestrator main() / create_notebook_with_podcast()
drive a fake Gemini Notebook client (NOTEBOOKLM_BIN, a tmp script that records its
calls). "Enough sources" is the module's podcast_defaults.min_sources (3). "Where I
can find it" is the module's podcast_output_dir (documented: podcasts land in
0-personal/content/podcasts/). "Reported" means the run prints a line that the
overnight wrapper (chief-of-staff cos_research.sh) turns into a group alert; its
patterns are read from that script, so the two cannot drift apart silently.

Seeded failure: the podcast step is skipped with enough sources; the notebook exists
only in NotebookLM with nothing written to the podcast folder; or a failed notebook
creation (expired session, missing client) is only logged.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _research_harness as H  # noqa: E402

COS_RESEARCH = H.MODULE.parent / "chief-of-staff" / "server" / "lib" / "cos_research.sh"
NB = "0a1b2c3d-1111-2222-3333-444455556666"

FAKE = """#!/bin/sh
echo "$@" >> "{log}"
case "$1 $2" in
  "create "*) {create} ;;
  "source add") exit 0 ;;
  "generate audio") {audio} ;;
esac
exit 0
"""


def _client(tmp_path, monkeypatch, create=f'echo "Created notebook {NB}"; exit 0', audio="exit 0"):
    log = tmp_path / "client.log"
    exe = tmp_path / "notebooklm"
    exe.write_text(FAKE.format(log=log, create=create, audio=audio))
    exe.chmod(0o755)
    monkeypatch.setenv("NOTEBOOKLM_BIN", str(exe))
    return log


def _min_sources():
    cfg = yaml.safe_load((H.MODULE / "module.yaml").read_text(encoding="utf-8"))
    d = (cfg.get("settings") or {}).get("podcast_defaults") or {}
    d = d.get("default", d)
    return int(d.get("min_sources", 3))


def _alert_patterns():
    text = COS_RESEARCH.read_text(encoding="utf-8")
    pats = re.findall(r'"\$OUT"\s*\|\s*grep\s+-q[iE]*\s+(?:\'([^\']+)\'|"([^"]+)")', text)
    return [a or b for a, b in pats]


def _queue(n):
    today = date.today().isoformat()
    return "#+TITLE: Research\n* Queue\n" + "".join(
        f"** TODO Read: Source {i}\n   :PROPERTIES:\n   :ID: knw5-{i}\n   :CREATED: [{today}]\n"
        f"   :END:\n   Link: https://example.test/{i}\n" for i in range(n))


def test_enough_sources_make_a_podcast_that_is_saved_in_the_podcast_folder(tmp_path, monkeypatch):
    R = H.load()
    t = H.point_at(R, tmp_path, monkeypatch)
    n = _min_sources()
    t.org.write_text(_queue(n), encoding="utf-8")
    log = _client(tmp_path, monkeypatch)
    H.capture_http(monkeypatch)
    monkeypatch.setattr(R, "fetch_url", lambda url: "Body " * 50)
    monkeypatch.setattr(R, "process_item", lambda item, c: H.analysis(item["title"].replace("Read: ", "")))
    monkeypatch.setattr(R, "send_telegram_summary", lambda *a, **k: True)

    H.run_main(R, monkeypatch)

    calls = log.read_text().splitlines() if log.exists() else []
    assert any(c.startswith("create ") for c in calls), f"no notebook was created from {n} sources"
    assert sum(c.startswith("source add") for c in calls) >= n, calls
    assert any(c.startswith("generate audio") for c in calls), "no podcast audio was requested"
    saved = [p for p in t.personal.joinpath("content", "podcasts").rglob("*") if p.is_file()] \
        if t.personal.joinpath("content", "podcasts").exists() else []
    assert any(NB in p.read_text(errors="ignore") or NB in p.name or p.suffix in (".mp3", ".m4a", ".wav")
               for p in saved), (
        "the podcast is not saved where the owner can find it: nothing about notebook "
        f"{NB} was written to podcast_output_dir ({R.PODCAST_DIR})")


def _reported(out):
    pats = _alert_patterns()
    assert pats, f"could not read the alert patterns from {COS_RESEARCH}"
    return [p for p in pats if re.search(p, out, re.I)]


def test_a_failed_notebook_creation_is_reported_with_its_reason(tmp_path, monkeypatch, capsys):
    R = H.load()
    H.point_at(R, tmp_path, monkeypatch)
    _client(tmp_path, monkeypatch,
            create='echo "Error: cached browser session is no longer usable" >&2; exit 1')
    processed = [{"title": f"S{i}", "literature_note": str(tmp_path / f"s{i}.md")} for i in range(3)]

    assert R.create_notebook_with_podcast(processed) is None
    out = capsys.readouterr().out
    assert _reported(out), (
        "a notebook creation that failed on an expired session printed no line the overnight "
        f"wrapper alerts on; the run said:\n{out}")


def test_a_missing_notebook_client_is_reported(tmp_path, monkeypatch, capsys):
    R = H.load()
    H.point_at(R, tmp_path, monkeypatch)
    monkeypatch.setattr(R, "_resolve_notebook_client", lambda: ("", ""))
    processed = [{"title": f"S{i}", "literature_note": str(tmp_path / f"s{i}.md")} for i in range(3)]

    assert R.create_notebook_with_podcast(processed) is None
    out = capsys.readouterr().out
    assert _reported(out), f"no podcast because no client is installed, and nobody is told:\n{out}"


def test_a_failed_audio_step_is_reported_with_its_reason(tmp_path, monkeypatch, capsys):
    R = H.load()
    H.point_at(R, tmp_path, monkeypatch)
    _client(tmp_path, monkeypatch,
            audio='echo "CreateAudioOverview: execute rpc: One or more arguments are invalid" >&2; exit 1')
    for i in range(3):
        (tmp_path / f"s{i}.md").write_text("x")
    processed = [{"title": f"S{i}", "literature_note": str(tmp_path / f"s{i}.md")} for i in range(3)]

    assert R.create_notebook_with_podcast(processed) is None
    out = capsys.readouterr().out
    assert _reported(out), f"the audio failure printed no line the wrapper alerts on:\n{out}"
    assert "not the credential" in out, "the report does not name the reason"
