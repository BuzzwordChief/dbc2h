#!/usr/bin/env python3
"""Set up a development environment.

  python tools/dev_env.py          create .venv/ with the vendored wheels and the dev tools
  python tools/dev_env.py --here   install into the running interpreter (used by CI)

The vendored runtime dependencies are installed offline from vendor/wheels/, so the
tests run against exactly the versions that end up in dbc2h.pyz.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import venv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vendor  # noqa: E402

REPO = vendor.REPO


def venv_python(env_dir: Path) -> Path:
    if sys.platform == "win32":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def install(python: Path | str) -> None:
    vendor.verify()
    pins = [f"{e.name}=={e.version}" for e in vendor.read_lock(vendor.DEFAULT_VENDOR_DIR)]
    wheels = str(vendor.DEFAULT_VENDOR_DIR / vendor.WHEELS_DIR_NAME)
    pip = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--quiet"]
    subprocess.run([*pip, "--no-index", "--find-links", wheels, *pins], check=True)
    subprocess.run([*pip, "-r", str(REPO / "requirements-dev.txt")], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--here", action="store_true", help="install into the running interpreter")
    parser.add_argument("--env-dir", type=Path, default=REPO / ".venv")
    args = parser.parse_args(argv)
    if args.here:
        install(sys.executable)
        return 0
    if not venv_python(args.env_dir).exists():
        venv.EnvBuilder(with_pip=True).create(args.env_dir)
    install(venv_python(args.env_dir))
    activate = r".venv\Scripts\activate" if sys.platform == "win32" else "source .venv/bin/activate"
    print(f"ready: {activate}  then  python -m pytest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
