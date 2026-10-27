#!/usr/bin/env node
// Thin npx/npm launcher: finds a Python >= 3.10 and runs noul_cli.py with the same arguments and stdio
// (so `npx -y noul-search-agent mcp` is a stdio MCP server). Override the interpreter with NOUL_PYTHON.
"use strict";
const { spawn, spawnSync } = require("child_process");
const os = require("os");
const path = require("path");

const CLI = path.join(__dirname, "..", "noul_cli.py");
const MIN_MINOR = 10;

function candidates() {
  const list = [];
  if (process.env.NOUL_PYTHON) list.push([process.env.NOUL_PYTHON]);
  if (process.platform === "win32") list.push(["py", "-3"], ["python"], ["python3"]);
  else list.push(["python3"], ["python"]);
  return list;
}

function findPython() {
  for (const [cmd, ...pre] of candidates()) {
    const r = spawnSync(cmd, [...pre, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], {
      encoding: "utf8",
      windowsHide: true,
    });
    if (r.status !== 0 || !r.stdout) continue;
    const [major, minor] = r.stdout.trim().split(".").map(Number);
    if (major > 3 || (major === 3 && minor >= MIN_MINOR)) return [cmd, pre];
  }
  return null;
}

const py = findPython();
if (!py) {
  console.error(
    `noul-search-agent needs Python 3.${MIN_MINOR}+ (no packages required). Install it from https://www.python.org/downloads/ ` +
      "or point NOUL_PYTHON at an existing interpreter."
  );
  process.exit(1);
}

const env = { ...process.env };
if (!env.NOUL_INSTALL_RUNNER) env.NOUL_INSTALL_RUNNER = "npx";
const child = spawn(py[0], [...py[1], CLI, ...process.argv.slice(2)], { stdio: "inherit", env, windowsHide: true });
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => {
    if (child.exitCode === null) child.kill(sig);
  });
}
child.on("error", (err) => {
  console.error(`noul-search-agent: failed to start ${py[0]}: ${err.message}`);
  process.exit(1);
});
child.on("exit", (code, signal) => {
  process.exit(signal ? 128 + (os.constants.signals[signal] || 1) : code ?? 1);
});
