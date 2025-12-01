#!/usr/bin/env python3
"""
Learning Metrics Tracker

Persists learning-reviewer pass/fail rates to state file for /today briefing.
State: .datacore/state/learning_metrics.yaml
"""

import os
from pathlib import Path
from datetime import datetime, timezone
from state_store import YamlStateStore

MAX_SESSIONS = 100


class LearningMetrics:
    """Track learning-reviewer quality gate pass rates."""

    def __init__(self, data_root: Path = None):
        self.data_root = data_root or Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
        self._store = YamlStateStore(
            ".datacore/state/learning_metrics.yaml",
            default={"sessions": []},
            data_root=self.data_root,
        )
        self.state = self._store.load()

    def _save(self):
        # Cap history to prevent unbounded growth
        sessions = self.state.get("sessions", [])
        if len(sessions) > MAX_SESSIONS:
            self.state["sessions"] = sessions[-MAX_SESSIONS:]
        self._store.save(self.state)

    def record(self, candidates_generated: int, passed: int, failed: int, retired: int = 0):
        """Record a learning-reviewer run."""
        entry = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "candidates_generated": candidates_generated,
            "passed": passed,
            "failed": failed,
            "retired": retired,
        }
        self.state.setdefault("sessions", []).append(entry)
        self._save()

    def summary(self, last_n: int = 10):
        """Get summary stats for /today briefing."""
        sessions = self.state.get("sessions", [])
        recent = sessions[-last_n:] if sessions else []
        total_generated = sum(s["candidates_generated"] for s in recent)
        total_passed = sum(s["passed"] for s in recent)
        total_failed = sum(s["failed"] for s in recent)

        return {
            "sessions_tracked": len(recent),
            "total_candidates": total_generated,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "overall_pass_rate": round(total_passed / total_generated, 2) if total_generated > 0 else 0.0,
            "target_range": "30-50%",
            "assessment": (
                "too loose" if total_generated > 0 and total_passed / total_generated > 0.5
                else "too strict" if total_generated > 0 and total_passed / total_generated < 0.3
                else "on target"
            ) if total_generated > 0 else "no data",
        }


def main():
    metrics = LearningMetrics()
    summary = metrics.summary()
    if summary["sessions_tracked"] > 0:
        print(f"Learning Metrics ({summary['sessions_tracked']} sessions):")
        print(f"  Pass rate: {summary['overall_pass_rate']:.0%} (target: {summary['target_range']})")
        print(f"  Assessment: {summary['assessment']}")
    else:
        print("No learning metrics recorded yet.")


if __name__ == "__main__":
    main()
