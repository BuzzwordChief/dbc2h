"""A parsed DBC file: the model plus its statements in file order.

Low-level primitives for keeping statements and model in sync. They do not record
undo information; the edit commands of a later milestone build on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dbc2h.core.model import AttributeValue, Message, Network, Signal
from dbc2h.core.statements import KIND_ORDER, AttributeTarget, Statement, StatementClass


def detect_newline(text: str) -> str:
    crlf = text.count("\r\n")
    return "\r\n" if crlf and crlf * 2 >= text.count("\n") else "\n"


@dataclass(eq=False)
class Document:
    network: Network
    statements: list[Statement]
    trailer: str = ""  # text after the last statement
    newline: str = "\n"
    warnings: list[str] = field(default_factory=list)

    # --- rendering ------------------------------------------------------------------

    def text(self) -> str:
        from dbc2h.core.writer import render

        parts = []
        for s in self.statements:
            parts.append(s.prefix)
            parts.append(render(s, self.newline) if s.dirty or s.text is None else s.text)
        parts.append(self.trailer)
        return "".join(parts)

    def commit_rendering(self) -> None:
        """After saving: rendered text becomes the new original text."""
        from dbc2h.core.writer import render

        for s in self.statements:
            if s.dirty or s.text is None:
                s.text = render(s, self.newline)
                s.dirty = False

    # --- statement primitives -------------------------------------------------------

    def mark_dirty(self, stmt: Statement) -> None:
        assert stmt.cls is StatementClass.MODELLED, "only modelled statements can be re-rendered"
        stmt.dirty = True

    def remove(self, stmt: Statement) -> None:
        self.statements.remove(stmt)

    def insert(self, stmt: Statement) -> None:
        """Insert a new statement at its canonical place.

        After the last statement of the same kind (for SG_: of the same message), else
        after the last statement of an earlier kind in KIND_ORDER, else at the start.
        """
        index = self._insert_index(stmt)
        first_of_kind = not any(s.kind == stmt.kind for s in self.statements)
        stmt.prefix = self._new_prefix(stmt.kind, first_of_kind)
        self.statements.insert(index, stmt)

    def _insert_index(self, stmt: Statement) -> int:
        if stmt.kind == "SG_":
            msg: Message = stmt.target.message
            owned = [i for i, s in enumerate(self.statements)
                     if (s.kind == "BO_" and s.target is msg) or (s.kind == "SG_" and s.target.message is msg)]
            if owned:
                return owned[-1] + 1
        same = [i for i, s in enumerate(self.statements) if s.kind == stmt.kind]
        if same:
            last = same[-1]
            if stmt.kind == "BO_":  # after the signals of the last message
                while last + 1 < len(self.statements) and self.statements[last + 1].kind == "SG_":
                    last += 1
            return last + 1
        rank = KIND_ORDER.index(stmt.kind) if stmt.kind in KIND_ORDER else len(KIND_ORDER)
        earlier = [i for i, s in enumerate(self.statements)
                   if s.kind in KIND_ORDER and KIND_ORDER.index(s.kind) <= rank]
        return earlier[-1] + 1 if earlier else 0

    def _new_prefix(self, kind: str, first_of_kind: bool) -> str:
        nl = self.newline
        if kind == "SG_":
            return nl + " "
        if first_of_kind or kind == "BO_":  # blank line between sections and messages
            return nl + nl
        return nl

    def new_statement(self, kind: str, target, owner, key: str) -> Statement:
        stmt = Statement(kind=kind, cls=StatementClass.MODELLED, text=None, target=target)
        owner.src[key] = stmt
        self.insert(stmt)
        return stmt

    # --- attribute values -------------------------------------------------------------

    def set_attribute(self, obj, name: str, value: AttributeValue) -> None:
        """Set attribute ``name`` on a network, node, message or signal (BA_)."""
        obj.attributes[name] = value
        key = f"BA_:{name}"
        stmt = obj.src.get(key)
        if stmt is None or stmt not in self.statements:
            self.new_statement("BA_", AttributeTarget(obj, name), obj, key)
        else:
            self.mark_dirty(stmt)

    def remove_attribute(self, obj, name: str) -> None:
        """Remove the explicit value, so the definition's default applies again."""
        obj.attributes.pop(name, None)
        key = f"BA_:{name}"
        for stmt in [s for s in self.statements if s.kind == "BA_" and isinstance(s.target, AttributeTarget)
                     and s.target.obj is obj and s.target.name == name]:
            self.remove(stmt)
        obj.src.pop(key, None)

    def all_signals(self) -> list[Signal]:
        return [s for m in self.network.messages for s in m.signals]
