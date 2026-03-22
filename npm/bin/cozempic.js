#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");

const args = process.argv.slice(2);

// Try direct cozempic command first, then python -m cozempic fallbacks
const candidates = [
  ["cozempic", args],
  ["python", ["-m", "cozempic", ...args]],
  ["python3", ["-m", "cozempic", ...args]],
];

for (const [cmd, cmdArgs] of candidates) {
  const r = spawnSync(cmd, cmdArgs, { stdio: "inherit" });
  if (!r.error) process.exit(r.status ?? 0);
}

console.error("cozempic not found. Install with: pip install cozempic");
process.exit(1);
