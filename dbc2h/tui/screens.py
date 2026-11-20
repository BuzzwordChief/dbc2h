"""Browse screens: main menu, network, nodes, messages, signals, findings, about.

Screens address model objects by ObjectRef and look them up again in refresh_model(),
because undo/redo replaces the document.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, OptionList, Static
from textual.widgets.option_list import Option

from dbc2h.core import lint, numerics, settings
from dbc2h.core.edit import ObjectRef, SetComment
from dbc2h.core.model import NO_NODE, Message, Signal
from dbc2h.tui.dialogs import InputBox
from dbc2h.tui.text import clean, text

SEVERITY_MARK = {lint.Severity.ERROR: "(!)", lint.Severity.WARNING: "(~)", lint.Severity.INFO: "(i)"}


class BrowseScreen(Screen):
    """Title bar, a panel with the content, a hint line and the footer."""

    BREADCRUMB = ""
    HINT = "<Enter> öffnet   <Esc> zurück   <?> Hilfe"
    BINDINGS = [Binding("escape", "back", "Zurück")]

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Vertical(classes="panel"):
            yield from self.body()
        yield Static(text(self.HINT), id="hint")
        yield Footer()
        yield Static(text("Das Terminal ist zu klein. dbc2h braucht mindestens 80×24 Zeichen."), id="too-small")

    def body(self) -> ComposeResult:
        yield from ()

    def on_mount(self) -> None:
        self.refresh_model()

    def breadcrumb(self) -> str:
        return self.BREADCRUMB

    def refresh_model(self) -> bool:
        """Redraw from the current document. False if the shown object no longer exists."""
        app = self.app
        name = app.dbc.path.name if app.dbc and app.dbc.path else "neue Datei"
        mark = " *" if app.modified else ""
        crumb = self.breadcrumb()
        self.query_one("#title", Static).update(text(f"dbc2h – {name}{mark}" + (f"  ›  {crumb}" if crumb else "")))
        return self.populate()

    def populate(self) -> bool:
        return True

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()

    def edit_comment(self, obj, label: str) -> None:
        if obj is None:
            return
        ref = ObjectRef.of(obj)

        def done(value: str | None) -> None:
            if value is not None:
                self.app.execute(SetComment(ref, value))

        self.app.push_screen(InputBox(f"Kommentar: {label}", "Kommentar (leer = entfernen)", obj.comment or ""), done)


def _table(*columns: str) -> DataTable:
    table = DataTable(cursor_type="row", zebra_stripes=False)
    table.add_columns(*columns)
    return table


def _row_key(table: DataTable) -> str | None:
    if table.row_count == 0:
        return None
    return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value


def _fill(table: DataTable, rows: list[tuple[str, list]]) -> None:
    """Replace all rows and keep the cursor on the same key if possible."""
    keep = _row_key(table)
    table.clear()
    for key, cells in rows:
        table.add_row(*[c if isinstance(c, Text) else text(c) for c in cells], key=key)
    keys = [k for k, _ in rows]
    if keep in keys:
        table.move_cursor(row=keys.index(keep))


# --- start and main menu --------------------------------------------------------------------------


class StartScreen(BrowseScreen):
    HINT = "<Enter> wählt   <q> beendet"

    def body(self) -> ComposeResult:
        yield Static(text("Keine Datei geöffnet."), classes="info")
        yield OptionList(
            Option("Datei öffnen …", id="open"),
            Option("Neue DBC-Datei", id="new"),
            Option("Beenden", id="quit"),
            id="menu",
        )

    def action_back(self) -> None:
        pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        match event.option.id:
            case "open":
                self.app.action_open()
            case "new":
                self.app.new_file()
            case "quit":
                self.app.action_quit()


class MainMenu(BrowseScreen):
    HINT = "<Enter> öffnet   <s> speichert   <u>/<U> rückgängig/wiederholen   <?> Hilfe   <q> beendet"

    def body(self) -> ComposeResult:
        yield OptionList(id="menu")

    def action_back(self) -> None:
        pass

    def populate(self) -> bool:
        net = self.app.dbc.document.network
        findings = self.app.findings
        errors = sum(f.severity is lint.Severity.ERROR for f in findings)
        warnings = sum(f.severity is lint.Severity.WARNING for f in findings)
        msg_mark = "(!) " if any(f.severity is lint.Severity.ERROR and f.obj is not None for f in findings) else "    "
        menu = self.query_one("#menu", OptionList)
        keep = menu.highlighted
        menu.clear_options()
        menu.add_options([
            Option(text("    Netzwerk-Einstellungen  --->"), id="network"),
            Option(text(f"    Knoten ({len(net.nodes)})  --->"), id="nodes"),
            Option(text(f"{msg_mark}Nachrichten ({len(net.messages)})  --->"), id="messages"),
            Option(text(f"{'(~) ' if errors or warnings else '    '}Prüfergebnisse "
                        f"({errors} Fehler, {warnings} Warnungen)  --->"), id="findings"),
            Option(text("    Über dbc2h  --->"), id="about"),
        ])
        menu.highlighted = keep if keep is not None else 0
        return True

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        screen = {"network": NetworkScreen, "nodes": NodesScreen, "messages": MessagesScreen,
                  "findings": FindingsScreen, "about": AboutScreen}[event.option.id]
        self.app.push_screen(screen())


# --- network and nodes -------------------------------------------------------------------------------


class NetworkScreen(BrowseScreen):
    BREADCRUMB = "Netzwerk-Einstellungen"
    HINT = "<c> Kommentar bearbeiten   <Esc> zurück"
    BINDINGS = [Binding("c", "comment", "Kommentar")]

    def body(self) -> ComposeResult:
        yield _table("Eigenschaft", "Wert")

    def populate(self) -> bool:
        dbc = self.app.dbc
        net = dbc.document.network

        def setting(name):
            return settings.get_setting(net, net, name)

        rows = [
            ("version", ["Version", net.version or ""]),
            ("comment", ["Kommentar", net.comment or "—"]),
            ("encoding", ["Encoding der Datei", dbc.format.codec + (" mit BOM" if dbc.format.bom else "")]),
            ("newline", ["Zeilenende", "CRLF" if dbc.document.newline == "\r\n" else "LF"]),
            ("prefix", ["dbc2h: Präfix", setting("dbc2h_Prefix") or "(aus dem Dateinamen)"]),
            ("float", ["dbc2h: Gleitkommatyp", setting("dbc2h_FloatType")]),
            ("eps", ["dbc2h: ε (LSB)", f"{float(setting('dbc2h_FloatEpsLsb')):g}"]),
            ("strict", ["dbc2h: strenge Quantisierung", "an" if setting("dbc2h_StrictQuantization") else "aus"]),
            ("outdir", ["dbc2h: Ausgabeverzeichnis", setting("dbc2h_OutputDir")]),
        ]
        _fill(self.query_one(DataTable), rows)
        return True

    def action_comment(self) -> None:
        self.edit_comment(self.app.dbc.document.network, "Netzwerk")


class NodesScreen(BrowseScreen):
    BREADCRUMB = "Knoten"
    HINT = "<c> Kommentar bearbeiten   <Esc> zurück"
    BINDINGS = [Binding("c", "comment", "Kommentar")]

    def body(self) -> ComposeResult:
        yield _table("Name", "Kommentar")

    def populate(self) -> bool:
        nodes = self.app.dbc.document.network.nodes
        _fill(self.query_one(DataTable), [(f"{i}:{n.name}", [n.name, n.comment or ""]) for i, n in enumerate(nodes)])
        return True

    def action_comment(self) -> None:
        key = _row_key(self.query_one(DataTable))
        if key is not None:
            node = self.app.dbc.document.network.nodes[int(key.split(":", 1)[0])]
            self.edit_comment(node, node.name)


# --- messages and signals ------------------------------------------------------------------------------


def _worst(findings: list[lint.Finding]) -> dict[int, lint.Severity]:
    out: dict[int, lint.Severity] = {}
    for f in findings:
        owners = [f.obj]
        if isinstance(f.obj, Signal):
            owners.append(f.obj.message)
        for owner in owners:
            if owner is not None and (id(owner) not in out or f.severity < out[id(owner)]):
                out[id(owner)] = f.severity
    return out


class MessagesScreen(BrowseScreen):
    BREADCRUMB = "Nachrichten"

    def body(self) -> ComposeResult:
        yield _table("", "ID", "Name", "DLC", "Sender", "Zyklus", "Signale")

    def populate(self) -> bool:
        net = self.app.dbc.document.network
        worst = _worst(self.app.findings)
        rows = []
        for m in net.messages:
            cycle = m.attributes.get("GenMsgCycleTime")
            cycle_text = f"{cycle.text} ms" if cycle is not None and not isinstance(cycle, str) and cycle.value else ""
            ident = f"0x{m.frame_id:08X} ext" if m.is_extended else f"0x{m.frame_id:03X}"
            rows.append((str(m.raw_id), [SEVERITY_MARK.get(worst.get(id(m)), ""), ident, m.name, str(m.size),
                                         m.transmitter or NO_NODE, cycle_text, str(len(m.signals))]))
        _fill(self.query_one(DataTable), rows)
        return True

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.push_screen(MessageScreen(ObjectRef("message", raw_id=int(event.row_key.value))))


class MessageScreen(BrowseScreen):
    HINT = "<Enter> Signal öffnen   <c> Kommentar bearbeiten   <Esc> zurück"
    BINDINGS = [Binding("c", "comment", "Kommentar")]

    def __init__(self, ref: ObjectRef) -> None:
        super().__init__()
        self.ref = ref

    def message(self) -> Message | None:
        return self.ref.resolve(self.app.dbc.document.network)

    def breadcrumb(self) -> str:
        msg = self.message()
        return f"Nachrichten  ›  {clean(msg.name) if msg else '?'}"

    def body(self) -> ComposeResult:
        yield Static(id="info", classes="info")
        yield _table("", "Signal", "Start", "Länge", "Order", "Faktor", "Offset", "Einheit", "C-Typ", "Numerik")

    def populate(self) -> bool:
        msg = self.message()
        if msg is None:
            return False
        net = self.app.dbc.document.network
        kind = "Extended" if msg.is_extended else "Standard"
        info = (f"ID 0x{msg.frame_id:X} ({kind})   DLC {msg.size}   Sender {msg.transmitter or NO_NODE}\n"
                f"Kommentar: {clean(msg.comment) if msg.comment else '—'}")
        self.query_one("#info", Static).update(text(info, single_line=False))
        worst = _worst(self.app.findings)
        rows = []
        for s in msg.signals:
            try:
                result = numerics.check(net, s)
                c_type, status = result.conversion.c_type or "-", result.status.value
            except ValueError:
                c_type, status = "-", "Faktor 0"
            order = "Intel" if s.byte_order == 1 else "Motorola"
            rows.append((s.name, [SEVERITY_MARK.get(worst.get(id(s)), ""), s.name, str(s.start_bit), str(s.length),
                                  order, s.factor.text, s.offset.text, s.unit, c_type, status]))
        _fill(self.query_one(DataTable), rows)
        return True

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.push_screen(SignalScreen(ObjectRef("signal", raw_id=self.ref.raw_id, name=event.row_key.value)))

    def action_comment(self) -> None:
        msg = self.message()
        if msg is not None:
            self.edit_comment(msg, msg.name)


class SignalScreen(BrowseScreen):
    HINT = "<c> Kommentar bearbeiten   <Esc> zurück"
    BINDINGS = [Binding("c", "comment", "Kommentar")]

    def __init__(self, ref: ObjectRef) -> None:
        super().__init__()
        self.ref = ref

    def signal(self) -> Signal | None:
        return self.ref.resolve(self.app.dbc.document.network)

    def breadcrumb(self) -> str:
        sig = self.signal()
        return f"Nachrichten  ›  {clean(sig.message.name)}  ›  {clean(sig.name)}" if sig else "?"

    def body(self) -> ComposeResult:
        yield Static(id="check", classes="info")
        yield _table("Eigenschaft", "Wert")

    def populate(self) -> bool:
        sig = self.signal()
        if sig is None:
            return False
        net = self.app.dbc.document.network
        try:
            result = numerics.check(net, sig)
            conv = result.conversion
            line = f"{conv.c_type or '-'} {result.summary()}"
            if result.remedies and result.status is numerics.Status.LOSSY:
                line += f"\n→ {', '.join(result.remedies)}"
            style = "bold red" if result.status is numerics.Status.LOSSY else ""
            lo, hi = conv.ranges.valid
            wire = conv.ranges.wire
        except ValueError as exc:
            line, style, lo, hi, wire = str(exc), "bold red", 0, 0, (0, 0)
        self.query_one("#check", Static).update(text(f"Prüfung: {line}", single_line=False, style=style))
        values = ", ".join(f"{v.text} = {d}" for v, d in sig.values) if sig.values else "—"
        rows = [
            ("name", ["Name", sig.name]),
            ("long", ["Langer Name", sig.long_name]),
            ("pos", ["Startbit / Länge", f"{sig.start_bit} / {sig.length}"]),
            ("order", ["Byte-Order", "Intel (little endian)" if sig.byte_order == 1 else "Motorola (big endian)"]),
            ("sign", ["Vorzeichen", "signed" if sig.is_signed else "unsigned"]),
            ("type", ["Bus-Typ", sig.value_type.name]),
            ("factor", ["Faktor / Offset", f"{sig.factor.text} / {sig.offset.text}"]),
            ("minmax", ["Min / Max", f"{sig.minimum.text} / {sig.maximum.text}"]),
            ("raw", ["Rohwerte (Leitung / gültig)", f"{wire[0]} … {wire[1]} / {lo} … {hi}"]),
            ("unit", ["Einheit", sig.unit or "—"]),
            ("mux", ["Multiplex", sig.mux.text or "—"]),
            ("receivers", ["Empfänger", ", ".join(sig.receivers) or NO_NODE]),
            ("values", ["Werte", values]),
            ("comment", ["Kommentar", sig.comment or "—"]),
        ]
        _fill(self.query_one(DataTable), rows)
        return True

    def action_comment(self) -> None:
        sig = self.signal()
        if sig is not None:
            self.edit_comment(sig, f"{sig.message.name}.{sig.name}")


# --- findings and about ----------------------------------------------------------------------------------


class FindingsScreen(BrowseScreen):
    BREADCRUMB = "Prüfergebnisse"
    HINT = "<Enter> springt zum Element   <Esc> zurück"

    def body(self) -> ComposeResult:
        yield _table("Zeile", "Regel", "Ort", "Meldung")

    def populate(self) -> bool:
        self._findings = list(self.app.findings)
        rows = [(str(i), [str(f.line or ""), f.rule, f.where, f.message]) for i, f in enumerate(self._findings)]
        _fill(self.query_one(DataTable), rows)
        return True

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        finding = self._findings[int(event.row_key.value)]
        obj = finding.obj
        if isinstance(obj, Signal):
            self.app.push_screen(MessageScreen(ObjectRef.of(obj.message)))
            self.app.push_screen(SignalScreen(ObjectRef.of(obj)))
        elif isinstance(obj, Message):
            self.app.push_screen(MessageScreen(ObjectRef.of(obj)))


class AboutScreen(BrowseScreen):
    BREADCRUMB = "Über dbc2h"
    HINT = "<Esc> zurück"

    def body(self) -> ComposeResult:
        from dbc2h.cli import version_text

        yield Static(text(version_text() + "\n\nLizenzen der gebündelten Bibliotheken: THIRD_PARTY_LICENSES.txt "
                          "in dbc2h.pyz.", single_line=False), classes="info")
