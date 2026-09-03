import importlib.util, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("lps", ROOT / ".datacore" / "lib" / "ledger_publish_safe.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)


def test_only_machine_written_paths_qualify():
    assert L.only_machine_written([".datacore/events/mac.jsonl"])
    assert L.only_machine_written([".datacore/events/mac.jsonl", ".datacore/state/venture/cadence-log/mac.yaml"])
    assert not L.only_machine_written([".datacore/events/mac.jsonl", "org/next_actions.org"]), "a human's file is dirty: leave the space alone"
    assert not L.only_machine_written(["roadmap.yaml"])
    assert not L.only_machine_written([]), "nothing dirty means nothing to publish"
