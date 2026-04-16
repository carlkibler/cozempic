# Codex Compatibility Sketch

**Status:** MVP implemented

## Current state

Cozempic now has a first real Codex MVP:

- discovers Codex sessions from `~/.codex/sessions/...`
- detects the current Codex session by cwd
- reads Codex transcript rows for `list`, `current`, `diagnose`, `treat`, and `strategy`
- estimates Codex context usage from `token_count` events
- prunes Codex reasoning rows and oversized tool outputs

It is **not** full feature parity with Claude Code. Claude-specific hook, guard,
checkpoint, and resume flows remain Claude-only.

## Why Codex needed a separate path

The original Cozempic implementation was Claude-specific in three big ways:

1. **Storage layout**
   - Claude: `~/.claude/projects/<project>/*.jsonl`
   - Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
2. **Transcript schema**
   - Claude messages look like conversational JSONL entries with Claude-specific metadata.
   - Codex sessions are event logs (`session_meta`, `event_msg`, `response_item`, etc.).
3. **Lifecycle integration**
   - Cozempic relies on Claude hooks, Claude compaction boundaries, and `claude --resume`.
   - Codex uses different storage, no matching hook model, and different resume/runtime behavior.

## What is still missing for Codex

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

### 1. Better Codex-native strategies

Current MVP mainly benefits from:

- reasoning stripping
- tool output trimming
- old tool-result compaction
- duplicate text/document trimming

A fuller Codex backend should add more Codex-specific strategies instead of
leaning mostly on Claude-compatible abstractions.

### 2. Better post-prune token estimation

Codex exposes rate-limit occupancy better than direct “current context tokens,”
so post-prune token counts are still approximations.

### 3. Codex-native continuity features

Not implemented:

- Codex equivalent of guard/daemon protection
- Codex equivalent of checkpoint/recovery reinjection
- Codex equivalent of automatic resume/reload orchestration

### 4. Clearer backend-specific CLI UX

Today, unsupported commands are gated at runtime. A nicer version would make
backend support clearer in help text and docs.

## Non-goals

- Reusing Claude hook logic for Codex
- Reusing Claude compaction assumptions for Codex
- Claiming feature parity before the Codex backend has its own tests

## Acceptance bar

The current MVP clears the minimum bar:

- fixture-backed Codex transcript tests
- current-session detection for Codex
- Codex-safe dry-run and write-path pruning
- explicit unsupported-command gating for Claude-only features

The next bar is “good Codex support,” not just “real Codex support.”
