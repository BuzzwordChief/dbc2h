"""Top-level statements of a DBC file.

Every statement belongs to one of three classes (PLAN.md 2.3):

MODELLED     Fully represented by model objects. ``target`` says what the statement
             renders; the writer re-renders it from the model when ``dirty`` is set.
REFERENCING  Kept as original text. ``refs`` gives the position of every reference to a
             node, message, signal, environment variable or attribute inside ``text``,
             so that renames can patch it. Statements that refer to objects that do not
             exist (orphans) are also kept in this class.
OPAQUE       Unknown to the parser, kept verbatim.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any


class StatementClass(enum.Enum):
    MODELLED = "modelled"
    REFERENCING = "referencing"
    OPAQUE = "opaque"


class RefKind(enum.StrEnum):
    MESSAGE = "message"      # name holds the raw message ID as written
    SIGNAL = "signal"        # message_id holds the raw ID of the owning message
    NODE = "node"
    ENVVAR = "envvar"
    ATTRIBUTE = "attribute"


@dataclass(frozen=True, slots=True)
class Ref:
    kind: RefKind
    name: str
    start: int  # offsets into Statement.text
    end: int
    message_id: int | None = None


@dataclass(frozen=True, slots=True)
class AttributeTarget:
    """Target of a BA_ statement: attribute ``name`` of model object ``obj``."""

    obj: Any
    name: str


# Canonical order of statement kinds in a DBC file. New statements without a sibling
# of the same kind are placed after the last statement of an earlier kind.
KIND_ORDER = [
    "VERSION", "NS_", "BS_", "BU_", "VAL_TABLE_", "BO_", "SG_", "BO_TX_BU_", "EV_",
    "ENVVAR_DATA_", "CM_", "BA_DEF_", "BA_DEF_REL_", "BA_DEF_DEF_", "BA_DEF_DEF_REL_",
    "BA_", "BA_REL_", "VAL_", "SIG_GROUP_", "SIG_VALTYPE_", "SG_MUL_VAL_",
]


@dataclass(eq=False, slots=True)
class Statement:
    kind: str
    cls: StatementClass
    text: str | None  # original text; None for statements created after loading
    prefix: str = ""  # whitespace and // comments before the statement
    line: int = 0
    target: Any = None
    refs: tuple[Ref, ...] = ()
    info: dict[str, Any] = field(default_factory=dict)
    dirty: bool = False

    def replace_ref(self, ref: Ref, new_text: str) -> None:
        """Replace the text of one reference and shift the positions of later ones."""
        assert self.text is not None and ref in self.refs
        delta = len(new_text) - (ref.end - ref.start)
        self.text = self.text[: ref.start] + new_text + self.text[ref.end :]
        shifted = []
        for r in self.refs:
            if r is ref:
                shifted.append(replace(r, name=new_text, end=r.end + delta))
            elif r.start >= ref.end:
                shifted.append(replace(r, start=r.start + delta, end=r.end + delta))
            else:
                shifted.append(r)
        self.refs = tuple(shifted)
