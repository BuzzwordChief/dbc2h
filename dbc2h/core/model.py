"""Data model of a DBC network.

Model objects know nothing about file syntax. The statements they came from are kept
in ``src`` (see statements.py) so that the writer can emit unchanged statements
verbatim and re-render only what was edited.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbc2h.core.statements import Statement

EXTENDED_FLAG = 0x80000000
FRAME_ID_MASK = 0x1FFFFFFF
NO_NODE = "Vector__XXX"
INDEPENDENT_SIGNALS_MESSAGE = "VECTOR__INDEPENDENT_SIG_MSG"
# CANdb++ limits names to 32 characters and stores longer ones in these attributes.
LONG_NAME_ATTRIBUTES = {
    "node": "SystemNodeLongSymbol",
    "message": "SystemMessageLongSymbol",
    "signal": "SystemSignalLongSymbol",
}


def _long_name(obj, kind: str) -> str:
    value = obj.attributes.get(LONG_NAME_ATTRIBUTES[kind])
    return value if isinstance(value, str) and value else obj.name


class ByteOrder(enum.IntEnum):
    MOTOROLA = 0  # "@0", big endian
    INTEL = 1     # "@1", little endian


class ValueType(enum.IntEnum):
    """SIG_VALTYPE_: how the raw bits are interpreted."""

    INTEGER = 0
    FLOAT32 = 1
    FLOAT64 = 2


class ObjectType(enum.StrEnum):
    """Object type keyword of BA_DEF_, BA_ and CM_. The network has none."""

    NETWORK = ""
    NODE = "BU_"
    MESSAGE = "BO_"
    SIGNAL = "SG_"
    ENVVAR = "EV_"


class AttributeType(enum.StrEnum):
    INT = "INT"
    HEX = "HEX"
    FLOAT = "FLOAT"
    STRING = "STRING"
    ENUM = "ENUM"


@dataclass(frozen=True, slots=True)
class Number:
    """A number as written in the file: exact value plus original spelling."""

    text: str
    value: Fraction

    @classmethod
    def parse(cls, text: str) -> Number:
        return cls(text, Fraction(text))

    @classmethod
    def of(cls, value: int | Fraction | str) -> Number:
        if isinstance(value, str):
            return cls.parse(value)
        value = Fraction(value)
        return cls(format_fraction(value), value)

    def is_integer(self) -> bool:
        return self.value.denominator == 1

    def __int__(self) -> int:
        if self.value.denominator != 1:
            raise ValueError(f"{self.text} is not an integer")
        return self.value.numerator


def format_fraction(value: Fraction) -> str:
    """Shortest exact decimal spelling. Raises ValueError for non-terminating decimals."""
    if value.denominator == 1:
        return str(value.numerator)
    den = value.denominator
    twos = fives = 0
    while den % 2 == 0:
        den //= 2
        twos += 1
    while den % 5 == 0:
        den //= 5
        fives += 1
    if den != 1:
        raise ValueError(f"{value} has no finite decimal representation")
    digits = max(twos, fives)
    scaled = abs(value) * 10**digits
    s = str(scaled.numerator).rjust(digits + 1, "0")
    s = f"{s[:-digits]}.{s[-digits:]}".rstrip("0").rstrip(".")
    return f"-{s}" if value < 0 else s


# An attribute value as written: a number, or a string (quoted or bare word in the file).
AttributeValue = Number | str


MUX_INDICATOR_RE = re.compile(r"^(?:m(\d+))?(M?)$")


@dataclass(frozen=True, slots=True)
class Multiplexing:
    """Mux indicator of a signal as written: "", "M", "m<n>" or "m<n>M".

    Invalid indicators found in real files (e.g. a bare "m") are kept verbatim for the
    round-trip; they carry no multiplexing meaning and ``is_valid`` is False.
    """

    indicator: str = ""

    @classmethod
    def of(cls, multiplexed_by: int | None = None, is_multiplexer: bool = False) -> Multiplexing:
        return cls((f"m{multiplexed_by}" if multiplexed_by is not None else "") + ("M" if is_multiplexer else ""))

    @property
    def is_valid(self) -> bool:
        return MUX_INDICATOR_RE.match(self.indicator) is not None

    @property
    def is_multiplexer(self) -> bool:
        m = MUX_INDICATOR_RE.match(self.indicator)
        return bool(m and m.group(2))

    @property
    def multiplexed_by(self) -> int | None:
        m = MUX_INDICATOR_RE.match(self.indicator)
        return int(m.group(1)) if m and m.group(1) is not None else None

    @property
    def text(self) -> str:
        return self.indicator


@dataclass(eq=False, slots=True)
class AttributeDefinition:
    name: str
    object_type: ObjectType
    type: AttributeType
    minimum: Number | None = None
    maximum: Number | None = None
    choices: list[str] = field(default_factory=list)
    default: AttributeValue | None = None
    src: dict[str, Statement] = field(default_factory=dict, repr=False)


@dataclass(eq=False, slots=True)
class Node:
    name: str
    comment: str | None = None
    attributes: dict[str, AttributeValue] = field(default_factory=dict)
    src: dict[str, Statement] = field(default_factory=dict, repr=False)

    @property
    def long_name(self) -> str:
        return _long_name(self, "node")


@dataclass(eq=False, slots=True)
class ValueTable:
    name: str
    entries: list[tuple[Number, str]] = field(default_factory=list)
    src: dict[str, Statement] = field(default_factory=dict, repr=False)


@dataclass(eq=False, slots=True)
class Signal:
    name: str
    start_bit: int
    length: int
    byte_order: ByteOrder
    is_signed: bool
    factor: Number
    offset: Number
    minimum: Number
    maximum: Number
    unit: str = ""
    receivers: list[str] = field(default_factory=list)
    mux: Multiplexing = Multiplexing()
    value_type: ValueType = ValueType.INTEGER
    values: list[tuple[Number, str]] | None = None
    comment: str | None = None
    attributes: dict[str, AttributeValue] = field(default_factory=dict)
    message: Message | None = field(default=None, repr=False)
    src: dict[str, Statement] = field(default_factory=dict, repr=False)

    @property
    def long_name(self) -> str:
        return _long_name(self, "signal")


@dataclass(eq=False, slots=True)
class Message:
    raw_id: int  # as in the file, bit 31 marks an extended ID
    name: str
    size: int
    transmitter: str | None
    signals: list[Signal] = field(default_factory=list)
    comment: str | None = None
    attributes: dict[str, AttributeValue] = field(default_factory=dict)
    src: dict[str, Statement] = field(default_factory=dict, repr=False)

    @property
    def long_name(self) -> str:
        return _long_name(self, "message")

    @property
    def is_extended(self) -> bool:
        return bool(self.raw_id & EXTENDED_FLAG)

    @property
    def frame_id(self) -> int:
        return self.raw_id & FRAME_ID_MASK

    @property
    def is_independent_signals(self) -> bool:
        """Vector's pseudo message that holds signals not mapped to any message."""
        return self.name == INDEPENDENT_SIGNALS_MESSAGE

    def signal(self, name: str) -> Signal | None:
        return next((s for s in self.signals if s.name == name), None)


@dataclass(eq=False, slots=True)
class Network:
    version: str | None = None
    new_symbols: list[str] = field(default_factory=list)
    bit_timing: str = ""
    nodes: list[Node] = field(default_factory=list)
    value_tables: list[ValueTable] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    attribute_definitions: list[AttributeDefinition] = field(default_factory=list)
    comment: str | None = None
    attributes: dict[str, AttributeValue] = field(default_factory=dict)
    src: dict[str, Statement] = field(default_factory=dict, repr=False)

    def node(self, name: str) -> Node | None:
        return next((n for n in self.nodes if n.name == name), None)

    def message(self, raw_id: int) -> Message | None:
        return next((m for m in self.messages if m.raw_id == raw_id), None)

    def message_by_name(self, name: str) -> Message | None:
        return next((m for m in self.messages if m.name == name), None)

    def attribute_definition(self, name: str) -> AttributeDefinition | None:
        return next((d for d in self.attribute_definitions if d.name == name), None)

    def value_table(self, name: str) -> ValueTable | None:
        return next((t for t in self.value_tables if t.name == name), None)


def snapshot(network: Network) -> dict[str, Any]:
    """Plain-data view of the model, for comparisons in tests and diffs."""

    def num(n: Number | None) -> str | None:
        return None if n is None else n.text

    def val(v: AttributeValue | None) -> Any:
        return num(v) if isinstance(v, Number) else v

    def attrs(d: dict[str, AttributeValue]) -> dict[str, Any]:
        return {k: val(v) for k, v in d.items()}

    def entries(e: list[tuple[Number, str]] | None) -> Any:
        return None if e is None else [(n.text, s) for n, s in e]

    return {
        "version": network.version,
        "new_symbols": list(network.new_symbols),
        "bit_timing": network.bit_timing,
        "comment": network.comment,
        "attributes": attrs(network.attributes),
        "nodes": [(n.name, n.comment, attrs(n.attributes)) for n in network.nodes],
        "value_tables": [(t.name, entries(t.entries)) for t in network.value_tables],
        "attribute_definitions": [
            (d.name, d.object_type.value, d.type.value, num(d.minimum), num(d.maximum), list(d.choices), val(d.default))
            for d in network.attribute_definitions
        ],
        "messages": [
            {
                "raw_id": m.raw_id,
                "name": m.name,
                "size": m.size,
                "transmitter": m.transmitter,
                "comment": m.comment,
                "attributes": attrs(m.attributes),
                "signals": [
                    {
                        "name": s.name,
                        "start_bit": s.start_bit,
                        "length": s.length,
                        "byte_order": s.byte_order.name,
                        "is_signed": s.is_signed,
                        "factor": s.factor.text,
                        "offset": s.offset.text,
                        "minimum": s.minimum.text,
                        "maximum": s.maximum.text,
                        "unit": s.unit,
                        "receivers": list(s.receivers),
                        "mux": s.mux.text,
                        "value_type": s.value_type.name,
                        "values": entries(s.values),
                        "comment": s.comment,
                        "attributes": attrs(s.attributes),
                    }
                    for s in m.signals
                ],
            }
            for m in network.messages
        ],
    }
