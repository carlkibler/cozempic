# Behavioral Digest — Feature Design

**Status: Design / Not Yet Built**
**Date: 2026-03-30 (updated 2026-03-31)**
**Project: Cozempic**

---

## Overview

The behavioral digest is a mechanism to extract behavioral corrections, rules, gates, and project-specific facts from the session JSONL and re-inject them at the high-attention tail position — surviving compaction, Cozempic treatments, and session resumption.

This transforms Cozempic from "context pruning tool" to "session continuity platform."

---

## The Problem

### What Survives Compaction

The following survive Claude Code's compaction intact:

- **CLAUDE.md** — re-read from disk and re-injected every session start/resume
- **MCP tool schemas** — part of the system prompt, rebuilt every API call
- **Skills (tool definitions)** — schema always re-injected
- **Files on disk** — never touched by compaction

### What Gets Lost in Compaction

The following exist only in conversation history and are summarized away:

- **Mid-session behavioral corrections** — "don't do X" only exists in the turn where the user said it
- **Repeated failure patterns** — "I told you 3 times" has no representation in a compaction summary
- **Session-specific learned facts** — things the user taught Claude mid-session that are not in any file
- **Team/agent state** — currently protected by Cozempic's guard, but behavioral state is not

### The Compaction Summary Problem

Claude writes its own compaction summary. The summary prioritizes **what happened** (code changes, decisions made) not **how to behave** (corrections, rules, gates). The behavioral signal is discarded — it has no representation in the compressed context.

### The Attention Position Problem

Per the Liu et al. (2023) "Lost in the Middle" research:

- U-curve: 20-30 percentage point accuracy drop for information placed in the middle of context
- CLAUDE.md and memory files re-inject at **head** — lowest attention position as window fills
- Information at **tail** (most recent) gets highest attention weight
- After compaction the attention cycle resets, but repeats. The head position problem is structural, not a bug.

### The Instruction Density Problem

Per IFScale (2025) and related research:

- At 500 instructions, best models achieve only 68% compliance
- 30 irrelevant rules causes measurable degradation of target rule adherence
- Too many instructions → all ignored uniformly (flat degradation, not selective)
- **Implication: Minimum viable ruleset is a hard requirement, not a nice-to-have**

### The Cycle Every Power User Experiences

```
Session start → corrections accumulate mid-session
→ compaction discards behavioral signal
→ same mistakes repeat in next session
→ user re-teaches same corrections
→ repeat
```

This is the dominant complaint in Claude Code power user communities. It's not a context size problem — it's a behavioral state persistence problem.

---

## Research Foundation

### Reflexion (Shinn et al., 2023 — NeurIPS)

**Verbal Reinforcement Learning**: Instead of gradient updates, reflections are stored in natural language and passed as context. Key architecture:

- **Short-term memory**: current context window (episodic, current attempt)
- **Long-term memory**: external file store of accumulated reflections (semantic, persists across attempts)
- **Reflection trigger**: failure signal (wrong answer, timeout, environment feedback)
- **Reflection format**: natural language observation + specific diagnosis of what went wrong + what to try instead
- **Injection strategy**: reflections prepended to next attempt's context

**What works from Reflexion for behavioral digest:**
- External storage (file-based, not in-context) for persistence
- Failure-triggered reflection, not continuous monitoring
- Natural language format outperforms structured/formal representations
- Reflections accumulate and are re-injected; they don't replace each other

**What doesn't directly apply:**
- Reflexion fires after each full episode (agent attempt) — behavioral digest fires asynchronously based on correction signals in ongoing conversation
- Reflexion has explicit task completion signal — behavioral digest must infer correction signals

### ExpeL (Zhao et al., 2023 — AAAI)

**Experiential Learning with insight accumulation**. Key contribution: a principled voting system for managing insight quality over time.

**The four operations on an insight set:**

| Operation | Meaning | Effect on importance count |
|-----------|---------|---------------------------|
| ADD | New insight extracted | Initialized to +1 |
| EDIT | Refine/update existing | Preserves count, updates content |
| UPVOTE | Agree with existing | +1 |
| DOWNVOTE | Disagree / obsolete | −1 |
| (auto) | Count reaches 0 | Removed from set |

**What works for behavioral digest:**
- UPVOTE/DOWNVOTE model maps perfectly to correction recurrence (same correction = upvote, user says "this is no longer needed" = downvote)
- EDIT for when a rule needs refinement not replacement
- Importance-count-based pruning prevents bloat without losing durable rules

### AutoRefine (2025)

**Dual-form expertise**: different extraction for different knowledge types:

1. **Static knowledge** → "skill patterns as guidelines" (what the behavioral digest captures)
2. **Procedural subtasks** → specialized subagents with independent reasoning (out of scope for behavioral digest v1)

**Continuous maintenance**: scores, prunes, and merges patterns. This is the "contract maintenance" pass — don't just accumulate, actively prune stale/redundant rules.

### Reflexion (Shinn et al., 2023 — NeurIPS) — Supplementary Findings

**Exact reflection generation prompt** (from AlfWorld codebase):

```
You will be given the history of a past experience in which you were placed in an environment
and given a task to complete. You were unsuccessful in completing the task. Do not summarize
your environment, but rather think about the strategy and path you took to attempt to complete
the task. Devise a concise, new plan of action that accounts for your mistake with reference
to specific actions that you should have taken. For example, if you tried A and B but forgot C,
then devise a plan to achieve C with environment-specific actions. You will need this later
when you are solving the same task. Give your plan after "Plan".
```

Key design choices:
- "Do not summarize your environment" — forces strategic rather than descriptive reflection
- "Specific actions" — grounds the reflection in executable steps
- "You will need this later" — reflection is explicitly written as a forward-looking instruction to future self
- Two-shot prompted with domain-specific examples

**Memory bounds:** Long-term memory capped at Ω = 1-3 reflections. Sliding window (oldest drops when cap hit). No priority weighting — pure recency. The paper uses `Trial #0: [reflection] Trial #1: [reflection]` format with explicit numbering.

**Three-part structure of effective reflections** (from verbatim examples across 4 domains):
1. **Diagnosis** — what I attempted / what went wrong
2. **Causal attribution** — why it failed (wrong assumption, wrong order, missing step)
3. **Corrective plan** — exactly what to do differently next time

**Critical prompt framing finding** (Huang et al. 2406.10400 — direct implication for extraction):
- Prompt framing as "identify mistakes" → false positive rate 17%+ (correct answers flagged wrong)
- Prompt framing as "verify correctness" → false positive rate 0.3%-3.1%
- **Lesson**: Extraction prompt must say "verify and suggest alternatives", NOT "find mistakes"

**Self-Contrast (2401.02009) — before/after pairs outperform introspection:**
- Generating explicit before/after contrast ("I did X, correction was Y, rule is Z") is more reliable than asking the model to introspect on why X was wrong
- Even contrasting two wrong answers produces better diagnostic signal than single-answer critique
- Reduces "toxic reflections" by 78.9%, invalid reflections by 30.8%
- Direct implication: store the before/after explicitly in rule records, not just the distilled rule

**ERL (Experiential Reflective Learning, 2603.24639) — transferable rule format:**
- Reflexion-style reflections are task-specific; ERL extracts reusable heuristics that transfer across tasks
- Stored as (trigger condition, learned guideline) pairs, not just narrative text
- Scoring per heuristic for relevance before injection → don't inject all rules every time

**ABC (Agent Behavioral Contracts, 2602.22302) — hard/soft distinction:**
- Hard rules (I_hard/G_hard): zero-tolerance, must hold at every step — inject always
- Soft rules (I_soft/G_soft): recoverable, can be violated transiently — inject by relevance
- Recovery mechanism (R): maps (violated constraint, current state) → corrective actions
- Ablation finding: "Recovery and soft constraints are the dominant contributors" — the mechanism for handling recoverable violations matters more than hard constraints alone

**Memory density finding** (Mason et al. 2405.06682):
- More information in the stored reflection = better next-attempt performance
- Accuracy improvement by reflection type: retry (none) +4.1%, instructions only +6.3%, explanation only +9.0%, full solution +13.9%, composite (all) +14.6%
- Include incorrect action + correct action + reasoning in each rule record

---

### Constitutional AI (Bai et al., 2022 — Anthropic)

**Critical negative finding for behavioral digest design:**

- CAI is **training-time only** — principles shape model weights, NOT runtime context
- The CAI "constitution" is a list of ~16 principles sampled stochastically for self-critique, not injected as context
- **There is NO hierarchy among principles** — stochastic sampling means no priority ordering emerges
- Direct implication: don't design behavioral contracts that try to replicate CAI at runtime — it doesn't work

**What DOES apply:**
- Principle format: short, specific, natural language — NOT abstract values ("be helpful") but concrete directives ("do not add code comments to lines you did not change")
- Critique prompting structure: the self-critique pattern (did this response violate any rule?) can be used as the extraction trigger
- Scope: only universally applicable rules make good contract entries — task-specific guidance doesn't generalize

### Lost in the Middle + Instruction Following Research

**Quantified findings that directly shape design:**

| Finding | Design Implication |
|---------|-------------------|
| U-curve: 20-30pt drop at middle positions | Inject contract at **tail**, not head |
| 40-50% context capacity cliff (−45.5% F1) | Guard should prompt contract extraction at 40%, not 95% |
| 54% constraint loss after 3 compactions | Post-compaction re-injection is non-optional |
| 39% multi-turn degradation average | Corrections mid-session fight a stable attractor — need explicit reinforcement |
| "Helpful rules" followed at 14% vs "harmless rules" at 99% | **Reframe rules as prohibitions, not aspirations** |
| 30 irrelevant rules degrades target rule adherence | Minimum viable ruleset — actively prune |
| 500 instructions → best model 68% compliance | Hard cap: keep contract ≤ 20 rules |
| NL rules >> formal logic | Always natural language, never structured schemas |
| Reminder injections reduce drift attractor by 14% | Periodic re-injection (not just at session start) has measurable effect |

**The "reframe as prohibitions" finding is especially important:**
- "Be concise" → 14% compliance
- "Do not add unnecessary text to your response" → much higher compliance
- All contract entries should be phrased as "do not" / "never" / "always" (absolute) rather than aspirational qualities

---

## Architecture

### Contract Storage

File: `~/.cozempic/behavioral-digest.md` (user-global) or `.cozempic/behavioral-digest.md` (project-local, takes precedence)

Project-local is almost always what you want — behavioral corrections are project-specific.

Format:

```markdown
# Behavioral Digest
<!-- cozempic: behavioral-digest v1 -->
<!-- project: /path/to/project -->
<!-- updated: 2026-03-31T14:23:00Z -->
<!-- session: <session_id_that_last_updated> -->

## Active Rules — Hard (always injected)

### [R001] No Co-Authored-By commits
**Priority**: HARD
**Source**: User correction (3 occurrences)
**First seen**: 2026-03-15 | **Last reinforced**: 2026-03-30
**Importance**: 5 (3 corrections + 2 upvotes)
**Scope**: git operations
**Rule**: Do not add "Co-Authored-By" lines to git commit messages.
**Before**: Added `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>` to commit
**After**: User deleted that line from the commit message
**Signal**: I added Co-Authored-By because default behavior includes it; user treats this project's commits as single-author.
**Trigger**: Any git commit operation

## Active Rules — Soft (injected by scope relevance)

### [R002] Edit not Write for existing files
**Priority**: SOFT
**Source**: User correction (2 occurrences)
**First seen**: 2026-03-20 | **Last reinforced**: 2026-03-28
**Importance**: 2
**Scope**: file operations
**Rule**: Do not use the Write tool to modify existing files — use Edit. Only use Write for new files.
**Before**: Used Write tool to replace entire file content
**After**: User said "use Edit not Write"
**Signal**: I used Write because the change was large; user prefers surgical edits.
**Trigger**: Any file modification operation on existing files

## Pending Rules (< 3 occurrences, under observation)

### [P001] Prefer uv over pip
**Priority**: SOFT (pending)
**Source**: User correction (1 occurrence)
**First seen**: 2026-03-31
**Importance**: 1
**Rule**: Use uv instead of pip for package operations in this project.
**Before**: Suggested `pip install ...`
**After**: User said "we use uv"
**Trigger**: Any package installation or management command
```

**Key format decisions (research-backed):**

1. **Prohibition framing** — all rules written as "Do not" / "Never" / "Always" (compliance 14% → ~99%)
2. **Hard/soft classification** (ABC paper) — hard rules always injected; soft rules injected by scope relevance
3. **Before/after contrast** (Self-Contrast paper) — storing the explicit before/after reduces false positives and makes the rule's scope unambiguous
4. **Signal tracing** — the specific misinterpreted signal is stored, not just the correction category
5. **Importance count** — ExpeL-derived voting system, not simple recurrence count
6. **Pending section** — rules < 3 occurrences are quarantined until promoted
7. **Scope + trigger tag** — limits when a rule is injected (only inject git rules in git-related turns)

---

### Extraction Architecture

#### When to Extract (Triggers)

**Hard triggers (always extract immediately):**
- First correction detected (fast-path — don't wait for counter)
- User repeats a correction they've made before (pattern match → upvote existing rule)
- PreCompact hook fires (extract from pre-compaction window before it's gone)

**Soft trigger (batch extraction):**
- Every N substantive user turns (> 150 chars), where N = 10-15
- Only on turns since last extraction delta

**Why NOT every turn:**
- `additionalContext` stored in JSONL, 1500 tokens × every turn = massive bloat
- Injection only matters at session start and post-compaction (when active session memory resets)
- Claude Code reads session file once at startup — mid-session JSONL writes have no effect on active session

**MAPLE (arXiv:2602.13258) architecture confirmation**: Async extraction (background after session or trigger event) prevents extraction quality from degrading response quality. Inline extraction during conversation response creates latency and competing attention. The right architecture is always: flag inline (cheap), extract async (expensive).

#### What to Extract (LLM Semantic Extraction)

**No keyword heuristics for WHAT.** Heuristics only for WHEN to trigger. Full LLM understanding for WHAT constitutes a correction.

##### Step 1 — Classification (per-turn, inline)

First classify each user turn using the FELT taxonomy (arXiv:2307.00279) + conversational repair theory:

| Class | Description | Rule candidacy |
|-------|-------------|---------------|
| `EXPLICIT_CORRECTION` | User directly negates / substitutes / meta-comments ("don't", "that's wrong", rewrites output) | HIGH |
| `IMPLICIT_CORRECTION` | User rephrases with added constraints, completes task themselves, abandons + restarts | MEDIUM (needs frequency) |
| `PREFERENCE` | Proactive declaration, no prior agent failure ("I prefer prose over bullets") | HIGH |
| `CLARIFICATION` | Resolves ambiguity about current task, agent asked for it (FELT "Peripheral" type) | ZERO — session-scoped only |
| `ONE_OFF` | Scoped to this specific task ("just this once", "for this response") | ZERO — never promote |
| `NONE` | Normal conversational turn, no feedback signal | Skip |

Only `EXPLICIT_CORRECTION`, `IMPLICIT_CORRECTION`, and `PREFERENCE` enter the extraction pipeline.

**Critical finding from A-MAC (arXiv:2603.04549)**: **Type classification is the single most important factor** for memory admission quality. Removing the Type Prior from A-MAC's composite score caused the largest performance drop of any ablation. Determine the type first, before any other scoring.

##### Step 2 — Extraction Prompt (per correction, applied to 3-turn context window)

> **Critical framing note** (Huang et al. 2406.10400): "Identify mistakes" framing produces 17%+ false positives (correct behavior flagged as wrong). "Verify and suggest alternatives" framing produces 0.3%-3.1% false positives. The prompt below uses verify/suggest framing.

```
You are verifying whether a user correction in this conversation warrants a persistent behavioral rule.

CONTEXT (3 turns before the correction):
[context]

FLAGGED TURN (classified as: EXPLICIT_CORRECTION / IMPLICIT_CORRECTION / PREFERENCE):
[turn text]

Step 1 — Verify persistence: Would this apply to other similar situations in this project?
- Yes → proceed to extraction
- No (task-specific only) → output {"persist": false}
- Already in CLAUDE.md or project files → output {"persist": false}

Step 2 — Extract (if persistent):
- VIOLATED_BEHAVIOR: What the assistant did that was wrong (one sentence, specific)
- DESIRED_BEHAVIOR: What the user wants instead (one sentence, specific)
- SIGNAL: Why the assistant likely made this error — what signal did it misread? (one sentence, specific)
- TRIGGER: Under what conditions does this apply? (specific situation, not "always")
- RULE: "Do not [X]" or "When [trigger], always [Y]" (one sentence, prohibition framing)
- PRIORITY: "hard" (user expressed as zero-tolerance) or "soft" (preference, context-dependent)
- SCOPE: git | file-ops | testing | communication | architecture | general
- CONFIDENCE: 0.0-1.0 (1.0 = explicit unambiguous correction, 0.3 = implicit/inferred)
- EVIDENCE: Direct quote from the conversation supporting this extraction

Output as JSON object.
```

##### Step 3 — Synthesis (when 3+ corrections match same pattern)

When the same pattern appears 3+ times, run synthesis to generalize to a single rule:

```
The following [N] corrections all match the pattern: "[PATTERN_NAME]"

[List of N corrections with their VIOLATED_BEHAVIOR, DESIRED_BEHAVIOR, SIGNAL, TRIGGER]

Synthesize into ONE behavioral rule that:
1. Covers all [N] cases above
2. Has a specific, recognizable trigger condition
3. States a concrete, unambiguous action (prohibition framing)
4. Is no more than 2 sentences
5. Does NOT overgeneralize beyond what the evidence supports

SYNTHESIZED_RULE:
TRIGGER: "When [specific condition]..."
ACTION: "Do not [X] / Always [Y]"
SCOPE:
COVERS_ALL_CASES: [yes/no + explanation if no]
```

##### Step 4 — Conflict Detection (before committing new rule)

```
Existing rule: "[RULE_A]"
New rule candidate: "[RULE_B]"

Relationship: COMPLEMENTARY | REDUNDANT | CONTRADICTORY | ORTHOGONAL

- COMPLEMENTARY → write merged rule maintaining trigger-action format
- REDUNDANT → keep the more specific one
- CONTRADICTORY → flag for human review, keep both marked CONFLICTED
- ORTHOGONAL → keep both as separate rules

Decision: [MERGE/KEEP_A/KEEP_B/CONFLICT]
Merged rule (if applicable):
```

##### Step 5 — Pre-commit Validation (DeCRIM pattern, arXiv:2410.06458)

Before writing to the contract file, validate:
1. **Decompose**: Is this rule covering exactly one behavioral concern?
2. **Critique**: Is the trigger specific enough to recognize? Is the action unambiguous? Does it conflict with existing rules?
3. **Refine**: Improve based on critique

Only rules that pass all three stages get committed.

#### When to Inject (Event-Driven Only)

**Injection events:**
1. **SessionStart hook** — inject full active rules at tail (most recent assistant turn position)
2. **PostCompact hook** — re-inject immediately after compaction resets the window
3. **Contract update detected** — mtime check: if `behavioral-digest.md` mtime > last injection timestamp, inject delta
4. **PreCompact hook** — extract from window before compaction destroys it (write-only, no injection)

**Never inject at every turn.** This creates bloat and negates the purpose of Cozempic.

#### How to Inject (Tail Position)

Injection appends a synthetic `user` turn at the tail of the JSONL.

**What gets injected:**
- All hard rules (always)
- Soft rules matching the current session scope (ERL-style relevance filtering)

At SessionStart, all hard + all soft rules are injected (full contract, scope unknown). At PostCompact, same. At contract-update events, only the delta (new/changed rules) is injected.

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": "<!-- __cozempic_behavioral_digest__: true -->\n[BEHAVIORAL CONTRACT]\n\n**Always apply:**\nDo not add Co-Authored-By lines to git commit messages.\n\n**Apply when working with files:**\nDo not use the Write tool to modify existing files — use Edit.\n\n[End of behavioral contract — Cozempic v1.x]"
  }
}
```

**The `__cozempic_behavioral_digest__: true` tag** is the protection marker — every Cozempic strategy checks for this and never strips or modifies these messages.

**Tail injection rationale**: Lost in the Middle research shows tail position gets highest attention weight. Rules injected at the tail survive context growth without attention degradation until the next compaction cycle.

**Explicit goal elicitation** (arXiv:2505.02709 finding): Prefixing the injected block with "Focus solely on these behavioral rules when applicable" substantially reduces drift. This 7-word addition is cheap and measurably effective.

---

### Contract Maintenance

The contract must not just accumulate — it must prune. This is the AutoRefine maintenance model.

**Promotion rules:**
- Pending → Active at 3 occurrences (3+ appearances = clear behavioral pattern)
- First occurrence → immediate fast-path: mark as pending, may inject once if HIGH confidence

**Composite confidence scoring (A-MAC, arXiv:2603.04549):**

```
confidence = w1 * (evidence_count / 3)           // normalize to promotion threshold
           + w2 * source_reliability              // 1.0 explicit, 0.6 implicit, 0.3 inferred
           + w3 * recency_decay                   // exp(-λ * days_since_last_confirmation)
           + w4 * type_prior                      // correction=0.8, preference=0.9, one-off=0.1
```

Admission threshold: 0.55 (from A-MAC). Rules below threshold stay in pending.

**Importance scoring (ExpeL voting model):**
- +1 per correction occurrence (ADD → promotes evidence_count)
- +1 per explicit user reinforcement (UPVOTE)
- EDIT updates content, preserves count
- −1 per "this is no longer needed" signal (DOWNVOTE)
- Remove from pending at 0, warn before removing from active

**Pruning rules:**
- Active rules with importance ≤ 0 → delete
- Active rules not seen in 30+ days and importance ≤ 1 → move to pending
- Duplicates (semantic similarity > 0.85) → merge, keep higher importance

**Hard cap**: 20 active rules maximum. At 20 rules, any new rule promotion requires either:
- Removing the lowest-importance existing rule, OR
- Merging with the most similar existing rule

This enforces the minimum viable ruleset principle (IFScale: 30 irrelevant rules degrades target adherence).

---

### Agent Team vs. Subagent vs. Heuristic Extraction

**When Agent Teams are available (cozempic is enabled at team level):**
- Spawn persistent behavioral digest agent alongside the main session agent
- Agent monitors the JSONL delta in real-time (polling the file)
- Extracts corrections as they happen, updates contract immediately
- Advantage: real-time, no batching delay, access to full conversation context per extraction

**When only subagents are available:**
- Fire a one-shot subagent on trigger events (N substantive turns reached, first correction fast-path)
- Subagent receives ONLY the delta (turns since last extraction)
- Returns structured JSON of extracted rules
- No persistent state needed — rule state lives in the contract file

**When neither is available (fallback heuristics for WHEN, LLM for WHAT):**
- WHEN heuristic: detect high-probability correction signals
  - User turn > 150 chars that starts with: "no", "don't", "stop", "please don't", "actually", "wait", "you should", "I said"
  - User rewrites a code block the assistant just produced
  - User explicitly negates the previous assistant turn
- WHAT: still use full LLM extraction prompt (never keyword extraction for content)

**Decision flow:**
```
Agent Teams available?
  ├─ Yes → Persistent monitoring agent (real-time extraction)
  └─ No → Subagents available?
            ├─ Yes → One-shot subagent on trigger events
            └─ No → Heuristic trigger + LLM extraction
```

---

## Contract Format Rationale (Research Summary)

| Design Choice | Research Basis |
|--------------|---------------|
| Prohibition framing ("Do not...") | "Helpful" rules 14% compliance vs "harmless" rules 99% (instruction-following research) |
| Hard/soft rule classification | ABC (2602.22302): hard = zero-tolerance always injected; soft = preference, inject by relevance |
| Before/after contrast in each rule | Self-Contrast (2401.02009): explicit contrast reduces false positives 78.9%, invalid reflections 30.8% |
| Tail injection position | U-curve: 20-30pt accuracy drop for middle positions (Lost in the Middle) |
| ≤ 20 rule hard cap | IFScale: 30 irrelevant rules degrades target adherence; 500 instructions → 68% compliance |
| NL format, not structured | NL rules >> formal logic (cross-study finding) |
| External file storage | Reflexion: short-term (in-context) + long-term (external file) separation |
| Importance voting | ExpeL: ADD/EDIT/UPVOTE/DOWNVOTE with importance count → auto-prune at 0 |
| Verify/suggest extraction framing | Huang et al. (2406.10400): "find mistakes" → 17% false positive; "verify + suggest" → 0.3%-3.1% |
| 5-class turn classification (FELT) | FELT taxonomy (2307.00279): "Peripheral" = clarification (zero candidacy); "Procedural" = rule candidate |
| Type Prior first in scoring | A-MAC (2603.04549): type classification is the single most important admission factor by ablation |
| A-MAC composite confidence formula | Multi-signal: evidence count + source reliability + recency decay + type prior; threshold 0.55 |
| Async extraction (not inline) | MAPLE (2602.13258): inline extraction degrades response quality; flag inline, extract async |
| 3-occurrence promotion threshold | Signal tracing + Roryteehan: patterns at 3+ "carry more behavioral weight than static instructions" |
| Signal tracing per rule | Specific misinterpreted signal stored — enables diagnosis not just prevention (Roryteehan) |
| Trigger condition per rule | ERL (2603.24639): (trigger, guideline) pairs transfer across tasks; pure text rules don't |
| Synthesis at threshold (HtT) | HtT (2310.07064): induction from multiple corrections → generalizable rule; +10-30% accuracy gain |
| Pre-commit DeCRIM validation | Decompose-critique-refine (2410.06458) before committing any extracted rule |
| Mem0 ADD/UPDATE/DELETE/NOOP | Merge strategy for new vs. existing rules — LLM-powered operation selection |
| Pending/active separation | Rules < 3 occurrences quarantined to prevent premature injection of uncertain patterns |
| Event-driven injection only | In-context injection at every turn creates bloat; JSONL writes don't affect active sessions mid-session |
| Post-compaction injection | 54% constraint loss after 3 compactions (instruction persistence research) — must re-inject |
| PreCompact extraction | Capture corrections before compaction destroys the delta (behavioral content systematically lost) |
| Explicit goal elicitation prefix | arXiv:2505.02709: "focus solely on assigned goal" substantially reduces drift in all models |
| Protection tag in JSONL | Prevents Cozempic strategies from stripping behavioral digest messages during treatment |

---

## Open Problems

### 1. Active Session Write Limitation

Claude Code reads the JSONL once at startup. Writing to the JSONL mid-session has no effect on Claude's active memory. This means behavioral digest injection only works at:
- Session start (new session picks up the written digest)
- Post-compaction (compaction re-reads the JSONL)

There is no way to inject mid-session without using `additionalContext` (which creates per-turn bloat).

**Potential mitigation**: CLAUDE.md writes DO take effect mid-session IF Claude re-reads it. Writing extracted rules to CLAUDE.md or a `.cozempic/behavioral-digest.md` that is referenced from CLAUDE.md could provide mid-session injection at the cost of polluting CLAUDE.md.

### 2. Cross-Session Rule Validity

Rules extracted in session A may be project-specific, user-preference, or session-artifact. The distinction matters:

- **Project-specific** → should persist indefinitely, project-scoped
- **User preference** → should persist across projects, user-scoped
- **Session artifact** → should NOT persist (e.g., "use this temporary variable name")

The extraction prompt must classify scope. Session-artifacts must be filtered.

### 3. Conflict Resolution

Multiple sessions can update the same contract file. Rule R001 from session A may conflict with an update from session B.

**Resolution strategy**: Last-write wins on content, merge on importance count. If two rules have semantic similarity > 0.85, merge them into a single rule with the higher of their importance counts.

### 4. The "Un-teaching" Problem

A user may correct a rule they previously established:
- Session A: "never use var in this project"
- Session B: "actually, use var for these specific cases"

The extraction prompt must detect retraction signals and DOWNVOTE or EDIT the existing rule rather than adding a conflicting new rule.

### 5. Rule Quality Degradation

As importance counts grow, high-importance rules accumulate and are never pruned. This creates "zombie rules" — rules that were once important but are now outdated.

**Mitigation**: time-based decay. Rules not reinforced in 30+ days lose 1 importance point per week. Protects against zombie rules without requiring explicit user action.

---

## Product Direction

### Phase 1: Read-only digest (extract + store, no injection)

- Extract corrections into contract file
- User can review / edit manually
- Establishes the "single source of behavioral truth" concept

### Phase 2: SessionStart injection

- Inject active rules at session start
- Simple, no hooks required beyond SessionStart
- Validates that injection actually prevents correction repetition

### Phase 3: PostCompact injection + PreCompact extraction

- Full compaction survival loop
- Requires Claude Code hooks to be set up

### Phase 4: Real-time monitoring (Agent Teams)

- Persistent monitoring agent for immediate extraction
- Project-local + user-global contract separation
- Full maintenance loop (prune/merge/decay)

---

---

## Appendix: Production Prompts

### Correction Classification Prompt (5-shot)

```
Classify this user turn in the conversation. Choose one:
EXPLICIT_CORRECTION | IMPLICIT_CORRECTION | PREFERENCE | CLARIFICATION | ONE_OFF | NONE

Rules:
- EXPLICIT_CORRECTION: User directly says agent was wrong, or provides replacement output
- IMPLICIT_CORRECTION: User rephrases with added constraints / completes task themselves
- PREFERENCE: Proactive style/format/approach declaration, no prior agent failure implied
- CLARIFICATION: Resolves current task ambiguity; agent asked or user volunteered context
- ONE_OFF: "For this response", "just this time", "in this case" → task-scoped only
- NONE: Normal turn, no feedback signal

Examples:
[A] "Don't use bullet points. I always want prose." → EXPLICIT_CORRECTION
[B] "What I mean is, I need it in markdown" → CLARIFICATION (answering agent question)
[C] "By the way, I prefer code examples over explanations" → PREFERENCE
[D] "For this one, use British spelling" → ONE_OFF
[E] User rephrases "summarize this" as "briefly summarize this in 2 sentences" → IMPLICIT_CORRECTION

Turn to classify: "[TURN]"
Classification:
Reasoning (one sentence):
Persistence: session | persistent
```

### Rule Quality Assessment Prompt

```
Evaluate this behavioral rule for quality. Score each criterion 1-5:

Rule: "[RULE_TEXT]"

TRIGGER_SPECIFICITY (1=vague, 5=precise): Can the agent reliably recognize when this applies?
ACTION_CLARITY (1=ambiguous, 5=unambiguous): Is the required action clear?
SCOPE_APPROPRIATENESS (1=too narrow/broad, 5=well-scoped): Session vs. universal correctly set?
CONFLICT_RISK (1=high risk, 5=no conflict): Does it contradict common behaviors?

OVERALL: [1-5]
REVISION_NEEDED: [yes/no]
IMPROVED_RULE:
```

---

## Cozempic Integration Points

1. **JSONL protection tag**: all strategies must skip messages with `__cozempic_behavioral_digest__: true`
2. **Guard**: extract at guard exit (final checkpoint before session ends)
3. **`cozempic treat`**: extract from session before applying treatment (capture any corrections before they're pruned)
4. **`cozempic doctor`**: add behavioral-digest health check (stale rules, zombie rules, cap warnings)
5. **MCP tool**: `get_behavioral_digest` — returns current active rules for diagnostic purposes
