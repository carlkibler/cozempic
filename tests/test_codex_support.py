from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import cozempic.strategies  # noqa: F401
from cozempic.executor import run_prescription
from cozempic.session import find_current_session, load_messages
from cozempic.tokens import detect_context_window, detect_model, extract_usage_tokens, quick_token_estimate


def _write_codex_session(path: Path, cwd: str, session_id: str = "codex-session-1") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "timestamp": "2026-04-15T20:00:00Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": cwd},
        },
        {
            "timestamp": "2026-04-15T20:00:01Z",
            "type": "event_msg",
            "payload": {"type": "task_started", "model_context_window": 258400},
        },
        {
            "timestamp": "2026-04-15T20:00:02Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5.4", "cwd": cwd},
        },
        {
            "timestamp": "2026-04-15T20:00:03Z",
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": [], "content": None, "encrypted_content": "x" * 4000},
        },
        {
            "timestamp": "2026-04-15T20:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "cat big.log"}),
                "call_id": "call_123",
            },
        },
        {
            "timestamp": "2026-04-15T20:00:05Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_123",
                "output": "line\n" * 500,
            },
        },
        {
            "timestamp": "2026-04-15T20:00:06Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
            },
        },
        {
            "timestamp": "2026-04-15T20:00:07Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 12000,
                        "cached_input_tokens": 3000,
                        "output_tokens": 400,
                        "reasoning_output_tokens": 50,
                        "total_tokens": 15450,
                    },
                    "model_context_window": 258400,
                },
                "rate_limits": {"primary": {"used_percent": 6.0}},
            },
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_find_current_session_prefers_codex_for_matching_cwd(tmp_path):
    codex_root = tmp_path / "codex" / "sessions" / "2026" / "04" / "15"
    session_path = _write_codex_session(
        codex_root / "rollout-test.jsonl",
        cwd="/Users/carl/dev/out/cozempic",
        session_id="019d-test-codex",
    )
    projects_dir = tmp_path / "claude-projects"
    projects_dir.mkdir()

    with (
        patch("cozempic.session.get_projects_dir", return_value=projects_dir),
        patch("cozempic.session.get_codex_sessions_dir", return_value=tmp_path / "codex" / "sessions"),
        patch("cozempic.session._session_id_from_process", return_value=None),
    ):
        sess = find_current_session(cwd="/Users/carl/dev/out/cozempic", strict=True)

    assert sess is not None
    assert sess["backend"] == "codex"
    assert sess["path"] == session_path
    assert sess["session_id"] == "019d-test-codex"


def test_codex_token_and_model_detection(tmp_path):
    path = _write_codex_session(tmp_path / "codex.jsonl", cwd="/tmp/project")
    messages = load_messages(path)

    assert detect_model(messages) == "gpt-5.4"
    assert detect_context_window(messages) == 258400
    usage = extract_usage_tokens(messages)
    assert usage is not None
    assert usage["total"] == 15450
    assert quick_token_estimate(path) == 15504


def test_codex_standard_pruning_removes_reasoning_and_trims_tool_output(tmp_path):
    path = _write_codex_session(tmp_path / "codex.jsonl", cwd="/tmp/project")
    messages = load_messages(path)
    new_messages, strategy_results = run_prescription(messages, ["thinking-blocks", "tool-output-trim"], {})

    assert sum(size for _, _, size in new_messages) < sum(size for _, _, size in messages)
    assert any(result.strategy_name == "thinking-blocks" and result.actions for result in strategy_results)
    assert any(result.strategy_name == "tool-output-trim" and result.actions for result in strategy_results)

    remaining_types = [
        msg.get("payload", {}).get("type")
        for _, msg, _ in new_messages
        if msg.get("type") == "response_item"
    ]
    assert "reasoning" not in remaining_types

    output_items = [
        msg for _, msg, _ in new_messages
        if msg.get("type") == "response_item" and msg.get("payload", {}).get("type") == "function_call_output"
    ]
    assert output_items
    assert "trimmed by cozempic" in output_items[0]["payload"]["output"]
