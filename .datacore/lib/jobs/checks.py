"""Artifact contract checks.

`run_check` verifies that a single `Artifact` (from `jobs.manifest`) holds
against the filesystem: the file exists, is fresh enough, and satisfies
its type-specific check (nonempty / json_has_keys / regex / exists). It
never raises -- every filesystem or content surprise (missing file, stale
mtime, unreadable file, binary garbage, bad JSON) is converted into a
human-readable error string naming the *expanded* path and the reason.
An empty list means the contract holds.

Check pipeline per artifact:
  1. file exists                         -- else error, stop (nothing
                                             else is checkable).
  2. freshness (if max_age_hours is set)  -- mtime >= now -
                                             max_age_hours*3600, else a
                                             "stale" error naming the age
                                             and the limit.
  3. type check (exists/nonempty/json_has_keys/regex).
Steps 2 and 3 both run and accumulate errors independently (e.g. a file
can be both stale AND missing JSON keys) -- only step 1 short-circuits.

`regex` check: matched with `re.MULTILINE` always on. Manifest regexes are
written as line-anchored assertions (`^##\\s+(Daily Briefing|Good Morning)`
means "some line in this file starts with one of these headings"), not as
whole-file assertions ("the file's first character is #"). Without
`re.MULTILINE`, `^`/`$` anchor only to the start/end of the entire text,
so a `^`-anchored pattern can never match a target line that isn't the
very first line of the file -- a real false-FAILED risk for any file with
a preamble (YAML frontmatter, a title line, etc.) before the checked
content, discovered live via `box-briefing`'s manifest entry (Task 6.3).

Path expansion: `{today}` is substituted with the local calendar date of
`now` (%Y-%m-%d) BEFORE `~` is expanded via `os.path.expanduser`. `now`
defaults to `time.time()` -- this module is the runner side of the job
contract system (not the deterministic fold), so reading the wall clock
here is allowed. All logic is still driven off the (possibly injected)
`now`, never a second independent clock read, so callers can fully
control behavior in tests.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

from jobs.manifest import Artifact


def expand_path(path: str, *, now: float | None = None) -> str:
    """Expand `{today}` (local date of `now`) then `~` in `path`."""
    if now is None:
        now = time.time()
    today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    expanded = os.path.expanduser(path.replace("{today}", today))
    if "*" not in expanded:
        return expanded
    # GLOB: resolve to the NEWEST match, or the literal pattern when nothing
    # matches so the caller reports a clean "does not exist".
    #
    # Needed because a daily-rotated file is written under YESTERDAY'S name
    # until the first event of the new day rolls it over. A `{today}` path
    # therefore fails every night between midnight and that first event —
    # observed on mac-agent-stream-rsync at 00:0x, with the previous day's file
    # last written 01:30 the same morning and the stream perfectly healthy.
    # A nightly false alarm is one people learn to ignore.
    #
    # Freshness (`max_age_hours`) is the real liveness signal here; the exact
    # filename is not.
    import glob as _glob
    matches = _glob.glob(expanded)
    if not matches:
        return expanded
    return max(matches, key=lambda m: os.path.getmtime(m))


def _read_text(path: str) -> tuple[str | None, str | None]:
    """Read `path` as utf-8 (replacing undecodable bytes). Never raises.

    Returns (text, None) on success, or (None, error_reason) on failure.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except (OSError, ValueError) as exc:
        return None, f"cannot read file ({exc})"
    return raw.decode("utf-8", errors="replace"), None


def run_check(artifact: Artifact, *, now: float | None = None) -> list[str]:
    """Check whether `artifact`'s contract holds.

    Returns a list of error strings naming the expanded path and reason;
    an empty list means the contract holds. Never raises -- filesystem
    and content surprises are converted into error strings.
    """
    if now is None:
        now = time.time()

    expanded = expand_path(artifact.path, now=now)

    try:
        st = os.stat(expanded)
    except (OSError, ValueError) as exc:
        return [f"{expanded}: does not exist ({exc})"]

    errors: list[str] = []

    if artifact.max_age_hours is not None:
        min_mtime = now - artifact.max_age_hours * 3600
        if st.st_mtime < min_mtime:
            age_hours = (now - st.st_mtime) / 3600
            errors.append(
                f"{expanded}: stale (age {age_hours:.2f}h exceeds "
                f"max_age_hours={artifact.max_age_hours})"
            )

    check = artifact.check

    if check == "exists":
        pass

    elif check == "nonempty":
        if st.st_size == 0:
            errors.append(f"{expanded}: empty file (nonempty check failed)")

    elif check == "min_bytes":
        # For artifacts a daemon RECREATES when lost. `exists` cannot detect
        # deletion of such a file (it reappears within milliseconds) and
        # `nonempty` cannot either (a fresh file has a header). Verified
        # 2026-08-10: moving the 2.6 GB lens DB aside was NOT detected --
        # the KeepAlive daemon recreated it and the check passed. A floor on
        # size distinguishes "the accumulated database" from "a database that
        # just started over".
        if not isinstance(artifact.arg, int) or artifact.arg < 0:
            errors.append(
                f"{expanded}: min_bytes artifact has invalid arg "
                f"(expected a non-negative int, got {artifact.arg!r})"
            )
        elif st.st_size < artifact.arg:
            errors.append(
                f"{expanded}: {st.st_size} bytes is below the "
                f"min_bytes floor of {artifact.arg} "
                f"(artifact may have been recreated from scratch)"
            )

    elif check == "json_has_keys":
        if not isinstance(artifact.arg, list):
            # Defend locally even though the manifest loader is supposed to
            # guarantee this: run_check must never crash on a malformed
            # Artifact built by hand (e.g. directly in tests, or by a
            # future caller that skips the loader).
            errors.append(
                f"{expanded}: json_has_keys artifact has invalid arg "
                f"(expected a list of keys, got {artifact.arg!r})"
            )
        else:
            text, read_error = _read_text(expanded)
            if read_error is not None:
                errors.append(f"{expanded}: {read_error}")
            else:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as exc:
                    errors.append(f"{expanded}: invalid JSON ({exc})")
                else:
                    if not isinstance(data, dict):
                        errors.append(
                            f"{expanded}: JSON root is not an object "
                            f"(got {type(data).__name__}), required keys: {list(artifact.arg)}"
                        )
                    else:
                        missing = [key for key in artifact.arg if key not in data]
                        if missing:
                            errors.append(f"{expanded}: missing JSON keys: {missing}")

    elif check == "regex":
        text, read_error = _read_text(expanded)
        if read_error is not None:
            errors.append(f"{expanded}: {read_error}")
        else:
            try:
                # re.MULTILINE, always: every manifest regex written so far
                # (`^##\s+...` heading checks) is line-anchored BY INTENT --
                # "does this file have a line starting with ##", not "does
                # this file's very first character happen to be #". Without
                # MULTILINE, `^` anchors only to position 0 of the whole
                # file string, so any file with content before the target
                # line (e.g. a YAML frontmatter block: every journal file
                # starts with `---\ndate: ...`) can NEVER match regardless
                # of whether the expected line is present later in the
                # text -- a false FAILED on well-formed content, not a real
                # contract violation. Found live: Task 6.3's first box run
                # of job_verify.py reported `box-briefing` FAILED against a
                # journal that, on direct inspection, actually contained
                # both "## Daily Briefing" and "## Good Morning" verbatim.
                matched = re.search(artifact.arg, text, re.MULTILINE)
            except re.error as exc:
                errors.append(f"{expanded}: invalid regex {artifact.arg!r} ({exc})")
            else:
                if not matched:
                    errors.append(f"{expanded}: regex {artifact.arg!r} did not match")

    return errors
