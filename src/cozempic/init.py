"""cozempic init — auto-wire hooks and slash command into a Claude Code project.

After `pip install cozempic`, users still need to:
  1. Wire hooks into .claude/settings.json for checkpoint triggers
  2. Optionally install the /cozempic slash command

This module automates both so `cozempic init` is the only setup step.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


# ─── Command resolver ────────────────────────────────────────────────────────
def _c(args: str) -> str:
    """Build a cozempic command that works regardless of PATH."""
    return f'{{ cozempic {args} 2>/dev/null || python3 -m cozempic {args} 2>/dev/null; }}'


# ─── Hook definitions ────────────────────────────────────────────────────────

COZEMPIC_HOOKS = {
    "SessionStart": [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": (
                        "INPUT=$(cat); "
                        "SESSION_ID=$(echo \"$INPUT\" | python3 -c \"import sys,json; print(json.load(sys.stdin).get('session_id',''))\" 2>/dev/null); "
                        "TRANSCRIPT=$(echo \"$INPUT\" | python3 -c \"import sys,json; print(json.load(sys.stdin).get('transcript_path',''))\" 2>/dev/null); "
                        "{ uv pip install --upgrade cozempic --quiet 2>/dev/null || "
                        "pip install --upgrade cozempic --quiet --disable-pip-version-check 2>/dev/null || "
                        "pipx upgrade cozempic 2>/dev/null || "
                        "uv tool upgrade cozempic 2>/dev/null; } & "
                        + _c("guard --daemon ${TRANSCRIPT:+--session $TRANSCRIPT}") + " || true; "
                        + _c("digest inject ${TRANSCRIPT:+--session $TRANSCRIPT}") + " || true"
                    ),
                }
            ],
        },
    ],
    "PostToolUse": [
        {
            "matcher": "Task",
            "hooks": [
                {
                    "type": "command",
                    "command": "{ cozempic checkpoint 2>/dev/null || python3 -m cozempic checkpoint 2>/dev/null; } || true",
                }
            ],
        },
        {
            "matcher": "TaskCreate|TaskUpdate",
            "hooks": [
                {
                    "type": "command",
                    "command": "{ cozempic checkpoint 2>/dev/null || python3 -m cozempic checkpoint 2>/dev/null; } || true",
                }
            ],
        },
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": "{ cozempic remind 2>/dev/null || python3 -m cozempic remind 2>/dev/null; } || true",
                }
            ],
        },
    ],
    "PreCompact": [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": (
                        "TRANSCRIPT=$(cat | python3 -c \"import sys,json; print(json.load(sys.stdin).get('transcript_path',''))\" 2>/dev/null); "
                        "{ cozempic checkpoint 2>/dev/null || python3 -m cozempic checkpoint 2>/dev/null; } || true; "
                        "{ cozempic digest flush ${TRANSCRIPT:+--session $TRANSCRIPT} 2>/dev/null || python3 -m cozempic digest flush ${TRANSCRIPT:+--session $TRANSCRIPT} 2>/dev/null; } || true"
                    ),
                }
            ],
        },
    ],
    "PostCompact": [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": "{ cozempic post-compact 2>/dev/null || python3 -m cozempic post-compact 2>/dev/null; } || true; { cozempic digest inject 2>/dev/null || python3 -m cozempic digest inject 2>/dev/null; } || true",
                }
            ],
        },
    ],
    "Stop": [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": (
                        "TRANSCRIPT=$(cat | python3 -c \"import sys,json; print(json.load(sys.stdin).get('transcript_path',''))\" 2>/dev/null); "
                        "{ cozempic checkpoint 2>/dev/null || python3 -m cozempic checkpoint 2>/dev/null; } || true; "
                        "{ cozempic digest flush ${TRANSCRIPT:+--session $TRANSCRIPT} 2>/dev/null || python3 -m cozempic digest flush ${TRANSCRIPT:+--session $TRANSCRIPT} 2>/dev/null; } || true"
                    ),
                }
            ],
        },
    ],
}


# ─── Core logic ──────────────────────────────────────────────────────────────

def _is_cozempic_hook(hook_entry: dict) -> bool:
    """Check if a hook entry was installed by cozempic."""
    for h in hook_entry.get("hooks", []):
        cmd = h.get("command", "")
        if "cozempic" in cmd:
            return True
    return False


def _is_cozempic_command_hook(hook: dict) -> bool:
    """Return True when a single hook command belongs to Cozempic."""
    return "cozempic" in hook.get("command", "")


def _matcher_label(entry: dict) -> str:
    """Human-readable label for hook matcher output."""
    return entry.get("matcher") or "(all)"


def _merge_hook_entry(existing_entry: dict, new_entry: dict) -> dict:
    """Replace only Cozempic hooks, preserving any unrelated hooks."""
    merged = copy.deepcopy(existing_entry)
    merged["matcher"] = new_entry.get("matcher", "")
    merged["hooks"] = [
        copy.deepcopy(h)
        for h in existing_entry.get("hooks", [])
        if not _is_cozempic_command_hook(h)
    ] + copy.deepcopy(new_entry.get("hooks", []))
    return merged


def _upsert_hook_entry(existing_entries: list[dict], new_entry: dict) -> tuple[list[dict], str]:
    """Add or upgrade one Cozempic hook entry for a matcher.

    Returns (updated_entries, status) where status is one of:
      - "added"
      - "updated"
      - "skipped"
    """
    matcher = new_entry.get("matcher", "")
    matching_indexes = [
        i
        for i, entry in enumerate(existing_entries)
        if entry.get("matcher", "") == matcher and _is_cozempic_hook(entry)
    ]

    if not matching_indexes:
        return existing_entries + [copy.deepcopy(new_entry)], "added"

    first_index = matching_indexes[0]
    merged_entry = _merge_hook_entry(existing_entries[first_index], new_entry)
    updated_entries = []

    for i, entry in enumerate(existing_entries):
        if i == first_index:
            updated_entries.append(merged_entry)
            continue

        if i in matching_indexes[1:]:
            cleaned_entry = copy.deepcopy(entry)
            cleaned_entry["hooks"] = [
                copy.deepcopy(h)
                for h in entry.get("hooks", [])
                if not _is_cozempic_command_hook(h)
            ]
            if cleaned_entry["hooks"]:
                updated_entries.append(cleaned_entry)
            continue

        updated_entries.append(entry)

    if merged_entry != existing_entries[first_index] or len(matching_indexes) > 1:
        return updated_entries, "updated"
    return updated_entries, "skipped"


def _settings_path(project_dir: str) -> Path:
    """Return the .claude/settings.json path for a project."""
    return Path(project_dir) / ".claude" / "settings.json"


def _load_settings(path: Path) -> dict:
    """Load settings.json, returning empty dict if missing."""
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _backup_settings(path: Path) -> Path | None:
    """Create timestamped backup of settings.json."""
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f".{ts}.bak")
    shutil.copy2(path, backup)
    return backup


def _save_settings(path: Path, settings: dict) -> None:
    """Write settings.json with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


def wire_hooks(project_dir: str) -> dict:
    """Add cozempic checkpoint hooks to .claude/settings.json.

    Idempotent — skips hooks that already match and upgrades stale Cozempic hooks.

    Returns dict with: added, updated, skipped, settings_path, backup_path.
    """
    path = _settings_path(project_dir)
    settings = _load_settings(path)

    hooks = settings.setdefault("hooks", {})

    added = []
    updated = []
    skipped = []

    for event_name, hook_entries in COZEMPIC_HOOKS.items():
        existing = hooks.get(event_name, [])

        for new_entry in hook_entries:
            existing, status = _upsert_hook_entry(existing, new_entry)
            matcher = _matcher_label(new_entry)

            if status == "added":
                added.append(f"{event_name}[{matcher}]")
            elif status == "updated":
                updated.append(f"{event_name}[{matcher}]")
            else:
                skipped.append(f"{event_name}[{matcher}]")

        hooks[event_name] = existing

    backup = None
    if added or updated:
        backup = _backup_settings(path)
        _save_settings(path, settings)

    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "settings_path": str(path),
        "backup_path": str(backup) if backup else None,
    }


def install_slash_command(project_dir: str) -> dict:
    """Copy the /cozempic slash command to ~/.claude/commands/.

    Always overwrites to keep the slash command up-to-date with the
    installed cozempic version.

    Returns dict with: installed (bool), path, already_existed (bool), updated (bool).
    """
    # Find the slash command source — bundled as package data
    source = Path(__file__).parent / "data" / "cozempic_slash_command.md"

    # Fallback: dev/editable install — check repo root
    if not source.exists():
        source = Path(__file__).parent.parent.parent / ".claude" / "commands" / "cozempic.md"

    from .session import get_claude_dir
    target_dir = get_claude_dir() / "commands"
    target = target_dir / "cozempic.md"

    if not source.exists():
        return {"installed": False, "path": None, "already_existed": False, "updated": False}

    already_existed = target.exists()

    # Check if content differs
    if already_existed:
        if source.read_text(encoding="utf-8") == target.read_text(encoding="utf-8"):
            return {"installed": False, "path": str(target), "already_existed": True, "updated": False}

    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

    return {"installed": True, "path": str(target), "already_existed": already_existed, "updated": already_existed}


def run_init(project_dir: str, skip_slash: bool = False) -> dict:
    """Full init: wire hooks + install slash command.

    Returns combined result dict.
    """
    hook_result = wire_hooks(project_dir)
    slash_result = {"installed": False, "path": None, "already_existed": False}

    if not skip_slash:
        slash_result = install_slash_command(project_dir)

    return {
        "hooks": hook_result,
        "slash_command": slash_result,
    }
