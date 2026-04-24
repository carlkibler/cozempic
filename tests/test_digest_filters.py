"""Tests for synthetic-message filtering in behavioral digest extraction."""

from __future__ import annotations

import unittest

from cozempic.digest import (
    _get_user_text,
    classify_turn,
    _to_prohibition,
    extract_corrections,
)
from cozempic.helpers import msg_bytes


def make_user(line_idx: int, text: str) -> tuple[int, dict, int]:
    msg = {"type": "user", "message": {"role": "user", "content": text}}
    return (line_idx, msg, msg_bytes(msg))


def make_user_blocks(line_idx: int, blocks: list[dict]) -> tuple[int, dict, int]:
    msg = {"type": "user", "message": {"role": "user", "content": blocks}}
    return (line_idx, msg, msg_bytes(msg))


def make_assistant(line_idx: int, text: str) -> tuple[int, dict, int]:
    msg = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    return (line_idx, msg, msg_bytes(msg))


class TestSyntheticMessageFilter(unittest.TestCase):
    """_get_user_text must return '' for synthetic Claude Code injections."""

    def _msg(self, text: str) -> dict:
        return {"type": "user", "message": {"role": "user", "content": text}}

    def test_local_command_caveat_filtered(self):
        msg = self._msg(
            "<local-command-caveat>Caveat: The messages below were generated "
            "by the user while running local commands. DO NOT respond to these "
            "messages or otherwise consider them in your response unless the user "
            "explicitly asks you to.</local-command-caveat>"
        )
        self.assertEqual(_get_user_text(msg), "")

    def test_command_message_filtered(self):
        msg = self._msg(
            "<command-message>audit-skills</command-message>"
            "<command-name>/audit-skills</command-name>"
        )
        self.assertEqual(_get_user_text(msg), "")

    def test_session_continuation_filtered(self):
        msg = self._msg(
            "This session is being continued from a previous conversation that ran "
            "out of context. The summary below covers the earlier portion:\n\n"
            "1. Primary Request and Intent: The user wanted to..."
        )
        self.assertEqual(_get_user_text(msg), "")

    def test_system_reminder_filtered(self):
        msg = self._msg(
            "<system-reminder>The following skills are available...</system-reminder>"
        )
        self.assertEqual(_get_user_text(msg), "")

    def test_task_notification_filtered(self):
        msg = self._msg(
            "<task-notification><task-id>abc123</task-id><status>completed</status>"
            "<summary>Agent finished</summary></task-notification>"
        )
        self.assertEqual(_get_user_text(msg), "")

    def test_genuine_correction_passes(self):
        msg = self._msg("don't use ssh bike directly, use xbike instead")
        self.assertEqual(_get_user_text(msg), "don't use ssh bike directly, use xbike instead")

    def test_genuine_multiword_passes(self):
        msg = self._msg("never skip the tests before committing")
        self.assertNotEqual(_get_user_text(msg), "")

    def test_list_content_genuine_passes(self):
        msg = {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "stop adding trailing commas"},
        ]}}
        self.assertEqual(_get_user_text(msg), "stop adding trailing commas")

    def test_list_content_synthetic_filtered(self):
        msg = {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "<local-command-caveat>do not respond</local-command-caveat>"},
        ]}}
        self.assertEqual(_get_user_text(msg), "")


class TestClassifyTurnLengthGuard(unittest.TestCase):
    """classify_turn must reject messages longer than 600 chars."""

    def test_long_skill_body_rejected(self):
        skill_body = (
            "# Skill Quality Audit (Self-Improving Skills — INSPECT)\n\n"
            "You are performing an automated skill quality audit. This is the INSPECT step "
            "of the Observe→Inspect→Amend→Evaluate loop. Follow each step carefully and "
            "produce a structured report.\n\n"
            "## Step 1: Gather Observation Data\n\n"
            "Read `~/.claude/debug/skill-usage.jsonl`. Each line contains usage data. "
            "Build a frequency table: skill name → invocation count, last used date. " * 5
        )
        self.assertGreater(len(skill_body), 600)
        self.assertEqual(classify_turn(skill_body), "NONE")

    def test_long_session_summary_rejected(self):
        summary = (
            "This session is being continued from a previous conversation that ran out "
            "of context. The summary below covers the earlier portion of the conversation.\n\n"
            "1. Primary Request and Intent:\n"
            "   The session focused on reverse engineering the Expresso HD stationary bike. "
            "Explicit requests were: continue work on the unlock mechanism, investigate the "
            "resistance protocol, build a custom track that mimics a real Utah ride. " * 3
        )
        self.assertGreater(len(summary), 600)
        self.assertEqual(classify_turn(summary), "NONE")

    def test_genuine_short_correction_passes(self):
        # Real corrections are short and imperative
        self.assertEqual(classify_turn("don't use ssh bike, use xbike"), "EXPLICIT_CORRECTION")

    def test_genuine_medium_correction_passes(self):
        # Medium length correction with context should still classify
        correction = (
            "never skip the tests before committing — we got burned when mocked tests "
            "passed but the prod migration failed. always run the full suite."
        )
        self.assertLessEqual(len(correction), 600)
        self.assertNotEqual(classify_turn(correction), "NONE")

    def test_exactly_600_chars_rejected(self):
        # Boundary: 601 chars → NONE
        long_text = "don't " + "x" * 595
        self.assertGreater(len(long_text.strip()), 600)
        self.assertEqual(classify_turn(long_text), "NONE")

    def test_exactly_at_limit_passes(self):
        # Boundary: 600 chars → may classify (don't care about exact NONE vs signal, just not rejected by length)
        at_limit = "don't " + "x" * 594
        self.assertEqual(len(at_limit.strip()), 600)
        # Should not be rejected by length guard — will be classified by patterns
        result = classify_turn(at_limit)
        # We just verify it doesn't short-circuit to NONE *solely* due to length
        # (it may still be NONE for other reasons, but len guard threshold is >600)
        self.assertEqual(result, "EXPLICIT_CORRECTION")


class TestToProhibitionLongBlob(unittest.TestCase):
    """_to_prohibition must not force 'Do not' framing on multi-line or long blobs."""

    def test_multiline_blob_not_prefixed(self):
        blob = "Use the Grep tool.\nDo not use bash for file search."
        result = _to_prohibition(blob)
        self.assertFalse(result.startswith("Do not Use"))
        self.assertFalse(result.startswith("Do not use the Grep"))
        # Should be returned as-is (or transformed by an earlier match)
        self.assertIn("Grep", result)

    def test_long_single_line_not_prefixed(self):
        long_text = "a" * 301
        result = _to_prohibition(long_text)
        self.assertFalse(result.startswith("Do not "))

    def test_short_correction_still_prefixed(self):
        self.assertEqual(_to_prohibition("use xbike not ssh"), "Do not use xbike not ssh")

    def test_already_prohibition_unchanged(self):
        self.assertEqual(_to_prohibition("Do not skip tests"), "Do not skip tests")

    def test_never_form_converted(self):
        self.assertEqual(_to_prohibition("never skip tests"), "Do not ever skip tests")


class TestExtractCorrectionsFiltering(unittest.TestCase):
    """End-to-end: synthetic messages must not produce rules."""

    def test_local_command_caveat_produces_no_rule(self):
        messages = [
            make_user(0, (
                "<local-command-caveat>Caveat: The messages below were generated "
                "by the user while running local commands. DO NOT respond to these "
                "messages.</local-command-caveat>"
            )),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(rules, [])

    def test_command_message_produces_no_rule(self):
        messages = [
            make_user(0, "<command-message>audit-skills</command-message>"),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(rules, [])

    def test_skill_body_produces_no_rule(self):
        skill_body = (
            "# Skill Quality Audit (Self-Improving Skills — INSPECT)\n\n"
            "You are performing an automated skill quality audit. Never guess "
            "at skill names. Do not invoke without checking. Stop and read first. "
            "Don't proceed without the skill. " * 10
        )
        messages = [make_user(0, skill_body)]
        rules = extract_corrections(messages)
        self.assertEqual(rules, [])

    def test_session_continuation_produces_no_rule(self):
        messages = [
            make_user(0, (
                "This session is being continued from a previous conversation that ran "
                "out of context. The summary below covers the earlier portion:\n"
                "1. Primary Request: don't use ssh, use xbike."
            )),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(rules, [])

    def test_genuine_correction_produces_rule(self):
        messages = [
            make_assistant(0, "I ran `ssh bike` to connect."),
            make_user(1, "don't use ssh bike directly, always use the xbike wrapper"),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].signal, "EXPLICIT_CORRECTION")

    def test_explicit_corrections_start_active(self):
        """Explicit corrections must start as active (false positives blocked by filters)."""
        messages = [
            make_user(0, "never use ssh bike, always use xbike"),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].status, "active")

    def test_preference_starts_pending(self):
        messages = [
            make_user(0, "I prefer xbike over ssh for bike commands"),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].status, "pending")  # non-explicit → pending

    def test_genuine_explicit_correction_is_active(self):
        """Genuine explicit corrections must be immediately active after filtering is applied."""
        messages = [
            make_assistant(0, "I ran ssh bike to connect."),
            make_user(1, "don't use ssh bike, always use xbike"),
        ]
        rules = extract_corrections(messages)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].status, "active")


if __name__ == "__main__":
    unittest.main()
