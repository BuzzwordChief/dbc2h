"""The dbc2h Textual application: file handling, undo/redo and navigation (M3)."""

from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

from textual import events
from textual.app import App
from textual.binding import Binding

from dbc2h.core import lint, textio
from dbc2h.core.dbcfile import DbcFile
from dbc2h.core.edit import Command, EditError, History
from dbc2h.core.lexer import DbcSyntaxError
from dbc2h.core.parser import parse
from dbc2h.tui.dialogs import Choice, InputBox, MessageBox
from dbc2h.tui.screens import BrowseScreen, MainMenu, StartScreen

MIN_WIDTH, MIN_HEIGHT = 80, 24
RECOVERY_SUFFIX = ".dbc2h-recovery"

HELP_TEXT = """Tasten
  Pfeiltasten     navigieren
  Enter           öffnen / auswählen
  Esc             zurück
  c               Kommentar bearbeiten (wo angeboten)
  s / S           speichern / speichern unter
  u / U           rückgängig / wiederholen
  q               beenden (fragt bei ungespeicherten Änderungen)
  Strg+P          Befehlspalette"""


def load_stylesheet(name: str = "dbc2h.tcss") -> str:
    """Read a stylesheet shipped with the package.

    Textual reads CSS_PATH with plain open(), which fails inside a zip archive.
    importlib.resources works in both the source tree and dbc2h.pyz.
    """
    return files("dbc2h.tui").joinpath(name).read_text(encoding="utf-8")


def recovery_path(path: Path | None) -> Path:
    return (path.with_name(path.name + RECOVERY_SUFFIX) if path
            else Path.cwd() / f"unbenannt.dbc{RECOVERY_SUFFIX}")


class Dbc2hApp(App[int]):
    CSS = load_stylesheet()
    TITLE = "dbc2h"
    BINDINGS = [
        Binding("q", "quit", "Beenden"),
        Binding("s", "save", "Speichern"),
        Binding("S", "save_as", "Speichern unter", show=False),
        Binding("u", "undo", "Rückgängig"),
        Binding("U", "redo", "Wiederholen", show=False),
        Binding("question_mark", "help", "Hilfe"),
    ]

    def __init__(self, dbc_path: Path | None = None, exit_after: float | None = None) -> None:
        super().__init__()
        self._start_path = dbc_path
        self._exit_after = exit_after
        self.dbc: DbcFile | None = None
        self.history: History | None = None
        self._findings: list[lint.Finding] | None = None

    # --- state -----------------------------------------------------------------------

    @property
    def modified(self) -> bool:
        return bool(self.history and self.history.modified)

    @property
    def findings(self) -> list[lint.Finding]:
        if self._findings is None:
            self._findings = lint.lint(self.dbc.document) if self.dbc else []
        return self._findings

    def on_mount(self) -> None:
        self.check_size(self.size.width, self.size.height)
        self.push_screen(StartScreen())
        if self._start_path is not None:
            self.open_file(self._start_path)
        if self._exit_after is not None:
            self.set_timer(self._exit_after, lambda: self.exit(0))

    def on_resize(self, event: events.Resize) -> None:
        self.check_size(event.size.width, event.size.height)

    def check_size(self, width: int, height: int) -> None:
        """Below 80x24 every screen shows a notice instead of a broken layout (see .-too-small in the CSS)."""
        self.set_class(width < MIN_WIDTH or height < MIN_HEIGHT, "-too-small")

    def set_document(self, dbc: DbcFile, history: History | None = None) -> None:
        self.dbc = dbc
        self.history = history or History(dbc)
        self._findings = None
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if isinstance(self.screen, StartScreen):
            self.push_screen(MainMenu())
        else:
            self.refresh_screens()

    def refresh_screens(self) -> None:
        self._findings = None
        for screen in reversed(self.screen_stack):
            if isinstance(screen, BrowseScreen) and screen.is_mounted:
                if not screen.refresh_model() and screen is self.screen:
                    self.pop_screen()

    def execute(self, command: Command) -> None:
        try:
            self.history.execute(command)
        except EditError as exc:
            self.push_screen(MessageBox("Änderung nicht möglich", str(exc)))
        self.refresh_screens()

    # --- files -------------------------------------------------------------------------

    def action_open(self) -> None:
        def done(value: str | None) -> None:
            if value:
                self.open_file(Path(value).expanduser())

        self.push_screen(InputBox("Datei öffnen", "Pfad der DBC-Datei:"), done)

    def open_file(self, path: Path) -> None:
        try:
            dbc = DbcFile.load(path)
        except DbcSyntaxError as exc:
            self.push_screen(MessageBox("Datei kann nicht gelesen werden", f"{path}\n{exc}"))
            return
        except OSError as exc:
            self.push_screen(MessageBox("Datei kann nicht geöffnet werden", f"{path}\n{exc.strerror or exc}"))
            return
        recovery = recovery_path(path)
        if not recovery.is_file():
            self.set_document(dbc)
            return

        def decided(choice: str | None) -> None:
            history = None
            if choice == "restore":
                saved = dbc.document.text()
                text, _ = textio.decode(recovery.read_bytes())
                try:
                    dbc.document = parse(text)
                except DbcSyntaxError as exc:
                    self.push_screen(MessageBox("Wiederherstellung fehlgeschlagen", str(exc)))
                else:
                    history = History(dbc)
                    history.saved_text = saved
            if choice in ("restore", "discard"):
                recovery.unlink(missing_ok=True)
            self.set_document(dbc, history)

        self.push_screen(Choice(
            "Wiederherstellung",
            f"Für {path.name} gibt es ungespeicherte Änderungen aus einer abgebrochenen Sitzung "
            f"({recovery.name}). Wiederherstellen?",
            [("Wiederherstellen", "restore"), ("Verwerfen", "discard"), ("Später", "keep")],
        ), decided)

    def new_file(self) -> None:
        self.set_document(DbcFile.new())

    def action_save(self) -> None:
        if self.dbc is None:
            return
        if self.dbc.path is None:
            self.action_save_as()
        else:
            self.save_to(self.dbc.path)

    def action_save_as(self, then=None) -> None:
        if self.dbc is None:
            return

        def done(value: str | None) -> None:
            if value:
                self.save_to(Path(value).expanduser(), then=then)

        current = str(self.dbc.path) if self.dbc.path else "neu.dbc"
        self.push_screen(InputBox("Speichern unter", "Pfad der DBC-Datei:", current), done)

    def save_to(self, path: Path, *, force: bool = False, then=None) -> None:
        try:
            self.dbc.save(path, force=force)
        except textio.ExternalChangeError:
            def decided(choice: str | None) -> None:
                if choice == "overwrite":
                    self.save_to(path, force=True, then=then)
                elif choice == "reload":
                    self.open_file(path)
                elif choice == "save_as":
                    self.action_save_as(then=then)

            self.push_screen(Choice(
                "Datei wurde geändert",
                f"{path.name} wurde seit dem Laden von einem anderen Programm geändert.",
                [("Überschreiben", "overwrite"), ("Neu laden", "reload"), ("Speichern unter", "save_as"),
                 ("Abbrechen", "cancel")],
            ), decided)
            return
        except textio.SaveError as exc:
            self.push_screen(MessageBox("Speichern fehlgeschlagen", str(exc)))
            return
        except OSError as exc:
            self.push_screen(MessageBox("Speichern fehlgeschlagen", f"{path}\n{exc.strerror or exc}"))
            return
        self.history.mark_saved()
        recovery_path(path).unlink(missing_ok=True)
        self.notify(f"Gespeichert: {path.name}")
        self.refresh_screens()
        if then is not None:
            then()

    # --- editing ---------------------------------------------------------------------------

    def action_undo(self) -> None:
        if self.history is None:
            return
        done = self.history.undo()
        self.notify(f"Rückgängig: {done}" if done else "Nichts rückgängig zu machen")
        self.refresh_screens()

    def action_redo(self) -> None:
        if self.history is None:
            return
        done = self.history.redo()
        self.notify(f"Wiederholt: {done}" if done else "Nichts zu wiederholen")
        self.refresh_screens()

    def action_help(self) -> None:
        self.push_screen(MessageBox("Hilfe", HELP_TEXT))

    def action_quit(self) -> None:
        if not self.modified:
            self.exit(0)
            return

        def decided(choice: str | None) -> None:
            if choice == "save":
                if self.dbc.path is None:
                    self.action_save_as(then=lambda: self.exit(0))
                else:
                    self.save_to(self.dbc.path, then=lambda: self.exit(0))
            elif choice == "discard":
                self.exit(0)

        self.push_screen(Choice("Ungespeicherte Änderungen", "Die Datei hat ungespeicherte Änderungen.",
                                [("Speichern", "save"), ("Verwerfen", "discard"), ("Abbrechen", "cancel")]), decided)


def write_recovery(app: Dbc2hApp) -> Path | None:
    """After a crash: keep unsaved changes next to the file."""
    if app.dbc is None or not app.modified:
        return None
    target = recovery_path(app.dbc.path)
    content = app.dbc.document.text()
    try:
        data = textio.encode(content, app.dbc.format)
    except UnicodeEncodeError:
        data = content.encode("utf-8")
    target.write_bytes(data)
    return target


def run(dbc_path: Path | None = None, exit_after: float | None = None) -> int:
    app = Dbc2hApp(dbc_path, exit_after)
    try:
        result = app.run()
    finally:
        if app.return_code not in (None, 0):
            saved = write_recovery(app)
            if saved is not None:
                print(f"dbc2h ist abgestürzt. Ungespeicherte Änderungen liegen in {saved}.", file=sys.stderr)
    return result if result is not None else (app.return_code or 0)
