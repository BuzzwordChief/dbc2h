"""TUI interaction tests (App.run_test: no terminal needed). The built .pyz is covered in test_build.py."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from textual.widgets import DataTable, Input, Static

from conftest import REPO
from dbc2h.cli import version_text
from dbc2h.core import textio
from dbc2h.tui.app import RECOVERY_SUFFIX, Dbc2hApp, write_recovery
from dbc2h.tui.dialogs import Choice, InputBox, MessageBox
from dbc2h.tui.screens import FindingsScreen, MainMenu, MessageScreen, MessagesScreen, SignalScreen, StartScreen
from dbc2h.tui.selftest import _run_checks

CHECK_FILE = REPO / "tests" / "data" / "candbpp" / "dbc2h_check_cp1252.dbc"
SIZE = (100, 30)


def run(coro):
    return asyncio.run(coro)


def copy_check_file(tmp_path: Path) -> Path:
    target = tmp_path / "net.dbc"
    shutil.copy(CHECK_FILE, target)
    return target


def title(app) -> str:
    return str(app.screen.query_one("#title", Static).render())


def test_selftest_checks_pass():
    checks, _ = run(_run_checks())
    assert [(n, d) for n, ok, d in checks if not ok] == []


def test_version_text_from_source_tree():
    assert "Start aus dem Quellbaum" in version_text()


def test_start_screen_and_new_file():
    async def scenario():
        app = Dbc2hApp()
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, StartScreen)
            await pilot.press("down", "enter")
            await pilot.pause()
            assert isinstance(app.screen, MainMenu)
            assert "neue Datei" in title(app) and "*" not in title(app)
            await pilot.press("q")
            await pilot.pause()
        assert app.return_code == 0

    run(scenario())


def test_navigate_messages_and_signals():
    async def scenario():
        app = Dbc2hApp(CHECK_FILE)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainMenu)
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            assert isinstance(app.screen, MessagesScreen)
            assert app.screen.query_one(DataTable).row_count == 2
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, MessageScreen) and "VehicleStatus" in title(app)
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, SignalScreen) and title(app).endswith("speed")
            assert "float EXACT" in str(app.screen.query_one("#check", Static).render())
            await pilot.press("escape", "escape", "escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenu)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenu)

    run(scenario())


async def _open_message(pilot):
    await pilot.press("down", "down", "enter")
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


async def _edit_comment(app, pilot, value: str):
    await pilot.press("c")
    await pilot.pause()
    assert isinstance(app.screen, InputBox)
    app.screen.query_one(Input).value = value
    await pilot.press("enter")
    await pilot.pause()


def test_comment_edit_undo_redo_and_save(tmp_path):
    path = copy_check_file(tmp_path)
    before = path.read_bytes()

    async def scenario():
        app = Dbc2hApp(path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await _open_message(pilot)
            await _edit_comment(app, pilot, "Neuer Kommentar")
            msg = app.dbc.document.network.message_by_name("VehicleStatus")
            assert msg.comment == "Neuer Kommentar" and title(app).endswith("VehicleStatus") and " *" in title(app)
            await pilot.press("u")
            await pilot.pause()
            assert app.dbc.document.network.message_by_name("VehicleStatus").comment != "Neuer Kommentar"
            assert " *" not in title(app)
            await pilot.press("U")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            assert " *" not in title(app)

    run(scenario())
    after = path.read_bytes()
    changed = [line for line in after.splitlines() if line not in before.splitlines()]
    assert changed == [b'CM_ BO_ 291 "Neuer Kommentar";']
    assert path.with_name("net.dbc.bak").read_bytes() == before


def test_external_change_asks_before_overwriting(tmp_path):
    path = copy_check_file(tmp_path)

    async def scenario():
        app = Dbc2hApp(path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await _open_message(pilot)
            await _edit_comment(app, pilot, "Mein Stand")
            path.write_bytes(path.read_bytes() + b"\r\n// fremde Aenderung\r\n")
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, Choice)
            await pilot.click("#choice-overwrite")
            await pilot.pause()
            assert not app.modified

    run(scenario())
    data = path.read_bytes()
    assert b"Mein Stand" in data and b"fremde" not in data


def test_quit_asks_with_unsaved_changes(tmp_path):
    path = copy_check_file(tmp_path)

    async def scenario():
        app = Dbc2hApp(path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await _open_message(pilot)
            await _edit_comment(app, pilot, "ungespeichert")
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app.screen, Choice)
            await pilot.click("#choice-cancel")
            await pilot.pause()
            assert app.is_running and isinstance(app.screen, MessageScreen)
            await pilot.press("q")
            await pilot.pause()
            await pilot.click("#choice-discard")
            await pilot.pause()
        return app

    app = run(scenario())
    assert app.return_code == 0
    assert b"ungespeichert" not in path.read_bytes()


def test_findings_jump_to_signal(tmp_path):
    path = tmp_path / "bad.dbc"
    path.write_text('BU_: A\nBO_ 1 M: 8 A\n SG_ odo : 0|32@1+ (0.001,0) [0|0] "km" A\n', encoding="cp1252")

    async def scenario():
        app = Dbc2hApp(path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert "1 Fehler" in str(app.screen.query_one("#menu").get_option_at_index(3).prompt)
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            assert isinstance(app.screen, FindingsScreen)
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, SignalScreen) and title(app).endswith("odo")
            check = str(app.screen.query_one("#check", Static).render())
            assert "LOSSY" in check and "double wählen" in check

    run(scenario())


def test_untrusted_text_is_shown_literally(tmp_path):
    path = tmp_path / "evil.dbc"
    path.write_text('BU_: A\nBO_ 1 M: 8 A\nCM_ BO_ 1 "\x1b[31mrot [b]fett[/b]";\n', encoding="latin-1")

    async def scenario():
        app = Dbc2hApp(path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await _open_message(pilot)
            info = str(app.screen.query_one("#info", Static).render())
            assert "\x1b" not in info and "�[31mrot [b]fett[/b]" in info

    run(scenario())


def test_too_small_terminal():
    async def scenario():
        app = Dbc2hApp(CHECK_FILE)
        async with app.run_test(size=(70, 20)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainMenu)
            assert app.screen.query_one("#too-small").display
            await pilot.resize_terminal(*SIZE)
            await pilot.pause()
            assert not app.screen.query_one("#too-small").display

    run(scenario())


def test_recovery_is_offered_and_restored(tmp_path):
    path = copy_check_file(tmp_path)
    original = path.read_bytes()
    recovered = original.replace(b'"Pfad', b'"Wiederhergestellt: Pfad')
    recovery = path.with_name(path.name + RECOVERY_SUFFIX)
    recovery.write_bytes(recovered)

    async def scenario():
        app = Dbc2hApp(path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, Choice)
            await pilot.click("#choice-restore")
            await pilot.pause()
            assert isinstance(app.screen, MainMenu) and app.modified
            comment = app.dbc.document.network.message_by_name("VehicleStatus").comment
            assert comment.startswith("Wiederhergestellt")

    run(scenario())
    assert not recovery.exists() and path.read_bytes() == original


def test_write_recovery_after_crash(tmp_path):
    path = copy_check_file(tmp_path)

    async def scenario():
        app = Dbc2hApp(path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await _open_message(pilot)
            await _edit_comment(app, pilot, "vor dem Absturz")
        return app

    app = run(scenario())
    saved = write_recovery(app)
    assert saved == path.with_name("net.dbc" + RECOVERY_SUFFIX)
    assert b"vor dem Absturz" in saved.read_bytes()


def test_unreadable_file_shows_message(tmp_path):
    path = tmp_path / "broken.dbc"
    path.write_text("BO_ x M: 8 A\n", encoding="cp1252")

    async def scenario():
        app = Dbc2hApp(path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MessageBox)
            assert "Zeile 1" in str(app.screen.query_one(".dialog-text", Static).render())
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, StartScreen)

    run(scenario())


def test_locked_file_keeps_new_content(tmp_path, monkeypatch):
    path = copy_check_file(tmp_path)

    def locked(src, dst):
        raise PermissionError(13, "locked")

    async def scenario():
        app = Dbc2hApp(path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await _open_message(pilot)
            await _edit_comment(app, pilot, "gesperrt")
            monkeypatch.setattr(textio.os, "replace", locked)
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, MessageBox)
            message = str(app.screen.query_one(".dialog-text", Static).render())
            assert "gesperrt" in message and ".tmp" in message
            assert app.modified

    run(scenario())
