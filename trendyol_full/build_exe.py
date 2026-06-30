"""
Build a standalone Windows .exe using PyInstaller.

Usage:
    python build_exe.py

Outputs dist/TrendyolFull.exe.
Swap assets/icon.ico with your real icon before building.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ICON = ASSETS / "icon.ico"
NAME = "TrendyolFull"


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
        "--windowed",
        "--name", NAME,
        "--paths", str(ROOT),
    ]
    if ICON.exists():
        args += ["--icon", str(ICON)]
    args.append(str(ROOT / "app.py"))

    print("Running:", " ".join(args))
    return subprocess.call(args, cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
