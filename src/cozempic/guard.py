"""Guard daemon — continuous team checkpointing + emergency prune.

Architecture:
  EVERY interval:  Extract team state → write checkpoint (lightweight, no prune)
  AT threshold:    Prune non-team messages → inject recovery → optionally reload

The checkpoint runs continuously so team state is ALWAYS on disk, regardless
of whether the threshold is ever hit. The threshold prune is the emergency
fallback — not the primary protection mechanism.

Checkpoint triggers:
  1. Every N seconds (guard daemon)
  2. On demand via `cozempic checkpoint` (hook-driven)
  3. At file size threshold (emergency prune)
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

from .executor import run_prescription
from .helpers import is_ssh_session, shell_quote
from .registry import PRESCRIPTIONS
import cozempic.strategies  # noqa: F401 — register strategies so guard_prune_cycle can actually prune (#15)
from .session import (
    PruneConflictError,
    PruneLockError,
    _PruneLock,
    cleanup_old_backups,
    find_claude_pid,
    find_current_session,
    find_sessions,
    load_messages,
    save_messages,
    snapshot_session,
)
from .team import TeamState, extract_team_state, inject_team_recovery, write_team_checkpoint
from .tokens import default_token_thresholds, quick_token_estimate


def _resolve_session_by_id(session_id: str, max_retries: int = 5, retry_delay: float = 1.0) -> dict | None:
    """Find a session by explicit ID, UUID prefix, or path.

    Retries up to max_retries times to handle the race condition where the
    SessionStart hook fires before Claude Code creates the JSONL file (#21).
    """
    p = Path(session_id)
    if p.exists() and p.suffix == ".jsonl":
        return {
            "path": p,
            "session_id": p.stem,
            "size": p.stat().st_size,
            "project": p.parent.name,
        }

    for attempt in range(max_retries):
        for sess in find_sessions():
            if sess["session_id"] == session_id or sess["session_id"].startswith(session_id):
                return sess
        if attempt < max_retries - 1:
            time.sleep(retry_delay)
    return None


# ─── Lightweight checkpoint (no prune) ───────────────────────────────────────

def checkpoint_team(
    cwd: str | None = None,
    session_path: Path | None = None,
    quiet: bool = False,
) -> TeamState | None:
    """Extract and save team state from the current session. No pruning.

    This is fast and safe — it only reads the JSONL and writes a checkpoint.
    Designed to be called from hooks, guard daemon, or CLI.

    Returns the extracted TeamState, or None if no session found.
    """
    if session_path is None:
        sess = find_current_session(cwd)
        if not sess:
            if not quiet:
                print("  No active session found.", file=sys.stderr)
            return None
        session_path = sess["path"]

    messages = load_messages(session_path)
    state = extract_team_state(messages)

    if state.is_empty():
        if not quiet:
            print("  No team state detected.")
        return state

    project_dir = session_path.parent
    cp_path = write_team_checkpoint(state, project_dir)

    if not quiet:
        agents = len(state.subagents)
        teammates = len(state.teammates)
        tasks = len(state.tasks)
        parts = []
        if agents:
            parts.append(f"{agents} subagents")
        if teammates:
            parts.append(f"{teammates} teammates")
        if tasks:
            parts.append(f"{tasks} tasks")
        summary = ", ".join(parts) if parts else "empty"
        print(f"  Checkpoint: {summary} → {cp_path.name}")

    return state


# ─── Team-aware pruning ──────────────────────────────────────────────────────

def prune_with_team_protect(
    messages: list,
    rx_name: str = "standard",
    config: dict | None = None,
) -> tuple[list, list, TeamState]:
    """Run a prescription but protect team-related messages from pruning.

    Returns (pruned_messages, strategy_results, team_state).

    Strategy:
    1. Extract team state first
    2. Mark team message indices
    3. Run prescription on non-team messages
    4. Re-insert team messages at their original positions
    5. Inject team recovery messages at the end
    """
    from .team import _is_team_message

    config = config or {}
    strategy_names = PRESCRIPTIONS.get(rx_name, PRESCRIPTIONS["standard"])

    # 1. Extract team state
    team_state = extract_team_state(messages)

    if team_state.is_empty():
        # No team — standard pruning
        new_messages, results = run_prescription(messages, strategy_names, config)
        return new_messages, results, team_state

    # 2. Build pending_task_ids — tool_use IDs for ALL team-related calls.
    # Covers Task results (agent output) AND TaskOutput, SendMessage, etc.
    from .team import TEAM_TOOL_NAMES
    pending_task_ids: set[str] = set()
    for _, msg_dict, _ in messages:
        inner = msg_dict.get("message", {})
        for block in (inner.get("content", []) if isinstance(inner.get("content"), list) else []):
            if block.get("type") == "tool_use" and block.get("name") in TEAM_TOOL_NAMES:
                tool_use_id = block.get("id", "")
                if tool_use_id:
                    pending_task_ids.add(tool_use_id)

    # 3. Separate team and non-team messages
    team_messages = []
    non_team_messages = []

    for msg_tuple in messages:
        line_idx, msg_dict, byte_size = msg_tuple
        if _is_team_message(msg_dict, pending_task_ids):
            team_messages.append(msg_tuple)
        else:
            non_team_messages.append(msg_tuple)

    # If the entire session is team context (no non-team messages to prune),
    # fall back to pruning all messages without team-protect (#21). This happens
    # when a long team session grows large with no "free" content to remove.
    if not non_team_messages:
        new_messages, results = run_prescription(messages, strategy_names, config)
        return new_messages, results, team_state

    # 3. Prune only non-team messages
    pruned_non_team, results = run_prescription(non_team_messages, strategy_names, config)

    # 4. Merge back: insert team messages at their original relative positions
    all_messages = list(pruned_non_team) + team_messages
    all_messages.sort(key=lambda m: m[0])  # Sort by original line index

    # 5. Inject team recovery messages at the end
    all_messages = inject_team_recovery(all_messages, team_state)

    return all_messages, results, team_state


# ─── Guard daemon ─────────────────────────────────────────────────────────────

def start_guard(
    cwd: str | None = None,
    threshold_mb: float = 50.0,
    soft_threshold_mb: float | None = None,
    rx_name: str = "standard",
    interval: int = 30,
    auto_reload: bool = True,
    config: dict | None = None,
    reactive: bool = True,
    threshold_tokens: int | None = None,
    soft_threshold_tokens: int | None = None,
    session_id: str | None = None,
) -> None:
    """Start the guard daemon with tiered pruning.

    Three-phase protection:
      1. CHECKPOINT every interval — extract team state, write to disk
      2. SOFT PRUNE at soft threshold — gentle prune, no reload, no disruption
      3. HARD PRUNE at hard threshold — full prune with team-protect + optional reload

    Thresholds can be bytes-based, token-based, or both. When both are set,
    whichever is hit first triggers the action.

    Default soft threshold is 60% of hard threshold if not specified.

    Args:
        cwd: Working directory for session detection.
        threshold_mb: Hard threshold in MB — emergency prune + optional reload.
        soft_threshold_mb: Soft threshold in MB — gentle prune, no reload.
            Defaults to 60% of threshold_mb.
        rx_name: Prescription to apply at hard threshold.
        interval: Check interval in seconds.
        auto_reload: If True, kill Claude and auto-resume after hard prune.
        config: Extra config for pruning strategies.
        threshold_tokens: Hard threshold in tokens (optional, checked alongside bytes).
        soft_threshold_tokens: Soft threshold in tokens (optional, checked alongside bytes).
        session_id: Explicit session ID to monitor (bypasses auto-detection).
    """
    hard_threshold_bytes = int(threshold_mb * 1024 * 1024)

    if soft_threshold_mb is None:
        soft_threshold_mb = round(threshold_mb * 0.6, 1)
    soft_threshold_bytes = int(soft_threshold_mb * 1024 * 1024)

    # Find the session — explicit ID or auto-detect
    # strict=True: guard is destructive, refuse to fall back to "most recently modified"
    if session_id:
        sess = _resolve_session_by_id(session_id)
    else:
        sess = find_current_session(cwd, strict=True)
    if not sess:
        print("  ERROR: Could not detect current session.", file=sys.stderr)
        if not session_id:
            print("  Tip: Use --session <session_id> for explicit targeting.", file=sys.stderr)
        sys.exit(1)

    session_path = sess["path"]

    # Detect context window from session data (used for display + overflow scaling)
    from .tokens import detect_context_window, default_token_thresholds_4tier, DEFAULT_HARD2_TOKEN_PCT
    messages_for_model = load_messages(session_path)
    context_window = detect_context_window(messages_for_model)

    # Default to 4-tier token thresholds when none specified
    if threshold_tokens is None:
        soft_threshold_tokens, threshold_tokens, hard2_threshold_tokens = default_token_thresholds_4tier(context_window)
    else:
        hard2_threshold_tokens = int(context_window * DEFAULT_HARD2_TOKEN_PCT)
        if soft_threshold_tokens is None:
            soft_threshold_tokens = int(threshold_tokens * 0.45)

    # Persist cwd + context_window to the sidecar so reload and guard resume
    # can resolve the project directory without relying on slug reversal.
    from .session import record_session
    record_session(sess["session_id"], cwd or os.getcwd(), context_window)

    # Auto-update check — force=True so it works even when guard runs via hook (no TTY)
    from .updater import maybe_auto_update, ping_install_if_new
    ping_install_if_new()
    maybe_auto_update(force=True)

    # Format context window for display
    if context_window >= 1_000_000:
        ctx_str = f"{context_window / 1_000_000:.1f}M"
    else:
        ctx_str = f"{context_window / 1_000:.0f}K"

    # Compute threshold %s for display
    soft_pct = int(soft_threshold_tokens / context_window * 100) if soft_threshold_tokens and context_window else 25
    hard1_pct = int(threshold_tokens / context_window * 100) if threshold_tokens and context_window else 55
    hard2_pct = int(hard2_threshold_tokens / context_window * 100) if hard2_threshold_tokens and context_window else 80

    print(
        f"\n  4-tier guard protecting context ({ctx_str} window):\n"
        f"    Soft  ({soft_pct}%): gentle prune, no reload (file maintenance)\n"
        f"    Hard1 ({hard1_pct}%): {rx_name} prune + reload\n"
        f"    Hard2 ({hard2_pct}%): aggressive prune + reload (emergency)\n"
        f"    User  (90%): manual aggressive (cozempic treat -rx aggressive --execute)\n"
    )

    # Reactive overflow recovery via file watcher
    overflow_watcher = None
    if reactive:
        import threading
        from .overflow import CircuitBreaker, OverflowRecovery
        from .watcher import JsonlWatcher

        # Scale danger thresholds based on context window size
        danger_mb = round(threshold_mb * 1.8, 1)
        danger_tokens = int(context_window * 0.90) if context_window else None

        breaker = CircuitBreaker(session_id=sess["session_id"])
        recovery = OverflowRecovery(
            session_path, sess["session_id"], cwd or os.getcwd(), breaker,
            danger_threshold_mb=danger_mb,
            danger_threshold_tokens=danger_tokens,
        )
        overflow_watcher = JsonlWatcher(
            str(session_path), on_growth=recovery.on_file_growth,
        )
        watcher_thread = threading.Thread(
            target=overflow_watcher.start, daemon=True, name="cozempic-watcher",
        )
        watcher_thread.start()

    # Graceful shutdown on SIGTERM
    def _graceful_shutdown(signum, frame):
        print(f"\n  [{_now()}] Signal {signum} received — final checkpoint...")
        checkpoint_team(session_path=session_path, quiet=False)
        if overflow_watcher:
            overflow_watcher.stop()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    # Claude process watchdog — detect exit even if Stop hook doesn't fire (#29767)
    claude_pid = find_claude_pid()
    claude_alive = True

    prune_count = 0
    soft_prune_count = 0
    checkpoint_count = 0
    cycle_count = 0
    last_team_hash = ""
    consecutive_empty_hard_prunes = 0

    try:
        while True:
            time.sleep(interval)
            cycle_count += 1

            # Periodic backup cleanup every 10 cycles (~5min)
            if cycle_count % 10 == 0:
                cleanup_old_backups(session_path, keep=3)

            # Re-check file exists
            if not session_path.exists():
                print("  WARNING: Session file disappeared. Stopping guard.")
                break

            # Watchdog: detect Claude exit (workaround for Stop hook not firing)
            if claude_pid and claude_alive:
                try:
                    os.kill(claude_pid, 0)
                except (ProcessLookupError, PermissionError):
                    claude_alive = False
                    print(f"  [{_now()}] Claude process exited (PID {claude_pid}). Final checkpoint...")
                    checkpoint_team(session_path=session_path, quiet=False)
                    print(f"  Guard stopping (Claude exited).")
                    break

            current_size = session_path.stat().st_size

            # ── Phase 1: Continuous checkpoint ────────────────────────
            state = checkpoint_team(
                session_path=session_path,
                quiet=True,
            )

            # Track team state changes silently — only note when prune/threshold fires
            if state and not state.is_empty():
                team_hash = f"{len(state.subagents)}:{len(state.tasks)}:{state.message_count}"
                if team_hash != last_team_hash:
                    checkpoint_count += 1
                    last_team_hash = team_hash

            # ── Token check (fast, from tail of file) ────────────────
            current_tokens = None
            if threshold_tokens is not None or soft_threshold_tokens is not None:
                current_tokens = quick_token_estimate(session_path)

            # ── Phase 4: HARD2 (80%) — aggressive + reload (emergency) ──
            hard2_tokens_hit = (
                hard2_threshold_tokens is not None
                and current_tokens is not None
                and current_tokens >= hard2_threshold_tokens
            )
            if hard2_tokens_hit:
                prune_count += 1
                reason = f"{current_tokens:,} tokens >= {hard2_threshold_tokens:,} (80%)"
                print(f"  [{_now()}] EMERGENCY THRESHOLD (80%): {reason}")
                print(f"  Aggressive prune + reload (cycle #{prune_count})...")

                result = guard_prune_cycle(
                    session_path=session_path,
                    rx_name="aggressive",
                    config=config,
                    auto_reload=auto_reload,
                    cwd=cwd or os.getcwd(),
                    session_id=sess["session_id"],
                )

                if result.get("reloading"):
                    print(f"  Reload triggered. Guard exiting.")
                    break

                print(f"  Pruned: {_fmt_prune_result(result)}")
                if result.get("team_name"):
                    print(f"  Team '{result['team_name']}' state preserved ({result['team_messages']} messages)")
                print()

            # ── Phase 3: HARD1 (55%) — standard + reload ─────────────
            elif (threshold_tokens is not None
                  and current_tokens is not None
                  and current_tokens >= threshold_tokens):
                prune_count += 1
                reason = f"{current_tokens:,} tokens >= {threshold_tokens:,} (55%)"
                print(f"  [{_now()}] HARD THRESHOLD (55%): {reason}")
                print(f"  Standard prune + reload (cycle #{prune_count})...")

                result = guard_prune_cycle(
                    session_path=session_path,
                    rx_name=rx_name,
                    config=config,
                    auto_reload=auto_reload,
                    cwd=cwd or os.getcwd(),
                    session_id=sess["session_id"],
                )

                if result.get("reloading"):
                    print(f"  Reload triggered. Guard exiting.")
                    break

                print(f"  Pruned: {_fmt_prune_result(result)}")
                if result.get("team_name"):
                    print(f"  Team '{result['team_name']}' state preserved ({result['team_messages']} messages)")

                if result.get("saved_mb", 0) <= 0:
                    consecutive_empty_hard_prunes += 1
                    if consecutive_empty_hard_prunes >= 3:
                        print(f"  [{_now()}] WARNING: Hard prune freed 0 bytes 3x in a row.")
                        consecutive_empty_hard_prunes = 0
                        time.sleep(interval * 4)
                else:
                    consecutive_empty_hard_prunes = 0
                print()

            # ── Phase 2: SOFT (25%) — gentle, no reload ──────────────
            else:
                hard_bytes_hit = current_size >= hard_threshold_bytes
                soft_bytes_hit = current_size >= soft_threshold_bytes
                soft_tokens_hit = (
                    soft_threshold_tokens is not None
                    and current_tokens is not None
                    and current_tokens >= soft_threshold_tokens
                )
                if hard_bytes_hit or soft_bytes_hit or soft_tokens_hit:
                    soft_prune_count += 1
                    size_mb = current_size / 1024 / 1024
                    reason = f"{current_tokens:,} tokens >= {soft_threshold_tokens:,} (25%)" if soft_tokens_hit else f"{size_mb:.1f}MB"
                    print(f"  [{_now()}] SOFT THRESHOLD (25%): {reason}")
                    print(f"  Gentle prune, no reload (cycle #{soft_prune_count})...")

                    result = guard_prune_cycle(
                        session_path=session_path,
                        rx_name="gentle",
                        config=config,
                        auto_reload=False,
                        cwd=cwd or os.getcwd(),
                        session_id=sess["session_id"],
                    )

                    print(f"  Trimmed: {_fmt_prune_result(result)}")
                    if result.get("team_name"):
                        print(f"  Team '{result['team_name']}' state preserved ({result['team_messages']} messages)")
                    print()

    except KeyboardInterrupt:
        # Stop reactive watcher
        if overflow_watcher:
            overflow_watcher.stop()

        # Final checkpoint before exit
        checkpoint_team(session_path=session_path, quiet=True)
        total_prunes = prune_count + soft_prune_count
        if total_prunes:
            print(f"\n  Guard stopped. Pruned {total_prunes}x during this session.")
        else:
            print(f"\n  Guard stopped.")


def guard_prune_cycle(
    session_path: Path,
    rx_name: str = "standard",
    config: dict | None = None,
    auto_reload: bool = True,
    cwd: str = "",
    session_id: str | None = None,
) -> dict:
    """Execute a single guard prune cycle.

    Holds a _PruneLock for the duration so concurrent guard instances cannot
    race each other.  Takes a _FileSnapshot before loading so that any lines
    Claude appends while pruning is in progress are preserved in the output
    (or the cycle is deferred on conflict).

    Returns dict with: saved_mb, team_name, team_messages, reloading, checkpoint_path
    """
    from .tokens import estimate_session_tokens, calibrate_ratio

    _no_change = {
        "saved_mb": 0.0,
        "original_tokens": 0,
        "final_tokens": 0,
        "team_name": None,
        "team_messages": 0,
        "checkpoint_path": None,
        "backup_path": None,
        "reloading": False,
    }

    try:
        with _PruneLock(session_path):
            # Snapshot before load so we can detect Claude appending mid-prune
            snap = snapshot_session(session_path)

            messages = load_messages(session_path)
            original_bytes = sum(b for _, _, b in messages)

            # Token estimate before pruning — capture calibrated ratio before metadata-strip
            pre_te = estimate_session_tokens(messages)
            pre_ratio = calibrate_ratio(messages)

            # Prune with team protection
            pruned_messages, results, team_state = prune_with_team_protect(
                messages, rx_name=rx_name, config=config,
            )

            final_bytes = sum(b for _, _, b in pruned_messages)
            saved_bytes = original_bytes - final_bytes

            # If pruning freed nothing (or grew the file via team recovery injection), don't
            # save — avoids backup accumulation and file growth on ineffective prescriptions (#16, #19).
            if saved_bytes <= 0:
                return {
                    "saved_mb": 0.0,
                    "original_tokens": pre_te.total,
                    "final_tokens": pre_te.total,
                    "team_name": team_state.team_name,
                    "team_messages": team_state.message_count,
                    "checkpoint_path": None,
                    "backup_path": None,
                    "reloading": False,
                }

            # Token estimate after pruning — pass pre-calibrated ratio
            post_te = estimate_session_tokens(pruned_messages, pre_calibrated_ratio=pre_ratio)

            # Write checkpoint if team exists
            checkpoint_path = None
            if not team_state.is_empty():
                project_dir = session_path.parent
                checkpoint_path = write_team_checkpoint(team_state, project_dir)

            # Save pruned session — snapshot enables append-aware atomic write
            backup = save_messages(session_path, pruned_messages, create_backup=True, snapshot=snap)

            # Cap backup retention at 3 files to prevent disk fill (#19)
            if backup:
                cleanup_old_backups(session_path, keep=3)

    except PruneLockError as exc:
        print(f"  [{_now()}] Prune deferred — lock held: {exc}", file=sys.stderr)
        return _no_change
    except PruneConflictError as exc:
        print(f"  [{_now()}] Prune deferred — conflict detected: {exc}", file=sys.stderr)
        return _no_change

    result = {
        "saved_mb": saved_bytes / 1024 / 1024,
        "original_tokens": pre_te.total,
        "final_tokens": post_te.total,
        "team_name": team_state.team_name,
        "team_messages": team_state.message_count,
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
        "backup_path": str(backup) if backup else None,
        "reloading": False,
    }

    # Trigger reload if configured — terminate Claude then auto-resume
    if auto_reload:
        claude_pid = find_claude_pid()
        if claude_pid:
            _terminate_and_resume(claude_pid, cwd, session_id=session_id)
            result["reloading"] = True
        else:
            resume_flag = f"--resume {session_id}" if session_id else "--resume"
            print("  WARNING: Could not find Claude PID. Pruned but not reloading.")
            print(f"  Restart manually: claude {resume_flag}")

    return result


def _terminate_and_resume(claude_pid: int, project_dir: str, session_id: str | None = None) -> None:
    """Send SIGTERM to Claude, wait up to 5s for exit, SIGKILL if needed, then spawn resume.

    In SSH sessions the resume watcher can't open a new terminal, so we skip
    termination entirely — the session was pruned in place and Claude will
    continue with the reduced context.
    """
    if is_ssh_session():
        resume_flag = f"--resume {session_id}" if session_id else "--resume"
        print(f"  SSH session — skipping terminate+resume. Resume manually: claude {resume_flag}")
        return

    system = platform.system()

    # 1. Ask Claude to exit
    try:
        if system == "Windows":
            subprocess.call(["taskkill", "/PID", str(claude_pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(claude_pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass  # Already dead

    # 2. Poll for exit up to 5s
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            os.kill(claude_pid, 0)
            time.sleep(0.2)
        except (ProcessLookupError, PermissionError, OSError):
            break
    else:
        # Still alive — force kill
        try:
            if system == "Windows":
                subprocess.call(["taskkill", "/F", "/PID", str(claude_pid)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(claude_pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    # 3. Spawn the resume watcher (opens new terminal after process fully exits)
    _spawn_reload_watcher(claude_pid, project_dir, session_id=session_id)


def _spawn_reload_watcher(claude_pid: int, project_dir: str, session_id: str | None = None):
    """Spawn a detached watcher that resumes Claude after exit."""
    resume_flag = f"--resume {session_id}" if session_id else "--resume"

    # SSH sessions can't open GUI terminals — skip auto-resume
    if is_ssh_session():
        print(f"  SSH session detected — skipping auto-resume.")
        print(f"  Resume manually: cd {project_dir} && claude {resume_flag}")
        return

    system = platform.system()

    if system == "Darwin":
        # Resume in the SAME terminal by typing the command into the frontmost window
        # instead of opening a new one. Falls back to new window if keystroke fails.
        resume_cmd = (
            f"osascript -e 'tell application \"System Events\" to keystroke "
            f"\"cd {project_dir} && claude {resume_flag}\" & return' 2>/dev/null || "
            f"osascript -e 'tell application \"Terminal\" to do script "
            f"\"cd {shell_quote(project_dir)} && claude {resume_flag}\"'"
        )
    elif system == "Linux":
        resume_cmd = (
            f"if command -v gnome-terminal >/dev/null 2>&1; then "
            f"gnome-terminal -- bash -c 'cd {shell_quote(project_dir)} && claude {resume_flag}; exec bash'; "
            f"elif command -v xterm >/dev/null 2>&1; then "
            f"xterm -e 'cd {shell_quote(project_dir)} && claude {resume_flag}' & "
            f"else echo 'No terminal emulator found' >> /tmp/cozempic_guard.log; fi"
        )
    elif system == "Windows":
        resume_cmd = (
            f"start cmd /c \"cd /d {project_dir} && claude {resume_flag}\""
        )
    else:
        print(f"  WARNING: Auto-resume not supported on {system}.")
        return

    watcher_script = (
        f"while kill -0 {claude_pid} 2>/dev/null; do sleep 1; done; "
        f"sleep 1; "
        f"{resume_cmd}; "
        f"echo \"$(date): Cozempic guard resumed Claude in {project_dir}\" >> /tmp/cozempic_guard.log"
    )

    subprocess.Popen(
        ["bash", "-c", watcher_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def _pid_file(cwd: str) -> Path:
    """Return the PID file path for a guard daemon in this project."""
    import hashlib
    slug = hashlib.md5(cwd.encode()).hexdigest()[:12]
    return Path("/tmp") / f"cozempic_guard_{slug}.pid"


def _session_file(cwd: str) -> Path:
    """Return the session file path that records which session the guard is watching."""
    import hashlib
    slug = hashlib.md5(cwd.encode()).hexdigest()[:12]
    return Path("/tmp") / f"cozempic_guard_{slug}_session.txt"


def _is_guard_running(cwd: str) -> int | None:
    """Check if a guard daemon is already running for this project.

    Returns the PID if running, None otherwise.
    """
    pid_path = _pid_file(cwd)
    if not pid_path.exists():
        return None

    try:
        pid = int(pid_path.read_text().strip())
        # Check if process is actually alive
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        # Stale PID file — clean it up
        pid_path.unlink(missing_ok=True)
        _session_file(cwd).unlink(missing_ok=True)
        return None


def start_guard_daemon(
    cwd: str | None = None,
    threshold_mb: float = 50.0,
    soft_threshold_mb: float | None = None,
    rx_name: str = "standard",
    interval: int = 30,
    auto_reload: bool = True,
    reactive: bool = True,
    threshold_tokens: int | None = None,
    soft_threshold_tokens: int | None = None,
    session_id: str | None = None,
) -> dict:
    """Start the guard as a background daemon.

    Spawns a detached subprocess running `cozempic guard` with output
    redirected to a log file. Uses a PID file to prevent double-starts.

    Returns dict with: started (bool), pid (int|None), pid_file, log_file,
    already_running (bool).
    """
    cwd = cwd or os.getcwd()

    existing_pid = _is_guard_running(cwd)
    if existing_pid:
        # Check whether the existing guard is watching the same session (#11)
        sess_path = _session_file(cwd)
        old_session = sess_path.read_text().strip() if sess_path.exists() else None

        if session_id is not None and old_session != session_id:
            # Session changed — kill the stale guard and start a new one
            print(
                f"  Replacing guard (session changed: "
                f"{old_session[:8] if old_session else '?'} → {session_id[:8]})"
            )
            try:
                os.kill(existing_pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            time.sleep(1)
            _pid_file(cwd).unlink(missing_ok=True)
            sess_path.unlink(missing_ok=True)
        else:
            return {
                "started": False,
                "pid": existing_pid,
                "pid_file": str(_pid_file(cwd)),
                "log_file": None,
                "already_running": True,
            }

    import hashlib
    slug = hashlib.md5(cwd.encode()).hexdigest()[:12]
    log_file = Path("/tmp") / f"cozempic_guard_{slug}.log"
    pid_path = _pid_file(cwd)

    # Build the guard command
    cmd_parts = [
        sys.executable, "-m", "cozempic.cli", "guard",
        "--cwd", cwd,
        "--threshold", str(threshold_mb),
        "--interval", str(interval),
        "-rx", rx_name,
    ]
    if soft_threshold_mb is not None:
        cmd_parts.extend(["--soft-threshold", str(soft_threshold_mb)])
    if not auto_reload:
        cmd_parts.append("--no-reload")
    if not reactive:
        cmd_parts.append("--no-reactive")
    if threshold_tokens is not None:
        cmd_parts.extend(["--threshold-tokens", str(threshold_tokens)])
    if soft_threshold_tokens is not None:
        cmd_parts.extend(["--soft-threshold-tokens", str(soft_threshold_tokens)])
    if session_id is not None:
        cmd_parts.extend(["--session", session_id])

    # Spawn detached process
    with open(log_file, "a", encoding="utf-8") as lf:
        from datetime import datetime
        lf.write(f"\n--- Guard daemon started at {datetime.now().isoformat()} ---\n")
        lf.write(f"CWD: {cwd}\n")
        lf.write(f"CMD: {' '.join(cmd_parts)}\n\n")
        lf.flush()

        # PYTHONUNBUFFERED=1 ensures guard log output is written immediately (#14)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            cmd_parts,
            stdout=lf,
            stderr=lf,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=cwd,
            env=env,
        )

    # Write PID file and session file so stale guards can be detected on restart (#11)
    pid_path.write_text(str(proc.pid))
    if session_id is not None:
        _session_file(cwd).write_text(session_id)

    return {
        "started": True,
        "pid": proc.pid,
        "pid_file": str(pid_path),
        "log_file": str(log_file),
        "already_running": False,
    }


def _fmt_prune_result(result: dict) -> str:
    """Format a prune cycle result, leading with tokens if available."""
    orig_tok = result.get("original_tokens")
    final_tok = result.get("final_tokens")
    if orig_tok and final_tok:
        saved_tok = orig_tok - final_tok
        tok_str = f"{saved_tok / 1000:.1f}K" if saved_tok >= 1000 else str(saved_tok)
        pct = f"{saved_tok / orig_tok * 100:.1f}%" if orig_tok > 0 else "0%"
        return f"{tok_str} tokens freed ({pct}), {result['saved_mb']:.1f}MB saved"
    return f"{result['saved_mb']:.1f}MB saved"


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")
