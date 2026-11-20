"""Command line handling of dbc2h.

Without a subcommand dbc2h starts the TUI. The non-interactive subcommands for build
systems (check; gen follows in M4) must not import Textual.
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


def build_check_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{_prog()} check",
        description="DBC-Datei prüfen (Lint und Numerik). Exit-Code 1 bei Fehlern, 2 wenn die Datei "
                    "nicht gelesen werden kann.",
    )
    parser.add_argument("file", type=Path, help="DBC-Datei")
    parser.add_argument("--signals", action="store_true", help="Numerik-Einstufung aller Signale auflisten")
    return parser


def check_command(argv: list[str]) -> int:
    from dbc2h.core import lint, numerics
    from dbc2h.core.dbcfile import DbcFile
    from dbc2h.core.lexer import DbcSyntaxError

    args = build_check_parser().parse_args(argv)
    try:
        doc = DbcFile.load(args.file).document
    except OSError as exc:
        print(f"{args.file}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    except DbcSyntaxError as exc:
        print(f"{args.file}: {exc}", file=sys.stderr)
        return 2
    if args.signals:
        for msg in doc.network.messages:
            for sig in msg.signals:
                try:
                    result = numerics.check(doc.network, sig)
                except ValueError as exc:
                    print(f"{msg.name}.{sig.name}: {exc}")
                    continue
                conv = result.conversion
                width = f"{sig.length}{'s' if sig.is_signed else 'u'}"
                name = f"{msg.name}.{sig.name}"
                print(f"{name:<44} {width:>4}  LSB {sig.factor.text:<10} "
                      f"off {sig.offset.text:<10} {conv.c_type or '-':<9} {result.summary()}")
        print()
    findings = lint.lint(doc)
    for f in findings:
        print(f.format())
    errors = sum(f.severity is lint.Severity.ERROR for f in findings)
    warnings = sum(f.severity is lint.Severity.WARNING for f in findings)
    signals = sum(len(m.signals) for m in doc.network.messages)
    print(f"{args.file.name}: {len(doc.network.messages)} Nachrichten, {signals} Signale, "
          f"{errors} Fehler, {warnings} Warnungen")
    return 1 if errors else 0


SUBCOMMANDS = {"check": check_command}


def main(argv: list[str] | None = None) -> int:
    use_utf8_for_redirected_output()
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in SUBCOMMANDS:
        return SUBCOMMANDS[argv[0]](argv[1:])
    args = build_parser().parse_args(argv)
    if args.version:
        print(version_text())
        return 0
    if args.selftest:
        from dbc2h.tui.selftest import run_selftest

        return run_selftest()
    from dbc2h.tui.app import run

    return run(args.file, exit_after=args.exit_after)
