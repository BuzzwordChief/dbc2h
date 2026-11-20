"""Edit commands with undo/redo.

Every change to a document goes through a Command. The History stores the file text
before and after each command (memento): undo and redo restore that text by parsing
it again. That keeps undo exact without per-command inverse logic, at the cost of one
parse per undo step. Model objects are therefore addressed by ObjectRef, not held
directly, because undo replaces them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dbc2h.core.dbcfile import DbcFile
from dbc2h.core.model import Message, Network, Node, Signal
from dbc2h.core.parser import parse


class EditError(ValueError):
    """A command cannot be applied (invalid input); the document is unchanged."""


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """Stable address of a model object across undo/redo."""

    kind: str  # "network", "node", "message", "signal"
    raw_id: int | None = None
    name: str | None = None

    @classmethod
    def of(cls, obj) -> ObjectRef:
        match obj:
            case Network():
                return cls("network")
            case Node():
                return cls("node", name=obj.name)
            case Message():
                return cls("message", raw_id=obj.raw_id)
            case Signal():
                return cls("signal", raw_id=obj.message.raw_id, name=obj.name)
        raise TypeError(type(obj).__name__)

    def resolve(self, network: Network):
        match self.kind:
            case "network":
                return network
            case "node":
                return network.node(self.name)
            case "message":
                return network.message(self.raw_id)
            case "signal":
                msg = network.message(self.raw_id)
                return msg.signal(self.name) if msg else None
        return None


class Command:
    description = "Änderung"

    def apply(self, dbc: DbcFile) -> None:
        raise NotImplementedError


@dataclass
class SetComment(Command):
    target: ObjectRef
    text: str

    @property
    def description(self) -> str:  # type: ignore[override]
        return "Kommentar ändern"

    def apply(self, dbc: DbcFile) -> None:
        doc = dbc.document
        obj = self.target.resolve(doc.network)
        if obj is None:
            raise EditError("Objekt existiert nicht mehr")
        if not dbc.can_store(self.text):
            raise EditError(f"Der Text enthält Zeichen, die in {dbc.format.codec} nicht darstellbar sind")
        stmt = obj.src.get("CM_")
        alive = stmt is not None and stmt in doc.statements
        if not self.text:
            obj.comment = None
            if alive:
                doc.remove(stmt)
            obj.src.pop("CM_", None)
            return
        obj.comment = self.text
        if alive:
            doc.mark_dirty(stmt)
        else:
            doc.new_statement("CM_", obj, obj, "CM_")


@dataclass
class _Step:
    description: str
    before: str
    after: str


@dataclass
class History:
    dbc: DbcFile
    undo_steps: list[_Step] = field(default_factory=list)
    redo_steps: list[_Step] = field(default_factory=list)
    saved_text: str = ""

    def __post_init__(self) -> None:
        self.saved_text = self.dbc.document.text()  # an untouched new file holds nothing to lose

    @property
    def modified(self) -> bool:
        return self.dbc.document.text() != self.saved_text

    def execute(self, command: Command) -> None:
        before = self.dbc.document.text()
        try:
            command.apply(self.dbc)
        except Exception:
            self._restore(before)  # leave no half-applied change behind
            raise
        after = self.dbc.document.text()
        if after != before:
            self.undo_steps.append(_Step(command.description, before, after))
            self.redo_steps.clear()

    def undo(self) -> str | None:
        if not self.undo_steps:
            return None
        step = self.undo_steps.pop()
        self._restore(step.before)
        self.redo_steps.append(step)
        return step.description

    def redo(self) -> str | None:
        if not self.redo_steps:
            return None
        step = self.redo_steps.pop()
        self._restore(step.after)
        self.undo_steps.append(step)
        return step.description

    def mark_saved(self) -> None:
        self.saved_text = self.dbc.document.text()

    def _restore(self, text: str) -> None:
        self.dbc.document = parse(text)
