#!/usr/bin/env bash
# Weekly cross-model calibration, one agent's run on the planted-defect practice
# project (AUD-4). Run by cadence_run for the templates audit-calibration-<agent>.
# Writes 2-datacore/1-tracks/dev/audits/calibration/runs/<date>-<agent>.yaml only.
AUDIT_MODE=calibrate exec "$(cd "$(dirname "$0")" && pwd)/audit_nightly.sh"
