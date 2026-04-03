"""Behavioral digest — extract correction signals from sessions, persist as structured rules.

Research basis: Reflexion (NeurIPS 2023), ExpeL (AAAI 2023), A-MAC (2603.04549),
Lost in the Middle, IFScale. See docs/behavioral-digest-design.md.

Phase 1: heuristic extraction + A-MAC admission gate + JSON persistence.
No injection yet (Phase 2). No LLM calls (heuristic only for Phase 1).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .helpers import get_content_blocks, get_msg_type, text_of
from .types import Message

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROTECTION_TAG = "__cozempic_behavioral_digest__"
DIGEST_DIR = Path.home() / ".cozempic"
DIGEST_FILE = DIGEST_DIR / "behavioral-digest.json"
DIGEST_MD_FILE = DIGEST_DIR / "behavioral-digest.md"

MAX_ACTIVE_RULES = 20  # IFScale: >30 irrelevant rules degrades ALL adherence
ADMISSION_THRESHOLD = 0.55  # A-MAC composite score gate
PRUNE_THRESHOLD = 0.30  # Below this → prune
PROMOTION_COUNT = 3  # Occurrences needed to promote pending → active
DECAY_DAYS = 30  # Universal decay period (MemoryArena 2602.16313)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DigestRule:
    """A single behavioral rule extracted from user corrections."""

    id: str  # R001, R002, etc.
    rule: str  # "Do not [X]" — prohibition framing
    priority: Literal["hard", "soft"] = "soft"
    scope: str = "general"  # git, file-ops, testing, communication, general
    trigger: str = ""  # When this rule applies

    # Decision attribution (Trajectory-Informed Memory 2603.10600)
    decision_step: str = ""  # Which step in agent's reasoning caused failure
    before: str = ""  # What agent did wrong
    after: str = ""  # What user wants instead

    # Evidence — stored verbatim, never paraphrased (2603.23013)
    signal: str = ""  # Why agent made the error
    evidence: str = ""  # Direct quote from conversation

    # Scoring (A-MAC 2603.04549)
    importance: int = 1  # ExpeL voting count
    source_reliability: float = 1.0  # 1.0 explicit, 0.6 implicit, 0.3 inferred
    type_prior: float = 0.8  # correction=0.8, preference=0.9, one-off=0.1

    # Lifecycle
    status: Literal["pending", "active", "conflicted"] = "pending"
    occurrence_count: int = 1
    first_seen: str = ""
    last_reinforced: str = ""
    last_injection: str | None = None


@dataclass
class DigestStore:
    """Persistent store for behavioral rules — dual memory banks (MemAPO)."""

    strategy_rules: list[DigestRule] = field(default_factory=list)
    failure_patterns: list[DigestRule] = field(default_factory=list)
    version: str = "1"
    project: str = ""
    updated: str = ""
    session_id: str = ""

    def is_empty(self) -> bool:
        return not self.strategy_rules and not self.failure_patterns

    def active_rules(self) -> list[DigestRule]:
        return [r for r in self.strategy_rules if r.status == "active"]

    def all_rules(self) -> list[DigestRule]:
        return self.strategy_rules + self.failure_patterns

    def next_id(self) -> str:
        existing = {r.id for r in self.all_rules()}
        for i in range(1, 1000):
            rid = f"R{i:03d}"
            if rid not in existing:
                return rid
        return f"R{len(self.all_rules()) + 1:03d}"


# ---------------------------------------------------------------------------
# Classification — FELT taxonomy (heuristic, no LLM)
# ---------------------------------------------------------------------------

# Correction signal patterns
_EXPLICIT_PATTERNS = [
    re.compile(r"^no[,.\s]", re.IGNORECASE),
    re.compile(r"\bdon'?t\b", re.IGNORECASE),
    re.compile(r"\bdo not\b", re.IGNORECASE),
    re.compile(r"\bstop\s+(doing|adding|using|creating)", re.IGNORECASE),
    re.compile(r"\bnever\b", re.IGNORECASE),
    re.compile(r"\bplease\s+(don'?t|remove|stop|undo)", re.IGNORECASE),
]

_IMPLICIT_PATTERNS = [
    re.compile(r"\bactually[,\s]", re.IGNORECASE),
    re.compile(r"\binstead[,\s]", re.IGNORECASE),
    re.compile(r"\brather\b", re.IGNORECASE),
    re.compile(r"\bthat'?s\s+(not|wrong)", re.IGNORECASE),
    re.compile(r"\bnot\s+what\s+I", re.IGNORECASE),
]

_PREFERENCE_PATTERNS = [
    re.compile(r"\bI\s+prefer\b", re.IGNORECASE),
    re.compile(r"\balways\s+(use|do|add|include)", re.IGNORECASE),
    re.compile(r"\bfrom\s+now\s+on\b", re.IGNORECASE),
    re.compile(r"\bremember\s+(to|that)\b", re.IGNORECASE),
]

_APOLOGY_PATTERNS = [
    re.compile(r"\bsorry\b", re.IGNORECASE),
    re.compile(r"\bI\s+apologize\b", re.IGNORECASE),
    re.compile(r"\bmy\s+(mistake|bad|error)\b", re.IGNORECASE),
]

TurnClass = Literal[
    "EXPLICIT_CORRECTION",
    "IMPLICIT_CORRECTION",
    "PREFERENCE",
    "APOLOGY_FOLLOW_UP",
    "ONE_OFF",
    "NONE",
]


def classify_turn(user_text: str, prev_assistant_text: str = "") -> TurnClass:
    """Classify a user turn by correction signal type.

    Content type prior IS the dominant factor (A-MAC ablation).
    """
    if not user_text or len(user_text.strip()) < 3:
        return "NONE"

    # Check if previous assistant apologized → this turn is a follow-up correction
    if prev_assistant_text:
        for pat in _APOLOGY_PATTERNS:
            if pat.search(prev_assistant_text):
                # User message after apology is likely a correction
                if len(user_text.strip()) > 10:
                    return "APOLOGY_FOLLOW_UP"

    # Explicit correction: strongest signal
    for pat in _EXPLICIT_PATTERNS:
        if pat.search(user_text):
            return "EXPLICIT_CORRECTION"

    # Preference: persistent behavioral instruction
    for pat in _PREFERENCE_PATTERNS:
        if pat.search(user_text):
            return "PREFERENCE"

    # Implicit correction: softer signal
    for pat in _IMPLICIT_PATTERNS:
        if pat.search(user_text):
            return "IMPLICIT_CORRECTION"

    return "NONE"


# ---------------------------------------------------------------------------
# Extraction — heuristic rule extraction from classified turns
# ---------------------------------------------------------------------------


def _get_user_text(msg: dict) -> str:
    """Extract user text from a message."""
    inner = msg.get("message", {})
    content = inner.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts)
    return ""


def _get_assistant_text(msg: dict) -> str:
    """Extract assistant text from a message."""
    blocks = get_content_blocks(msg)
    parts = []
    for block in blocks:
        t = text_of(block)
        if t and block.get("type") in ("text", None, ""):
            parts.append(t)
    return " ".join(parts)


def _infer_scope(text: str) -> str:
    """Infer the scope of a correction from its content."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ("git", "commit", "push", "branch", "merge", "co-authored")):
        return "git"
    if any(kw in text_lower for kw in ("file", "edit", "write", "read", "path", "directory")):
        return "file-ops"
    if any(kw in text_lower for kw in ("test", "pytest", "unittest", "mock", "assert")):
        return "testing"
    if any(kw in text_lower for kw in ("message", "comment", "pr ", "issue", "slack")):
        return "communication"
    return "general"


def _to_prohibition(text: str) -> str:
    """Convert a user correction into prohibition framing.

    "Don't add X" → "Do not add X"
    "Stop doing X" → "Do not do X"
    "No, use Y instead" → "Do not use the previous approach; use Y instead"
    """
    text = text.strip()
    # Already in prohibition form
    if text.lower().startswith("do not ") or text.lower().startswith("don't "):
        return text[0].upper() + text[1:]

    # "Stop doing X" → "Do not X"
    m = re.match(r"(?i)stop\s+(doing\s+|adding\s+|using\s+|creating\s+)?(.*)", text)
    if m:
        action = m.group(2).strip()
        return f"Do not {action}" if action else text

    # "Never X" → "Do not ever X"
    m = re.match(r"(?i)never\s+(.*)", text)
    if m:
        return f"Do not ever {m.group(1).strip()}"

    # "No, ..." → extract the instruction
    m = re.match(r"(?i)^no[,.\s]+\s*(.*)", text)
    if m:
        rest = m.group(1).strip()
        if rest:
            return rest[0].upper() + rest[1:]

    # Default: prefix with "Do not"
    if len(text) > 5:
        return f"Do not {text[0].lower()}{text[1:]}"
    return text


def extract_corrections(
    messages: list[Message],
    since_turn: int = 0,
) -> list[DigestRule]:
    """Extract behavioral corrections from a message window.

    Scans user turns for correction signals, builds DigestRule for each.
    Stores verbatim evidence — never paraphrased (arXiv:2603.23013).
    """
    now = datetime.now(timezone.utc).isoformat()
    rules: list[DigestRule] = []

    prev_assistant_text = ""
    for pos, (idx, msg, _) in enumerate(messages):
        if pos < since_turn:
            # Track assistant text even before our window
            if get_msg_type(msg) == "assistant":
                prev_assistant_text = _get_assistant_text(msg)
            continue

        mtype = get_msg_type(msg)

        if mtype == "assistant":
            prev_assistant_text = _get_assistant_text(msg)
            continue

        if mtype != "user":
            continue

        user_text = _get_user_text(msg)
        if not user_text:
            continue

        turn_class = classify_turn(user_text, prev_assistant_text)
        if turn_class == "NONE":
            prev_assistant_text = ""
            continue

        # Map classification to scoring
        reliability_map = {
            "EXPLICIT_CORRECTION": 1.0,
            "IMPLICIT_CORRECTION": 0.6,
            "PREFERENCE": 0.9,
            "APOLOGY_FOLLOW_UP": 0.8,
            "ONE_OFF": 0.3,
        }
        type_prior_map = {
            "EXPLICIT_CORRECTION": 0.8,
            "IMPLICIT_CORRECTION": 0.6,
            "PREFERENCE": 0.9,
            "APOLOGY_FOLLOW_UP": 0.7,
            "ONE_OFF": 0.1,
        }

        rule_text = _to_prohibition(user_text)
        scope = _infer_scope(user_text)

        rule = DigestRule(
            id="",  # Assigned on admission
            rule=rule_text[:500],  # Cap rule length
            priority="hard" if turn_class == "EXPLICIT_CORRECTION" else "soft",
            scope=scope,
            trigger="",
            before=prev_assistant_text[:200] if prev_assistant_text else "",
            after=user_text[:200],
            signal=turn_class,
            evidence=user_text[:500],  # Verbatim, never paraphrased
            importance=1,
            source_reliability=reliability_map.get(turn_class, 0.5),
            type_prior=type_prior_map.get(turn_class, 0.5),
            status="pending",
            occurrence_count=1,
            first_seen=now,
            last_reinforced=now,
        )
        rules.append(rule)

        prev_assistant_text = ""

    return rules


# ---------------------------------------------------------------------------
# Admission gate — A-MAC composite scoring (arXiv:2603.04549)
# ---------------------------------------------------------------------------


def score_rule(rule: DigestRule, days_since_last: float = 0.0) -> float:
    """Compute A-MAC composite score for a rule.

    composite = w1*(count/3) + w2*source_reliability + w3*recency + w4*type_prior
    Threshold: 0.55 for admission, 0.30 for pruning.
    """
    evidence_score = min(rule.occurrence_count / PROMOTION_COUNT, 1.0)
    recency_decay = math.exp(-0.05 * days_since_last)  # λ=0.05 → halves in ~14 days
    composite = (
        0.25 * evidence_score
        + 0.30 * rule.source_reliability
        + 0.20 * recency_decay
        + 0.25 * rule.type_prior
    )
    return round(composite, 4)


def _normalize_for_match(text: str) -> set[str]:
    """Normalize text for duplicate matching — strip stop words, lowercase."""
    _STOP = {"do", "not", "don't", "dont", "the", "a", "an", "to", "is", "it", "of", "in", "for"}
    words = set(text.lower().split())
    return words - _STOP


def _find_duplicate(new_rule: DigestRule, store: DigestStore) -> DigestRule | None:
    """Find a semantically similar existing rule (normalized word overlap for Phase 1)."""
    new_words = _normalize_for_match(new_rule.rule)
    if not new_words:
        return None
    # Also check evidence for stronger matching
    new_evidence_words = _normalize_for_match(new_rule.evidence) if new_rule.evidence else set()

    for existing in store.strategy_rules:
        existing_words = _normalize_for_match(existing.rule)
        if not existing_words:
            continue
        # Match against rule text
        overlap = len(new_words & existing_words) / max(len(new_words), len(existing_words))
        if overlap > 0.5:
            return existing
        # Also match new evidence against existing rule (user may phrase differently)
        if new_evidence_words:
            ev_overlap = len(new_evidence_words & existing_words) / max(len(new_evidence_words), len(existing_words))
            if ev_overlap > 0.5:
                return existing
    return None


def admit_rule(rule: DigestRule, store: DigestStore) -> str:
    """A-MAC admission gate. Returns action taken: 'added', 'upvoted', 'rejected'.

    Quality gate BEFORE any rule enters store (arXiv:2505.16067).
    """
    # Check for duplicate/similar existing rule
    existing = _find_duplicate(rule, store)
    if existing:
        # ExpeL UPVOTE: reinforce existing rule
        existing.occurrence_count += 1
        existing.importance += 1
        existing.last_reinforced = rule.last_reinforced or datetime.now(timezone.utc).isoformat()
        # Promote if threshold reached
        if existing.status == "pending" and existing.occurrence_count >= PROMOTION_COUNT:
            existing.status = "active"
        return "upvoted"

    # Score the new rule
    score = score_rule(rule)
    if score < ADMISSION_THRESHOLD:
        return "rejected"

    # Assign ID and add
    rule.id = store.next_id()
    store.strategy_rules.append(rule)

    # Cap enforcement
    active = store.active_rules()
    if len(active) > MAX_ACTIVE_RULES:
        # Demote lowest-scored active rule
        scored = [(score_rule(r), r) for r in active]
        scored.sort(key=lambda x: x[0])
        scored[0][1].status = "pending"

    return "added"


# ---------------------------------------------------------------------------
# Persistence — JSON on disk
# ---------------------------------------------------------------------------


def load_digest_store(project_dir: str = "") -> DigestStore:
    """Load the digest store from disk."""
    if not DIGEST_FILE.exists():
        return DigestStore(project=project_dir)
    try:
        data = json.loads(DIGEST_FILE.read_text(encoding="utf-8"))
        store = DigestStore(
            version=data.get("version", "1"),
            project=data.get("project", project_dir),
            updated=data.get("updated", ""),
            session_id=data.get("session_id", ""),
        )
        for rd in data.get("strategy_rules", []):
            store.strategy_rules.append(DigestRule(**rd))
        for rd in data.get("failure_patterns", []):
            store.failure_patterns.append(DigestRule(**rd))
        return store
    except (json.JSONDecodeError, TypeError, KeyError):
        return DigestStore(project=project_dir)


def save_digest_store(store: DigestStore) -> None:
    """Save the digest store to disk (JSON + human-readable markdown mirror)."""
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    store.updated = datetime.now(timezone.utc).isoformat()

    data = {
        "version": store.version,
        "project": store.project,
        "updated": store.updated,
        "session_id": store.session_id,
        "strategy_rules": [asdict(r) for r in store.strategy_rules],
        "failure_patterns": [asdict(r) for r in store.failure_patterns],
    }
    DIGEST_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Write human-readable markdown mirror
    _write_digest_md(store)


def _write_digest_md(store: DigestStore) -> None:
    """Write a human-readable markdown version of the digest."""
    lines = [
        "# Behavioral Digest",
        f"Updated: {store.updated}",
        f"Project: {store.project}",
        "",
    ]

    active = [r for r in store.strategy_rules if r.status == "active"]
    pending = [r for r in store.strategy_rules if r.status == "pending"]

    if active:
        lines.append(f"## Active Rules ({len(active)})")
        lines.append("")
        for r in active:
            lines.append(f"- **[{r.id}|{r.scope}|{r.priority}]** {r.rule}")
            if r.trigger:
                lines.append(f"  - When: {r.trigger}")
            if r.evidence:
                lines.append(f"  - Evidence: \"{r.evidence[:100]}\"")
            lines.append(f"  - Score: {score_rule(r):.2f} | Seen: {r.occurrence_count}x")
        lines.append("")

    if pending:
        lines.append(f"## Pending Rules ({len(pending)})")
        lines.append("")
        for r in pending:
            lines.append(f"- **[{r.id}|{r.scope}]** {r.rule} (seen {r.occurrence_count}x)")
        lines.append("")

    if store.failure_patterns:
        lines.append(f"## Failure Patterns ({len(store.failure_patterns)})")
        lines.append("")
        for r in store.failure_patterns:
            lines.append(f"- **[{r.id}]** {r.rule}")
        lines.append("")

    DIGEST_MD_FILE.write_text("\n".join(lines), encoding="utf-8")


def clear_digest_store() -> None:
    """Remove all digest files."""
    for f in (DIGEST_FILE, DIGEST_MD_FILE):
        if f.exists():
            f.unlink()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def update_digest(
    messages: list[Message],
    since_turn: int = 0,
    project_dir: str = "",
    session_id: str = "",
) -> tuple[int, int, int]:
    """Extract corrections from messages and update the digest store.

    Returns (new_rules, upvoted, rejected).
    """
    store = load_digest_store(project_dir)
    store.session_id = session_id

    candidates = extract_corrections(messages, since_turn=since_turn)

    added = 0
    upvoted = 0
    rejected = 0

    for rule in candidates:
        result = admit_rule(rule, store)
        if result == "added":
            added += 1
        elif result == "upvoted":
            upvoted += 1
        else:
            rejected += 1

    if added > 0 or upvoted > 0:
        save_digest_store(store)

    return added, upvoted, rejected


def show_digest() -> str:
    """Return a formatted string of the current digest."""
    store = load_digest_store()
    if store.is_empty():
        return "No behavioral rules stored."

    lines = []
    active = store.active_rules()
    pending = [r for r in store.strategy_rules if r.status == "pending"]

    if active:
        lines.append(f"Active rules ({len(active)}):")
        for r in active:
            lines.append(f"  [{r.id}|{r.scope}|{r.priority}] {r.rule}")
            lines.append(f"    Score: {score_rule(r):.2f} | Seen: {r.occurrence_count}x")

    if pending:
        lines.append(f"\nPending rules ({len(pending)}):")
        for r in pending:
            lines.append(f"  [{r.id}|{r.scope}] {r.rule} (seen {r.occurrence_count}x)")

    if store.failure_patterns:
        lines.append(f"\nFailure patterns ({len(store.failure_patterns)}):")
        for r in store.failure_patterns:
            lines.append(f"  [{r.id}] {r.rule}")

    return "\n".join(lines)
