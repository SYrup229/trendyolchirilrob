"""
Build a standalone Windows .exe for the judet exporter using PyInstaller.

Usage:
    python build_judet_exe.py

Outputs dist/JudetExport.exe — a single console program. Double-click it and it
writes judet_export.csv next to itself, reading the app's database from
%APPDATA%\\TrendyolFull\\trendyol_full.sqlite3.

The script uses only the Python standard library, so the exe is small and needs
no extra packages.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "JudetExport"
# Reuse the main app's icon if it's present, so the tool looks like part of the app.
ICON = ROOT.parent / "trendyol_full" / "assets" / "icon.ico"


def main() -> int:
    for d in ("build", "dist"):
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)
    spec = ROOT / f"{NAME}.spec"
    if spec.exists():
        spec.unlink()

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",              # CLI tool: keep the window so users see the summary
        "--name", NAME,
    ]
    if ICON.exists():
        args += ["--icon", str(ICON)]
    args.append(str(ROOT / "export_judet.py"))

    print("Running:", " ".join(args))
    return subprocess.call(args, cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
