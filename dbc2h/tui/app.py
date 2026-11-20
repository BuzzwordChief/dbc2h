"""The dbc2h Textual application.

M0 state: a styled hello-world that proves the bundled stylesheet loads from
inside dbc2h.pyz. The menu tree follows in M3.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Static

from dbc2h import __version__


def load_stylesheet(name: str = "dbc2h.tcss") -> str:
    """Read a stylesheet shipped with the package.

    Textual reads CSS_PATH with plain open(), which fails inside a zip archive.
    importlib.resources works in both the source tree and dbc2h.pyz.
    """
    return files("dbc2h.tui").joinpath(name).read_text(encoding="utf-8")


class Dbc2hApp(App[int]):
    CSS = load_stylesheet()
    TITLE = "dbc2h"
    BINDINGS = [Binding("q", "quit", "Beenden")]

    def __init__(self, dbc_path: Path | None = None, exit_after: float | None = None) -> None:
        super().__init__()
        self._dbc_path = dbc_path
        self._exit_after = exit_after

    def compose(self) -> ComposeResult:
        file_text = str(self._dbc_path) if self._dbc_path else "keine Datei geöffnet"
        with Vertical(id="dialog"):
            yield Static(f" dbc2h {__version__} ", id="title")
            yield Static(
                "Grundgerüst (Meilenstein M0).\n\n"
                f"Datei: {file_text}\n\n"
                "<q> beendet das Programm.",
                id="welcome",
                markup=False,
            )
        yield Footer()

    def on_mount(self) -> None:
        if self._exit_after is not None:
            self.set_timer(self._exit_after, lambda: self.exit(0))

    def action_quit(self) -> None:
        self.exit(0)


def run(dbc_path: Path | None = None, exit_after: float | None = None) -> int:
    result = Dbc2hApp(dbc_path, exit_after).run()
    return result if result is not None else 0
