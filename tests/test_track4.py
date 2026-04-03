"""Tests for Track 4: digest injection, precompact instructions, flush/recover cycle, hooks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cozempic.digest import (
    PROTECTION_TAG,
    DigestRule,
    DigestStore,
    build_injection_text,
    flush_digest,
    inject_digest_at_tail,
    load_digest_store,
    precompact_instructions,
    recover_digest,
    save_digest_store,
)
from cozempic.helpers import is_protected, msg_bytes

import cozempic.strategies  # noqa: F401


def make_message(line_idx: int, msg: dict) -> tuple[int, dict, int]:
    return (line_idx, msg, msg_bytes(msg))


def make_user(line_idx: int, text: str = "hi") -> tuple[int, dict, int]:
    return make_message(line_idx, {
        "type": "user",
        "message": {"role": "user", "content": text},
    })


def make_assistant(line_idx: int, text: str = "ok") -> tuple[int, dict, int]:
    return make_message(line_idx, {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    })


def _make_store_with_rules() -> DigestStore:
    """Create a store with both active and pending rules."""
    store = DigestStore(project="/test")
    store.strategy_rules.append(DigestRule(
        id="R001", rule="Do not add Co-Authored-By to commits",
        priority="hard", scope="git", status="active",
        occurrence_count=5, source_reliability=1.0, type_prior=0.8,
        evidence="don't add Co-Authored-By",
        first_seen="2026-04-01", last_reinforced="2026-04-01",
    ))
    store.strategy_rules.append(DigestRule(
        id="R002", rule="Always use Edit for existing files, not Write",
        priority="soft", scope="file-ops", status="active",
        occurrence_count=3, source_reliability=0.9, type_prior=0.9,
        evidence="use Edit instead of Write",
        first_seen="2026-04-01", last_reinforced="2026-04-01",
    ))
    store.strategy_rules.append(DigestRule(
        id="R003", rule="Do not mock the database in tests",
        priority="soft", scope="testing", status="pending",
        occurrence_count=1, source_reliability=0.6, type_prior=0.6,
    ))
    return store


# ---------------------------------------------------------------------------
# build_injection_text
# ---------------------------------------------------------------------------

class TestBuildInjectionText(unittest.TestCase):

    def test_returns_none_for_empty_store(self):
        store = DigestStore()
        self.assertIsNone(build_injection_text(store))

    def test_returns_none_for_only_pending(self):
        store = DigestStore()
        store.strategy_rules.append(DigestRule(id="R001", rule="test", status="pending"))
        self.assertIsNone(build_injection_text(store))

    def test_formats_hard_and_soft_rules(self):
        store = _make_store_with_rules()
        text = build_injection_text(store)
        self.assertIsNotNone(text)
        self.assertIn("BEHAVIORAL CONTRACT", text)
        self.assertIn("PROHIBITIONS:", text)
        self.assertIn("PREFERENCES:", text)
        self.assertIn("Co-Authored-By", text)
        self.assertIn("Edit for existing files", text)

    def test_excludes_pending_rules(self):
        store = _make_store_with_rules()
        text = build_injection_text(store)
        self.assertNotIn("mock the database", text)

    def test_hard_rules_first(self):
        store = _make_store_with_rules()
        text = build_injection_text(store)
        prohibitions_pos = text.index("PROHIBITIONS:")
        preferences_pos = text.index("PREFERENCES:")
        self.assertLess(prohibitions_pos, preferences_pos)


# ---------------------------------------------------------------------------
# inject_digest_at_tail
# ---------------------------------------------------------------------------

class TestInjectDigestAtTail(unittest.TestCase):

    def test_injects_at_tail(self):
        store = _make_store_with_rules()
        messages = [make_user(0, "hello"), make_assistant(1, "hi")]
        result = inject_digest_at_tail(messages, store)
        self.assertEqual(len(result), 3)
        # Last message should be the injection
        last_msg = result[-1][1]
        self.assertTrue(last_msg.get(PROTECTION_TAG))
        self.assertTrue(last_msg.get("isVisibleInTranscriptOnly"))
        self.assertIn("BEHAVIORAL CONTRACT", last_msg["message"]["content"])

    def test_no_injection_for_empty_store(self):
        store = DigestStore()
        messages = [make_user(0)]
        result = inject_digest_at_tail(messages, store)
        self.assertEqual(len(result), 1)

    def test_replaces_existing_injection(self):
        """Re-injection should remove old digest message and add fresh one."""
        store = _make_store_with_rules()
        messages = [
            make_user(0, "hello"),
            make_message(1, {
                "type": "user",
                PROTECTION_TAG: True,
                "isVisibleInTranscriptOnly": True,
                "message": {"role": "user", "content": "old rules"},
            }),
            make_assistant(2, "response"),
        ]
        result = inject_digest_at_tail(messages, store)
        # Old injection removed, new one at tail
        digest_msgs = [m for _, m, _ in result if m.get(PROTECTION_TAG)]
        self.assertEqual(len(digest_msgs), 1)
        self.assertIn("BEHAVIORAL CONTRACT", digest_msgs[0]["message"]["content"])

    def test_injected_message_is_protected(self):
        store = _make_store_with_rules()
        messages = [make_user(0)]
        result = inject_digest_at_tail(messages, store)
        last_msg = result[-1][1]
        self.assertTrue(is_protected(last_msg))

    def test_updates_last_injection_timestamp(self):
        store = _make_store_with_rules()
        self.assertIsNone(store.strategy_rules[0].last_injection)
        inject_digest_at_tail([make_user(0)], store)
        self.assertIsNotNone(store.strategy_rules[0].last_injection)


# ---------------------------------------------------------------------------
# precompact_instructions
# ---------------------------------------------------------------------------

class TestPrecompactInstructions(unittest.TestCase):

    def test_returns_empty_for_no_rules(self):
        store = DigestStore()
        self.assertEqual(precompact_instructions(store), "")

    def test_returns_empty_for_only_soft_rules(self):
        store = DigestStore()
        store.strategy_rules.append(DigestRule(
            id="R001", rule="Use snake_case", priority="soft", status="active",
        ))
        self.assertEqual(precompact_instructions(store), "")

    def test_returns_hard_rules(self):
        store = _make_store_with_rules()
        result = precompact_instructions(store)
        self.assertIn("MUST be preserved", result)
        self.assertIn("Co-Authored-By", result)

    def test_caps_at_10_rules(self):
        store = DigestStore()
        for i in range(15):
            store.strategy_rules.append(DigestRule(
                id=f"R{i:03d}", rule=f"Hard rule number {i}",
                priority="hard", status="active",
            ))
        result = precompact_instructions(store)
        # Should have at most 10 "- " prefixed lines
        rule_lines = [l for l in result.split("\n") if l.startswith("- ")]
        self.assertLessEqual(len(rule_lines), 10)


# ---------------------------------------------------------------------------
# Flush / Recover cycle
# ---------------------------------------------------------------------------

class TestFlushRecoverCycle(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_flush_extracts_and_saves(self):
        messages = [
            make_assistant(0, "I'll add the Co-Authored-By"),
            make_user(1, "don't add Co-Authored-By"),
        ]
        digest_file = self.tmpdir / "behavioral-digest.json"
        digest_md = self.tmpdir / "behavioral-digest.md"

        with patch("cozempic.digest.DIGEST_DIR", self.tmpdir), \
             patch("cozempic.digest.DIGEST_FILE", digest_file), \
             patch("cozempic.digest.DIGEST_MD_FILE", digest_md):
            added, upvoted, rejected = flush_digest(messages, project_dir="/test")
            self.assertGreater(added, 0)
            self.assertTrue(digest_file.exists())

    def test_recover_injects_at_tail(self):
        digest_file = self.tmpdir / "behavioral-digest.json"
        digest_md = self.tmpdir / "behavioral-digest.md"

        # Pre-populate store with active rule
        store = _make_store_with_rules()
        with patch("cozempic.digest.DIGEST_DIR", self.tmpdir), \
             patch("cozempic.digest.DIGEST_FILE", digest_file), \
             patch("cozempic.digest.DIGEST_MD_FILE", digest_md):
            save_digest_store(store)

            # Simulate post-compaction: just a compact summary
            messages = [
                make_message(0, {"type": "user", "isCompactSummary": True,
                                 "message": {"role": "user", "content": "Summary..."}}),
                make_user(1, "continue working"),
            ]
            result = recover_digest(messages, project_dir="/test")
            # Should have original 2 + injected 1
            self.assertEqual(len(result), 3)
            last = result[-1][1]
            self.assertTrue(last.get(PROTECTION_TAG))

    def test_full_cycle(self):
        """Simulate: session → flush (PreCompact) → compaction → recover (PostCompact)."""
        digest_file = self.tmpdir / "behavioral-digest.json"
        digest_md = self.tmpdir / "behavioral-digest.md"

        with patch("cozempic.digest.DIGEST_DIR", self.tmpdir), \
             patch("cozempic.digest.DIGEST_FILE", digest_file), \
             patch("cozempic.digest.DIGEST_MD_FILE", digest_md):

            # Step 1: Session with corrections — flush extracts rules
            pre_compact = [
                make_assistant(0, "I'll add Co-Authored-By"),
                make_user(1, "don't add Co-Authored-By"),
                make_assistant(2, "sorry, I won't"),
                make_user(3, "also always use Edit for existing files"),
            ]
            flush_digest(pre_compact, project_dir="/test")

            # Verify rules extracted
            store = load_digest_store("/test")
            self.assertGreater(len(store.strategy_rules), 0)

            # Manually promote rules to active (simulates multi-session accumulation)
            for r in store.strategy_rules:
                r.status = "active"
                r.occurrence_count = 5
            save_digest_store(store)

            # Step 2: Simulate compaction (lose all content)
            post_compact = [
                make_message(0, {"type": "user", "isCompactSummary": True,
                                 "message": {"role": "user", "content": "Summary of conversation"}}),
            ]

            # Step 3: Recover — re-inject rules
            recovered = recover_digest(post_compact, project_dir="/test")
            digest_msgs = [m for _, m, _ in recovered if m.get(PROTECTION_TAG)]
            # Rules should be back at tail
            self.assertEqual(len(digest_msgs), 1)
            self.assertIn("BEHAVIORAL CONTRACT", digest_msgs[0]["message"]["content"])


# ---------------------------------------------------------------------------
# Hooks.json validation
# ---------------------------------------------------------------------------

class TestHooksJson(unittest.TestCase):

    def test_hooks_json_is_valid(self):
        hooks_path = Path(__file__).parent.parent / "plugin" / "hooks" / "hooks.json"
        if not hooks_path.exists():
            self.skipTest("hooks.json not found")
        data = json.loads(hooks_path.read_text())
        self.assertIn("hooks", data)
        hooks = data["hooks"]
        # Verify all expected hook events exist
        self.assertIn("SessionStart", hooks)
        self.assertIn("PreCompact", hooks)
        self.assertIn("PostCompact", hooks)
        self.assertIn("Stop", hooks)

    def test_digest_commands_in_hooks(self):
        hooks_path = Path(__file__).parent.parent / "plugin" / "hooks" / "hooks.json"
        if not hooks_path.exists():
            self.skipTest("hooks.json not found")
        raw = hooks_path.read_text()
        self.assertIn("digest inject", raw)
        self.assertIn("digest flush", raw)
        self.assertIn("digest recover", raw)


if __name__ == "__main__":
    unittest.main()
