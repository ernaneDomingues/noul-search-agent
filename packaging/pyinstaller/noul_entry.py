"""Entry point of the single-file `noul` executable: the full CLI (`noul search ...`, `noul mcp`, `noul install`)."""

import sys

from noul.cli import main

if __name__ == "__main__":
    sys.exit(main())
