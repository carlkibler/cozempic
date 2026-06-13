"""Guard audit ledger and value-report tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestGuardAudit(unittest.TestCase):
    def test_records_and_summarizes_value_events(self):
        from cozempic.guard_audit import (
            load_guard_events,
            record_guard_event,
            summarize_guard_events,
            verdict,
        )

        with tempfile.TemporaryDirectory() as td:
            audit_file = Path(td) / "audit.jsonl"
            with patch.dict(os.environ, {"COZEMPIC_GUARD_AUDIT_FILE": str(audit_file)}):
                record_guard_event("guard_start", session_id="s1", cwd="/tmp/project")
                record_guard_event(
                    "guard_prune_result",
                    session_id="s1",
                    cwd="/tmp/project",
                    tier="hard1",
                    saved_mb=1.25,
                    tokens_saved=42_000,
                    reloading=True,
                    live_write_skipped=False,
                )

                events = load_guard_events(path=audit_file)

        summary = summarize_guard_events(events)
        self.assertEqual(summary["guard_starts"], 1)
        self.assertEqual(summary["hard_checks"], 1)
        self.assertEqual(summary["real_prunes"], 1)
        self.assertEqual(summary["reloads"], 1)
        self.assertEqual(summary["tokens_saved"], 42_000)
        self.assertEqual(summary["cwds"], ["/tmp/project"])
        self.assertEqual(verdict(summary), ("keep", "1 real prune(s), 1 reload(s), 42,000 tokens saved"))

    def test_report_command_json_shape(self):
        from cozempic.cli import build_parser, cmd_guard_report
        from cozempic.guard_audit import record_guard_event

        with tempfile.TemporaryDirectory() as td:
            audit_file = Path(td) / "audit.jsonl"
            log_dir = Path(td) / "logs"
            log_dir.mkdir()
            with patch.dict(os.environ, {"COZEMPIC_GUARD_AUDIT_FILE": str(audit_file)}):
                record_guard_event("guard_start", session_id="s1", cwd="/tmp/project")

            parser = build_parser()
            args = parser.parse_args([
                "guard-report",
                "--audit-file", str(audit_file),
                "--log-dir", str(log_dir),
                "--json",
            ])

            import io
            import sys

            captured = io.StringIO()
            with patch.object(sys, "stdout", captured):
                cmd_guard_report(args)
            payload = json.loads(captured.getvalue())

        self.assertEqual(payload["verdict"], "no-evidence-yet")
        self.assertEqual(payload["summary"]["guard_starts"], 1)


if __name__ == "__main__":
    unittest.main()
