"""Lint rules (PLAN.md 5).

Errors prevent wrong generated code and block generation. Warnings have a defined
behaviour and block nothing. Messages and signals excluded with dbc2h_MsgIgnore /
dbc2h_SigIgnore only get the file-integrity rules (duplicate names, dangling references).
"""

from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass

from dbc2h.core import layout, naming, numerics, settings
from dbc2h.core.document import Document
from dbc2h.core.model import NO_NODE, Message, Signal, ValueType
from dbc2h.core.statements import RefKind, Statement, StatementClass

STANDARD_ID_MAX = 0x7FF
VALID_SIZES = set(range(9)) | {12, 16, 20, 24, 32, 48, 64}  # classic CAN and CAN FD


class Severity(enum.IntEnum):
    ERROR = 0
    WARNING = 1
    INFO = 2

    @property
    def label(self) -> str:
        return {0: "Fehler", 1: "Warnung", 2: "Info"}[self.value]


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    severity: Severity
    where: str  # e.g. "VehicleStatus.speed"
    message: str
    line: int | None = None
    obj: object = None

    def format(self) -> str:
        pos = f"Zeile {self.line}: " if self.line else ""
        return f"{pos}{self.rule} {self.where}: {self.message}"


def _line(obj, key: str) -> int | None:
    stmt = obj.src.get(key) if obj is not None else None
    return stmt.line if stmt is not None else None


def _sig_where(sig: Signal) -> str:
    return f"{sig.message.name}.{sig.name}"


class _Linter:
    def __init__(self, doc: Document) -> None:
        self.doc = doc
        self.net = doc.network
        self.out: list[Finding] = []

    def add(self, rule: str, severity: Severity, where: str, message: str, obj=None, key: str | None = None,
            line: int | None = None) -> None:
        if line is None and obj is not None and key is not None:
            line = _line(obj, key)
        self.out.append(Finding(rule, severity, where, message, line, obj))

    def error(self, rule, where, message, obj=None, key=None, line=None):
        self.add(rule, Severity.ERROR, where, message, obj, key, line)

    def warning(self, rule, where, message, obj=None, key=None, line=None):
        self.add(rule, Severity.WARNING, where, message, obj, key, line)

    # ------------------------------------------------------------------------------

    def ignored(self, obj) -> bool:
        if isinstance(obj, Message):
            return bool(settings.get_setting(self.net, obj, "dbc2h_MsgIgnore")) or obj.is_independent_signals
        return bool(settings.get_setting(self.net, obj, "dbc2h_SigIgnore")) or self.ignored(obj.message)

    def run(self) -> list[Finding]:
        self.names()
        self.references()
        active = [m for m in self.net.messages if not self.ignored(m)]
        self.duplicate_ids(active)
        extended_mux = {s.info.get("message_id") for s in self.doc.statements if s.kind == "SG_MUL_VAL_"}
        for msg in active:
            self.message(msg, msg.raw_id in extended_mux)
        self.identifiers(active)
        self.out.sort(key=lambda f: (f.line or 0, f.severity, f.rule))
        return self.out

    def names(self) -> None:
        for name, count in Counter(m.name for m in self.net.messages).items():
            if count > 1:
                msg = self.net.message_by_name(name)
                self.error("E107", name, f"Nachrichtenname kommt {count}× vor", msg, "BO_")
        for msg in self.net.messages:
            for name, count in Counter(s.name for s in msg.signals).items():
                if count > 1:
                    self.error("E107", f"{msg.name}.{name}", f"Signalname kommt in der Nachricht {count}× vor", msg, "BO_")
        bu_line = _line(self.net, "BU_")
        for name, count in Counter(n.name for n in self.net.nodes).items():
            if count > 1:
                self.warning("W107", name, f"Knoten ist {count}× in BU_ aufgeführt", line=bu_line)
        named = [(n, n.name, bu_line) for n in self.net.nodes]
        for msg in self.net.messages:
            named.append((msg, msg.name, _line(msg, "BO_")))
            named.extend((sig, _sig_where(sig), _line(sig, "SG_")) for sig in msg.signals)
        for obj, where, line in named:
            if obj.name[:1].isdigit():
                self.warning("W406", where, f"Name '{obj.name}' beginnt mit einer Ziffer (in DBC nicht erlaubt)",
                             obj, line=line)

    def references(self) -> None:
        envvars = {r.name for s in self.doc.statements if s.kind == "EV_" for r in s.refs[:1]}
        rel_attrs = {r.name for s in self.doc.statements if s.kind == "BA_DEF_REL_" for r in s.refs}
        nodes = {n.name for n in self.net.nodes} | {NO_NODE}
        for stmt in self.doc.statements:
            if stmt.cls is not StatementClass.REFERENCING:
                continue
            for ref in stmt.refs:
                if not self._resolves(stmt, ref, nodes, envvars, rel_attrs):
                    self.error("E108", stmt.kind, f"Verweis auf nicht vorhandenes Objekt: {ref.kind.value} '{ref.name}'",
                               line=stmt.line)
                    break

    def _resolves(self, stmt: Statement, ref, nodes, envvars, rel_attrs) -> bool:
        match ref.kind:
            case RefKind.MESSAGE:
                return self.net.message(int(ref.name)) is not None
            case RefKind.SIGNAL:
                msg = self.net.message(ref.message_id)
                return msg is not None and msg.signal(ref.name) is not None
            case RefKind.NODE:
                return ref.name in nodes
            case RefKind.ENVVAR:
                return ref.name in envvars
            case RefKind.ATTRIBUTE:
                if stmt.kind in ("BA_REL_", "BA_DEF_DEF_REL_", "BA_DEF_REL_"):
                    return ref.name in rel_attrs
                return self.net.attribute_definition(ref.name) is not None
        return True

    def identifiers(self, messages: list[Message]) -> None:
        """E301: two objects would get the same C identifier (checked with a neutral prefix)."""
        groups = [(m, [s for s in m.signals if not self.ignored(s)]) for m in messages]
        reported: set[tuple[int, int]] = set()
        for ident, first, second in naming.collisions("p", groups):
            if (id(first), id(second)) in reported:
                continue
            reported.add((id(first), id(second)))
            where = f"{second.message.name}.{second.name}" if isinstance(second, Signal) else second.name
            other = first if isinstance(first, str) else (
                f"{first.message.name}.{first.name}" if isinstance(first, Signal) else first.name)
            key = "SG_" if isinstance(second, Signal) else "BO_"
            shown = ident[2:] if ident.startswith(("p_", "P_")) else ident
            self.error("E301", where, f"gleicher C-Bezeichner wie {other} ('{shown}')", second, key)

    def duplicate_ids(self, messages: list[Message]) -> None:
        for raw_id, count in Counter(m.raw_id for m in messages).items():
            if count > 1:
                msg = self.net.message(raw_id)
                self.error("E106", msg.name, f"ID 0x{msg.frame_id:X} kommt {count}× vor", msg, "BO_")

    def message(self, msg: Message, extended_mux: bool) -> None:
        if not msg.is_extended and msg.frame_id > STANDARD_ID_MAX:
            self.error("E112", msg.name, f"Standard-ID 0x{msg.frame_id:X} ist größer als 0x7FF (fehlt das Extended-Flag?)",
                       msg, "BO_")
        if msg.size not in VALID_SIZES:
            self.error("E102", msg.name, f"DLC {msg.size} ist keine gültige CAN-/CAN-FD-Länge", msg, "BO_")
        signals = [s for s in msg.signals if not self.ignored(s)]
        if extended_mux:
            self.error("E110", msg.name, "erweitertes Multiplexing (SG_MUL_VAL_) wird vom Generator nicht unterstützt",
                       msg, "BO_")
        else:
            self.multiplexing(msg, signals)
        for a, b, common in layout.overlaps(s for s in signals if s.length >= 1):
            self.error("E101", f"{msg.name}.{a.name}", f"überlappt mit {b.name} in Bit {', '.join(map(str, common[:8]))}"
                       + (" …" if len(common) > 8 else ""), a, "SG_")
        for sig in signals:
            self.signal(sig)

    def multiplexing(self, msg: Message, signals: list[Signal]) -> None:
        for sig in signals:
            if not sig.mux.is_valid:
                self.error("E111", _sig_where(sig), f"ungültige Multiplex-Angabe '{sig.mux.text}'", sig, "SG_")
        selectors = [s for s in signals if s.mux.is_valid and s.mux.is_multiplexer]
        muxed = [s for s in signals if s.mux.is_valid and s.mux.multiplexed_by is not None]
        if len(selectors) > 1:
            self.error("E109", msg.name, f"mehrere Multiplexer ({', '.join(s.name for s in selectors)}) ohne SG_MUL_VAL_",
                       msg, "BO_")
        elif muxed and not selectors:
            self.error("E109", msg.name, "gemultiplexte Signale ohne Multiplexer-Signal (M)", msg, "BO_")
        elif selectors:
            sel = selectors[0]
            top = (1 << sel.length) - 1
            for s in muxed:
                if s is not sel and s.mux.multiplexed_by > top:
                    self.error("E109", _sig_where(s), f"Mux-Wert {s.mux.multiplexed_by} passt nicht in {sel.name} "
                               f"({sel.length} Bit)", s, "SG_")

    def signal(self, sig: Signal) -> None:
        where = _sig_where(sig)
        if not 1 <= sig.length <= layout.MAX_SIGNAL_LENGTH:
            self.error("E113", where, f"Länge {sig.length} ist ungültig (1 … 64)", sig, "SG_")
            return
        expected = {ValueType.FLOAT32: 32, ValueType.FLOAT64: 64}.get(sig.value_type)
        if expected and sig.length != expected:
            self.error("E113", where, f"IEEE-Signal braucht {expected} Bit, hat {sig.length}", sig, "SG_")
        if not layout.fits(sig, sig.message.size):
            need = layout.bytes_needed(sig)
            self.error("E102", where, f"reicht über das DLC hinaus (braucht {need} Byte, DLC {sig.message.size})",
                       sig, "SG_")
        if sig.factor.value == 0:
            self.error("E105", where, "Faktor 0", sig, "SG_")
            return
        rng = numerics.ranges(sig)
        if rng.issue:
            self.warning(rng.issue, where, rng.detail, sig, "SG_")
        result = numerics.check(self.net, sig)
        if result.status is numerics.Status.LOSSY:
            hint = f" → {', '.join(result.remedies)}" if result.remedies else ""
            self.error(result.rule or "E201", where, result.reason + hint, sig, "SG_")


def lint(doc: Document) -> list[Finding]:
    return _Linter(doc).run()


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity is Severity.ERROR for f in findings)

