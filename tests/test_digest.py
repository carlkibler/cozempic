"""Tests for behavioral digest — Phase 1: extraction, scoring, persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cozempic.digest import (
    ADMISSION_THRESHOLD,
    DIGEST_DIR,
    DIGEST_FILE,
    MAX_ACTIVE_RULES,
    PROMOTION_COUNT,
    PROTECTION_TAG,
    DigestRule,
    DigestStore,
    _find_duplicate,
    _to_prohibition,
    admit_rule,
    classify_turn,
    clear_digest_store,
    extract_corrections,
    load_digest_store,
    save_digest_store,
    score_rule,
    show_digest,
    update_digest,
)
from cozempic.helpers import is_protected, msg_bytes

import cozempic.strategies  # noqa: F401


def make_message(line_idx: int, msg: dict) -> tuple[int, dict, int]:
    return (line_idx, msg, msg_bytes(msg))


def make_user(line_idx: int, text: str) -> tuple[int, dict, int]:
    return make_message(line_idx, {
        "type": "user",
        "message": {"role": "user", "content": text},
    })


def make_assistant(line_idx: int, text: str) -> tuple[int, dict, int]:
    return make_message(line_idx, {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    })


# ---------------------------------------------------------------------------
# classify_turn
# ---------------------------------------------------------------------------

class TestClassifyTurn(unittest.TestCase):

    def test_explicit_no(self):
        self.assertEqual(classify_turn("No, don't do that"), "EXPLICIT_CORRECTION")

    def test_explicit_dont(self):
        self.assertEqual(classify_turn("don't add Co-Authored-By"), "EXPLICIT_CORRECTION")

    def test_explicit_do_not(self):
        self.assertEqual(classify_turn("do not use Write on existing files"), "EXPLICIT_CORRECTION")

    def test_explicit_stop(self):
        self.assertEqual(classify_turn("stop adding comments to every function"), "EXPLICIT_CORRECTION")

    def test_explicit_never(self):
        self.assertEqual(classify_turn("never push to main without asking"), "EXPLICIT_CORRECTION")

    def test_explicit_please_dont(self):
        self.assertEqual(classify_turn("please don't summarize after each change"), "EXPLICIT_CORRECTION")

    def test_implicit_actually(self):
        self.assertEqual(classify_turn("actually, use the other approach"), "IMPLICIT_CORRECTION")

    def test_implicit_instead(self):
        self.assertEqual(classify_turn("instead, use Edit not Write"), "IMPLICIT_CORRECTION")

    def test_implicit_thats_not(self):
        self.assertEqual(classify_turn("that's not what I meant"), "IMPLICIT_CORRECTION")

    def test_preference_always(self):
        self.assertEqual(classify_turn("always use snake_case for variables"), "PREFERENCE")

    def test_preference_from_now_on(self):
        self.assertEqual(classify_turn("from now on, run tests after each change"), "PREFERENCE")

    def test_preference_remember(self):
        self.assertEqual(classify_turn("remember to check for null values"), "PREFERENCE")

    def test_apology_follow_up(self):
        result = classify_turn("use the correct import path", "sorry about that mistake")
        self.assertEqual(result, "APOLOGY_FOLLOW_UP")

    def test_none_normal(self):
        self.assertEqual(classify_turn("can you read that file?"), "NONE")

    def test_none_short(self):
        self.assertEqual(classify_turn("ok"), "NONE")

    def test_none_empty(self):
        self.assertEqual(classify_turn(""), "NONE")


# ---------------------------------------------------------------------------
# _to_prohibition
# ---------------------------------------------------------------------------

class TestToProhibition(unittest.TestCase):

    def test_already_prohibition(self):
        self.assertEqual(_to_prohibition("Don't add X"), "Don't add X")

    def test_do_not(self):
        self.assertEqual(_to_prohibition("do not mock the database"), "Do not mock the database")

    def test_stop_doing(self):
        result = _to_prohibition("stop adding comments")
        self.assertEqual(result, "Do not comments")

    def test_never(self):
        result = _to_prohibition("never push to main")
        self.assertEqual(result, "Do not ever push to main")

    def test_no_prefix(self):
        result = _to_prohibition("No, use Edit instead")
        self.assertEqual(result, "Use Edit instead")


# ---------------------------------------------------------------------------
# extract_corrections
# ---------------------------------------------------------------------------

class TestExtractCorrections(unittest.TestCase):

    def test_extracts_explicit_correction(self):
        messages = [
            make_assistant(0, "I'll add Co-Authored-By"),
            make_user(1, "don't add Co-Authored-By to commits"),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].priority, "hard")
        self.assertIn("Co-Authored-By", rules[0].evidence)

    def test_extracts_preference(self):
        messages = [
            make_user(0, "always use snake_case for function names"),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].source_reliability, 0.9)

    def test_skips_normal_messages(self):
        messages = [
            make_user(0, "can you read the config file?"),
            make_assistant(1, "sure, let me read it"),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(len(rules), 0)

    def test_respects_since_turn(self):
        messages = [
            make_user(0, "don't do that"),  # Before window
            make_assistant(1, "ok"),
            make_user(2, "stop using mocks"),  # In window
        ]
        rules = extract_corrections(messages, since_turn=2)
        self.assertEqual(len(rules), 1)
        self.assertIn("mock", rules[0].evidence.lower())

    def test_infers_git_scope(self):
        messages = [make_user(0, "don't push to main branch")]
        rules = extract_corrections(messages)
        self.assertEqual(rules[0].scope, "git")

    def test_infers_file_scope(self):
        messages = [make_user(0, "don't use Write on existing files")]
        rules = extract_corrections(messages)
        self.assertEqual(rules[0].scope, "file-ops")

    def test_caps_rule_length(self):
        # Use 501 chars — above the 500-char rule cap but below the 600-char length guard.
        long_text = "don't " + "x" * 495
        self.assertLess(len(long_text), 600)  # passes length guard
        messages = [make_user(0, long_text)]
        rules = extract_corrections(messages)
        self.assertLessEqual(len(rules[0].rule), 500)


# ---------------------------------------------------------------------------
# score_rule
# ---------------------------------------------------------------------------

class TestScoreRule(unittest.TestCase):

    def test_new_explicit_correction(self):
        rule = DigestRule(id="R001", rule="test", occurrence_count=1,
                          source_reliability=1.0, type_prior=0.8)
        score = score_rule(rule, days_since_last=0)
        # 0.25*(1/2) + 0.30*1.0 + 0.20*1.0 + 0.25*0.8 = 0.125 + 0.30 + 0.20 + 0.20 = 0.825
        self.assertAlmostEqual(score, 0.825, places=3)

    def test_above_admission(self):
        rule = DigestRule(id="R001", rule="test", occurrence_count=1,
                          source_reliability=1.0, type_prior=0.8)
        self.assertGreater(score_rule(rule), ADMISSION_THRESHOLD)

    def test_low_reliability_rejected(self):
        rule = DigestRule(id="R001", rule="test", occurrence_count=1,
                          source_reliability=0.3, type_prior=0.1)
        score = score_rule(rule, days_since_last=0)
        self.assertLess(score, ADMISSION_THRESHOLD)

    def test_decay_reduces_score(self):
        rule = DigestRule(id="R001", rule="test", occurrence_count=1,
                          source_reliability=1.0, type_prior=0.8)
        fresh = score_rule(rule, days_since_last=0)
        old = score_rule(rule, days_since_last=30)
        self.assertGreater(fresh, old)

    def test_high_occurrence_helps(self):
        low = DigestRule(id="R001", rule="test", occurrence_count=1,
                         source_reliability=0.5, type_prior=0.5)
        high = DigestRule(id="R002", rule="test", occurrence_count=5,
                          source_reliability=0.5, type_prior=0.5)
        self.assertGreater(score_rule(high), score_rule(low))


# ---------------------------------------------------------------------------
# admit_rule
# ---------------------------------------------------------------------------

class TestAdmitRule(unittest.TestCase):

    def test_admits_strong_rule(self):
        store = DigestStore()
        rule = DigestRule(id="", rule="Do not add Co-Authored-By",
                          source_reliability=1.0, type_prior=0.8,
                          first_seen="2026-04-01", last_reinforced="2026-04-01")
        result = admit_rule(rule, store)
        self.assertEqual(result, "added")
        self.assertEqual(len(store.strategy_rules), 1)
        self.assertEqual(store.strategy_rules[0].id, "R001")

    def test_rejects_weak_rule(self):
        store = DigestStore()
        rule = DigestRule(id="", rule="maybe do something",
                          source_reliability=0.3, type_prior=0.1)
        result = admit_rule(rule, store)
        self.assertEqual(result, "rejected")
        self.assertEqual(len(store.strategy_rules), 0)

    def test_upvotes_duplicate(self):
        store = DigestStore()
        rule1 = DigestRule(id="R001", rule="Do not add Co-Authored-By",
                           source_reliability=1.0, type_prior=0.8,
                           occurrence_count=1, status="pending")
        store.strategy_rules.append(rule1)

        rule2 = DigestRule(id="", rule="Do not add Co-Authored-By to commits",
                           evidence="don't add Co-Authored-By",
                           source_reliability=1.0, type_prior=0.8)
        result = admit_rule(rule2, store)
        self.assertEqual(result, "upvoted")
        self.assertEqual(store.strategy_rules[0].occurrence_count, 2)

    def test_promotes_after_threshold(self):
        """Pending rule gets promoted to active after PROMOTION_COUNT upvotes."""
        store = DigestStore()
        # Start as pending (implicit correction, not auto-promoted)
        rule = DigestRule(id="R001", rule="Use snake_case for variables",
                          source_reliability=0.6, type_prior=0.6,
                          occurrence_count=PROMOTION_COUNT - 1, status="pending")
        store.strategy_rules.append(rule)

        dup = DigestRule(id="", rule="Use snake_case for variable names",
                         evidence="use snake_case for variables",
                         source_reliability=0.6, type_prior=0.6)
        admit_rule(dup, store)
        self.assertEqual(store.strategy_rules[0].status, "active")

    def test_caps_active_rules(self):
        store = DigestStore()
        # Fill with MAX_ACTIVE_RULES active rules
        for i in range(MAX_ACTIVE_RULES):
            store.strategy_rules.append(DigestRule(
                id=f"R{i:03d}", rule=f"Rule number {i}",
                source_reliability=0.8, type_prior=0.8,
                occurrence_count=5, status="active",
            ))
        # Add one more
        new_rule = DigestRule(id="", rule="A brand new unique rule about something special",
                              source_reliability=1.0, type_prior=0.9)
        admit_rule(new_rule, store)
        active = store.active_rules()
        self.assertLessEqual(len(active), MAX_ACTIVE_RULES)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_dir = DIGEST_DIR
        self._orig_file = DIGEST_FILE

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        store = DigestStore(project="/test", session_id="sess-1")
        store.strategy_rules.append(DigestRule(
            id="R001", rule="Do not add Co-Authored-By",
            source_reliability=1.0, type_prior=0.8,
            occurrence_count=3, status="active",
            first_seen="2026-04-01", last_reinforced="2026-04-01",
        ))

        digest_file = self.tmpdir / "behavioral-digest.json"
        digest_md = self.tmpdir / "behavioral-digest.md"

        with patch("cozempic.digest.DIGEST_DIR", self.tmpdir), \
             patch("cozempic.digest.DIGEST_FILE", digest_file), \
             patch("cozempic.digest.DIGEST_MD_FILE", digest_md):
            save_digest_store(store)
            self.assertTrue(digest_file.exists())
            self.assertTrue(digest_md.exists())

            loaded = load_digest_store("/test")
            self.assertEqual(len(loaded.strategy_rules), 1)
            self.assertEqual(loaded.strategy_rules[0].id, "R001")
            self.assertEqual(loaded.strategy_rules[0].rule, "Do not add Co-Authored-By")
            self.assertEqual(loaded.strategy_rules[0].status, "active")

    def test_load_missing_file(self):
        with patch("cozempic.digest.DIGEST_FILE", self.tmpdir / "nonexistent.json"):
            store = load_digest_store("/test")
            self.assertTrue(store.is_empty())

    def test_clear(self):
        digest_file = self.tmpdir / "behavioral-digest.json"
        digest_md = self.tmpdir / "behavioral-digest.md"
        digest_file.write_text("{}")
        digest_md.write_text("# test")

        with patch("cozempic.digest.DIGEST_FILE", digest_file), \
             patch("cozempic.digest.DIGEST_MD_FILE", digest_md):
            clear_digest_store()
            self.assertFalse(digest_file.exists())
            self.assertFalse(digest_md.exists())


# ---------------------------------------------------------------------------
# update_digest (integration)
# ---------------------------------------------------------------------------

class TestUpdateDigest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_end_to_end(self):
        messages = [
            make_assistant(0, "I'll add the Co-Authored-By line"),
            make_user(1, "don't add Co-Authored-By to commits"),
            make_assistant(2, "ok, I won't"),
            make_user(3, "always use Edit for existing files"),
        ]

        digest_file = self.tmpdir / "behavioral-digest.json"
        digest_md = self.tmpdir / "behavioral-digest.md"

        with patch("cozempic.digest.DIGEST_DIR", self.tmpdir), \
             patch("cozempic.digest.DIGEST_FILE", digest_file), \
             patch("cozempic.digest.DIGEST_MD_FILE", digest_md):
            added, upvoted, rejected = update_digest(messages, project_dir="/test")
            self.assertGreater(added, 0)

            # Verify persisted
            data = json.loads(digest_file.read_text())
            self.assertGreater(len(data["strategy_rules"]), 0)


# ---------------------------------------------------------------------------
# Protection tag
# ---------------------------------------------------------------------------

class TestProtectionTag(unittest.TestCase):

    def test_digest_tagged_message_is_protected(self):
        msg = {"type": "user", PROTECTION_TAG: True, "message": {"role": "user", "content": "rules"}}
        self.assertTrue(is_protected(msg))

    def test_normal_message_not_protected(self):
        msg = {"type": "user", "message": {"role": "user", "content": "hello"}}
        self.assertFalse(is_protected(msg))


# ---------------------------------------------------------------------------
# DigestStore
# ---------------------------------------------------------------------------

class TestDigestStore(unittest.TestCase):

    def test_is_empty(self):
        self.assertTrue(DigestStore().is_empty())

    def test_not_empty(self):
        store = DigestStore()
        store.strategy_rules.append(DigestRule(id="R001", rule="test"))
        self.assertFalse(store.is_empty())

    def test_next_id_sequential(self):
        store = DigestStore()
        self.assertEqual(store.next_id(), "R001")
        store.strategy_rules.append(DigestRule(id="R001", rule="test"))
        self.assertEqual(store.next_id(), "R002")

    def test_active_rules(self):
        store = DigestStore()
        store.strategy_rules.append(DigestRule(id="R001", rule="active", status="active"))
        store.strategy_rules.append(DigestRule(id="R002", rule="pending", status="pending"))
        self.assertEqual(len(store.active_rules()), 1)


if __name__ == "__main__":
    unittest.main()
