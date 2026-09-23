"""Two defects from the 2026-09-23 core survey that were damaging data live.

* org_date_hook (a PostToolUse hook on every Edit/Write of .org/.md) rewrote
  any word that merely STARTS with a day abbreviation after a date:
  "2026-09-24 Monitor" became "2026-09-24 Thuitor".
* rotate_learning_backup purged by the glob "<stem>-*<suffix>", which for
  `engrams.yaml` also matches `engrams-candidates-*.yaml` backups; those sort
  later, so every backup of engrams.yaml was deleted on the spot.
"""
import importlib

import org_date_hook
import rotate_learning_backup


# --- org_date_hook ------------------------------------------------------------


def _fix(tmp_path, text):
    p = tmp_path / "n.md"
    p.write_text(text)
    n = org_date_hook.fix_dates(str(p))
    return n, p.read_text()


def test_hook_leaves_prose_words_alone(tmp_path):
    text = "2026-09-24 Monitor the queue\n2026-09-24 Sunset review\n2026-09-24 Monday-ish\n"
    n, out = _fix(tmp_path, text)
    assert (n, out) == (0, text)


def test_hook_still_fixes_a_wrong_stamp(tmp_path):
    # 2026-09-24 is a Thursday.
    n, out = _fix(tmp_path, "<2026-09-24 Mon> and 2026-09-24 Mon\n")
    assert n == 2 and out == "<2026-09-24 Thu> and 2026-09-24 Thu\n"


def test_hook_never_joins_lines(tmp_path):
    text = "Due 2026-09-24\nSat with Bob\n"
    n, out = _fix(tmp_path, text)
    assert (n, out) == (0, text)


def test_hook_ignores_non_ascii_continuations(tmp_path):
    text = "2026-09-24 Sat\u00e9 x\n"
    n, out = _fix(tmp_path, text)
    assert (n, out) == (0, text)


def test_hook_does_not_mangle_full_day_names(tmp_path):
    text = "2026-09-24 Monday\n"
    n, out = _fix(tmp_path, text)
    assert "Thuday" not in out and out == text


# --- rotate_learning_backup ---------------------------------------------------


def test_rotation_keeps_backups_of_a_file_whose_stem_prefixes_another(tmp_path, monkeypatch):
    monkeypatch.setattr(rotate_learning_backup, "BACKUP_ROOT", tmp_path / "backups")
    src = tmp_path / "learning"
    src.mkdir()
    main = src / "engrams.yaml"
    cand = src / "engrams-candidates-2026-03-04.yaml"
    main.write_text("main\n")
    cand.write_text("cand\n")
    for i in range(9):
        main.write_text(f"main {i}\n")
        cand.write_text(f"cand {i}\n")
        rotate_learning_backup.rotate(cand)
        rotate_learning_backup.rotate(main)
    backups = list((tmp_path / "backups").rglob("*"))
    mains = [b for b in backups if b.name.startswith("engrams-") and "candidates" not in b.name]
    cands = [b for b in backups if "candidates" in b.name]
    assert len(mains) == rotate_learning_backup.KEEP
    assert len(cands) == rotate_learning_backup.KEEP


def test_two_rotations_in_one_second_keep_both_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(rotate_learning_backup, "BACKUP_ROOT", tmp_path / "backups")
    f = tmp_path / "patterns.md"
    f.write_text("v1\n")
    a = rotate_learning_backup.rotate(f)
    f.write_text("v2\n")
    b = rotate_learning_backup.rotate(f)
    assert a != b and a.read_text() == "v1\n" and b.read_text() == "v2\n"
