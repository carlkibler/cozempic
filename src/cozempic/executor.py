"""Action executor and prescription runner."""

from __future__ import annotations

from .helpers import get_content_blocks, msg_bytes, set_content_blocks
from .registry import STRATEGIES
from .types import Message, PruneAction, StrategyResult


def execute_actions(
    messages: list[Message],
    actions: list[PruneAction],
) -> list[Message]:
    """Apply PruneActions to messages and return the new message list."""
    removals: set[int] = set()
    replacements: dict[int, dict] = {}

    for action in actions:
        if action.action == "remove":
            removals.add(action.line_index)
        elif action.action == "replace" and action.replacement:
            replacements[action.line_index] = action.replacement

    result: list[Message] = []
    for idx, msg, size in messages:
        if idx in removals:
            continue
        if idx in replacements:
            new_msg = replacements[idx]
            new_size = msg_bytes(new_msg)
            result.append((idx, new_msg, new_size))
        else:
            result.append((idx, msg, size))

    return result


def fix_orphaned_tool_results(messages: list[Message]) -> tuple[list[Message], int]:
    """Remove or fix tool_result blocks whose matching tool_use was removed.

    The Claude API requires every tool_result to have a corresponding tool_use
    in the preceding message. When strategies remove messages containing
    tool_use blocks, the paired tool_result becomes orphaned and causes
    400 errors on compact/resume.

    Returns (fixed_messages, orphans_fixed).
    """
    # Pass 1: collect all tool_use IDs present in the messages
    tool_use_ids: set[str] = set()
    for _, msg, _ in messages:
        for block in get_content_blocks(msg):
            if block.get("type") == "tool_use":
                use_id = block.get("id", "")
                if use_id:
                    tool_use_ids.add(use_id)

    # Pass 2: find and remove orphaned tool_result blocks
    orphans_fixed = 0
    result: list[Message] = []

    for idx, msg, size in messages:
        blocks = get_content_blocks(msg)
        if not blocks:
            result.append((idx, msg, size))
            continue

        has_orphan = False
        for block in blocks:
            if block.get("type") == "tool_result":
                use_id = block.get("tool_use_id", "")
                if use_id and use_id not in tool_use_ids:
                    has_orphan = True
                    break

        if not has_orphan:
            result.append((idx, msg, size))
            continue

        # Filter out orphaned tool_result blocks, keep everything else
        new_blocks = []
        for block in blocks:
            if block.get("type") == "tool_result":
                use_id = block.get("tool_use_id", "")
                if use_id and use_id not in tool_use_ids:
                    orphans_fixed += 1
                    continue
            new_blocks.append(block)

        if new_blocks:
            new_msg = set_content_blocks(msg, new_blocks)
            result.append((idx, new_msg, msg_bytes(new_msg)))
        else:
            # All blocks were orphaned — drop the entire message
            orphans_fixed += 1

    return result, orphans_fixed


def run_prescription(
    messages: list[Message],
    strategy_names: list[str],
    config: dict,
) -> tuple[list[Message], list[StrategyResult]]:
    """Run strategies sequentially, each on the result of the previous.

    This ensures replacements compose correctly when multiple strategies
    modify the same message. After all strategies run, a validation pass
    removes any orphaned tool_result blocks to prevent API 400 errors.
    """
    current = messages
    results: list[StrategyResult] = []
    for sname in strategy_names:
        if sname not in STRATEGIES:
            continue
        sr = STRATEGIES[sname].func(current, config)
        results.append(sr)
        if sr.actions:
            current = execute_actions(current, sr.actions)

    # Post-treatment validation: fix orphaned tool_results
    current, orphans = fix_orphaned_tool_results(current)
    if orphans > 0:
        results.append(StrategyResult(
            strategy_name="orphan-fix",
            actions=[],
            original_bytes=0,
            pruned_bytes=0,
            messages_affected=orphans,
            messages_removed=0,
            messages_replaced=orphans,
            summary=f"Fixed {orphans} orphaned tool_result block(s)",
        ))

    return current, results
