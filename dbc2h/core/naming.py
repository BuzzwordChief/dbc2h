"""C identifiers for generated code.

Message and signal names become snake_case (long names from System*LongSymbol take
precedence), macros use the upper-case form. Every global identifier carries the
prefix, so names starting with a digit are fine there; struct members get a leading
underscore instead. Identifiers never contain "__" (reserved in C++).
"""

from __future__ import annotations

import re
import unicodedata

from dbc2h.core import settings
from dbc2h.core.model import Message, Network, Signal

C_KEYWORDS = frozenset("""
    auto break case char const continue default do double else enum extern float for goto if inline int
    long register restrict return short signed sizeof static struct switch typedef union unsigned void
    volatile while _Alignas _Alignof _Atomic _Bool _Complex _Generic _Imaginary _Noreturn _Static_assert
    _Thread_local
""".split())
CPP_KEYWORDS = frozenset("""
    alignas alignof and and_eq asm bitand bitor bool catch char16_t char32_t char8_t class compl concept
    consteval constexpr constinit const_cast co_await co_return co_yield decltype delete dynamic_cast
    explicit export false friend mutable namespace new noexcept not not_eq nullptr operator or or_eq
    private protected public reinterpret_cast requires static_assert static_cast template this
    thread_local throw true try typeid typename using virtual wchar_t xor xor_eq
""".split())
KEYWORDS = C_KEYWORDS | CPP_KEYWORDS

# Fixed identifiers of the generated header, after the prefix.
RESERVED_SUFFIXES = {"status_t", "round_f32_i32", "round_f32_i64", "round_f64_i64"}

_CAMEL_1 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_2 = re.compile(r"([a-z])([A-Z])")
_CAMEL_3 = re.compile(r"([0-9])([A-Z][a-z])")
_INVALID = re.compile(r"[^A-Za-z0-9_]")
_UNDERSCORES = re.compile(r"_{2,}")


def snake(name: str) -> str:
    s = _INVALID.sub("_", name)
    s = _CAMEL_1.sub(r"\1_\2", s)
    s = _CAMEL_2.sub(r"\1_\2", s)
    s = _CAMEL_3.sub(r"\1_\2", s)
    s = _UNDERSCORES.sub("_", s).strip("_").lower()
    return s or "unnamed"


def member(name: str) -> str:
    """Struct member for a signal."""
    s = snake(name)
    if s[0].isdigit():
        s = "_" + s
    if s in KEYWORDS:
        s += "_"
    return s


def prefix(network: Network, source_stem: str) -> str:
    """Prefix from dbc2h_Prefix, else from the file name."""
    configured = settings.get_setting(network, network, "dbc2h_Prefix")
    p = snake(configured or source_stem)
    return p if not p[0].isdigit() else f"dbc_{p}"


def message_name(msg: Message) -> str:
    return snake(msg.long_name)


def message_identifiers(p: str, msg: Message) -> list[str]:
    m = message_name(msg)
    up = f"{p}_{m}".upper()
    return [f"{p}_{m}_t", f"{p}_{m}_pack", f"{p}_{m}_unpack",
            f"{up}_ID", f"{up}_IS_EXTENDED", f"{up}_DLC", f"{up}_CYCLE_MS"]


def signal_identifiers(p: str, msg: Message, sig: Signal) -> list[str]:
    up = f"{p}_{message_name(msg)}_{snake(sig.long_name)}".upper()
    return [f"{up}_RAW_MIN", f"{up}_RAW_MAX"]


def collisions(p: str, messages: list[tuple[Message, list[Signal]]]) -> list[tuple[str, object, object]]:
    """Identifiers generated twice: (identifier, first owner, second owner)."""
    owners: dict[str, object] = {f"{p}_{s}": "dbc2h" for s in RESERVED_SUFFIXES}
    out: list[tuple[str, object, object]] = []

    def claim(ident: str, owner: object) -> None:
        if ident in owners and owners[ident] is not owner:
            out.append((ident, owners[ident], owner))
        else:
            owners[ident] = owner

    for msg, sigs in messages:
        for ident in message_identifiers(p, msg):
            claim(ident, msg)
        members: dict[str, Signal] = {}
        for sig in sigs:
            name = member(sig.long_name)
            if name in members:
                out.append((f"{message_name(msg)}.{name}", members[name], sig))
            members[name] = sig
            for ident in signal_identifiers(p, msg, sig):
                claim(ident, sig)
    return out


# --- ASCII-only comments ---------------------------------------------------------------------

_ASCII_MAP = {"°": "deg", "µ": "u", "²": "^2", "³": "^3", "ß": "ss", "€": "EUR", "±": "+/-", "–": "-", "—": "-",
              "„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'", "…": "...", "×": "x", "÷": "/"}


def ascii_text(text: str) -> str:
    """Comment text that is plain ASCII (MSVC with /WX rejects other bytes in some code pages)."""
    out = []
    for ch in text:
        if ch in _ASCII_MAP:
            out.append(_ASCII_MAP[ch])
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        if base.isascii() and base.isprintable():
            out.append(base)
        elif ch in "\r\n\t":
            out.append(" ")
        else:
            out.append("?")
    return "".join(out).replace("*/", "* /").replace("/*", "/ *")

