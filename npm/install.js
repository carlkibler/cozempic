#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const { existsSync, readFileSync, writeFileSync, mkdirSync } = require("fs");
const { join } = require("path");
const os = require("os");

// ── 1. Install Python package ─────────────────────────────────────────────────

const check = spawnSync("cozempic", ["--version"], { stdio: "pipe" });
const alreadyInstalled = check.status === 0;

if (!alreadyInstalled) {
  console.log("Installing cozempic Python package...");

  const attempts = [
    ["pip", ["install", "cozempic", "--quiet", "--disable-pip-version-check"]],
    ["pip3", ["install", "cozempic", "--quiet", "--disable-pip-version-check"]],
    ["python", ["-m", "pip", "install", "cozempic", "--quiet"]],
    ["python3", ["-m", "pip", "install", "cozempic", "--quiet"]],
  ];

  let installed = false;
  for (const [cmd, args] of attempts) {
    const r = spawnSync(cmd, args, { stdio: "inherit" });
    if (r.status === 0) { installed = true; break; }
  }

  if (!installed) {
    console.log("\ncozempic could not be auto-installed. Run manually:\n  pip install cozempic\n");
    process.exit(0);
  }

  // Ping install counter on first install
  try {
    const https = require("https");
    https.get("https://api.counterapi.dev/v1/cozempic/installs/up", { headers: { "User-Agent": "cozempic-npm" } }, () => {}).on("error", () => {});
  } catch {}
}

// ── 2. Wire global SessionStart hook in ~/.claude/settings.json ──────────────
// This ensures cozempic auto-configures on every Claude Code session the user
// opens — even in projects they haven't run `cozempic init` in yet.

const claudeDir = join(os.homedir(), ".claude");
const globalSettingsPath = join(claudeDir, "settings.json");
const hookCmd = "command -v cozempic >/dev/null 2>&1 || pip install cozempic --quiet; [ -d .claude ] && cozempic init --quiet 2>/dev/null; cozempic guard --daemon 2>/dev/null || true";

if (existsSync(claudeDir)) {
  let settings = {};
  if (existsSync(globalSettingsPath)) {
    try { settings = JSON.parse(readFileSync(globalSettingsPath, "utf8")); } catch {}
  }

  settings.hooks = settings.hooks || {};
  settings.hooks.SessionStart = settings.hooks.SessionStart || [];

  const alreadyWired = settings.hooks.SessionStart.some(h =>
    (h.hooks || []).some(hh => hh.command && hh.command.includes("cozempic"))
  );

  if (!alreadyWired) {
    settings.hooks.SessionStart.push({
      hooks: [{ type: "command", command: hookCmd }]
    });
    writeFileSync(globalSettingsPath, JSON.stringify(settings, null, 2));
    console.log("Global SessionStart hook wired — cozempic will auto-configure on every Claude Code session.");
  }
}

// ── 3. Auto-configure if already inside a Claude Code project ────────────────

const cwd = process.env.INIT_CWD || process.cwd();
const isClaudeProject = existsSync(join(cwd, ".claude"));

if (isClaudeProject) {
  console.log("Claude Code project detected — running cozempic init...");
  const r = spawnSync("cozempic", ["init"], { stdio: "inherit", cwd });
  if (r.status === 0) {
    console.log("Guard daemon and hooks wired. Auto-pruning is active.");
  } else {
    console.log("cozempic init failed — run manually: cozempic init");
  }
} else {
  console.log("cozempic ready. Auto-guard will activate on your next Claude Code session.");
}
