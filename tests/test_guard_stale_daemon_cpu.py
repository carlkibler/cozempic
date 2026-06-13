import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cozempic.guard as guard_mod


class _EmptyState:
    subagents = []
    tasks = []
    message_count = 0
    team_name = ""

    def is_empty(self):
        return True


class _StopLoop(Exception):
    pass


def _session(tmpdir):
    path = Path(tmpdir) / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.jsonl"
    path.write_text('{"type":"user","message":{"role":"user","content":"x"}}\n', encoding="utf-8")
    return {"session_id": path.stem, "path": path, "project": "p", "size": path.stat().st_size}


class TestStaleDaemonCpuRegressions(unittest.TestCase):
    def _common_patches(self, sess, token_estimate):
        return [
            mock.patch.object(guard_mod, "_resolve_session_by_id", return_value=sess),
            mock.patch.object(guard_mod, "find_current_session", return_value=sess),
            mock.patch.object(guard_mod, "find_claude_pid", return_value=None),
            mock.patch.object(guard_mod, "checkpoint_team", return_value=_EmptyState()),
            mock.patch.object(guard_mod, "quick_token_estimate", return_value=token_estimate),
            mock.patch.object(guard_mod, "load_messages", return_value=[]),
            mock.patch.object(guard_mod, "cleanup_old_backups"),
            mock.patch.object(guard_mod, "ping_install_if_new"),
            mock.patch.object(guard_mod, "maybe_auto_update"),
            mock.patch.object(guard_mod, "_cleanup_stale_watchers"),
            mock.patch.object(guard_mod, "_detect_interactive", return_value=False),
            mock.patch.object(guard_mod, "_safe_unlink_session_pidfile"),
            mock.patch("cozempic.tokens.detect_context_window", return_value=1_000_000),
            mock.patch("cozempic.session.record_session"),
        ]

    def test_guard_without_claude_pid_exits_before_polling_or_pruning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sess = _session(tmpdir)
            patches = self._common_patches(sess, token_estimate=300_000)
            prune = mock.Mock(name="guard_prune_cycle")
            sleep = mock.Mock(name="sleep")
            patches.extend([
                mock.patch.object(guard_mod, "guard_prune_cycle", prune),
                mock.patch.object(guard_mod.time, "sleep", sleep),
            ])
            for patcher in patches:
                patcher.start()
            try:
                guard_mod.start_guard(
                    cwd=tmpdir,
                    session_id=sess["session_id"],
                    interval=1,
                    reactive=False,
                    auto_reload=True,
                    threshold_tokens=550_000,
                    soft_threshold_tokens=250_000,
                )
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
            sleep.assert_not_called()
            prune.assert_not_called()

    def test_soft_readonly_tier_with_live_pid_does_not_run_prune_pipeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sess = _session(tmpdir)
            patches = self._common_patches(sess, token_estimate=300_000)
            prune = mock.Mock(name="guard_prune_cycle")
            sleeps = iter([None, _StopLoop()])

            def fake_sleep(_seconds):
                nxt = next(sleeps)
                if isinstance(nxt, BaseException):
                    raise nxt

            patches.extend([
                mock.patch.object(guard_mod, "find_claude_pid", return_value=12345),
                mock.patch.object(guard_mod, "_record_claude_identity"),
                mock.patch.object(guard_mod, "_pid_identity_match", return_value=True),
                mock.patch.object(guard_mod, "_is_claude_process", return_value=True),
                mock.patch.object(guard_mod.os, "kill", return_value=None),
                mock.patch.object(guard_mod, "guard_prune_cycle", prune),
                mock.patch.object(guard_mod.time, "sleep", side_effect=fake_sleep),
            ])
            for patcher in patches:
                patcher.start()
            try:
                with self.assertRaises(_StopLoop):
                    guard_mod.start_guard(
                        cwd=tmpdir,
                        session_id=sess["session_id"],
                        interval=1,
                        reactive=False,
                        auto_reload=True,
                        threshold_tokens=550_000,
                        soft_threshold_tokens=250_000,
                    )
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
            prune.assert_not_called()

    def test_orphaned_hard_result_exits_instead_of_looping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sess = _session(tmpdir)
            patches = self._common_patches(sess, token_estimate=600_000)
            prune = mock.Mock(return_value={
                "saved_mb": 0.0,
                "live_write_skipped": True,
                "orphaned_guard": True,
                "reloading": False,
            })
            patches.extend([
                mock.patch.object(guard_mod, "find_claude_pid", return_value=12345),
                mock.patch.object(guard_mod, "_record_claude_identity"),
                mock.patch.object(guard_mod, "_pid_identity_match", return_value=True),
                mock.patch.object(guard_mod, "_is_claude_process", return_value=True),
                mock.patch.object(guard_mod.os, "kill", return_value=None),
                mock.patch.object(guard_mod, "guard_prune_cycle", prune),
                mock.patch.object(guard_mod.time, "sleep", return_value=None),
            ])
            for patcher in patches:
                patcher.start()
            try:
                guard_mod.start_guard(
                    cwd=tmpdir,
                    session_id=sess["session_id"],
                    interval=1,
                    reactive=False,
                    auto_reload=True,
                    threshold_tokens=550_000,
                    soft_threshold_tokens=250_000,
                )
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
            self.assertEqual(prune.call_count, 1)


if __name__ == "__main__":
    unittest.main()
