"""`python -m noul ...` runs the CLI (`python -m noul mcp` starts the MCP server)."""

import sys

from noul.cli import main

if __name__ == "__main__":
    sys.exit(main())
