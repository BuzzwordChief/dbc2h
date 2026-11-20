"""Modal dialogs in whiptail style: message box, choice, input."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from dbc2h.tui.text import text


class _Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Abbrechen")]

    def action_cancel(self) -> None:
        self.dismiss(None)


class MessageBox(_Dialog):
    """Information with a single OK button."""

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._title, self._message = title, message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(text(self._title), classes="dialog-title")
            yield Static(text(self._message, single_line=False), classes="dialog-text")
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", id="ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#ok", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class Choice(_Dialog):
    """Question with several answers; dismisses with the chosen value (None on Esc)."""

    def __init__(self, title: str, message: str, choices: list[tuple[str, str]]) -> None:
        super().__init__()
        self._title, self._message, self._choices = title, message, choices

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(text(self._title), classes="dialog-title")
            yield Static(text(self._message, single_line=False), classes="dialog-text")
            with Horizontal(classes="dialog-buttons"):
                for label, value in self._choices:
                    yield Button(label, id=f"choice-{value}")

    def on_mount(self) -> None:
        self.query(Button).first().focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id.removeprefix("choice-"))


class InputBox(_Dialog):
    """One line of input; dismisses with the text (None on Esc)."""

    def __init__(self, title: str, prompt: str, value: str = "") -> None:
        super().__init__()
        self._title, self._prompt, self._value = title, prompt, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(text(self._title), classes="dialog-title")
            yield Static(text(self._prompt), classes="dialog-text")
            yield Input(value=self._value, id="input")
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", id="ok", variant="primary")
                yield Button("Abbrechen", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self.query_one("#input", Input).value if event.button.id == "ok" else None)

