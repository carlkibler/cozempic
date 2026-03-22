#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");

// Skip if already installed
const check = spawnSync("cozempic", ["--version"], { stdio: "pipe" });
if (check.status === 0) process.exit(0);

console.log("Installing cozempic Python package...");

const attempts = [
  ["pip", ["install", "cozempic", "--quiet", "--disable-pip-version-check"]],
  ["pip3", ["install", "cozempic", "--quiet", "--disable-pip-version-check"]],
  ["python", ["-m", "pip", "install", "cozempic", "--quiet"]],
  ["python3", ["-m", "pip", "install", "cozempic", "--quiet"]],
];

for (const [cmd, args] of attempts) {
  const r = spawnSync(cmd, args, { stdio: "inherit" });
  if (r.status === 0) {
    console.log("cozempic ready.");
    process.exit(0);
  }
}

console.log("\ncozempic could not be auto-installed. Run manually:\n  pip install cozempic\n");
