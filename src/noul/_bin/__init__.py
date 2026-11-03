"""Absolute-path launchers used by `noul-install` when no console script is installed (e.g. a source checkout).

Agents start MCP servers inside the user's project folder. Running one of these files by its absolute path keeps
`sys.path[0]` on this folder - never the project folder, as `python -m noul` would - so a cloned repository that
contains `noul/` or a stdlib-named module cannot inject code. The file names avoid shadowing `mcp` and `http`."""
