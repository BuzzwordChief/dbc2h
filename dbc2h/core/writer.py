"""Canonical rendering of modelled statements (used for new and edited statements).

The format follows what Vector CANdb++ writes. Unchanged statements are never
rendered; Document.text() emits their original text.
"""

from __future__ import annotations

from dbc2h.core.lexer import escape
from dbc2h.core.model import (
    AttributeDefinition,
    AttributeType,
    AttributeValue,
    Message,
    Network,
    Node,
    NO_NODE,
    Number,
    ObjectType,
    Signal,
    ValueTable,
)
from dbc2h.core.statements import AttributeTarget, Statement, StatementClass


def quote(s: str) -> str:
    return f'"{escape(s)}"'


def _value(v: AttributeValue) -> str:
    return v.text if isinstance(v, Number) else quote(v)


def _pairs(entries: list[tuple[Number, str]]) -> str:
    return "".join(f" {n.text} {quote(d)}" for n, d in entries)


def _object_part(obj) -> str:
    match obj:
        case Network():
            return ""
        case Node():
            return f"BU_ {obj.name} "
        case Message():
            return f"BO_ {obj.raw_id} "
        case Signal():
            return f"SG_ {obj.message.raw_id} {obj.name} "
    raise TypeError(f"no object reference for {type(obj).__name__}")


def render(stmt: Statement, newline: str = "\n") -> str:
    if stmt.cls is not StatementClass.MODELLED:
        assert stmt.text is not None
        return stmt.text
    t = stmt.target
    match stmt.kind:
        case "VERSION":
            return f"VERSION {quote(t.version or '')}"
        case "NS_":
            return "NS_ : " + "".join(f"{newline}\t{s}" for s in t.new_symbols)
        case "BS_":
            return "BS_:" + (f" {t.bit_timing}" if t.bit_timing else "")
        case "BU_":
            return "BU_:" + "".join(f" {n.name}" for n in stmt.info.get("nodes", t.nodes))
        case "VAL_TABLE_":
            table: ValueTable = t
            return f"VAL_TABLE_ {table.name}{_pairs(table.entries)} ;"
        case "BO_":
            m: Message = t
            return f"BO_ {m.raw_id} {m.name}: {m.size} {m.transmitter or NO_NODE}"
        case "SG_":
            s: Signal = t
            mux = f"{s.mux.text} " if s.mux.text else ""
            sign = "-" if s.is_signed else "+"
            receivers = ",".join(s.receivers) or NO_NODE
            return (
                f"SG_ {s.name} {mux}: {s.start_bit}|{s.length}@{int(s.byte_order)}{sign}"
                f" ({s.factor.text},{s.offset.text}) [{s.minimum.text}|{s.maximum.text}]"
                f" {quote(s.unit)} {receivers}"
            )
        case "CM_":
            return f"CM_ {_object_part(t)}{quote(t.comment or '')};"
        case "BA_DEF_":
            d: AttributeDefinition = t
            otype = f"{d.object_type.value} " if d.object_type is not ObjectType.NETWORK else ""
            if d.type in (AttributeType.INT, AttributeType.HEX, AttributeType.FLOAT):
                args = f" {d.minimum.text} {d.maximum.text}" if d.minimum and d.maximum else ""
            elif d.type is AttributeType.ENUM:
                args = " " + ",".join(quote(c) for c in d.choices)
            else:
                args = " "
            return f"BA_DEF_ {otype} {quote(d.name)} {d.type.value}{args};"
        case "BA_DEF_DEF_":
            d = t
            return f"BA_DEF_DEF_  {quote(d.name)} {_value(d.default if d.default is not None else '')};"
        case "BA_":
            at: AttributeTarget = t
            return f"BA_ {quote(at.name)} {_object_part(at.obj)}{_value(at.obj.attributes[at.name])};"
        case "VAL_":
            s = t
            return f"VAL_ {s.message.raw_id} {s.name}{_pairs(s.values or [])} ;"
        case "SIG_VALTYPE_":
            s = t
            return f"SIG_VALTYPE_ {s.message.raw_id} {s.name} : {int(s.value_type)};"
    raise ValueError(f"cannot render statement kind {stmt.kind}")
