---
description: Configure Noul Search (contact email for Unpaywall/polite API pools, workspace and output folders)
argument-hint: "[email:you@university.edu] [workspace:/path] [download_dir:papers] [research_dir:research] [clear]"
allowed-tools: mcp__noul__configure_noul, mcp__plugin_noul-search-agent_noul__configure_noul, Bash
---
Configure the Noul Search agent with the `configure_noul` MCP tool. If the MCP tool is unavailable, run the CLI
instead: `python3 ${CLAUDE_PLUGIN_ROOT}/noul_cli.py config [email:...] [--workspace W] [--download-dir D] [--research-dir R] [--clear]`.

User arguments: $ARGUMENTS

1. Parse the arguments as `key:value` pairs. Accepted keys: `email`, `workspace`, `download_dir`, `research_dir`.
   A bare value containing `@` is the email. The single word `clear` resets the configuration (`clear=true`).
2. If no arguments were given, call `configure_noul` with no arguments, show the current settings, and ask the user
   which email to use. **Never invent, guess or reuse an email address that the user did not type.**
3. Otherwise call `configure_noul` with exactly the parsed values (an empty value removes that setting).
4. Report the result briefly: email (and whether Unpaywall is enabled), workspace, download/research folders and the
   config file path. Remind the user that downloads and dossiers are always kept inside the workspace.
