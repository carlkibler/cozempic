#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const { existsSync } = require("fs");
const { join } = require("path");

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
}

// ── 2. Auto-configure if inside a Claude Code project ────────────────────────

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
  console.log("cozempic ready. Run inside your Claude Code project to enable auto-guard:\n  cd your-project/ && cozempic init");
}
