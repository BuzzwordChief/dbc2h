"""TUI tests against the source tree (the built .pyz is covered in test_build.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import Static

from dbc2h.cli import version_text
from dbc2h.tui.app import Dbc2hApp
from dbc2h.tui.selftest import _run_checks


def test_selftest_checks_pass():
    checks, _ = asyncio.run(_run_checks())
    failed = [(name, detail) for name, ok, detail in checks if not ok]
    assert failed == []


def test_shows_opened_file_name():
    async def scenario() -> str:
        app = Dbc2hApp(Path("powertrain.dbc"))
        async with app.run_test(size=(80, 24)):
            return str(app.query_one("#welcome", Static).render())

    assert "powertrain.dbc" in asyncio.run(scenario())


def test_version_text_from_source_tree():
    assert "Start aus dem Quellbaum" in version_text()
