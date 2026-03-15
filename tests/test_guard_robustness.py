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


if __name__ == "__main__":
    unittest.main()
