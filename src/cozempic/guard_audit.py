"""Local guard audit ledger and report helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .helpers import _HostFileLock


def audit_path() -> Path:
    raw = os.environ.get("COZEMPIC_GUARD_AUDIT_FILE")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cozempic_guard_audit.jsonl"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def record_guard_event(event: str, **fields: Any) -> None:
    if os.environ.get("COZEMPIC_NO_GUARD_AUDIT"):
        return
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("COZEMPIC_GUARD_AUDIT_FILE"):
        return
    try:
        from . import __version__

        path = audit_path()
        item = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "pid": os.getpid(),
            "version": __version__,
        }
        item.update({k: _jsonable(v) for k, v in fields.items()})
        line = json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _HostFileLock(path):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass


def parse_since(value: str | None = None, days: int | None = None) -> datetime | None:
    if value:
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if days is not None and days > 0:
        return datetime.now(timezone.utc) - timedelta(days=days)
    return None


def load_guard_events(path: Path | None = None, since: datetime | None = None) -> list[dict[str, Any]]:
    path = path or audit_path()
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since is not None:
                    raw_ts = str(item.get("ts", "")).replace("Z", "+00:00")
                    try:
                        ts = datetime.fromisoformat(raw_ts)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        ts = ts.astimezone(timezone.utc)
                    except ValueError:
                        continue
                    if ts < since:
                        continue
                events.append(item)
    except OSError:
        return []
    return events


def summarize_guard_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "events": len(events),
        "guard_starts": 0,
        "soft_checkpoints": 0,
        "hard_checks": 0,
        "real_prunes": 0,
        "reloads": 0,
        "read_only_skips": 0,
        "orphaned_guard_events": 0,
        "reload_unsafe": 0,
        "deferred_conflicts": 0,
        "futile_reloads_skipped": 0,
        "tokens_saved": 0,
        "mb_saved": 0.0,
        "exits": {},
        "sessions": set(),
        "cwds": set(),
        "first_ts": None,
        "last_ts": None,
    }
    for item in events:
        ts = item.get("ts")
        if ts:
            if summary["first_ts"] is None or ts < summary["first_ts"]:
                summary["first_ts"] = ts
            if summary["last_ts"] is None or ts > summary["last_ts"]:
                summary["last_ts"] = ts
        session_id = item.get("session_id")
        if session_id:
            summary["sessions"].add(session_id)
        cwd = item.get("cwd")
        if cwd:
            summary["cwds"].add(cwd)

        event = item.get("event")
        if event == "guard_start":
            summary["guard_starts"] += 1
        elif event == "guard_soft_checkpoint":
            summary["soft_checkpoints"] += 1
        elif event == "guard_prune_result":
            tier = item.get("tier")
            if tier in {"hard1", "hard2"}:
                summary["hard_checks"] += 1
            if item.get("reloading"):
                summary["reloads"] += 1
            if item.get("live_write_skipped"):
                summary["read_only_skips"] += 1
            if item.get("orphaned_guard"):
                summary["orphaned_guard_events"] += 1
            if item.get("reload_unsafe"):
                summary["reload_unsafe"] += 1
            if item.get("prune_deferred_conflict"):
                summary["deferred_conflicts"] += 1
            if item.get("futile_reload_skipped"):
                summary["futile_reloads_skipped"] += 1

            tokens_saved = int(item.get("tokens_saved") or 0)
            saved_mb = float(item.get("saved_mb") or 0.0)
            persisted = not (
                item.get("live_write_skipped")
                or item.get("reload_unsafe")
                or item.get("futile_reload_skipped")
                or item.get("orphaned_guard")
            )
            if persisted and (tokens_saved > 0 or saved_mb > 0):
                summary["real_prunes"] += 1
                summary["tokens_saved"] += max(0, tokens_saved)
                summary["mb_saved"] += max(0.0, saved_mb)
        elif event == "guard_exit":
            reason = str(item.get("reason") or "unknown")
            exits = summary["exits"]
            exits[reason] = exits.get(reason, 0) + 1

    summary["sessions"] = sorted(summary["sessions"])
    summary["cwds"] = sorted(summary["cwds"])
    summary["mb_saved"] = round(summary["mb_saved"], 3)
    return summary


def verdict(summary: dict[str, Any], stuck_loop_count: int = 0) -> tuple[str, str]:
    if stuck_loop_count:
        return "kill", f"{stuck_loop_count} stuck guard loop(s) detected"
    if summary["real_prunes"] or summary["reloads"]:
        return "keep", (
            f"{summary['real_prunes']} real prune(s), {summary['reloads']} reload(s), "
            f"{summary['tokens_saved']:,} tokens saved"
        )
    if summary["read_only_skips"] or summary["orphaned_guard_events"]:
        return "probation-negative", (
            f"{summary['read_only_skips']} read-only skip(s), "
            f"{summary['orphaned_guard_events']} orphaned/no-PID guard event(s), no persisted prunes"
        )
    if summary["guard_starts"]:
        return "no-evidence-yet", "guard started, but no threshold work happened"
    return "no-data", "no guard audit events found"
