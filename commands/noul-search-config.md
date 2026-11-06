---
description: Configure Noul Search (contact email for Unpaywall/polite API pools and output folders)
argument-hint: "[email:you@university.edu] [download_dir:papers] [research_dir:research] [clear]"
allowed-tools: mcp__noul__configure_noul, mcp__plugin_noul-search-agent_noul__configure_noul
---
Configure the Noul Search agent with the `configure_noul` MCP tool. If the MCP tool is unavailable, show the user
the command to run themselves: `uv run --project ${CLAUDE_PLUGIN_ROOT} noul config [email:...] [--download-dir D] [--research-dir R]`.

User arguments: $ARGUMENTS

1. Parse the arguments as `key:value` pairs. Accepted keys: `email`, `download_dir`, `research_dir`.
   A bare value containing `@` is the email. The single word `clear` resets the configuration (`clear=true`).
2. If no arguments were given, call `configure_noul` with no arguments, show the current settings, and ask the user
   which email to use. **Never invent, guess or reuse an email address that the user did not type.**
3. Otherwise call `configure_noul` with exactly the parsed values (an empty value removes that setting).
4. Report the result briefly: email (and whether Unpaywall is enabled), workspace, download/research folders and the
   config file path. Remind the user that downloads and dossiers are always kept inside the workspace.
5. The workspace and the Zotero credentials cannot be set here, and `clear` keeps them. If the user asks to change
   them, show the commands to run themselves in a terminal: `noul config --workspace W`,
   `noul config --zotero-user-id ID --zotero-api-key-stdin`, or the variables `NOUL_WORKSPACE`, `NOUL_ZOTERO_USER_ID`
   and `NOUL_ZOTERO_API_KEY`, which win over stored values. In the Claude Desktop extension, the workspace is its
   "Noul folder" setting. `noul config --clear` deletes all of it. Never ask for a key in chat.
