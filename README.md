# Cozempic

![Downloads](https://img.shields.io/badge/downloads-11k%2B-brightgreen) ![Version](https://img.shields.io/badge/version-1.2.1-blue) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

Context cleaning for [Claude Code](https://claude.ai/code) — **remove the bloat, keep everything that matters, protect Agent Teams from context loss**.

### What gets removed

Claude Code context fills up with dead weight that wastes your token budget: hundreds of progress tick messages, repeated thinking blocks and signatures, stale file reads that were superseded by edits, duplicate document injections, oversized tool outputs, and metadata bloat (token counts, stop reasons, cost fields). A typical session carries 8-46MB — most of it noise. Cozempic identifies and removes all of it using 13 composable strategies, while your actual conversation, decisions, tool results, and working context stay untouched.

### Token-aware diagnostics

File size is a poor proxy for context usage — a 1.1MB JSONL can hold 158K tokens. Cozempic reads the **exact token counts** from Claude's `usage` fields in the session file, so you see real context window usage instead of misleading byte sizes. Every command shows tokens and a context % bar, and guard thresholds can be set in tokens for precise compaction prevention.

### Agent Teams context loss protection

When context gets too large, Claude's auto-compaction summarizes away critical state. For **Agent Teams**, this is catastrophic: the lead agent's context is compacted, team coordination messages (TeamCreate, SendMessage, TaskCreate/Update) are discarded, the lead forgets its teammates exist, and subagents are orphaned with no recovery path. ([#23620](https://github.com/anthropics/claude-code/issues/23620), [#23821](https://github.com/anthropics/claude-code/issues/23821), [#24052](https://github.com/anthropics/claude-code/issues/24052), [#21925](https://github.com/anthropics/claude-code/issues/21925))

Cozempic prevents this with five layers of protection:

1. **Continuous checkpoint** — saves team state to disk every N seconds so it's always recoverable
2. **Hook-driven checkpoint** — fires after every Task spawn, TaskCreate/Update, before compaction, and at session end
3. **Tiered pruning** — soft threshold gently trims bloat without disruption; hard threshold does full prune + optional reload
4. **Reactive overflow recovery** — kqueue/polling file watcher detects inbox-flood overflow within milliseconds, auto-prunes with escalating prescriptions, and resumes the session (~10s downtime vs permanently dead). Circuit breaker prevents infinite recovery loops. ([#23876](https://github.com/anthropics/claude-code/issues/23876))
5. **Config.json ground truth** — reads `~/.claude/teams/*/config.json` for authoritative team state (lead, members, models, cwds)

**Zero external dependencies.** Python 3.10+ stdlib only.

## Changelog

### v1.2.1

- **Auto-update** — cozempic checks PyPI once per day on startup. If a newer version is available, it upgrades itself in-place and prints the result. No-ops silently on network failures, in piped/CI contexts, or when already up-to-date.

### v1.2.0

- **Atomic file writes** — all session writes use `write → fsync → os.replace()`. No partial writes on crash, interrupt, or full disk.
- **Strict session resolution** — destructive operations (`--execute`, `reload`, guard auto-detect) now refuse to act when the session match is ambiguous. Previously they'd pick the most recently modified file and silently operate on the wrong session.
- **Schema-first team detection** — team messages are now classified by `tool_use` block names and `<task-notification>` XML only. Keyword scanning of text content has been removed, eliminating false positives that could accidentally protect non-team messages from pruning.
- **Strong-join config merge** — `~/.claude/teams/*/config.json` is now merged into state only when there's a matching `leadSessionId`, `leadAgentId`, or member intersection. Loose "most-recent config wins" fallback removed.
- **Real supervisor on guard reload** — guard now sends SIGTERM, waits up to 5s for clean exit, then SIGKILL before spawning the resume watcher. Previously it only spawned the watcher without terminating Claude first. SSH sessions detect this and print manual instructions instead.
- **Dual-channel token metrics** — `metadata-strip` records the exact token count from `usage` fields *before* stripping them. If the session had usage data, you see real pre-treatment token counts; `treat` prints a note when the method shifts from exact to heuristic after stripping.
- **Stale-backups scope fix** — `doctor` now targets `*.jsonl.bak` files only (not all `*.bak` files), preventing accidental deletion of non-Cozempic backup files.

### v1.1.0

PostCompact recovery, tab completion, 1M context scaling, safety limits.

## Install

### As a Claude Code Plugin (Recommended)

```bash
pip install cozempic
```

Then inside Claude Code:

```
/plugin marketplace add Ruya-AI/cozempic
/plugin install cozempic
```

This gives you everything: MCP tools, skills (`/cozempic:diagnose`, `/cozempic:treat`, etc.), auto-wired hooks for guard daemon and team checkpointing. See [Plugin](#plugin-recommended) for details.

### CLI Only

```bash
pip install cozempic
cozempic init
```

`cozempic init` wires hooks and the `/cozempic` slash command into your project — same protection, just without the plugin framework. See [Setup](#setup) for what gets wired.

### From Source

```bash
git clone https://github.com/Ruya-AI/cozempic.git
cd cozempic
pip install -e .
```

## Setup

After installing via CLI, run `init` from your project directory:

```bash
cd your-project/
cozempic init
```

That's it. This auto-wires everything:

1. **Guard daemon auto-start** — `SessionStart` hook spawns `cozempic guard --daemon` when Claude Code opens. Background process, PID file prevents double-starts, logs to `/tmp/cozempic_guard_*.log`
2. **Checkpoint hooks** — `PostToolUse[Task|TaskCreate|TaskUpdate]`, `PreCompact`, `PostCompact`, `Stop` capture team state at every critical moment
3. **`/cozempic` slash command** — installed to `~/.claude/commands/` for in-session diagnosis and treatment

Idempotent — safe to run multiple times. Existing hooks and settings are preserved. No second terminal needed.

## Quick Start

```bash
# One-time setup: wire hooks + slash command
cozempic init

# List all sessions with sizes
cozempic list

# Auto-detect and diagnose the current session
cozempic current --diagnose

# Dry-run the standard prescription on current session
cozempic treat current

# Apply with backup
cozempic treat current --execute

# Go aggressive on a specific session
cozempic treat <session_id> -rx aggressive --execute

# Save team/agent state right now (no pruning, instant)
cozempic checkpoint --show

# Guard auto-starts on session open (after cozempic init)
# Or run manually with custom thresholds:
cozempic guard --threshold 50 -rx standard

# Guard now defaults to token-based thresholds automatically (75%/45% of context window)
# Override if needed for heavy configs (many rules files, MCP servers):
cozempic guard --system-overhead-tokens 34000

# Run as background daemon (what the SessionStart hook uses):
cozempic guard --daemon

# Treat + auto-resume in a new terminal
cozempic reload -rx gentle
```

Session IDs accept full UUIDs, UUID prefixes, file paths, or `current` for auto-detection based on your working directory.

## How It Works

Each type of bloat has a dedicated **strategy** that knows exactly what to remove and what to keep. Strategies are grouped into **prescriptions** — presets that balance cleaning depth against risk:

| Prescription | Strategies | Risk | Typical Savings |
|---|---|---|---|
| `gentle` | 3 | Minimal | 5-8% |
| `standard` | 7 | Low | 15-20% |
| `aggressive` | 13 | Moderate | 20-25% |

**Dry-run is the default.** Nothing is modified until you pass `--execute`. Backups are always created automatically.

## Strategies

| # | Strategy | What It Does | Expected |
|---|----------|-------------|----------|
| 1 | `progress-collapse` | Collapse consecutive progress tick messages | 40-48% |
| 2 | `file-history-dedup` | Deduplicate file-history-snapshot messages | 3-6% |
| 3 | `metadata-strip` | Strip token usage stats, stop_reason, costs — captures exact token count from `usage` fields before stripping for dual-channel accuracy | 1-3% |
| 4 | `thinking-blocks` | Remove/truncate thinking content + signatures | 2-5% |
| 5 | `tool-output-trim` | Trim large tool results (>8KB or >100 lines) | 1-8% |
| 6 | `stale-reads` | Remove file reads superseded by later edits | 0.5-2% |
| 7 | `system-reminder-dedup` | Deduplicate repeated system-reminder tags | 0.1-3% |
| 8 | `http-spam` | Collapse consecutive HTTP request runs | 0-2% |
| 9 | `error-retry-collapse` | Collapse repeated error-retry sequences | 0-5% |
| 10 | `background-poll-collapse` | Collapse repeated polling messages | 0-1% |
| 11 | `document-dedup` | Deduplicate large document blocks | 0-44% |
| 12 | `mega-block-trim` | Trim any content block over 32KB | safety net |
| 13 | `envelope-strip` | Strip constant envelope fields | 2-4% |

Run a single strategy:

```bash
cozempic strategy progress-collapse <session_id> -v
cozempic strategy thinking-blocks <session_id> --thinking-mode truncate
```

## Commands

```
cozempic init                               Wire hooks + slash command into project
cozempic list [--project NAME]              List sessions with sizes
cozempic current [-d]                       Show/diagnose current session (auto-detect)
cozempic diagnose <session>                 Analyze bloat sources (read-only)
cozempic treat <session> [-rx PRESET]       Run prescription (dry-run default)
cozempic treat <session> --execute          Apply changes with backup
cozempic strategy <name> <session>          Run single strategy
cozempic reload [-rx PRESET]                Treat + auto-resume in new terminal
cozempic checkpoint [--show]                Save team/agent state to disk (no pruning)
cozempic post-compact                       Output team state after compaction (PostCompact hook)
cozempic guard [--threshold MB]             Tiered guard: checkpoint + soft/hard prune
cozempic guard --soft-threshold 25          Custom soft threshold (default: 60% of hard)
cozempic guard --threshold-tokens 180000    Override hard threshold in tokens (default: 75% of context window)
cozempic guard --soft-threshold-tokens N    Override soft threshold in tokens (default: 45% of context window)
cozempic guard --system-overhead-tokens N   Override system overhead estimate (default: 21000)
cozempic guard --no-reactive                Disable reactive overflow recovery
cozempic doctor [--fix]                     Check for known Claude Code issues
cozempic formulary                          Show all strategies & prescriptions
```

Use `current` as the session argument in any command to auto-detect the active session for your working directory.

## Checkpoint — Instant Team State Snapshot

Save your current team/agent state to disk without pruning or modifying anything:

```bash
# Save team state
cozempic checkpoint

# Save and print the state
cozempic checkpoint --show
```

Output:

```
  Checkpoint: 6 subagents, 9 tasks -> team-checkpoint.md

Active agent team: agents

Subagents (6):
  - af9763f [Explore] — Explore memory system [completed]
    Result: Complete understanding of the memory system...
  - aa79e90 [Explore] — Explore platform adapters [completed]
    Result: Comprehensive technical summary of platform...
  ...

Shared task list:
  - [COMPLETED] Fix team detection
  - [IN_PROGRESS] Add continuous checkpoint
  - [PENDING] Update README
```

### What gets detected

Team message classification is **schema-first**: a message is considered a team coordination message only if it contains a `tool_use` block with a known team tool name (Task, TaskCreate, TaskUpdate, TeamCreate, SendMessage, TaskOutput, TaskStop, TaskGet, TaskList) or a `<task-notification>` XML element. Plain text mentioning team keywords is never mis-classified as team coordination.

Cozempic scans two data sources and merges them:

**JSONL session file** (runtime state):

| Pattern | Source | What's Extracted |
|---------|--------|-----------------|
| `Task` tool calls | Subagent spawns | agent_id, subagent_type, description, prompt |
| `<task-notification>` | Agent completion messages | status, summary, full result text |
| `TaskCreate` / `TaskUpdate` | Shared todo list | task_id, subject, status, owner |
| `TaskOutput` / `TaskStop` | Background agent management | agent status updates |
| `TeamCreate` / `SendMessage` | Explicit team coordination | team name, teammate roles |

**`~/.claude/teams/*/config.json`** (ground truth):

| Field | What's Extracted |
|-------|-----------------|
| `name` | Authoritative team name |
| `leadAgentId` | Lead agent identifier |
| `leadSessionId` | Lead agent's session UUID |
| `members[].model` | Model used by each teammate (e.g., `claude-opus-4-6`) |
| `members[].cwd` | Working directory for each teammate |
| `members[].agentType` | Role/type of each teammate |

Config.json fields are authoritative — they override JSONL-inferred values. JSONL is authoritative for runtime state (subagent progress, task status, results).

The checkpoint is written to `.claude/projects/<project>/team-checkpoint.md`.

## Guard — Continuous Protection

Guard is a background daemon with two complementary systems:

**Proactive polling loop** (every N seconds):

- **Phase 1: Continuous checkpoint** — extracts team state and writes to disk. Lightweight read-only scan. Team state is always recoverable even if Claude crashes. Also merges `~/.claude/teams/*/config.json` as ground truth for team name, lead agent, member models, and working directories.
- **Phase 2: Soft prune** (at soft threshold) — when file size crosses the soft threshold, applies a `gentle` prescription to trim easy bloat. **No reload** — the session continues uninterrupted.
- **Phase 3: Hard prune** (at hard threshold) — applies the full prescription with team-protect, injects recovery messages, and optionally kills + resumes Claude.

**Reactive overflow recovery** (sub-second, enabled by default):

When agent team sessions go idle, Claude's InboxPoller can deliver all queued teammate messages at once, spiking the JSONL past the 200k token limit in seconds — faster than the polling loop can react. The reactive watcher uses kqueue (macOS, 0.04ms latency) or stat polling (Linux, 200ms) to detect this overflow within milliseconds. On detection:

1. **Circuit breaker check** — prevents infinite prune → resume → crash loops (max 3 recoveries in 5 minutes)
2. **Escalating prescription** — recovery #1 uses `gentle`, #2 uses `standard`, #3 uses `aggressive`
3. **Pre-flight check** — if post-prune estimate is still too large, skips resume
4. **Team-protected prune** → SIGTERM → 5s grace → SIGKILL → auto-resume (~10s downtime vs permanently dead session). SSH-detected sessions skip the terminate step and print manual recovery instructions instead.
5. **Breaker trip** — after 3 rapid recoveries, halts with a clear message and saves a final checkpoint

Disable with `--no-reactive` if needed. Zero impact on normal sessions — the watcher runs silently and fast-path exits for small files.

Token thresholds now default automatically to 75% (hard) and 45% (soft) of the detected context window — no flags needed. The byte soft threshold defaults to 60% of the hard threshold. If your setup has heavy rules files, MCP servers, or a large CLAUDE.md, use `--system-overhead-tokens` to get more accurate estimates (the default 21K can underestimate by 10K+ tokens in complex configs).

```bash
# Standard — run in a separate terminal
cozempic guard

# Custom thresholds and interval
cozempic guard --threshold 40 --soft-threshold 25 --interval 15 -rx standard

# Without auto-reload (just clean, no restart)
cozempic guard --threshold 50 --no-reload

# Disable reactive overflow recovery (polling only)
cozempic guard --no-reactive

# Token thresholds are automatic — override only if needed
cozempic guard --threshold 50 --threshold-tokens 160000 --soft-threshold-tokens 100000

# Heavy config (many rules files, MCP servers) — increase overhead estimate for accuracy
cozempic guard --system-overhead-tokens 35000

# Aggressive at hard threshold, gentle at soft (automatic)
cozempic guard --threshold 30 -rx aggressive
```

Output:

```
  COZEMPIC GUARD v3
  ===================================================================
  Session:     abc123.jsonl
  Size:        5.4MB
  Soft:        30.0MB (gentle prune, no reload)
  Hard:        50.0MB (full prune + reload)
  Soft tokens: 90,000 (auto)
  Hard tokens: 150,000 (auto)
  Rx:          gentle (soft) / standard (hard)
  Interval:    30s
  Team-protect: enabled
  Checkpoint:  continuous (every 30s)
  Reactive:    enabled

  Guarding... (Ctrl+C to stop)

  [14:23:01] Checkpoint #1: 6 agents, 9 tasks, 121 msgs (5.4MB)
  [14:25:31] Checkpoint #2: 8 agents, 12 tasks, 156 msgs (6.1MB)
  [14:28:01] Checkpoint #3: 8 agents, 12 tasks, 189 msgs (7.2MB)
  [14:45:01] SOFT THRESHOLD: 121,432 tokens >= 120,000
             Gentle prune, no reload (cycle #1)
             Trimmed: 4.1MB saved
  [15:10:01] HARD THRESHOLD: 50.3MB >= 50.0MB
             Emergency prune with standard (cycle #1)
             Pruned: 12.4MB saved
             Team 'dev-agents' state preserved (87 messages)
```

On Ctrl+C, guard writes a final checkpoint before exiting.

### How team-protect works

During prune (soft or hard):

1. **Extract** full team state from JSONL + `~/.claude/teams/*/config.json`
2. **Separate** team messages from non-team messages
3. **Prune** only non-team messages using the prescription
4. **Merge** team messages back at their original positions
5. **Inject** a synthetic message pair confirming team state (Claude *sees* this as conversation history)
6. **Save** with backup, then optionally reload (hard only)

## Hook Integration

For the strongest protection, wire `cozempic checkpoint` into Claude Code hooks. This captures team state at every critical moment — not just on a timer.

Add to your project's `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Task",
        "hooks": [
          {
            "type": "command",
            "command": "cozempic checkpoint 2>/dev/null || true"
          }
        ]
      },
      {
        "matcher": "TaskCreate|TaskUpdate",
        "hooks": [
          {
            "type": "command",
            "command": "cozempic checkpoint 2>/dev/null || true"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "cozempic checkpoint 2>/dev/null || true"
          }
        ]
      }
    ],
    "PostCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "cozempic post-compact 2>/dev/null || true"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "cozempic checkpoint 2>/dev/null || true"
          }
        ]
      }
    ]
  }
}
```

This checkpoints team state:

| Hook | When | Why |
|------|------|-----|
| `PostToolUse[Task]` | After every subagent spawn | Capture new agent immediately |
| `PostToolUse[TaskCreate\|TaskUpdate]` | After todo list changes | Track task progress |
| `PreCompact` | Right before auto-compaction | Last chance to save state |
| `PostCompact` | Right after auto-compaction | Re-inject team state into conversation |
| `Stop` | Session end | Final checkpoint |

### Protection layers summary

| Layer | Trigger | What it does |
|-------|---------|-------------|
| **Hooks** | Every Task/TaskCreate/TaskUpdate, PreCompact, PostCompact, Stop | Instant checkpoint to disk |
| **Guard (checkpoint)** | Every N seconds | Extract team state + config.json, write checkpoint |
| **Guard (soft prune)** | At soft threshold (default 60% of hard) | Gentle prune, no reload, no disruption |
| **Guard (hard prune)** | At hard threshold | Full prune + team-protect + optional reload |
| **Guard (reactive)** | Sub-second file watcher (kqueue/polling) | Detect inbox-flood overflow → escalating prune → kill → resume |
| **Reload** | Manual (`cozempic reload`) | One-shot prune + auto-resume |
| **Checkpoint** | Manual (`cozempic checkpoint`) | One-shot state save |

## Reload — Treat + Auto-Resume

Prune the current session and automatically resume Claude in a new terminal:

```bash
cozempic reload -rx gentle
```

This:
1. Treats the current session with the chosen prescription
2. Generates a compact recap of the conversation
3. Spawns a watcher that waits for Claude to exit
4. When you type `/exit`, a new terminal opens with `claude --resume`
5. The recap is displayed before the resume prompt

## Doctor

Beyond context cleaning, Cozempic can check for known Claude Code configuration issues:

```bash
cozempic doctor        # Diagnose issues
cozempic doctor --fix  # Auto-fix where possible
```

Current checks:

| Check | What It Detects | Auto-Fix |
|-------|----------------|----------|
| `trust-dialog-hang` | `hasTrustDialogAccepted=true` causing resume hangs on Windows | Reset flag |
| `claude-json-corruption` | Truncated JSON, missing auth, corruption cascades from concurrent sessions ([#28847](https://github.com/anthropics/claude-code/issues/28847)) | Restore from backup |
| `corrupted-tool-use` | `tool_use.name` >200 chars from serialization bugs ([#25812](https://github.com/anthropics/claude-code/issues/25812)) | Parse and repair |
| `orphaned-tool-results` | `tool_result` blocks missing their matching `tool_use` — causes 400 errors on compact/resume | Strip orphans |
| `zombie-teams` | Stale team directories with idle/dead agents ([#29908](https://github.com/anthropics/claude-code/issues/29908)) | Remove stale dirs |
| `oversized-sessions` | Session files >50MB likely to hang on resume | — |
| `stale-backups` | Old `.jsonl.bak` files from previous treatments wasting disk | Delete old backups |
| `disk-usage` | Total session storage exceeding healthy thresholds | — |

The `--fix` flag auto-applies all available fixes with backups created before any modification.

## Claude Code Integration

### Plugin (Recommended)

Cozempic ships as a Claude Code plugin with skills, hooks, and an MCP server. The plugin gives Claude direct access to cozempic tools — it can diagnose context pressure, run treatments, and monitor sessions without you running CLI commands.

Install from the repo:

```bash
git clone https://github.com/Ruya-AI/cozempic.git
claude --plugin-dir ./cozempic/plugin
```

The plugin registers:

**Skills** — invoke from the chat or let Claude trigger automatically:

| Skill | Description |
|-------|-------------|
| `/cozempic:diagnose` | Analyze session bloat, token count, context % |
| `/cozempic:treat [rx]` | Prune session with gentle/standard/aggressive |
| `/cozempic:reload [rx]` | Treat + auto-resume in new terminal |
| `/cozempic:guard` | Start background sentinel daemon |
| `/cozempic:doctor` | Run health checks |

**MCP Tools** — Claude can call these directly as tool use:

| Tool | What It Does |
|------|-------------|
| `diagnose_current` | Full session diagnosis with token counts and bloat breakdown |
| `estimate_tokens` | Quick token count + context % (reads only the tail of the file) |
| `list_sessions` | All sessions with sizes and token estimates |
| `treat_session` | Dry-run or apply a prescription |
| `list_strategies` | Available strategies and prescriptions |

**Hooks** — auto-registered when the plugin is enabled:

| Event | Action |
|-------|--------|
| `SessionStart` | Start guard daemon in background |
| `PostToolUse` (Task/TaskCreate/TaskUpdate) | Checkpoint agent team state |
| `PreCompact` | Emergency checkpoint before compaction |
| `PostCompact` | Re-inject team state after compaction |
| `Stop` | Final checkpoint on session end |

The MCP server requires `fastmcp` (installed automatically via `uv`). See [plugin/README.md](plugin/README.md) for details.

### Standalone MCP Server

To use the MCP tools without the full plugin, add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "cozempic": {
      "command": "uv",
      "args": [
        "run",
        "--with", "fastmcp",
        "--with", "cozempic",
        "python",
        "path/to/plugin/servers/cozempic_mcp.py"
      ]
    }
  }
}
```

### Slash Command

Cozempic also ships with a `/cozempic` slash command that's automatically installed to `~/.claude/commands/` when you run `cozempic init`. It works in any Claude Code project without the plugin.

Type `/cozempic` in any session to get an interactive menu:

1. **Diagnose** — Analyze bloat sources and recommend a prescription (read-only, no changes)
2. **Treat & Reload** (Recommended) — Diagnose, prune session, and auto-open a new terminal with clean context
3. **Treat Only** — Diagnose and prune session in-place (you resume manually with `claude --resume`)
4. **Guard Mode** — Start a background sentinel that auto-prunes before compaction kills agent teams

You can also skip the menu with arguments: `/cozempic diagnose`, `/cozempic treat`, `/cozempic guard`, or `/cozempic doctor`.

The slash command is kept up-to-date — running `cozempic init` again after upgrading will update it if a newer version is available.

### SessionStart Hook (Optional)

To persist the session ID as an environment variable for use in scripts and other hooks:

```bash
cp .claude/hooks/persist-session-id.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/persist-session-id.sh
```

Add to your `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/persist-session-id.sh"
      }]
    }]
  }
}
```

This makes `$CLAUDE_SESSION_ID` available in all Bash commands during the session.

## Safety

- **Always dry-run by default** — `--execute` flag required to modify files
- **Atomic file writes** — all session writes go through `write → fsync → os.replace()`: no partial writes, no corruption on crash or interrupt
- **Strict session resolution** — `--execute`, `reload`, and guard auto-detect refuse to act when the active session is ambiguous (multiple candidates, no clear match); they error out rather than guess
- **Timestamped backups** — automatic `.jsonl.bak` files before any modification
- **Never touches uuid/parentUuid** — conversation DAG stays intact
- **Never removes summary/queue-operation messages** — structurally important
- **Team messages are protected** — guard and checkpoint never prune Task, TaskCreate, TaskUpdate, TeamCreate, or SendMessage tool calls
- **task-notification results preserved** — agent completion results (the actual output) are captured and checkpointed
- **Strategies compose sequentially** — each runs on the output of the previous, so savings are accurate and don't overlap

## Example Output

```
  Prescription: aggressive
  Before: 158.2K tokens (29.56MB, 6602 messages)
  After:  121.5K tokens (23.09MB, 5073 messages)
  Freed:  36.7K tokens (23.2%) — 6.47MB, 1529 removed, 4038 modified
  Context: [============--------] 61%

  Strategy Results:
    progress-collapse              1.63MB saved  (5.5%)  (1525 removed)
    file-history-dedup              2.0KB saved  (0.0%)  (4 removed)
    metadata-strip                693.9KB saved  (2.3%)  (2735 modified)
    thinking-blocks                 1.11MB saved  (3.8%)  (1127 modified)
    tool-output-trim               1.72MB saved  (5.8%)  (167 modified)
    stale-reads                   710.0KB saved  (2.3%)  (176 modified)
    system-reminder-dedup          27.6KB saved  (0.1%)  (92 modified)
    envelope-strip                509.2KB saved  (1.7%)  (4657 modified)
```

Diagnosis output:

```
  Patient: abc123
  Weight:  1.01MB (223 messages)
  Tokens:  83.0K (exact)
  Context: [========------------] 42%

  Vital Signs:
    Progress ticks:         41
    File history snaps:      8
    ...
```

The `list` command also shows a Tokens column for every session:

```
  Session ID                                     Size   Tokens Messages Modified             Project
  ──────────────────────────────────────── ────────── ──────── ──────── ────────────────────
  38cbd1d7-d465-456e-892b-61c7d70725ab        43.88MB   143.2K      828 2026-01-23 20:28     ...
  547f8a35-8162-4bb0-b727-1fe2f6452c07        12.79MB   152.0K     5102 2026-02-16 20:51     ...
```

## Contributing

Contributions welcome. To add a strategy:

1. Create a function in the appropriate tier file under `src/cozempic/strategies/`
2. Decorate with `@strategy(name, description, tier, expected_savings)`
3. Return a `StrategyResult` with a list of `PruneAction`s
4. Add to the appropriate prescription in `src/cozempic/registry.py`

```python
from cozempic.registry import strategy
from cozempic.types import Message, PruneAction, StrategyResult

@strategy("my-strategy", "What it does", "standard", "1-5%")
def my_strategy(messages: list[Message], config: dict) -> StrategyResult:
    actions = []
    # ... analyze messages, build PruneAction list ...
    return StrategyResult(
        strategy_name="my-strategy",
        actions=actions,
        # ...
    )
```

## Known Limitations

### Extended thinking content is invisible

Claude's extended thinking blocks appear in session JSONL with empty content (`"thinking": ""`). The actual thinking content is processed server-side and not stored in the session file. Cozempic can trim thinking block signatures but cannot access or prune the thinking content itself.

### System overhead varies by setup

The default system overhead estimate (21K tokens) is calibrated for standard setups. Heavy configurations (many MCP servers, large CLAUDE.md, 10+ rules files) can use 35–50K tokens of overhead. Override with:

```bash
export COZEMPIC_SYSTEM_OVERHEAD_TOKENS=40000
```

### 1M context window support

Cozempic auto-detects 1M context windows via model ID or the `--context-window` flag. Token thresholds scale automatically. If auto-detection doesn't work, override manually:

```bash
export COZEMPIC_CONTEXT_WINDOW=1000000
```

## License

MIT - see [LICENSE](LICENSE).

Built by [Ruya AI](https://ruya.ai).
