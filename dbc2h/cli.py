"""Command line handling of dbc2h.

Without a subcommand dbc2h starts the TUI. Non-interactive subcommands for build
systems (gen, check) are added in later milestones; they must not import Textual.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from importlib import resources
from pathlib import Path

from dbc2h import __version__

BUNDLE_MANIFEST = "_bundle.json"  # written into the package by tools/build_pyz.py


def _prog() -> str:
    name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return name if name.endswith(".pyz") else "dbc2h"


def bundled_packages() -> list[dict[str, str]] | None:
    """Packages bundled into the .pyz, or None when running from a source tree."""
    try:
        text = resources.files("dbc2h").joinpath(BUNDLE_MANIFEST).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return json.loads(text)["packages"]


def version_text() -> str:
    lines = [
        f"dbc2h {__version__}",
        f"Python {platform.python_version()} ({platform.python_implementation()}, {sys.platform})",
    ]
    bundle = bundled_packages()
    if bundle is None:
        lines.append("Gebündelt: nichts (Start aus dem Quellbaum)")
    else:
        lines.append("Gebündelt:")
        lines.extend(f"  {p['name']} {p['version']} ({p['license']})" for p in bundle)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_prog(),
        description="DBC-Dateien erstellen, verwalten und als C11-Header exportieren.",
    )
    parser.add_argument("file", nargs="?", type=Path, help="DBC-Datei, die in der TUI geöffnet wird")
    parser.add_argument("--version", action="store_true", help="Version und gebündelte Bibliotheken anzeigen")
    # Hidden options for automated tests of the built .pyz.
    parser.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--exit-after", type=float, metavar="SEK", help=argparse.SUPPRESS)
    return parser


def use_utf8_for_redirected_output() -> None:
    """Write UTF-8 to pipes and files on every platform and Python version.

    Python 3.15 does this by default (PEP 686). Python 3.13 and 3.14 on Windows use the
    ANSI code page instead, so build tools reading our output would get cp1252.
    Terminals keep their own encoding.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    use_utf8_for_redirected_output()
    args = build_parser().parse_args(argv)
    if args.version:
        print(version_text())
        return 0
    if args.selftest:
        from dbc2h.tui.selftest import run_selftest

        return run_selftest()
    from dbc2h.tui.app import run

    return run(args.file, exit_after=args.exit_after)
