"""Tests for upgrading stale project hook wiring."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cozempic.init import COZEMPIC_HOOKS, wire_hooks


class TestWireHooks(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_dir = Path(self.tmpdir) / "proj"
        self.settings_path = self.project_dir / ".claude" / "settings.json"
        self.settings_path.parent.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_wire_hooks_upgrades_stale_entries(self):
        self.settings_path.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "cozempic guard --daemon 2>/dev/null || true"},
                            {"type": "command", "command": "echo keep-me"},
                        ],
                    }
                ],
                "PreCompact": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "cozempic checkpoint 2>/dev/null || true"},
                        ],
                    }
                ],
            }
        }, indent=2))

        result = wire_hooks(str(self.project_dir))
        settings = json.loads(self.settings_path.read_text())

        self.assertIn("SessionStart[(all)]", result["updated"])
        self.assertIn("PreCompact[(all)]", result["updated"])
        self.assertIn("PostCompact[(all)]", result["added"])
        self.assertIsNotNone(result["backup_path"])

        session_start = settings["hooks"]["SessionStart"][0]
        commands = [hook["command"] for hook in session_start["hooks"]]
        self.assertIn("echo keep-me", commands)
        self.assertTrue(any("digest inject" in command for command in commands))
        self.assertFalse(any(command == "cozempic guard --daemon 2>/dev/null || true" for command in commands))

        precompact = settings["hooks"]["PreCompact"][0]
        self.assertTrue(any("digest flush" in hook["command"] for hook in precompact["hooks"]))

    def test_wire_hooks_is_idempotent_when_current(self):
        self.settings_path.write_text(json.dumps({"hooks": COZEMPIC_HOOKS}, indent=2))

        result = wire_hooks(str(self.project_dir))

        self.assertEqual(result["added"], [])
        self.assertEqual(result["updated"], [])
        self.assertEqual(sorted(result["skipped"]), sorted([
            "SessionStart[(all)]",
            "PostToolUse[Task]",
            "PostToolUse[TaskCreate|TaskUpdate]",
            "PostToolUse[(all)]",
            "PreCompact[(all)]",
            "PostCompact[(all)]",
            "Stop[(all)]",
        ]))
        self.assertIsNone(result["backup_path"])
