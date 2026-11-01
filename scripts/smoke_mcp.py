#!/usr/bin/env python3
"""Smoke-test an MCP stdio server command: initialize, tools/list and one offline tool call.

Usage: python scripts/smoke_mcp.py [--min-tools N] -- <command> [args...]
Exits non-zero (with the server's stderr) on any failure. Stdlib only, so it runs on every CI runner."""

import argparse
import json
import subprocess
import sys
import threading


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-tools", type=int, default=41)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not command:
        ap.error("missing server command")
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()
    timer = threading.Timer(a.timeout, proc.kill)
    timer.start()

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def reply(request_id):
        for line in proc.stdout:
            message = json.loads(line)  # stdout must be pure JSON-RPC
            if message.get("id") == request_id:
                return message
        raise RuntimeError(f"server exited before answering request {request_id}")

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "0"},
                },
            }
        )
        init = reply(1)["result"]
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = reply(2)["result"]["tools"]
        if len(tools) < a.min_tools:
            raise RuntimeError(f"expected >= {a.min_tools} tools, got {len(tools)}")
        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "evidence_meter",
                    "arguments": {"items": [{"stance": "supportive", "evidence": "rct"}]},
                },
            }
        )
        result = reply(3)["result"]
        if result.get("isError") or result["structuredContent"]["total_papers"] != 1:
            raise RuntimeError(f"unexpected tool result: {result}")
        print(
            f"ok: {init['serverInfo']['name']} {init['serverInfo']['version']}, {len(tools)} tools, protocol {init['protocolVersion']}"
        )
        return 0
    except Exception as exc:
        print(f"smoke test failed: {exc}", file=sys.stderr)
        print("".join(stderr)[-4000:], file=sys.stderr)
        return 1
    finally:
        timer.cancel()
        proc.stdin.close()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
