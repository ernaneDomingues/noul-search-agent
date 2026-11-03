"""Run the Noul CLI (`noul`) from an absolute path; see `noul._bin`."""

import sys
from pathlib import Path

sys.path.insert(1, str(Path(__file__).resolve().parents[2]))  # the folder that contains the `noul` package

from noul.cli import main  # noqa: E402

sys.exit(main())
