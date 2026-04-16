#!/bin/bash
# Cozempic Outreach Digest — daily summary of GitHub activity
# Covers: anthropics/claude-code comments, Ruya-AI/cozempic issues/PRs/comments
# Run: ./scripts/outreach-digest.sh [hours]  (default: 24)

set -euo pipefail

HOURS="${1:-24}"
SINCE=$(date -v-${HOURS}H -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -d "${HOURS} hours ago" -u +"%Y-%m-%dT%H:%M:%SZ")
ACCOUNT="junaidtitan"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  COZEMPIC OUTREACH DIGEST — last ${HOURS}h"
echo "  Since: ${SINCE}"
echo "═══════════════════════════════════════════════════════════════"

# ── 1. New comments on issues we've commented on (anthropics/claude-code) ────

echo ""
echo "── ANTHROPICS/CLAUDE-CODE — replies on our threads ──"
echo ""

CC_ACTIVITY=0
for issue_num in $(gh search issues "commenter:${ACCOUNT}" --repo anthropics/claude-code --sort updated --limit 200 --json number,updatedAt --jq ".[] | select(.updatedAt > \"${SINCE}\") | .number"); do
  # Get comments since our cutoff, excluding our own
  COMMENTS=$(gh api "repos/anthropics/claude-code/issues/${issue_num}/comments?since=${SINCE}&per_page=10" \
    --jq "[.[] | select(.user.login != \"${ACCOUNT}\")] | length" 2>/dev/null || echo "0")

  if [ "$COMMENTS" -gt 0 ]; then
    TITLE=$(gh issue view "$issue_num" --repo anthropics/claude-code --json title --jq .title 2>/dev/null | head -c 70)
    echo "  #${issue_num} (${COMMENTS} new) — ${TITLE}"

    # Show latest comment preview
    gh api "repos/anthropics/claude-code/issues/${issue_num}/comments?since=${SINCE}&per_page=3" \
      --jq "[.[] | select(.user.login != \"${ACCOUNT}\")] | .[-1] | \"    @\" + .user.login + \": \" + (.body | split(\"\n\")[0] | .[0:100])" 2>/dev/null || true

    CC_ACTIVITY=$((CC_ACTIVITY + 1))
  fi
done

if [ "$CC_ACTIVITY" -eq 0 ]; then
  echo "  No new replies."
fi

# ── 2. Our repo: new issues ─────────────────────────────────────────────────

echo ""
echo "── RUYA-AI/COZEMPIC — new issues ──"
echo ""

NEW_ISSUES=$(gh issue list --repo Ruya-AI/cozempic --state all --json number,title,author,createdAt,state \
  --jq "[.[] | select(.createdAt > \"${SINCE}\")] | length" 2>/dev/null || echo "0")

if [ "$NEW_ISSUES" -gt 0 ]; then
  gh issue list --repo Ruya-AI/cozempic --state all --json number,title,author,createdAt,state \
    --jq ".[] | select(.createdAt > \"${SINCE}\") | \"  #\" + (.number|tostring) + \" [\" + .state + \"] @\" + .author.login + \" — \" + (.title|.[0:65])" 2>/dev/null || true
else
  echo "  None."
fi

# ── 3. Our repo: new PRs ────────────────────────────────────────────────────

echo ""
echo "── RUYA-AI/COZEMPIC — new PRs ──"
echo ""

NEW_PRS=$(gh pr list --repo Ruya-AI/cozempic --state all --json number,title,author,createdAt \
  --jq "[.[] | select(.createdAt > \"${SINCE}\")] | length" 2>/dev/null || echo "0")

if [ "$NEW_PRS" -gt 0 ]; then
  gh pr list --repo Ruya-AI/cozempic --state all --json number,title,author,createdAt,state \
    --jq ".[] | select(.createdAt > \"${SINCE}\") | \"  PR #\" + (.number|tostring) + \" [\" + .state + \"] @\" + .author.login + \" — \" + (.title|.[0:65])" 2>/dev/null || true
else
  echo "  None."
fi

# ── 4. Our repo: new comments from others ────────────────────────────────────

echo ""
echo "── RUYA-AI/COZEMPIC — community comments ──"
echo ""

REPO_COMMENTS=0
for issue_num in $(gh issue list --repo Ruya-AI/cozempic --state all --json number --jq ".[].number" 2>/dev/null); do
  COMMENTS=$(gh api "repos/Ruya-AI/cozempic/issues/${issue_num}/comments?since=${SINCE}&per_page=5" \
    --jq "[.[] | select(.user.login != \"${ACCOUNT}\")] | length" 2>/dev/null || echo "0")

  if [ "$COMMENTS" -gt 0 ]; then
    TITLE=$(gh issue view "$issue_num" --repo Ruya-AI/cozempic --json title --jq .title 2>/dev/null | head -c 65)
    echo "  #${issue_num} (${COMMENTS} new) — ${TITLE}"

    gh api "repos/Ruya-AI/cozempic/issues/${issue_num}/comments?since=${SINCE}&per_page=3" \
      --jq "[.[] | select(.user.login != \"${ACCOUNT}\")] | .[-1] | \"    @\" + .user.login + \": \" + (.body | split(\"\n\")[0] | .[0:100])" 2>/dev/null || true

    REPO_COMMENTS=$((REPO_COMMENTS + 1))
  fi
done

if [ "$REPO_COMMENTS" -eq 0 ]; then
  echo "  None."
fi

# ── 5. Stars / forks change ─────────────────────────────────────────────────

echo ""
echo "── RUYA-AI/COZEMPIC — repo stats ──"
echo ""
gh api repos/Ruya-AI/cozempic --jq '"  Stars: " + (.stargazers_count|tostring) + " | Forks: " + (.forks_count|tostring) + " | Open issues: " + (.open_issues_count|tostring)' 2>/dev/null || true

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Total: ${CC_ACTIVITY} CC threads with replies, ${NEW_ISSUES} new issues, ${NEW_PRS} new PRs"
echo "═══════════════════════════════════════════════════════════════"
echo ""
