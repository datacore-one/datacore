#!/bin/bash
# v2_box_setup.sh -- Datacore v2 Phase 6 box installer (Task 6.2).
#
# Idempotent, check-then-apply installer for the chief-of-staff box
# ("Winston"). Every step here checks current state before mutating
# anything, so running this script twice (or a hundred times) in --apply
# mode is safe and the second run is a no-op.
#
# STANDALONE tracked script: `.datacore/modules/chief-of-staff/server/lib/
# cos-server-setup.sh` -- the private module installer -- is gitignored in
# this public repo, so the v2 logic lives here instead (Task 6.2 brief:
# `.superpowers/sdd/2026-07-29-datacore-v2/task-6.2-brief.md`). Task 6.3
# wires this file into the box's real install flow -- see
# `task-6.2-report.md` for the exact invocation line. IMPORTANT: this
# script must be INVOKED as a subprocess (`bash v2_box_setup.sh --apply`),
# never `source`d -- it calls `exit` on completion, which would abort a
# sourcing parent script early.
#
# Modes (exactly one required):
#   --verify   assert-only, makes no changes, exit 0 (all OK) / 1 (any FAILED)
#   --apply    check-then-apply each step, mutating only what's missing
#
# DATACORE_V2_SETUP_PREFIX (test-only): when set, every absolute /root/...
# (and /etc/...) path below is prefixed with it, so this script can be
# exercised against a local fixture tree instead of the real box
# filesystem. Leave unset on the real box. TWO steps have no local
# equivalent to fake safely and are skipped outright whenever this is
# set (both echo a `SKIPPED (test prefix)` line and touch nothing real):
#   - cryptography>=41: skipped so the test suite never runs a REAL
#     `pip3 install` against whatever machine happens to run it.
#   - job_verify cron: skipped so the test suite never reads/writes the
#     REAL root crontab.
set -u

PREFIX="${DATACORE_V2_SETUP_PREFIX:-}"
ROOT="$PREFIX/root/Data"
HOME_DC="$PREFIX/root/.datacore"
CANONICAL_ENV="$HOME_DC/datacore.env"
LEGACY_COS_ENV="$PREFIX/root/.config/cos.env"
LEGACY_DATACORED_ENV="$PREFIX/etc/datacored.env"
LEGACY_HERMES_ENV="$PREFIX/root/.hermes/.env"
MANIFEST="$ROOT/.datacore/lib/jobs/manifest.yaml"
# 08:00 assumed UTC: this box's crontab already runs on UTC wall-clock time
# with no timezone conversion anywhere in its setup -- cos_morning.sh is
# installed at "0 4 * * *" and COS-SERVER.md documents that exact slot as
# "04:00" (UTC), so 08:00 here follows the same, already-established
# convention rather than introducing a new one.
CRON_LINE="0 8 * * * DATACORE_V2=1 DATACORE_ROOT=/root/Data python3 /root/Data/.datacore/lib/job_verify.py --machine box --alert telegram >> /root/.datacore/state/job_verify.log 2>&1"

log() { echo "[v2] $1: $2 $3"; }

MODE=""
FLAG_COUNT=0
for arg in "$@"; do
  case "$arg" in
    --verify) MODE="verify"; FLAG_COUNT=$((FLAG_COUNT + 1)) ;;
    --apply) MODE="apply"; FLAG_COUNT=$((FLAG_COUNT + 1)) ;;
  esac
done
if [ "$FLAG_COUNT" -ne 1 ]; then
  echo "usage: $0 --verify|--apply (exactly one required)" >&2
  exit 2
fi

# ── Step 1: cryptography>=41 ────────────────────────────────────────────
step_cryptography() {
  if [ -n "$PREFIX" ]; then
    log cryptography SKIPPED "(test prefix)"
    return 0
  fi
  if python3 -c "import cryptography, sys; sys.exit(0 if int(cryptography.__version__.split('.')[0]) >= 41 else 1)" 2>/dev/null; then
    log cryptography OK "$(python3 -c 'import cryptography; print(cryptography.__version__)' 2>/dev/null)"
    return 0
  fi
  if [ "$MODE" = "verify" ]; then
    log cryptography FAILED "cryptography>=41 not satisfied"
    return 1
  fi
  if pip3 install -q 'cryptography>=41' >/dev/null 2>&1; then
    log cryptography APPLIED "installed cryptography>=41"
    return 0
  fi
  log cryptography FAILED "pip3 install 'cryptography>=41' failed"
  return 1
}

# ── Step 2: canonical env + legacy migration (values never leave the box) ─
step_env() {
  if [ ! -f "$CANONICAL_ENV" ]; then
    if [ "$MODE" = "verify" ]; then
      log env FAILED "$CANONICAL_ENV missing"
      return 1
    fi
    if ! mkdir -p "$(dirname "$CANONICAL_ENV")" 2>/dev/null; then
      log env FAILED "mkdir -p $(dirname "$CANONICAL_ENV") failed"
      return 1
    fi
    if ! (umask 077 && : > "$CANONICAL_ENV") 2>/dev/null; then
      log env FAILED "could not create $CANONICAL_ENV (permission denied?)"
      return 1
    fi
    if ! chmod 0600 "$CANONICAL_ENV" 2>/dev/null; then
      log env FAILED "chmod 0600 $CANONICAL_ENV failed"
      return 1
    fi
    log env APPLIED "created $CANONICAL_ENV (0600)"
  fi

  # GNU first: the deploy target (the box) is always Linux/GNU coreutils,
  # where `-c` is the format-string flag. The BSD form (`-f`, macOS) is a
  # fallback for local fixture tests on this Mac only -- GNU's `-f` means
  # something else entirely ("filesystem status", not "format string"), so
  # trying BSD first on Linux doesn't just fail cleanly: GNU parses `-f`'s
  # would-be format string as a second FILE argument, fails on that bogus
  # file (stderr), but ALSO succeeds in filesystem-status mode on the real
  # file, printing a multi-line filesystem-info block to stdout before the
  # overall nonzero exit triggers the `-c` fallback -- polluting $perm with
  # both outputs concatenated even though the fallback still fires. GNU
  # first avoids ever exercising that failure mode on the real box.
  local perm
  perm=$(stat -c '%a' "$CANONICAL_ENV" 2>/dev/null || stat -f '%Lp' "$CANONICAL_ENV" 2>/dev/null)
  if [ "$perm" != "600" ]; then
    if [ "$MODE" = "apply" ]; then
      if ! chmod 0600 "$CANONICAL_ENV" 2>/dev/null; then
        log env FAILED "chmod 0600 $CANONICAL_ENV failed (was $perm)"
        return 1
      fi
      log env APPLIED "chmod 0600 $CANONICAL_ENV (was $perm)"
    else
      log env FAILED "$CANONICAL_ENV mode is $perm, expected 600"
      return 1
    fi
  fi

  if [ "$MODE" = "verify" ]; then
    if [ -s "$CANONICAL_ENV" ]; then
      log env OK "$CANONICAL_ENV exists, 0600, non-empty"
      return 0
    fi
    log env FAILED "$CANONICAL_ENV is empty"
    return 1
  fi

  # --apply: migrate legacy KEY=VALUE lines, first-source-wins on conflict.
  # Values are copied box-side only -- they never transit this repo or its
  # git history. Legacy files themselves are NOT touched or deleted (Phase
  # 6 keeps them until the retirement gates in DIP-0039 pass).
  #
  # Key validation is the FULL `^[A-Za-z_][A-Za-z0-9_]*$` shape (not just a
  # first-char check) -- anything else (embedded spaces, dots, etc.) is
  # dropped with an explicit "(invalid key)" SKIP rather than silently
  # migrated or silently ignored. Leading whitespace on the key is trimmed
  # BEFORE validation (a legitimately-indented legacy line still migrates);
  # a trailing \r (CRLF line ending) is stripped from the whole line before
  # any parsing, so it never contaminates the migrated value. Once a key
  # passes this strict charset check, it contains no regex metacharacters,
  # so the dedup `grep -q "^${key}="` below needs no additional escaping.
  local legacy_file raw_key key value valid rc=0
  for legacy_file in "$LEGACY_COS_ENV" "$LEGACY_DATACORED_ENV" "$LEGACY_HERMES_ENV"; do
    [ -f "$legacy_file" ] || continue
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line%$'\r'}"
      case "$line" in
        ''|'#'*) continue ;;
      esac
      case "$line" in
        *=*) ;;
        *) continue ;;
      esac
      raw_key="${line%%=*}"
      value="${line#*=}"
      key="${raw_key#"${raw_key%%[![:space:]]*}"}"
      case "$key" in
        '') valid=0 ;;
        *[!A-Za-z0-9_]*) valid=0 ;;
        [A-Za-z_]*) valid=1 ;;
        *) valid=0 ;;
      esac
      if [ "$valid" -ne 1 ]; then
        log env SKIP "$key (invalid key)"
        continue
      fi
      if grep -q "^${key}=" "$CANONICAL_ENV" 2>/dev/null; then
        log env SKIP "$key (already set)"
      else
        if echo "${key}=${value}" >> "$CANONICAL_ENV"; then
          log env APPLIED "migrated $key from $(basename "$legacy_file")"
        else
          log env FAILED "could not append $key to $CANONICAL_ENV"
          rc=1
        fi
      fi
    done < "$legacy_file"
  done

  [ "$rc" -eq 0 ] && log env OK "canonical env ready at $CANONICAL_ENV"
  return "$rc"
}

# `cron_line_present` matches the FULL, exact `$CRON_LINE` (`grep -qxF`: `-x`
# whole-line, `-F` fixed-string so the line's own `*` characters are never
# interpreted as regex) -- not a loose "job_verify.py" substring. A crontab
# line that merely *mentions* job_verify.py (a stale entry, a commented-out
# older attempt, a different schedule/flags) must NOT be treated as
# "already installed"; only the exact line counts, so a crontab containing
# such a different job_verify.py line still gets the correct entry appended
# alongside it.
#
# This function has no automated fixture-test coverage: it reads/writes the
# REAL root crontab, and `step_cron` below unconditionally skips itself
# whenever `DATACORE_V2_SETUP_PREFIX` is set specifically so the test suite
# never touches a real crontab (see the top-of-file note). Reviewed by
# inspection instead: `grep -qxF "$CRON_LINE"` against `crontab -l`'s
# output is an exact, whole-line, fixed-string match -- there is no partial-
# match, no regex-metacharacter risk (the `-F` fixed-string mode neutralizes
# `$CRON_LINE`'s own `*` characters), and no substring false-positive
# against an unrelated or stale line.
cron_line_present() { crontab -l 2>/dev/null | grep -qxF "$CRON_LINE"; }

# ── Step 3: job_verify cron (root crontab, 08:00 UTC, DATACORE_V2 guard) ──
step_cron() {
  if [ -n "$PREFIX" ]; then
    log cron SKIPPED "(test prefix)"
    return 0
  fi
  if cron_line_present; then
    log cron OK "job_verify cron already present"
    return 0
  fi
  if [ "$MODE" = "verify" ]; then
    log cron FAILED "job_verify cron missing from root crontab"
    return 1
  fi
  if { crontab -l 2>/dev/null; echo "$CRON_LINE"; } | crontab -; then
    log cron APPLIED "installed job_verify cron line"
    return 0
  fi
  log cron FAILED "crontab install failed"
  return 1
}

# ── Step 4: TODO(verify-on-box) resolution report (pure report, no mutation) ─
step_todo_report() {
  echo "[v2] todo-report:"
  if [ ! -f "$MANIFEST" ]; then
    echo "  (manifest not found: $MANIFEST)"
    return 0
  fi
  local any=0 last_path="" lp marker path_part expanded parent cand today
  today=$(date +%Y-%m-%d)
  while IFS= read -r line; do
    lp=$(printf '%s\n' "$line" | sed -n 's/.*path:[[:space:]]*"\([^"]*\)".*/\1/p')
    [ -n "$lp" ] && last_path="$lp"
    case "$line" in
      *'TODO(verify-on-box)'*)
        any=1
        marker=$(printf '%s\n' "$line" | sed -n 's/^.*\(TODO(verify-on-box).*\)$/\1/p')
        path_part="$lp"
        [ -n "$path_part" ] || path_part="$last_path"
        if [ -z "$path_part" ]; then
          echo "  $marker -> (no artifact path found on preceding/same entry) -> UNKNOWN"
          continue
        fi
        case "$path_part" in
          "~/"*) expanded="$PREFIX/root/${path_part#\~/}" ;;
          "~") expanded="$PREFIX/root" ;;
          /*) expanded="$PREFIX$path_part" ;;
          *) expanded="$path_part" ;;
        esac
        expanded="${expanded//\{today\}/$today}"
        if [ -e "$expanded" ]; then
          echo "  $marker -> $path_part ($expanded) -> EXISTS"
        else
          parent=$(dirname "$expanded")
          cand=""
          [ -d "$parent" ] && cand=$(ls -1 "$parent" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
          if [ -n "$cand" ]; then
            echo "  $marker -> $path_part ($expanded) -> MISSING (parent dir has: $cand)"
          else
            echo "  $marker -> $path_part ($expanded) -> MISSING (parent dir not found or empty)"
          fi
        fi
        ;;
    esac
  done < "$MANIFEST"
  [ "$any" -eq 1 ] || echo "  (none)"
  return 0
}

# ── run all steps, aggregate exit code ──────────────────────────────────
RC=0
step_cryptography || RC=1
step_env || RC=1
step_cron || RC=1
step_todo_report

if [ "$MODE" = "verify" ]; then
  if [ "$RC" -eq 0 ]; then
    log verify OK "all v2 box-setup checks passed"
  else
    log verify FAILED "one or more v2 box-setup checks failed (see above)"
  fi
else
  if [ "$RC" -eq 0 ]; then
    log apply OK "all v2 box-setup steps applied/verified"
  else
    log apply FAILED "one or more v2 box-setup steps failed (see above)"
  fi
fi

exit "$RC"
