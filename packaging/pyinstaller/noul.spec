# PyInstaller spec for the single-file `noul` executable (CLI + MCP server + installer).
# Build from the repository root:  pyinstaller packaging/pyinstaller/noul.spec --noconfirm --clean
# The output is dist/noul (dist/noul.exe on Windows).
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent.parent  # noqa: F821 (SPECPATH is provided by PyInstaller)

datas = [
    (str(ROOT / "skills/noul-search/SKILL.md"), "skills/noul-search"),
    (str(ROOT / "agents"), "agents"),
    (str(ROOT / "commands"), "commands"),
]
binaries = []
hiddenimports = collect_submodules("noul")
# mcp.cli needs the optional `typer` and is not used by Noul.
hiddenimports += collect_submodules("mcp", filter=lambda name: not name.startswith("mcp.cli"))
datas += collect_data_files("mcp")
for package in ("mcp_types", "uvicorn", "starlette", "sse_starlette", "pypdf", "httpx", "anyio"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging/pyinstaller/noul_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "mypy", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="noul",
    console=True,
    upx=False,
    strip=False,
    debug=False,
)
