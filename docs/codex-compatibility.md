# Codex Compatibility Sketch

**Status:** design only, not implemented

## Short answer

Cozempic does not work with Codex as a drop-in today.

It is currently Claude Code-specific in three big ways:

1. **Storage layout**
   - Claude: `~/.claude/projects/<project>/*.jsonl`
   - Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
2. **Transcript schema**
   - Claude messages look like conversational JSONL entries with Claude-specific metadata.
   - Codex sessions are event logs (`session_meta`, `event_msg`, `response_item`, etc.).
3. **Lifecycle integration**
   - Cozempic relies on Claude hooks, Claude compaction boundaries, and `claude --resume`.
   - Codex uses different storage, no matching hook model, and different resume/runtime behavior.

## What a real Codex adapter would need

### 1. A pluggable backend layer

Introduce an internal backend interface:

- `discover_sessions()`
- `find_current_session()`
- `load_transcript()`
- `save_transcript()`
- `estimate_context()`
- `resume_command()`
- `supports_hooks()`

Claude would become one backend. Codex would be a second backend.

### 2. A Codex-native parser

Codex needs a separate parser that understands:

- `session_meta`
- `event_msg`
- `response_item`
- tool call / tool result records
- model context metadata

Most current Cozempic strategies are tied to Claude message shapes and would need per-strategy compatibility checks.

### 3. Strategy triage

Some strategies could port cleanly:

- large tool output trimming
- duplicate document collapse
- image/base64 trimming
- progress/status collapse

Some are Claude-only and should stay Claude-only:

- `compact-summary-collapse`
- Claude hook wiring
- Claude memory/digest injection
- `claude --resume` flow
- compact boundary / PostCompact recovery logic

### 4. Codex-safe continuity features

A Codex version should likely focus on:

- transcript slimming
- oversized event trimming
- tool-output compaction
- session diagnostics
- optional recap generation before resume/fork

It should **not** pretend Claude features exist when they do not.

### 5. Separate integration surface

Probable command shape:

- `cozempic diagnose --backend codex`
- `cozempic treat --backend codex`
- or auto-detect backend from cwd / session path

But hook setup should remain backend-specific:

- `cozempic init` for Claude project hooks
- a future `cozempic codex-init` only if Codex gains an equivalent integration point worth supporting

## Recommended implementation order

1. **Backend abstraction**
2. **Read-only Codex diagnose**
3. **Codex-safe treat (dry-run first)**
4. **Codex write path with backups**
5. **Optional Codex continuity extras**

## Non-goals

- Reusing Claude hook logic for Codex
- Reusing Claude compaction assumptions for Codex
- Claiming feature parity before the Codex backend has its own tests

## Acceptance bar

A Codex backend is only “real” when it has:

- fixture-backed transcript parsing tests
- dry-run diagnosis output on real Codex session samples
- backup-safe write tests
- explicit per-strategy compatibility coverage
