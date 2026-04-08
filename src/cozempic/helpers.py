"""Shared helper functions for message inspection and manipulation."""

from __future__ import annotations

import copy
import json as _json
from pathlib import Path as _Path

_SAVINGS_FILE = _Path.home() / ".cozempic_savings.json"


def record_savings(tokens_saved: int, total_tokens: int = 0, turn_count: int = 0) -> None:
    """Add tokens saved to the lifetime tracker. Called after successful prune+reload.

    If total_tokens and turn_count are provided, estimates extra turns gained
    from the freed headroom.
    """
    if tokens_saved <= 0:
        return
    try:
        data = _json.loads(_SAVINGS_FILE.read_text()) if _SAVINGS_FILE.exists() else {}
    except Exception:
        data = {}
    data["tokens_saved"] = data.get("tokens_saved", 0) + tokens_saved
    data["tokens_processed"] = data.get("tokens_processed", 0) + total_tokens
    data["prune_count"] = data.get("prune_count", 0) + 1
    if "since" not in data:
        from datetime import date
        data["since"] = date.today().isoformat()

    # Estimate extra turns gained from freed headroom
    if turn_count > 0 and total_tokens > 0:
        avg_per_turn = total_tokens / turn_count
        if avg_per_turn > 0:
            extra_turns = int(tokens_saved / avg_per_turn)
            data["turns_gained"] = data.get("turns_gained", 0) + extra_turns

    try:
        _SAVINGS_FILE.write_text(_json.dumps(data))
    except Exception:
        pass

    # Ping global counters (anonymous, no user data, quick with short timeout)
    try:
        from urllib.request import Request, urlopen
        urlopen(Request("https://api.counterapi.dev/v1/cozempic/prunes/up",
                       headers={"User-Agent": "cozempic"}), timeout=2)
        if tokens_saved < 100_000:
            bucket = "saved-under-100k"
        elif tokens_saved < 500_000:
            bucket = "saved-100k-500k"
        elif tokens_saved < 1_000_000:
            bucket = "saved-500k-1m"
        else:
            bucket = "saved-over-1m"
        urlopen(Request(f"https://api.counterapi.dev/v1/cozempic/{bucket}/up",
                       headers={"User-Agent": "cozempic"}), timeout=2)
    except Exception:
        pass


def get_savings_line() -> str | None:
    """Return a single-line lifetime savings summary, or None if no savings recorded."""
    try:
        if not _SAVINGS_FILE.exists():
            return None
        data = _json.loads(_SAVINGS_FILE.read_text())
        total = data.get("tokens_saved", 0)
        processed = data.get("tokens_processed", 0)
        count = data.get("prune_count", 0)
        turns = data.get("turns_gained", 0)
        since = data.get("since", "")
        if total <= 0:
            return None
        if total >= 1_000_000:
            tok_str = f"{total / 1_000_000:.1f}M"
        elif total >= 1_000:
            tok_str = f"{total / 1_000:.0f}K"
        else:
            tok_str = str(total)

        # Session extension multiplier: processed / (processed - saved)
        remaining = processed - total
        multiplier = f"{processed / remaining:.1f}x" if remaining > 0 else ""

        parts = [f"Cozempic: {tok_str} tokens saved"]
        if multiplier:
            parts.append(f"{multiplier} longer sessions")
        if turns > 0:
            parts.append(f"~{turns} extra turns")
        return " | ".join(parts)
    except Exception:
        return None
import json


def msg_bytes(msg: dict) -> int:
    """Calculate the serialized byte size of a message."""
    return len(json.dumps(msg, separators=(",", ":")).encode("utf-8"))


def get_msg_type(msg: dict) -> str:
    """Get the type field from a message."""
    return msg.get("type", "unknown")


def get_content_blocks(msg: dict) -> list[dict]:
    """Extract content blocks from a message's inner message object."""
    m = msg.get("message", {})
    content = m.get("content", [])
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return []


def content_block_bytes(block: dict) -> int:
    """Calculate the serialized byte size of a content block."""
    return len(json.dumps(block, separators=(",", ":")).encode("utf-8"))


def set_content_blocks(msg: dict, blocks: list[dict]) -> dict:
    """Return a deep copy of msg with content blocks replaced."""
    msg = copy.deepcopy(msg)
    if "message" in msg:
        msg["message"]["content"] = blocks
    return msg


def shell_quote(s: str) -> str:
    """Single-quote a string for shell use."""
    return "'" + s.replace("'", "'\\''") + "'"


def is_ssh_session() -> bool:
    """Detect if we're running inside an SSH session."""
    import os
    return bool(
        os.environ.get("SSH_TTY")
        or os.environ.get("SSH_CONNECTION")
        or os.environ.get("SSH_CLIENT")
    )


_PROTECTED_TYPES = frozenset({
    "content-replacement",
    "marble-origami-commit",
    "marble-origami-snapshot",
    "worktree-state",
    "task-summary",
})


def is_protected(msg: dict) -> bool:
    """Return True if this entry must NEVER be removed or structurally modified."""
    t = msg.get("type", "")
    if t in _PROTECTED_TYPES:
        return True
    if t == "user" and msg.get("isCompactSummary"):
        return True
    if t == "system" and msg.get("subtype") in ("compact_boundary", "microcompact_boundary"):
        return True
    if msg.get("isVisibleInTranscriptOnly"):
        return True
    if msg.get("__cozempic_behavioral_digest__"):
        return True
    return False


def find_active_background_tasks(messages: list) -> list[dict]:
    """Find background tasks that were spawned but have no completion result.

    Returns list of {tool_use_id, description} for each active task.
    """
    import re
    spawns: dict[str, str] = {}  # tool_use_id -> description
    completions: set[str] = set()

    for _, msg, _ in messages:
        inner = msg.get("message", {})
        content = inner.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use" and block.get("name") == "Task":
                        inp = block.get("input", {})
                        if inp.get("run_in_background"):
                            spawns[block.get("id", "")] = inp.get("description", "")
                    if block.get("type") == "tool_result":
                        completions.add(block.get("tool_use_id", ""))

        # Check queue-operation for completed tasks
        if msg.get("type") == "queue-operation":
            body = str(msg.get("content", "") or msg.get("body", ""))
            if "<status>completed</status>" in body or "<status>failed</status>" in body:
                m = re.search(r"<tool-use-id>(.*?)</tool-use-id>", body)
                if m:
                    completions.add(m.group(1))

    return [
        {"tool_use_id": tid, "description": desc}
        for tid, desc in spawns.items()
        if tid not in completions
    ]


def text_of(block: dict) -> str:
    """Get the text content of a content block, handling all block types."""
    result = block.get("text", "") or block.get("thinking", "") or block.get("content", "")
    if isinstance(result, list):
        return " ".join(
            sub.get("text", "") for sub in result if isinstance(sub, dict)
        )
    if not isinstance(result, str):
        return ""
    return result
