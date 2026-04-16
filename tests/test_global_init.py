"""Tests for global auto-init + uninstall + opt-out paths."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestGlobalAutoInit(unittest.TestCase):
    def _stub_marker(self, tmpdir):
        return Path(tmpdir) / ".cozempic_global_initialized"

    def _stub_home_claude(self, tmpdir):
        d = Path(tmpdir) / ".claude"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_skipped_when_env_set(self):
        from cozempic import cli
        with tempfile.TemporaryDirectory() as tmp:
            self._stub_home_claude(tmp)
            with mock.patch.dict(os.environ, {"COZEMPIC_NO_GLOBAL_INIT": "1"}):
                with mock.patch.object(cli, "_GLOBAL_INIT_MARKER", self._stub_marker(tmp)):
                    with mock.patch.object(cli.Path, "home", return_value=Path(tmp)):
                        cli._maybe_global_init(["list"])
                        # Marker must NOT have been touched
                        self.assertFalse(self._stub_marker(tmp).exists())

    def test_skipped_when_marker_exists(self):
        from cozempic import cli
        with tempfile.TemporaryDirectory() as tmp:
            self._stub_home_claude(tmp)
            marker = self._stub_marker(tmp)
            marker.touch()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("COZEMPIC_NO_GLOBAL_INIT", None)
                with mock.patch.object(cli, "_GLOBAL_INIT_MARKER", marker):
                    with mock.patch.object(cli.Path, "home", return_value=Path(tmp)):
                        # Should bail out before calling run_init
                        with mock.patch.object(cli, "run_init") as ri:
                            cli._maybe_global_init(["list"])
                            ri.assert_not_called()

    def test_skipped_when_no_claude_dir(self):
        from cozempic import cli
        with tempfile.TemporaryDirectory() as tmp:
            # No ~/.claude/ created
            os.environ.pop("COZEMPIC_NO_GLOBAL_INIT", None)
            marker = self._stub_marker(tmp)
            with mock.patch.object(cli, "_GLOBAL_INIT_MARKER", marker):
                with mock.patch.object(cli.Path, "home", return_value=Path(tmp)):
                    with mock.patch.object(cli, "run_init") as ri:
                        cli._maybe_global_init(["list"])
                        ri.assert_not_called()
                        self.assertFalse(marker.exists())

    def test_runs_when_unconfigured(self):
        from cozempic import cli
        with tempfile.TemporaryDirectory() as tmp:
            self._stub_home_claude(tmp)
            os.environ.pop("COZEMPIC_NO_GLOBAL_INIT", None)
            marker = self._stub_marker(tmp)
            with mock.patch.object(cli, "_GLOBAL_INIT_MARKER", marker):
                with mock.patch.object(cli.Path, "home", return_value=Path(tmp)):
                    cli._maybe_global_init(["list"])
                    # Hooks were written
                    settings = Path(tmp) / ".claude" / "settings.json"
                    self.assertTrue(settings.exists())
                    data = json.loads(settings.read_text())
                    self.assertIn("hooks", data)
                    self.assertIn("SessionStart", data["hooks"])
                    # Marker was set so we don't re-run
                    self.assertTrue(marker.exists())


class TestUninstallHooks(unittest.TestCase):
    def test_removes_cozempic_hooks_only(self):
        from cozempic.init import wire_hooks, uninstall_hooks
        with tempfile.TemporaryDirectory() as tmp:
            # Set up a settings.json with a non-cozempic hook + cozempic hooks
            (Path(tmp) / ".claude").mkdir()
            settings_path = Path(tmp) / ".claude" / "settings.json"
            settings_path.write_text(json.dumps({
                "hooks": {
                    "SessionStart": [{
                        "matcher": "",
                        "hooks": [{"type": "command", "command": "echo 'user-hook'"}],
                    }],
                }
            }))
            # Wire cozempic on top
            wire_hooks(tmp)
            after_wire = json.loads(settings_path.read_text())
            self.assertGreater(len(after_wire["hooks"]["SessionStart"]), 1)

            # Uninstall — user hook stays, cozempic hooks go
            result = uninstall_hooks(tmp)
            self.assertGreater(len(result["removed"]), 0)
            after = json.loads(settings_path.read_text())
            ss = after["hooks"]["SessionStart"]
            self.assertEqual(len(ss), 1)
            self.assertIn("user-hook", ss[0]["hooks"][0]["command"])

    def test_idempotent_on_missing_settings(self):
        from cozempic.init import uninstall_hooks
        with tempfile.TemporaryDirectory() as tmp:
            result = uninstall_hooks(tmp)
            self.assertEqual(result["removed"], [])


if __name__ == "__main__":
    unittest.main()
