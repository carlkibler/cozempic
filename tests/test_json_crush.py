"""Tests for the json-crush (SmartCrusher) strategy."""

from __future__ import annotations

import json
import unittest

from cozempic.helpers import msg_bytes
from cozempic.registry import PRESCRIPTIONS, STRATEGIES

import cozempic.strategies  # noqa: F401  (registers strategies)


def make_tool_result(line_idx: int, content, tool_id: str = "tool-1") -> tuple[int, dict, int]:
    msg = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}],
        },
        "uuid": f"uuid-{line_idx}",
    }
    return (line_idx, msg, msg_bytes(msg))


def result_content(replacement: dict):
    return replacement["message"]["content"][0]["content"]


class TestJsonCrush(unittest.TestCase):
    def _run(self, messages, config=None):
        return STRATEGIES["json-crush"].func(messages, config or {})

    def test_registered_in_standard_and_aggressive_before_tool_output_trim(self):
        self.assertIn("json-crush", STRATEGIES)
        for tier in ("standard", "aggressive"):
            rx = PRESCRIPTIONS[tier]
            self.assertIn("json-crush", rx)
            self.assertLess(rx.index("json-crush"), rx.index("tool-output-trim"),
                            f"json-crush must run before tool-output-trim in {tier}")

    def test_compacts_pretty_printed_json_and_preserves_keys(self):
        obj = {"status": "ok", "data": {"id": 42, "name": "widget", "tags": ["a", "b"]}, "pad": "x" * 9000}
        pretty = json.dumps(obj, indent=4)
        messages = [make_tool_result(0, pretty)]
        sr = self._run(messages)

        self.assertEqual(len(sr.actions), 1)
        crushed = result_content(sr.actions[0].replacement)
        self.assertLess(len(crushed.encode("utf-8")), len(pretty.encode("utf-8")))
        parsed = json.loads(crushed)  # still valid JSON
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["id"], 42)

    def test_truncates_long_arrays_with_count_sentinel(self):
        obj = {"items": [{"i": n} for n in range(1000)]}
        messages = [make_tool_result(0, json.dumps(obj))]
        sr = self._run(messages, {"json_crush_keep_items": 3})

        self.assertEqual(len(sr.actions), 1)
        parsed = json.loads(result_content(sr.actions[0].replacement))
        items = parsed["items"]
        self.assertEqual(len(items), 4)  # 3 kept + 1 sentinel
        self.assertEqual(items[0], {"i": 0})
        self.assertIn("997 more items", items[-1])

    def test_truncates_long_string_values(self):
        obj = {"log": "y" * 10000, "ok": True}
        messages = [make_tool_result(0, json.dumps(obj))]
        sr = self._run(messages, {"json_crush_max_str": 200})

        parsed = json.loads(result_content(sr.actions[0].replacement))
        self.assertTrue(parsed["ok"])
        self.assertLess(len(parsed["log"]), 5000)
        self.assertIn("chars crushed", parsed["log"])

    def test_ignores_non_json_content(self):
        messages = [make_tool_result(0, "this is plain log output\n" * 1000)]
        sr = self._run(messages)
        self.assertEqual(sr.actions, [])  # left for tool-output-trim

    def test_ignores_small_json(self):
        messages = [make_tool_result(0, json.dumps({"ok": True, "n": 3}))]
        sr = self._run(messages)
        self.assertEqual(sr.actions, [])

    def test_no_action_when_already_compact_and_short(self):
        # Large but already-compact array of scalars under item limit
        obj = {"items": list(range(2))}
        messages = [make_tool_result(0, json.dumps(obj, separators=(",", ":")))]
        sr = self._run(messages)
        self.assertEqual(sr.actions, [])

    def test_skips_protected_messages(self):
        idx, msg, size = make_tool_result(0, json.dumps({"pad": "x" * 9000}))
        msg["__cozempic_team_protected__"] = True
        sr = self._run([(idx, msg, size)])
        self.assertEqual(sr.actions, [])

    def test_crushes_json_inside_list_text_block(self):
        big = json.dumps({"rows": [{"v": n} for n in range(1000)]})
        content = [{"type": "text", "text": big}]
        messages = [make_tool_result(0, content)]
        sr = self._run(messages, {"json_crush_keep_items": 2})

        self.assertEqual(len(sr.actions), 1)
        new_blocks = sr.actions[0].replacement["message"]["content"]
        new_text = new_blocks[0]["content"][0]["text"]
        parsed = json.loads(new_text)
        self.assertEqual(len(parsed["rows"]), 3)  # 2 + sentinel
