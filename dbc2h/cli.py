"""Command line handling of dbc2h.

Without a subcommand dbc2h starts the TUI. The non-interactive subcommands for build
systems (check, gen) must not import Textual.
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


def build_gen_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{_prog()} gen",
        description="C-Header aus einer DBC-Datei erzeugen. Exit-Code 1 bei Fehlern in der DBC oder "
                    "(mit --check) bei veralteten Dateien, 2 wenn die Datei nicht gelesen werden kann.",
    )
    parser.add_argument("file", type=Path, help="DBC-Datei")
    parser.add_argument("-o", "--output", type=Path, help="Ausgabeverzeichnis (Standard: dbc2h_OutputDir "
                        "relativ zur DBC)")
    parser.add_argument("--tests", action="store_true", help="zusätzlich test_<prefix>.c erzeugen")
    parser.add_argument("--check", action="store_true", help="nichts schreiben, nur prüfen, ob die Dateien "
                        "aktuell sind")
    parser.add_argument("--run-tests", action="store_true", help="test_<prefix>.c übersetzen und ausführen "
                        "(impliziert --tests)")
    return parser


def gen_command(argv: list[str]) -> int:
    import tempfile

    from dbc2h.core import codegen_c, compilers, settings
    from dbc2h.core.dbcfile import DbcFile
    from dbc2h.core.lexer import DbcSyntaxError

    args = build_gen_parser().parse_args(argv)
    try:
        dbc = DbcFile.load(args.file)
    except (OSError, DbcSyntaxError) as exc:
        print(f"{args.file}: {getattr(exc, 'strerror', None) or exc}", file=sys.stderr)
        return 2
    doc = dbc.document
    try:
        out = codegen_c.generate(doc, args.file.name)
    except codegen_c.GenerationError as exc:
        for f in exc.findings:
            print(f.format())
        print(f"{args.file.name}: {len(exc.findings)} Fehler, nichts erzeugt")
        return 1
    out_dir = args.output or (args.file.parent / settings.get_setting(doc.network, doc.network, "dbc2h_OutputDir"))
    files = {out.header_name: out.header}
    if args.tests or args.run_tests:
        files[out.test_name] = out.test
    for name, reason in out.skipped:
        print(f"übersprungen: {name} ({reason})")
    if args.check:
        stale = [n for n, text in files.items()
                 if not (out_dir / n).is_file() or (out_dir / n).read_bytes() != text.encode("ascii")]
        for n in stale:
            print(f"veraltet: {out_dir / n}")
        return 1 if stale else 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out_dir / name).write_bytes(text.encode("ascii"))
        print(f"geschrieben: {out_dir / name}")
    if args.run_tests:
        found = compilers.find_compilers()
        if not found:
            print("kein C-Compiler gefunden (gcc, clang, cc oder cl)", file=sys.stderr)
            return 1
        with tempfile.TemporaryDirectory() as tmp:
            result = compilers.build_and_run(found[0], out_dir / out.test_name, out_dir, Path(tmp))
        print(f"{found[0].version}: {result.output.strip()}")
        return 0 if result.ok else 1
    return 0


SUBCOMMANDS = {"check": check_command, "gen": gen_command}


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
