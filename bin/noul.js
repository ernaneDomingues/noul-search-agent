#!/usr/bin/env node
// Thin npx/npm launcher: runs the bundled Python package through uv (`uv run --project <package> noul ...`),
// which provisions Python and the dependencies on first use. `npx -y noul-search-agent mcp` is a stdio MCP
// server. Override the uv executable with NOUL_UV.
"use strict";
const { spawn, spawnSync } = require("child_process");
const os = require("os");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const UV = process.env.NOUL_UV || "uv";

function hasUv() {
  const r = spawnSync(UV, ["--version"], { encoding: "utf8", windowsHide: true });
  return r.status === 0;
}

if (!hasUv()) {
  console.error(
    "noul-search-agent needs uv (https://docs.astral.sh/uv/getting-started/installation/), which installs Python " +
      "and the dependencies automatically.\n" +
      "  macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh\n" +
      '  Windows:     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"\n' +
      "Then run this command again (or set NOUL_UV to the uv executable)."
  );
  process.exit(1);
}

const env = { ...process.env };
if (!env.NOUL_INSTALL_RUNNER) env.NOUL_INSTALL_RUNNER = "npx";
const args = ["run", "--project", ROOT, "--quiet", "noul", ...process.argv.slice(2)];
const child = spawn(UV, args, { stdio: "inherit", env, windowsHide: true });
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => {
    if (child.exitCode === null) child.kill(sig);
  });
}
child.on("error", (err) => {
  console.error(`noul-search-agent: failed to start ${UV}: ${err.message}`);
  process.exit(1);
});
child.on("exit", (code, signal) => {
  process.exit(signal ? 128 + (os.constants.signals[signal] || 1) : code ?? 1);
});
