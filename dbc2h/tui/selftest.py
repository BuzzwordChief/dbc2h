"""Headless self-test of the TUI, used by the tests of the built dbc2h.pyz.

Runs the app without a terminal, checks that the bundled stylesheet is applied
and that <q> quits, and prints timings for the cold-start measurement.
"""

from __future__ import annotations

import asyncio
import time

# Must match dbc2h.tcss. Textual's defaults differ, so a match proves the file was loaded.
EXPECTED_DIALOG_BACKGROUND = "#aaaaaa"
EXPECTED_SCREEN_BACKGROUND = "#0000aa"


async def _run_checks() -> tuple[list[tuple[str, bool, str]], dict[str, float]]:
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    from textual.color import Color

    from dbc2h.tui.app import Dbc2hApp

    timings["import_ms"] = (time.perf_counter() - t0) * 1000

    checks: list[tuple[str, bool, str]] = []
    app = Dbc2hApp()
    t1 = time.perf_counter()
    async with app.run_test(size=(80, 24)) as pilot:
        timings["ready_ms"] = (time.perf_counter() - t1) * 1000
        dialog = app.query_one("#dialog")
        got = dialog.styles.background
        checks.append(("stylesheet #dialog", got == Color.parse(EXPECTED_DIALOG_BACKGROUND), got.hex))
        got = app.screen.styles.background
        checks.append(("stylesheet Screen", got == Color.parse(EXPECTED_SCREEN_BACKGROUND), got.hex))
        await pilot.press("q")
        await pilot.pause()
    checks.append(("quit with q", app.return_code == 0, f"return_code={app.return_code}"))
    return checks, timings


def run_selftest() -> int:
    checks, timings = asyncio.run(_run_checks())
    for name, ok, detail in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name} ({detail})")
    print(f"timing import_ms={timings['import_ms']:.0f} ready_ms={timings['ready_ms']:.0f}")
    return 0 if all(ok for _, ok, _ in checks) else 1
