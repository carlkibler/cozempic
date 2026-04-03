"""Tests for Track 1 bug fixes: is_protected, envelope-strip, token formula, parent relink, sibling protection."""

from __future__ import annotations

import unittest

from cozempic.helpers import is_protected, msg_bytes
from cozempic.executor import execute_actions, _relink_parent_chain
from cozempic.tokens import extract_usage_tokens
from cozempic.registry import STRATEGIES
from cozempic.types import PruneAction

import cozempic.strategies  # noqa: F401


def make_message(line_idx: int, msg: dict) -> tuple[int, dict, int]:
    return (line_idx, msg, msg_bytes(msg))


# ---------------------------------------------------------------------------
# T1.1: is_protected
# ---------------------------------------------------------------------------

class TestIsProtected(unittest.TestCase):

    def test_content_replacement(self):
        self.assertTrue(is_protected({"type": "content-replacement"}))

    def test_marble_origami_commit(self):
        self.assertTrue(is_protected({"type": "marble-origami-commit"}))

    def test_marble_origami_snapshot(self):
        self.assertTrue(is_protected({"type": "marble-origami-snapshot"}))

    def test_worktree_state(self):
        self.assertTrue(is_protected({"type": "worktree-state"}))

    def test_task_summary(self):
        self.assertTrue(is_protected({"type": "task-summary"}))

    def test_compact_summary(self):
        self.assertTrue(is_protected({"type": "user", "isCompactSummary": True}))

    def test_compact_boundary(self):
        self.assertTrue(is_protected({"type": "system", "subtype": "compact_boundary"}))

    def test_microcompact_boundary(self):
        self.assertTrue(is_protected({"type": "system", "subtype": "microcompact_boundary"}))

    def test_visible_in_transcript_only(self):
        self.assertTrue(is_protected({"type": "user", "isVisibleInTranscriptOnly": True}))

    def test_normal_user_not_protected(self):
        self.assertFalse(is_protected({"type": "user", "message": {"role": "user", "content": "hi"}}))

    def test_normal_assistant_not_protected(self):
        self.assertFalse(is_protected({"type": "assistant"}))

    def test_progress_not_protected(self):
        self.assertFalse(is_protected({"type": "progress"}))

    def test_file_history_not_protected(self):
        self.assertFalse(is_protected({"type": "file-history-snapshot"}))


class TestIsProtectedGuardsStrategies(unittest.TestCase):
    """Verify every strategy skips protected messages."""

    def _make_compact_boundary(self, line_idx: int) -> tuple[int, dict, int]:
        msg = {"type": "system", "subtype": "compact_boundary"}
        return make_message(line_idx, msg)

    def _make_compact_summary(self, line_idx: int) -> tuple[int, dict, int]:
        msg = {
            "type": "user",
            "isCompactSummary": True,
            "message": {"role": "user", "content": "Summary of conversation..."},
            "cwd": "/test",
            "version": "2.1.0",
            "slug": "test",
            "gitBranch": "main",
            "userType": "external",
            "costUSD": 0.01,
            "duration": 100,
            "toolUseResult": {"oldString": "a", "newString": "b"},
        }
        return make_message(line_idx, msg)

    def test_metadata_strip_skips_protected(self):
        """metadata-strip should not strip fields from compact summary messages."""
        messages = [self._make_compact_summary(0)]
        sr = STRATEGIES["metadata-strip"].func(messages, {})
        self.assertEqual(len(sr.actions), 0)

    def test_envelope_strip_skips_protected(self):
        """envelope-strip should not strip fields from protected messages."""
        protected = self._make_compact_summary(0)
        normal = make_message(1, {
            "type": "user", "cwd": "/test", "version": "2.1.0",
            "slug": "test", "gitBranch": "main", "userType": "external",
            "message": {"role": "user", "content": "hi"},
        })
        normal2 = make_message(2, {
            "type": "user", "cwd": "/test", "version": "2.1.0",
            "slug": "test", "gitBranch": "main", "userType": "external",
            "message": {"role": "user", "content": "there"},
        })
        messages = [protected, normal, normal2]
        sr = STRATEGIES["envelope-strip"].func(messages, {})
        # Should only modify normal messages, not the protected one
        for action in sr.actions:
            self.assertNotEqual(action.line_index, 0)

    def test_mega_block_trim_skips_protected(self):
        """mega-block-trim should not trim protected messages."""
        msg = {
            "type": "user",
            "isCompactSummary": True,
            "message": {"role": "user", "content": [
                {"type": "text", "text": "x" * 100000},
            ]},
        }
        messages = [make_message(0, msg)]
        sr = STRATEGIES["mega-block-trim"].func(messages, {})
        self.assertEqual(len(sr.actions), 0)


# ---------------------------------------------------------------------------
# T1.2: envelope-strip no longer strips isSidechain
# ---------------------------------------------------------------------------

class TestEnvelopeStripPreservesIsSidechain(unittest.TestCase):

    def test_isSidechain_preserved(self):
        """isSidechain must NOT be stripped — it's a load-time routing key."""
        messages = [
            make_message(0, {
                "type": "user", "cwd": "/test", "version": "2.1.0",
                "slug": "test", "gitBranch": "main", "userType": "external",
                "isSidechain": False,
                "message": {"role": "user", "content": "hi"},
            }),
            make_message(1, {
                "type": "user", "cwd": "/test", "version": "2.1.0",
                "slug": "test", "gitBranch": "main", "userType": "external",
                "isSidechain": False,
                "message": {"role": "user", "content": "there"},
            }),
        ]
        sr = STRATEGIES["envelope-strip"].func(messages, {})
        for action in sr.actions:
            if action.replacement:
                self.assertIn("isSidechain", action.replacement,
                              "isSidechain was stripped — this breaks subagent routing")


# ---------------------------------------------------------------------------
# T1.3: token formula includes output_tokens
# ---------------------------------------------------------------------------

class TestTokenFormulaIncludesOutput(unittest.TestCase):

    def test_output_tokens_in_total(self):
        msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 100,
                },
            },
        }
        messages = [make_message(0, msg)]
        result = extract_usage_tokens(messages)
        self.assertIsNotNone(result)
        # total = 1000 + 200 + 100 + 500 = 1800
        self.assertEqual(result["total"], 1800)
        self.assertEqual(result["output_tokens"], 500)

    def test_zero_output_tokens(self):
        msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        }
        messages = [make_message(0, msg)]
        result = extract_usage_tokens(messages)
        self.assertEqual(result["total"], 1000)


# ---------------------------------------------------------------------------
# T1.4: parentUuid re-linking
# ---------------------------------------------------------------------------

class TestRelinkParentChain(unittest.TestCase):

    def test_relinks_through_removed_message(self):
        """A → B → C, remove B → C.parentUuid should point to A."""
        messages = [
            make_message(0, {"type": "user", "uuid": "aaa", "message": {"role": "user", "content": "hi"}}),
            make_message(1, {"type": "assistant", "uuid": "bbb", "parentUuid": "aaa",
                             "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}}),
            make_message(2, {"type": "user", "uuid": "ccc", "parentUuid": "bbb",
                             "message": {"role": "user", "content": "next"}}),
        ]
        actions = [PruneAction(line_index=1, action="remove", reason="test", original_bytes=0, pruned_bytes=0)]
        result = execute_actions(messages, actions)
        # Should have A and C
        self.assertEqual(len(result), 2)
        # C's parentUuid should now point to A
        c_msg = result[1][1]
        self.assertEqual(c_msg["parentUuid"], "aaa")

    def test_relinks_logical_parent(self):
        """logicalParentUuid should also be relinked."""
        messages = [
            make_message(0, {"type": "user", "uuid": "aaa"}),
            make_message(1, {"type": "assistant", "uuid": "bbb", "parentUuid": "aaa", "logicalParentUuid": "aaa"}),
            make_message(2, {"type": "user", "uuid": "ccc", "parentUuid": "bbb", "logicalParentUuid": "bbb"}),
        ]
        actions = [PruneAction(line_index=1, action="remove", reason="test", original_bytes=0, pruned_bytes=0)]
        result = execute_actions(messages, actions)
        c_msg = result[1][1]
        self.assertEqual(c_msg["parentUuid"], "aaa")
        self.assertEqual(c_msg["logicalParentUuid"], "aaa")

    def test_no_change_when_parent_kept(self):
        """No relinking needed when parent is not removed."""
        messages = [
            make_message(0, {"type": "user", "uuid": "aaa"}),
            make_message(1, {"type": "user", "uuid": "bbb", "parentUuid": "aaa"}),
        ]
        result = execute_actions(messages, [])
        self.assertEqual(result[1][1]["parentUuid"], "aaa")

    def test_chain_of_removals(self):
        """A → B → C → D, remove B and C → D.parentUuid should point to A."""
        messages = [
            make_message(0, {"type": "user", "uuid": "aaa"}),
            make_message(1, {"type": "user", "uuid": "bbb", "parentUuid": "aaa"}),
            make_message(2, {"type": "user", "uuid": "ccc", "parentUuid": "bbb"}),
            make_message(3, {"type": "user", "uuid": "ddd", "parentUuid": "ccc"}),
        ]
        actions = [
            PruneAction(line_index=1, action="remove", reason="test", original_bytes=0, pruned_bytes=0),
            PruneAction(line_index=2, action="remove", reason="test", original_bytes=0, pruned_bytes=0),
        ]
        result = execute_actions(messages, actions)
        self.assertEqual(len(result), 2)
        d_msg = result[1][1]
        self.assertEqual(d_msg["parentUuid"], "aaa")


# ---------------------------------------------------------------------------
# T1.5: sibling tool_use protection
# ---------------------------------------------------------------------------

class TestSiblingToolUseProtection(unittest.TestCase):

    def test_tool_use_kept_when_result_kept(self):
        """If a tool_result references a tool_use marked for removal, un-remove the tool_use."""
        messages = [
            make_message(0, {
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                ]},
            }),
            make_message(1, {
                "type": "user",
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "file data"},
                ]},
            }),
        ]
        # Try to remove the tool_use message
        actions = [PruneAction(line_index=0, action="remove", reason="test", original_bytes=0, pruned_bytes=0)]
        result = execute_actions(messages, actions)
        # tool_use should be kept because its tool_result is kept
        self.assertEqual(len(result), 2)

    def test_unrelated_removal_still_works(self):
        """Removing a message with no tool_use/result dependency should still work."""
        messages = [
            make_message(0, {"type": "progress", "data": {"type": "hook_progress"}}),
            make_message(1, {
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                ]},
            }),
            make_message(2, {
                "type": "user",
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "data"},
                ]},
            }),
        ]
        actions = [PruneAction(line_index=0, action="remove", reason="test", original_bytes=0, pruned_bytes=0)]
        result = execute_actions(messages, actions)
        self.assertEqual(len(result), 2)  # progress removed, tool pair kept

    def test_both_removed_when_result_also_removed(self):
        """If both tool_use and tool_result are removed, both should stay removed."""
        messages = [
            make_message(0, {
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                ]},
            }),
            make_message(1, {
                "type": "user",
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "data"},
                ]},
            }),
        ]
        actions = [
            PruneAction(line_index=0, action="remove", reason="test", original_bytes=0, pruned_bytes=0),
            PruneAction(line_index=1, action="remove", reason="test", original_bytes=0, pruned_bytes=0),
        ]
        result = execute_actions(messages, actions)
        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
