#!/bin/sh
export ANTHROPIC_MODEL="claude-opus-4-6[1m]"
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=2
claude --dangerously-skip-permissions
