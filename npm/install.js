#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const { existsSync, readFileSync, writeFileSync } = require("fs");
const { join } = require("path");
const os = require("os");

// ── 1. Install or upgrade Python package ─────────────────────────────────────
// Always try to upgrade — ensures users on old versions get the latest.

const attempts = [
  ["uv", ["pip", "install", "--upgrade", "cozempic", "--quiet"]],
  ["pip", ["install", "--upgrade", "cozempic", "--quiet", "--disable-pip-version-check"]],
  ["pip3", ["install", "--upgrade", "cozempic", "--quiet", "--disable-pip-version-check"]],
  ["python3", ["-m", "pip", "install", "--upgrade", "cozempic", "--quiet"]],
  ["python", ["-m", "pip", "install", "--upgrade", "cozempic", "--quiet"]],
];

let installed = false;
for (const [cmd, args] of attempts) {
  try {
    const r = spawnSync(cmd, args, { stdio: "pipe", timeout: 60000 });
    if (r.status === 0) { installed = true; break; }
  } catch {}
}

if (!installed) {
  console.log("Cozempic: could not install/upgrade. Run: pip install --upgrade cozempic");
  process.exit(0);
}

// Ping install counter
try {
  const https = require("https");
  https.get("https://api.counterapi.dev/v1/cozempic/installs/up",
    { headers: { "User-Agent": "cozempic-npm" } }, () => {}).on("error", () => {});
} catch {}

// ── 2. Wire global SessionStart hook in ~/.claude/settings.json ──────────────

const noAutoUpdate = process.env.COZEMPIC_NO_AUTO_UPDATE;

if (!noAutoUpdate) {
  const claudeDir = join(os.homedir(), ".claude");
  const globalSettingsPath = join(claudeDir, "settings.json");
  const hookCmd = "HOOK_DATA=$(cat); TRANSCRIPT=$(echo \"$HOOK_DATA\" | python3 -c \"import sys,json; print(json.load(sys.stdin).get('transcript_path',''))\" 2>/dev/null); { cozempic guard --daemon ${TRANSCRIPT:+--session $TRANSCRIPT} 2>/dev/null || python3 -m cozempic guard --daemon ${TRANSCRIPT:+--session $TRANSCRIPT} 2>/dev/null; } || true";

  try {
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
      }
    }
  } catch {}
}

// ── 3. Auto-configure if inside a Claude Code project ────────────────────────

const cwd = process.env.INIT_CWD || process.cwd();

if (!noAutoUpdate) {
  try {
    if (existsSync(join(cwd, ".claude"))) {
      let r = spawnSync("cozempic", ["init", "--quiet"], { stdio: "pipe", cwd });
      if (r.status !== 0) {
        spawnSync("python3", ["-m", "cozempic", "init", "--quiet"], { stdio: "pipe", cwd });
      }
    }
  } catch {}
}
