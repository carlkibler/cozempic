"""Tests for guard daemon robustness improvements."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestGuardSignalHandling(unittest.TestCase):
    def test_sigterm_constant_exists(self):
        """SIGTERM is available on this platform."""
        import signal
        self.assertTrue(hasattr(signal, 'SIGTERM'))


class TestBackupCleanupIntegration(unittest.TestCase):
    def test_cleanup_old_backups_importable(self):
        """cleanup_old_backups can be imported from session module."""
        from cozempic.session import cleanup_old_backups
        self.assertTrue(callable(cleanup_old_backups))


class TestGuardDaemonPidHandoff(unittest.TestCase):
    def test_start_guard_daemon_passes_explicit_claude_pid_to_child(self):
        from cozempic.guard import start_guard_daemon

        with tempfile.TemporaryDirectory() as tmpdir:
            session_log = Path("/tmp/cozempic_guard_test-session.log")
            session_pid = Path("/tmp/cozempic_guard_test-session.pid")
            captured = {}

            class DummyProc:
                pid = 4242

            def fake_popen(cmd_parts, **kwargs):
                captured["cmd_parts"] = cmd_parts
                return DummyProc()

            with (
                patch("cozempic.guard._cleanup_legacy_pid"),
                patch("cozempic.guard._is_guard_running_for_session", return_value=None),
                patch("cozempic.guard.find_claude_pid", return_value=9999),
                patch("cozempic.guard.subprocess.Popen", side_effect=fake_popen),
            ):
                result = start_guard_daemon(
                    cwd=tmpdir,
                    session_id="test-session",
                    threshold_tokens=123,
                )

            self.assertTrue(result["started"])
            self.assertIn("--claude-pid", captured["cmd_parts"])
            self.assertIn("9999", captured["cmd_parts"])

            session_log.unlink(missing_ok=True)
            session_pid.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
