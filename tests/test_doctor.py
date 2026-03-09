"""Tests for doctor health checks."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cozempic.doctor import (
    check_agent_model_mismatch,
    check_claude_json_corruption,
    check_corrupted_tool_use,
    check_hooks_trust_flag,
    check_orphaned_tool_results,
    check_zombie_teams,
    fix_claude_json_corruption,
    fix_hooks_trust_flag,
)


class TestClaudeJsonCorruption(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.claude_json = Path(self.tmpdir) / ".claude.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_json_ok(self):
        self.claude_json.write_text(json.dumps({"numStartups": 50, "auth": "token123"}))
        with patch("cozempic.doctor.get_claude_json_path", return_value=self.claude_json):
            result = check_claude_json_corruption()
        self.assertEqual(result.status, "ok")

    def test_empty_file_is_issue(self):
        self.claude_json.write_text("")
        with patch("cozempic.doctor.get_claude_json_path", return_value=self.claude_json):
            result = check_claude_json_corruption()
        self.assertEqual(result.status, "issue")
        self.assertIn("empty", result.message)

    def test_truncated_json_is_issue(self):
        self.claude_json.write_text('{"numStartups": 50, "auth": "tok')
        with patch("cozempic.doctor.get_claude_json_path", return_value=self.claude_json):
            result = check_claude_json_corruption()
        self.assertEqual(result.status, "issue")
        self.assertIn("invalid JSON", result.message)

    def test_missing_file_is_ok(self):
        missing = Path(self.tmpdir) / "nonexistent.json"
        with patch("cozempic.doctor.get_claude_json_path", return_value=missing):
            result = check_claude_json_corruption()
        self.assertEqual(result.status, "ok")

    def test_fix_restores_from_backup(self):
        # Create corrupted file
        self.claude_json.write_text("corrupted{{{")
        # Create valid backup
        backup = self.claude_json.parent / ".claude.json.bak"
        backup.write_text(json.dumps({"numStartups": 100, "auth": "valid"}))

        with patch("cozempic.doctor.get_claude_json_path", return_value=self.claude_json):
            msg = fix_claude_json_corruption()

        self.assertIn("Restored", msg)
        # Verify restored content is valid
        data = json.loads(self.claude_json.read_text())
        self.assertEqual(data["numStartups"], 100)


class TestCorruptedToolUse(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.session_path = Path(self.tmpdir) / "projects" / "test" / "session.jsonl"
        self.session_path.parent.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_session(self, messages):
        with open(self.session_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

    def test_detects_long_tool_name(self):
        self._write_session([
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Task" + "x" * 300, "input": {}}
                    ],
                },
            }
        ])
        sessions = [{"path": self.session_path, "session_id": "test", "size": 1000}]
        with patch("cozempic.doctor.find_sessions", return_value=sessions):
            result = check_corrupted_tool_use()
        self.assertEqual(result.status, "issue")

    def test_normal_tool_name_ok(self):
        self._write_session([
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/foo"}}
                    ],
                },
            }
        ])
        sessions = [{"path": self.session_path, "session_id": "test", "size": 1000}]
        with patch("cozempic.doctor.find_sessions", return_value=sessions):
            result = check_corrupted_tool_use()
        self.assertEqual(result.status, "ok")


class TestOrphanedToolResults(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.session_path = Path(self.tmpdir) / "projects" / "test" / "session.jsonl"
        self.session_path.parent.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_session(self, messages):
        with open(self.session_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

    def test_detects_orphaned_tool_result(self):
        self._write_session([
            # tool_result with no matching tool_use
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "missing_id", "content": "result"}
                    ],
                },
            }
        ])
        sessions = [{"path": self.session_path, "session_id": "test", "size": 1000}]
        with patch("cozempic.doctor.find_sessions", return_value=sessions):
            result = check_orphaned_tool_results()
        self.assertEqual(result.status, "issue")

    def test_paired_tool_use_result_ok(self):
        self._write_session([
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "file content"}
                    ],
                },
            }
        ])
        sessions = [{"path": self.session_path, "session_id": "test", "size": 1000}]
        with patch("cozempic.doctor.find_sessions", return_value=sessions):
            result = check_orphaned_tool_results()
        self.assertEqual(result.status, "ok")


class TestZombieTeams(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.teams_dir = Path(self.tmpdir) / ".claude" / "teams"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_teams_dir_ok(self):
        claude_dir = Path(self.tmpdir) / ".claude"
        claude_dir.mkdir(parents=True)
        with patch("cozempic.doctor.get_claude_dir", return_value=claude_dir):
            result = check_zombie_teams()
        self.assertEqual(result.status, "ok")

    def test_team_without_config_is_stale(self):
        self.teams_dir.mkdir(parents=True)
        stale_team = self.teams_dir / "dead-team"
        stale_team.mkdir()
        # No config.json inside

        claude_dir = Path(self.tmpdir) / ".claude"
        with patch("cozempic.doctor.get_claude_dir", return_value=claude_dir):
            result = check_zombie_teams()
        self.assertIn(result.status, ("warning", "issue"))
        self.assertIn("stale", result.message)

    def test_fresh_team_with_config_ok(self):
        self.teams_dir.mkdir(parents=True)
        active_team = self.teams_dir / "active-team"
        active_team.mkdir()
        config = active_team / "config.json"
        config.write_text(json.dumps({"name": "active", "members": []}))

        claude_dir = Path(self.tmpdir) / ".claude"
        with patch("cozempic.doctor.get_claude_dir", return_value=claude_dir):
            result = check_zombie_teams()
        self.assertEqual(result.status, "ok")


class TestHooksTrustFlag(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.claude_json = Path(self.tmpdir) / ".claude.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_trusted_workspace_missing_hooks_flag_is_issue(self):
        self.claude_json.write_text(json.dumps({
            "/path/to/project": {"hasTrustDialogAccepted": True},
        }))
        with patch("cozempic.doctor.get_claude_json_path", return_value=self.claude_json):
            result = check_hooks_trust_flag()
        self.assertEqual(result.status, "issue")
        self.assertIn("hasTrustDialogHooksAccepted", result.message)

    def test_trusted_workspace_with_hooks_flag_is_ok(self):
        self.claude_json.write_text(json.dumps({
            "/path/to/project": {
                "hasTrustDialogAccepted": True,
                "hasTrustDialogHooksAccepted": True,
            },
        }))
        with patch("cozempic.doctor.get_claude_json_path", return_value=self.claude_json):
            result = check_hooks_trust_flag()
        self.assertEqual(result.status, "ok")

    def test_untrusted_workspace_is_ok(self):
        self.claude_json.write_text(json.dumps({
            "/path/to/project": {"hasTrustDialogAccepted": False},
        }))
        with patch("cozempic.doctor.get_claude_json_path", return_value=self.claude_json):
            result = check_hooks_trust_flag()
        self.assertEqual(result.status, "ok")

    def test_missing_file_is_ok(self):
        missing = Path(self.tmpdir) / "nonexistent.json"
        with patch("cozempic.doctor.get_claude_json_path", return_value=missing):
            result = check_hooks_trust_flag()
        self.assertEqual(result.status, "ok")

    def test_fix_sets_hooks_flag(self):
        self.claude_json.write_text(json.dumps({
            "/path/to/project": {"hasTrustDialogAccepted": True},
        }))
        with patch("cozempic.doctor.get_claude_json_path", return_value=self.claude_json):
            msg = fix_hooks_trust_flag()
        self.assertIn("1", msg)
        data = json.loads(self.claude_json.read_text())
        self.assertTrue(data["/path/to/project"]["hasTrustDialogHooksAccepted"])

    def test_fix_multiple_projects(self):
        self.claude_json.write_text(json.dumps({
            "/project/a": {"hasTrustDialogAccepted": True},
            "/project/b": {"hasTrustDialogAccepted": True},
            "/project/c": {"hasTrustDialogAccepted": False},
        }))
        with patch("cozempic.doctor.get_claude_json_path", return_value=self.claude_json):
            msg = fix_hooks_trust_flag()
        self.assertIn("2", msg)
        data = json.loads(self.claude_json.read_text())
        self.assertTrue(data["/project/a"]["hasTrustDialogHooksAccepted"])
        self.assertTrue(data["/project/b"]["hasTrustDialogHooksAccepted"])
        self.assertNotIn("hasTrustDialogHooksAccepted", data["/project/c"])


class TestAgentModelMismatch(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.claude_dir = Path(self.tmpdir) / ".claude"
        self.claude_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_teams_dir_is_ok(self):
        with patch("cozempic.doctor.get_claude_dir", return_value=self.claude_dir):
            result = check_agent_model_mismatch()
        self.assertEqual(result.status, "ok")

    def test_empty_teams_dir_is_ok(self):
        (self.claude_dir / "teams").mkdir()
        with patch("cozempic.doctor.get_claude_dir", return_value=self.claude_dir):
            result = check_agent_model_mismatch()
        self.assertEqual(result.status, "ok")

    def test_teams_with_model_in_settings_is_ok(self):
        (self.claude_dir / "teams" / "my-team").mkdir(parents=True)
        (self.claude_dir / "settings.json").write_text(
            json.dumps({"model": "claude-opus-4-6"})
        )
        with patch("cozempic.doctor.get_claude_dir", return_value=self.claude_dir):
            result = check_agent_model_mismatch()
        self.assertEqual(result.status, "ok")
        self.assertIn("claude-opus-4-6", result.message)

    def test_teams_without_model_in_settings_is_warning(self):
        (self.claude_dir / "teams" / "my-team").mkdir(parents=True)
        (self.claude_dir / "settings.json").write_text(json.dumps({"theme": "dark"}))
        with patch("cozempic.doctor.get_claude_dir", return_value=self.claude_dir):
            result = check_agent_model_mismatch()
        self.assertEqual(result.status, "warning")

    def test_teams_without_settings_file_is_warning(self):
        (self.claude_dir / "teams" / "my-team").mkdir(parents=True)
        with patch("cozempic.doctor.get_claude_dir", return_value=self.claude_dir):
            result = check_agent_model_mismatch()
        self.assertEqual(result.status, "warning")


if __name__ == "__main__":
    unittest.main()
