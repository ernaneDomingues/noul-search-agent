"""Start the Noul MCP server (`noul-mcp`) from an absolute path; see `noul._bin`."""

import sys
from pathlib import Path

sys.path.insert(1, str(Path(__file__).resolve().parents[2]))  # the folder that contains the `noul` package

from noul.server.mcp import main  # noqa: E402

sys.exit(main())
