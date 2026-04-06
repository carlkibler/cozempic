"""Tests for auto-update logic."""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestVersionTuple(unittest.TestCase):
    def test_parses_version(self):
        from cozempic.updater import _version_tuple
        self.assertEqual(_version_tuple("1.2.0"), (1, 2, 0))
        self.assertEqual(_version_tuple("2.0.0"), (2, 0, 0))

    def test_bad_version_returns_zeros(self):
        from cozempic.updater import _version_tuple
        self.assertEqual(_version_tuple("bad"), (0,))


class TestShouldCheck(unittest.TestCase):
    def test_no_cache_file_means_should_check(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / ".cozempic_update_check"
            with patch("cozempic.updater._CACHE_FILE", cache):
                from cozempic.updater import _should_check
                self.assertTrue(_should_check())

    def test_recent_check_means_skip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / ".cozempic_update_check"
            cache.write_text(str(time.time()))
            with patch("cozempic.updater._CACHE_FILE", cache):
                from cozempic.updater import _should_check
                self.assertFalse(_should_check())

    def test_old_check_means_should_check(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / ".cozempic_update_check"
            cache.write_text(str(time.time() - 90000))  # 25 hours ago
            with patch("cozempic.updater._CACHE_FILE", cache):
                from cozempic.updater import _should_check
                self.assertTrue(_should_check())


class TestMaybeAutoUpdate(unittest.TestCase):
    def test_skips_when_env_var_set(self):
        """COZEMPIC_NO_AUTO_UPDATE=1 disables all update activity."""
        with patch.dict(os.environ, {"COZEMPIC_NO_AUTO_UPDATE": "1"}):
            with patch("cozempic.updater._should_check") as mock_check:
                from cozempic.updater import maybe_auto_update
                maybe_auto_update()
                mock_check.assert_not_called()

    def test_works_without_tty(self):
        """Auto-update should work even without TTY (hooks, daemons)."""
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            with patch("cozempic.updater._should_check", return_value=False) as mock_check:
                from cozempic.updater import maybe_auto_update
                maybe_auto_update()
                mock_check.assert_called()  # Should still check (TTY no longer blocks)

    def test_skips_when_already_checked(self):
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            with patch("cozempic.updater._should_check", return_value=False):
                with patch("cozempic.updater._get_latest_version") as mock_get:
                    from cozempic.updater import maybe_auto_update
                    maybe_auto_update()
                    mock_get.assert_not_called()

    def test_skips_when_already_up_to_date(self):
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            with patch("cozempic.updater._should_check", return_value=True), \
                 patch("cozempic.updater._mark_checked"), \
                 patch("cozempic.updater._get_latest_version", return_value="0.0.1"), \
                 patch("cozempic.updater._do_upgrade") as mock_upgrade:
                from cozempic.updater import maybe_auto_update
                maybe_auto_update()
                mock_upgrade.assert_not_called()

    def test_upgrades_when_newer_version_available(self, capsys=None):
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            with patch("cozempic.updater._should_check", return_value=True), \
                 patch("cozempic.updater._mark_checked"), \
                 patch("cozempic.updater._get_latest_version", return_value="99.99.99"), \
                 patch("cozempic.updater._do_upgrade", return_value=True) as mock_upgrade:
                from cozempic.updater import maybe_auto_update
                maybe_auto_update()
                mock_upgrade.assert_called_once_with("99.99.99")

    def test_prints_failure_message_on_upgrade_error(self):
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            calls = []
            mock_stdout.write = lambda s: calls.append(s)
            with patch("cozempic.updater._should_check", return_value=True), \
                 patch("cozempic.updater._mark_checked"), \
                 patch("cozempic.updater._get_latest_version", return_value="99.99.99"), \
                 patch("cozempic.updater._do_upgrade", return_value=False), \
                 patch("builtins.print") as mock_print:
                from cozempic.updater import maybe_auto_update
                maybe_auto_update()
                printed = " ".join(str(a) for call in mock_print.call_args_list for a in call[0])
                self.assertIn("auto-update failed", printed)

    def test_no_op_when_pypi_unreachable(self):
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            with patch("cozempic.updater._should_check", return_value=True), \
                 patch("cozempic.updater._mark_checked"), \
                 patch("cozempic.updater._get_latest_version", return_value=None), \
                 patch("cozempic.updater._do_upgrade") as mock_upgrade:
                from cozempic.updater import maybe_auto_update
                maybe_auto_update()
                mock_upgrade.assert_not_called()
