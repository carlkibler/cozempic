"""Tests for guard daemon robustness improvements."""
import unittest


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


class TestReloadSelfDaemon(unittest.TestCase):
    def test_no_daemon_no_op(self):
        """reload_self_daemon returns reloaded=False when no daemon exists for the session."""
        from cozempic.guard import reload_self_daemon
        result = reload_self_daemon(
            cwd="/tmp",
            session_id="absolutely-nonexistent-session-uuid-for-test",
        )
        self.assertFalse(result["reloaded"])
        self.assertIn("no daemon", result["reason"].lower())

    def test_explicit_session_with_no_daemon_does_not_spawn(self):
        """When the named session has no live daemon, reload_self must not spawn one."""
        from cozempic.guard import reload_self_daemon
        # Explicit, fake session id — no PID file, no daemon. Must short-circuit
        # without ever calling start_guard_daemon.
        result = reload_self_daemon(
            cwd="/tmp",
            session_id="11111111-2222-3333-4444-555555555555",
        )
        self.assertFalse(result["reloaded"])
        self.assertIsNone(result.get("new_pid"))
        self.assertIn("no daemon", result["reason"].lower())


if __name__ == "__main__":
    unittest.main()
