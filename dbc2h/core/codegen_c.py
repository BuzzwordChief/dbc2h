"""C11 code generator: one header-only file that can also be included from C++.

Design rules (PLAN.md 4): no bitfields or unions over the buffer, byte-wise shifts
independent of host endianness, range checks on integer raw values, every signal is
checked before anything is written (atomic pack), the whole frame is written, and the
output is plain ASCII with LF line endings and no timestamps.

The test file (test_<prefix>.c) uses values from the Python reference in numerics and
layout, so compiling and running it checks the generated code on a real compiler.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
import random
from dataclasses import dataclass, field
from fractions import Fraction

from dbc2h import __version__
from dbc2h.core import layout, lint, naming, numerics, settings
from dbc2h.core.document import Document
from dbc2h.core.model import Message, Signal, ValueType, format_fraction
from dbc2h.core.numerics import Conversion, Kind

INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1
INT64_MIN, INT64_MAX = -(1 << 63), (1 << 63) - 1
UINT64_MAX = (1 << 64) - 1
TYPE_LIMITS = {
    "int8_t": (-(1 << 7), (1 << 7) - 1), "uint8_t": (0, (1 << 8) - 1),
    "int16_t": (-(1 << 15), (1 << 15) - 1), "uint16_t": (0, (1 << 16) - 1),
    "int32_t": (INT32_MIN, INT32_MAX), "uint32_t": (0, (1 << 32) - 1),
    "int64_t": (INT64_MIN, INT64_MAX), "uint64_t": (0, UINT64_MAX),
}
VECTORS_PER_MESSAGE = 8


class GenerationError(Exception):
    """The DBC has errors; generation is refused."""

    def __init__(self, findings: list[lint.Finding]) -> None:
        super().__init__(f"{len(findings)} Fehler verhindern die Generierung")
        self.findings = findings


@dataclass(frozen=True, slots=True)
class Output:
    header_name: str
    header: str
    test_name: str
    test: str
    skipped: list[tuple[str, str]]  # (message, reason)


# --- C literals ------------------------------------------------------------------------------


def c_float(x: float) -> str:
    """Shortest decimal literal that the compiler rounds to exactly this float."""
    if x == 0:
        return "0.0f"
    for digits in range(1, 10):
        text = f"{x:.{digits}g}"
        if numerics.round_binary32(Fraction(text)) == x:
            break
    return _plain(text) + "f"


def _plain(text: str) -> str:
    """Positional notation for everyday magnitudes, and always a decimal point."""
    if "e" in text:
        value = Decimal(text)
        if Decimal("1e-5") <= abs(value) < Decimal("1e16"):
            text = format(value, "f")
    if "e" not in text and "." not in text:
        text += ".0"
    return text


def c_double(x: float) -> str:
    return _plain(repr(x))


def c_int(value: int, c_type: str) -> str:
    """Integer constant of the given type, valid in C and C++ and free of sign warnings."""
    if c_type == "uint64_t":
        return f"UINT64_C({value})"
    if c_type == "int64_t" or not INT32_MIN <= value <= INT32_MAX:
        if value == INT64_MIN:
            return "(-INT64_C(9223372036854775807) - 1)"
        return f"INT64_C({value})"
    if c_type.startswith("uint"):
        return f"{value}u"
    if value == INT32_MIN:
        return "(-2147483647 - 1)"
    return str(value)


def c_literal(value: float | int, kind: Kind, c_type: str) -> str:
    if kind in (Kind.FLOAT, Kind.IEEE32):
        return c_float(value)
    if kind in (Kind.DOUBLE, Kind.IEEE64):
        return c_double(value)
    return c_int(int(value), c_type)


def hex_mask(bits: int) -> str:
    return f"0x{(1 << bits) - 1:X}u"


# --- generator model ----------------------------------------------------------------------------


@dataclass
class _Sig:
    sig: Signal
    member: str
    macro: str
    conv: Conversion
    raw_type: str    # C type of the raw value: int32_t, int64_t or uint64_t
    word: str        # uint32_t or uint64_t for the bit pattern
    segments: list[tuple[int, int, int, int]]  # (byte, bit offset in byte, raw shift, bit count)

    @property
    def wire(self) -> tuple[int, int]:
        return self.conv.ranges.wire

    @property
    def valid(self) -> tuple[int, int]:
        return self.conv.ranges.valid

    @property
    def is_float(self) -> bool:
        return self.conv.kind in (Kind.FLOAT, Kind.DOUBLE)

    @property
    def identity(self) -> bool:
        return self.conv.kind is Kind.INTEGER and self.conv.factor == 1 and self.conv.offset == 0


@dataclass
class _Msg:
    msg: Message
    name: str
    type_name: str
    macro: str
    sigs: list[_Sig] = field(default_factory=list)
    cycle_ms: int | None = None


def _segments(sig: Signal) -> list[tuple[int, int, int, int]]:
    segs: list[list[int]] = []
    for i, pos in enumerate(layout.bits(sig)):
        byte, bit = divmod(pos, 8)
        if segs and segs[-1][0] == byte and segs[-1][1] + segs[-1][3] == bit:
            segs[-1][3] += 1
        else:
            segs.append([byte, bit, i, 1])
    return [tuple(s) for s in segs]


def _raw_type(sig: Signal) -> str:
    lo, hi = numerics.wire_range(sig)
    if hi > INT64_MAX:
        return "uint64_t"
    return "int32_t" if INT32_MIN <= lo and hi <= INT32_MAX else "int64_t"


# --- generation ---------------------------------------------------------------------------------


def generate(doc: Document, source_name: str, *, source_text: str | None = None) -> Output:
    """Generate header and test file. Raises GenerationError if the DBC has errors."""
    errors = [f for f in lint.lint(doc) if f.severity is lint.Severity.ERROR]
    if errors:
        raise GenerationError(errors)
    net = doc.network
    stem = source_name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    p = naming.prefix(net, stem)
    strict = bool(settings.get_setting(net, net, "dbc2h_StrictQuantization"))
    messages, skipped = [], []
    for msg in net.messages:
        if msg.is_independent_signals or settings.get_setting(net, msg, "dbc2h_MsgIgnore"):
            continue
        sigs = [s for s in msg.signals if not settings.get_setting(net, s, "dbc2h_SigIgnore")]
        if any(s.mux.is_multiplexer or s.mux.multiplexed_by is not None for s in sigs):
            skipped.append((msg.name, "Multiplexing wird ab M7 generiert"))
            continue
        if any(s.value_type is not ValueType.INTEGER for s in sigs):
            skipped.append((msg.name, "IEEE-Signale (SIG_VALTYPE_) werden ab M7 generiert"))
            continue
        name = naming.message_name(msg)
        gm = _Msg(msg, name, f"{p}_{name}_t", f"{p}_{name}".upper())
        cycle = msg.attributes.get("GenMsgCycleTime")
        if cycle is not None and not isinstance(cycle, str) and cycle.is_integer() and int(cycle) > 0:
            gm.cycle_ms = int(cycle)
        for s in sigs:
            gm.sigs.append(_Sig(s, naming.member(s.long_name), f"{gm.macro}_{naming.snake(s.long_name)}".upper(),
                                numerics.conversion(net, s), _raw_type(s),
                                "uint32_t" if s.length <= 32 else "uint64_t", _segments(s)))
        messages.append(gm)
    text = source_text if source_text is not None else doc.text()
    digest = hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()
    header = _Header(p, messages, skipped, strict, source_name, digest).render()
    test = _TestFile(p, messages, strict).render()
    for content in (header, test):
        assert content.isascii(), "generated code must be ASCII"
    return Output(f"{p}.h", header, f"test_{p}.c", test, skipped)


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line.rstrip())

    def text(self) -> str:
        return "\n".join(self.lines).rstrip("\n") + "\n"


class _Header:
    def __init__(self, p: str, messages: list[_Msg], skipped, strict: bool, source: str, digest: str) -> None:
        self.p, self.P = p, p.upper()
        self.messages, self.skipped, self.strict = messages, skipped, strict
        self.source, self.digest = source, digest
        self.w = _Writer()
        kinds = {s.conv.kind for m in messages for s in m.sigs}
        self.uses_float = Kind.FLOAT in kinds
        self.uses_double = Kind.DOUBLE in kinds

    def render(self) -> str:
        w, P = self.w, self.P
        guard = f"{P}_DBC2H_H"
        w(f"/* Generated by dbc2h {__version__} from {naming.ascii_text(self.source)}. DO NOT EDIT.")
        w(f" * Source content sha256 (line endings normalised to LF): {self.digest} */")
        w(f"#ifndef {guard}")
        w(f"#define {guard}")
        w()
        for inc in ("stdint.h", "stddef.h", "float.h", "limits.h"):
            w(f"#include <{inc}>")
        w()
        w("#ifdef __cplusplus")
        w(f"#  define {P}_STATIC_ASSERT(cond, msg) static_assert(cond, msg)")
        w('extern "C" {')
        w("#else")
        w(f"#  define {P}_STATIC_ASSERT(cond, msg) _Static_assert(cond, msg)")
        w("#endif")
        w()
        w(f'{P}_STATIC_ASSERT(CHAR_BIT == 8, "dbc2h: 8-bit bytes required");')
        if self.uses_float:
            w(f'{P}_STATIC_ASSERT(FLT_RADIX == 2 && FLT_MANT_DIG == 24, "dbc2h: IEEE-754 binary32 float required");')
        if self.uses_double:
            w(f'{P}_STATIC_ASSERT(FLT_RADIX == 2 && DBL_MANT_DIG == 53, "dbc2h: IEEE-754 binary64 double required");')
        if self.uses_float or self.uses_double:
            w("#if defined(__FAST_MATH__) || defined(_M_FP_FAST)")
            w('#  error "dbc2h: fast-math invalidates the checked conversion guarantees"')
            w("#endif")
            w("#if defined(FLT_EVAL_METHOD) && FLT_EVAL_METHOD != 0")
            w('#  error "dbc2h: excess-precision evaluation (e.g. x87) differs from the checked model"')
            w("#endif")
        w()
        w(f"typedef uint32_t {self.p}_status_t;")
        w(f"#define {P}_OK                0u")
        w(f"#define {P}_ERR_LENGTH        (1u << 0)")
        w(f"#define {P}_ERR_NOT_FINITE    (1u << 1)")
        w(f"#define {P}_ERR_RANGE         (1u << 2)")
        w(f"#define {P}_ERR_QUANTIZATION  (1u << 3)")
        w()
        if self.strict:
            w("/* Strict quantization is on (dbc2h_StrictQuantization = 1): pack() rejects values that are")
            w(" * more than eps * LSB away from the nearest raw step. */")
        else:
            w("/* pack() rounds to the nearest raw value (ties away from zero). Strict quantization is off. */")
        w()
        self.helpers()
        for name, reason in self.skipped:
            w(f"/* Message {naming.ascii_text(name)} skipped: {naming.ascii_text(reason)} */")
        if self.skipped:
            w()
        for m in self.messages:
            self.message(m)
        w("#ifdef __cplusplus")
        w("}")
        w("#endif")
        w(f"#endif /* {guard} */")
        return w.text()

    def helpers(self) -> None:
        w, p = self.w, self.p
        if self.uses_float:
            w("/* Round to nearest, ties away from zero. The argument is inside the int32_t range. */")
            w(f"static inline int32_t {p}_round_f32_i32(float x)")
            w("{")
            w("    int32_t r = (int32_t)x;")
            w("    const float frac = x - (float)r;")
            w("    if (frac >= 0.5f) {")
            w("        ++r;")
            w("    } else if (frac <= -0.5f) {")
            w("        --r;")
            w("    }")
            w("    return r;")
            w("}")
            w()
        if self.uses_double:
            w("/* Round to nearest, ties away from zero. The argument is inside the int64_t range. */")
            w(f"static inline int64_t {p}_round_f64_i64(double x)")
            w("{")
            w("    int64_t r = (int64_t)x;")
            w("    const double frac = x - (double)r;")
            w("    if (frac >= 0.5) {")
            w("        ++r;")
            w("    } else if (frac <= -0.5) {")
            w("        --r;")
            w("    }")
            w("    return r;")
            w("}")
            w()

    # --- one message ---------------------------------------------------------------------

    def message(self, m: _Msg) -> None:
        w, p, P = self.w, self.p, self.P
        msg = m.msg
        w(f"/* ---- {naming.ascii_text(msg.long_name)} " + "-" * max(4, 60 - len(msg.long_name)) + " */")
        w()
        w(f"#define {m.macro}_ID           0x{msg.frame_id:X}u")
        w(f"#define {m.macro}_IS_EXTENDED  {1 if msg.is_extended else 0}")
        w(f"#define {m.macro}_DLC          {msg.size}u")
        if m.cycle_ms is not None:
            w(f"#define {m.macro}_CYCLE_MS     {m.cycle_ms}u")
        for s in m.sigs:
            lo, hi = s.valid
            w(f"#define {s.macro}_RAW_MIN  {c_int(lo, s.raw_type)}")
            w(f"#define {s.macro}_RAW_MAX  {c_int(hi, s.raw_type)}")
        w()
        sender = f", sender {naming.ascii_text(msg.transmitter)}" if msg.transmitter else ""
        doc = naming.ascii_text(msg.comment) if msg.comment else ""
        w(f"/** {naming.ascii_text(msg.long_name)}{sender}{': ' + doc if doc else ''} */")
        w("typedef struct {")
        if not m.sigs:
            w("    char dbc2h_no_signals; /**< placeholder, the message has no signals */")
        for s in m.sigs:
            if s.sig.comment:
                w(f"    /** {naming.ascii_text(s.sig.comment)} */")
            w(f"    {s.conv.c_type:<9} {s.member}; /**< {self.describe(s)} */")
        w(f"}} {m.type_name};")
        w()
        self.pack(m)
        self.unpack(m)

    def describe(self, s: _Sig) -> str:
        sig, conv = s.sig, s.conv
        unit = f"[{naming.ascii_text(sig.unit)}] " if sig.unit else ""
        lo, hi = s.valid
        ends = sorted((lo * conv.factor + conv.offset, hi * conv.factor + conv.offset))
        rng = f"{_num(ends[0])} .. {_num(ends[1])}"
        order = "Intel" if sig.byte_order == 1 else "Motorola"
        sign = "signed" if sig.is_signed else "unsigned"
        return (f"{unit}{rng}, factor {sig.factor.text}, offset {sig.offset.text}, "
                f"{sig.length} bit {sign}, {order} @{sig.start_bit}")

    def pack(self, m: _Msg) -> None:
        w, p, P = self.w, self.p, self.P
        w(f"static inline {p}_status_t {p}_{m.name}_pack(uint8_t *buf, size_t len, const {m.type_name} *msg)")
        w("{")
        w(f"    {p}_status_t st = {P}_OK;")
        for s in m.sigs:
            w(f"    {s.raw_type} r_{s.member} = 0;")
        if m.msg.size:
            w("    size_t i;")
        if not m.sigs:
            w("    (void)msg;")
        w("    /* 1. Validate every signal. Nothing is written yet. */")
        if m.msg.size:
            w(f"    if (len < {m.macro}_DLC) {{")
            w(f"        st |= {P}_ERR_LENGTH;")
            w("    }")
        else:
            w("    (void)len;")
            w("    (void)buf;")
        for s in m.sigs:
            self.pack_signal(s)
        w(f"    if (st != {P}_OK) {{")
        w("        return st;")
        w("    }")
        w("    /* 2. Write the whole frame. Bits not covered by a signal are 0. */")
        if m.msg.size:
            w(f"    for (i = 0u; i < {m.macro}_DLC; ++i) {{")
            w("        buf[i] = 0u;")
            w("    }")
        for s in m.sigs:
            mask = f" & {hex_mask(s.sig.length)}" if s.sig.length < (32 if s.word == "uint32_t" else 64) else ""
            w("    {")
            w(f"        const {s.word} u = ({s.word})r_{s.member}{mask};")
            for byte, off, shift, n in s.segments:
                part = f"u >> {shift}" if shift else "u"
                if shift + n < s.sig.length:  # higher signal bits go to other bytes
                    part = f"({part}) & {hex_mask(n)}"
                expr = f"(uint8_t)(({part}) << {off})" if off else f"(uint8_t)({part})"
                w(f"        buf[{byte}] = (uint8_t)(buf[{byte}] | {expr});")
            w("    }")
        w(f"    return {P}_OK;")
        w("}")
        w()

    def pack_signal(self, s: _Sig) -> None:
        w, p, P = self.w, self.p, self.P
        conv = s.conv
        field_ = f"msg->{s.member}"
        lo, hi = s.valid
        w(f"    {{ /* {naming.ascii_text(s.sig.name)} */")
        if conv.kind is Kind.INTEGER:
            tmin, tmax = TYPE_LIMITS[conv.c_type]
            if s.identity:
                conds = []
                if lo > tmin:
                    conds.append(f"{field_} < {c_int(lo, conv.c_type)}")
                if hi < tmax:
                    conds.append(f"{field_} > {c_int(hi, conv.c_type)}")
                if conds:
                    w(f"        if ({' || '.join(conds)}) {{")
                    w(f"            st |= {P}_ERR_RANGE;")
                    w("        } else {")
                    w(f"            r_{s.member} = ({s.raw_type}){field_};")
                    w("        }")
                else:
                    w(f"        r_{s.member} = ({s.raw_type}){field_};")
            else:
                f_, o_ = int(conv.factor), int(conv.offset)
                ends = sorted((lo * f_ + o_, hi * f_ + o_))
                glo, ghi = ends[0] - abs(f_), ends[1] + abs(f_)
                conds = []
                if glo > tmin:
                    conds.append(f"{field_} < {c_int(glo, conv.c_type)}")
                if ghi < tmax:
                    conds.append(f"{field_} > {c_int(ghi, conv.c_type)}")
                inner = "        "
                if conds:
                    w(f"        if ({' || '.join(conds)}) {{")
                    w(f"            st |= {P}_ERR_RANGE;")
                    w("        } else {")
                    inner = "            "
                v = f"(int64_t){field_}" + (f" {_signed(c_int(abs(o_), 'int64_t'), o_, invert=True)}" if o_ else "")
                w(f"{inner}const int64_t v = {v};")
                if f_ == 1:
                    w(f"{inner}const int64_t q = v;")
                else:
                    w(f"{inner}int64_t q = v / {c_int(f_, 'int64_t')};")
                    w(f"{inner}const int64_t rem = v % {c_int(f_, 'int64_t')};")
                    if self.strict:
                        w(f"{inner}if (rem != 0) {{")
                        w(f"{inner}    st |= {P}_ERR_QUANTIZATION;")
                        w(f"{inner}}}")
                    else:
                        absf = c_int(abs(f_), "int64_t")
                        w(f"{inner}if (2 * (rem < 0 ? -rem : rem) >= {absf}) {{")
                        step = ("-1 : 1") if f_ > 0 else ("1 : -1")
                        w(f"{inner}    q += v < 0 ? {step};")
                        w(f"{inner}}}")
                w(f"{inner}if (q < {s.macro}_RAW_MIN || q > {s.macro}_RAW_MAX) {{")
                w(f"{inner}    st |= {P}_ERR_RANGE;")
                w(f"{inner}}} else {{")
                w(f"{inner}    r_{s.member} = ({s.raw_type})q;")
                w(f"{inner}}}")
                if conds:
                    w("        }")
            w("    }")
            return
        # float / double
        is_f = conv.kind is Kind.FLOAT
        T = "float" if is_f else "double"
        lit = c_float if is_f else c_double
        maxv = "FLT_MAX" if is_f else "DBL_MAX"
        rnd = f"{p}_round_f32_i32" if is_f else f"{p}_round_f64_i64"
        q_type = "int32_t" if is_f else "int64_t"
        glo, ghi = numerics.guards(conv)
        w(f"        const {T} v = {field_};")
        w(f"        if (!(v >= -{maxv} && v <= {maxv})) {{")
        w(f"            st |= {P}_ERR_NOT_FINITE;")
        w(f"        }} else if (!(v >= {lit(glo)} && v <= {lit(ghi)})) {{")
        w(f"            st |= {P}_ERR_RANGE;")
        w("        } else {")
        num = "v" if conv.offset == 0 else f"(v {_signed(lit(abs(conv.offset_lit)), conv.offset_lit, invert=True)})"
        if conv.inverse is not None:
            expr = f"{num} * {lit(conv.inverse)}"
        else:
            expr = f"{num} / {lit(conv.factor_lit)}"
        w(f"            const {q_type} q = {rnd}({expr});")
        w(f"            if (q < {s.macro}_RAW_MIN || q > {s.macro}_RAW_MAX) {{")
        w(f"                st |= {P}_ERR_RANGE;")
        if self.strict:
            eps = numerics.to_precision(conv.eps_lsb * abs(conv.factor), conv.precision)
            w("            } else {")
            w(f"                const {T} d = v - ({self.decode_expr(s, 'q')});")
            w(f"                if (d < -{lit(eps)} || d > {lit(eps)}) {{")
            w(f"                    st |= {P}_ERR_QUANTIZATION;")
            w("                } else {")
            w(f"                    r_{s.member} = ({s.raw_type})q;")
            w("                }")
            w("            }")
        else:
            w("            } else {")
            w(f"                r_{s.member} = ({s.raw_type})q;")
            w("            }")
        w("        }")
        w("    }")

    def decode_expr(self, s: _Sig, raw: str) -> str:
        conv = s.conv
        if conv.kind is Kind.INTEGER:
            if s.identity:
                return f"({conv.c_type}){raw}"
            parts = f"(int64_t){raw}"
            if conv.factor != 1:
                parts += f" * {c_int(int(conv.factor), 'int64_t')}"
            if conv.offset != 0:
                parts += f" {_signed(c_int(abs(int(conv.offset)), 'int64_t'), int(conv.offset))}"
            return f"({conv.c_type})({parts})"
        is_f = conv.kind is Kind.FLOAT
        lit = c_float if is_f else c_double
        T = "float" if is_f else "double"
        expr = f"({T}){raw} * {lit(conv.factor_lit)}"
        if conv.offset != 0:
            expr += f" {_signed(lit(abs(conv.offset_lit)), conv.offset_lit)}"
        return expr

    def unpack(self, m: _Msg) -> None:
        w, p, P = self.w, self.p, self.P
        w(f"static inline {p}_status_t {p}_{m.name}_unpack({m.type_name} *msg, const uint8_t *buf, size_t len)")
        w("{")
        w(f"    {p}_status_t st = {P}_OK;")
        if m.msg.size:
            w(f"    if (len < {m.macro}_DLC) {{")
            w(f"        return {P}_ERR_LENGTH;")
            w("    }")
        else:
            w("    (void)len;")
        if not m.sigs:
            w("    (void)msg;")
            w("    (void)buf;")
        for s in m.sigs:
            sig = s.sig
            w(f"    {{ /* {naming.ascii_text(sig.name)} */")
            w(f"        {s.word} u = 0u;")
            for byte, off, shift, n in s.segments:
                part = f"(({s.word})buf[{byte}] >> {off})" if off else f"({s.word})buf[{byte}]"
                if off + n < 8:  # other signals' bits above
                    part = f"({part} & {hex_mask(n)})"
                w(f"        u |= {part} << {shift};" if shift else f"        u |= {part};")
            lo, hi = s.wire
            if s.raw_type == "uint64_t":
                w("        {")
                w("            const uint64_t raw = u;")
            elif sig.is_signed:
                sign_bit = f"0x{1 << (sig.length - 1):X}u"
                mask = hex_mask(sig.length) if sig.length < (32 if s.word == "uint32_t" else 64) else (
                    "0xFFFFFFFFu" if s.word == "uint32_t" else "UINT64_C(0xFFFFFFFFFFFFFFFF)")
                if s.word == "uint64_t":
                    sign_bit = f"UINT64_C(0x{1 << (sig.length - 1):X})"
                    if sig.length < 64:
                        mask = f"UINT64_C(0x{(1 << sig.length) - 1:X})"
                w("        {")
                w(f"            const {s.raw_type} raw = (u & {sign_bit}) != 0u ? "
                  f"-({s.raw_type})(~u & {mask}) - 1 : ({s.raw_type})u;")
            else:
                w("        {")
                w(f"            const {s.raw_type} raw = ({s.raw_type})u;")
            vlo, vhi = s.valid
            conds = []
            if vlo > lo:
                conds.append(f"raw < {s.macro}_RAW_MIN")
            if vhi < hi:
                conds.append(f"raw > {s.macro}_RAW_MAX")
            if conds:
                w(f"            if ({' || '.join(conds)}) {{")
                w(f"                st |= {P}_ERR_RANGE;")
                w("            }")
            w(f"            msg->{s.member} = {self.decode_expr(s, 'raw')};")
            w("        }")
            w("    }")
        w("    return st;")
        w("}")
        w()


def _signed(literal_abs: str, value, invert: bool = False) -> str:
    """'+ x' or '- x' for an offset term; ``invert`` for subtraction of the offset."""
    negative = value < 0
    if invert:
        negative = not negative
    return f"- {literal_abs}" if negative else f"+ {literal_abs}"


def _num(q: Fraction) -> str:
    try:
        return format_fraction(q)
    except ValueError:
        return f"{float(q):.10g}"


# --- test file ----------------------------------------------------------------------------------


_TEST_HELPERS = {
    "close_enough": [
        "static int close_enough(double got, double want, double tol)", "{",
        "    const double d = got - want;", "    return d <= tol && d >= -tol;", "}"],
    "nan_f": [
        "static float nan_f(void)", "{", "    const uint32_t bits = 0x7FC00000u;", "    float f;",
        "    memcpy(&f, &bits, sizeof f);", "    return f;", "}"],
    "nan_d": [
        "static double nan_d(void)", "{", "    const uint64_t bits = UINT64_C(0x7FF8000000000000);", "    double d;",
        "    memcpy(&d, &bits, sizeof d);", "    return d;", "}"],
    "unchanged": [
        "static int unchanged(const uint8_t *buf, size_t n, uint8_t fill)", "{", "    size_t i;",
        "    for (i = 0u; i < n; ++i) {", "        if (buf[i] != fill) {", "            return 0;", "        }",
        "    }", "    return 1;", "}"],
}


class _TestFile:
    def __init__(self, p: str, messages: list[_Msg], strict: bool) -> None:
        self.p, self.P = p, p.upper()
        self.messages = messages
        self.strict = strict
        self.w = _Writer()

    def render(self) -> str:
        w = self.w
        w(f"/* Generated by dbc2h {__version__}: tests for {self.p}.h. DO NOT EDIT.")
        w(" * Build with the production compiler and flags, then run. Exit code 0 means all tests passed. */")
        w(f'#include "{self.p}.h"')
        w("#include <stdio.h>")
        w("#include <string.h>")
        w()
        w("static unsigned failures = 0u;")
        w()
        body = _Writer()
        self.w = body
        for m in self.messages:
            self.message(m)
        self.w = w
        used = "\n".join(body.lines)
        if "fail(" in used:
            w("static void fail(const char *test, const char *what, size_t index)")
            w("{")
            w("    ++failures;")
            w('    printf("FAIL %s: %s (vector %u)\\n", test, what, (unsigned)index);')
            w("}")
            w()
        for name, lines in _TEST_HELPERS.items():
            if f"{name}(" in used:
                for line in lines:
                    w(line)
                w()
        w.lines.extend(body.lines)
        w("int main(void)")
        w("{")
        for m in self.messages:
            w(f"    test_{m.name}();")
        w("    if (failures != 0u) {")
        w('        printf("%u failures\\n", failures);')
        w("        return 1;")
        w("    }")
        w(f'    printf("{self.p}: {len(self.messages)} messages ok\\n");')
        w("    return 0;")
        w("}")
        return w.text()

    def vectors(self, m: _Msg) -> list[list[int]]:
        rnd = random.Random(f"{self.p}:{m.msg.raw_id}:{m.msg.name}")
        out = []
        for k in range(VECTORS_PER_MESSAGE):
            row = []
            for s in m.sigs:
                lo, hi = s.valid
                if k == 0:
                    raw = lo
                elif k == 1:
                    raw = hi
                elif k == 2:
                    raw = min(max(0, lo), hi)
                else:
                    raw = rnd.randint(lo, hi)
                row.append(raw)
            out.append(row)
        return out

    def frame(self, m: _Msg, raws: list[int]) -> bytes:
        buf = bytearray(m.msg.size)
        for s, raw in zip(m.sigs, raws):
            layout.insert_raw(buf, s.sig, raw)
        return bytes(buf)

    def value_literal(self, s: _Sig, value) -> str:
        return c_literal(value, s.conv.kind, s.conv.c_type)

    def message(self, m: _Msg) -> None:
        w, p, P = self.w, self.p, self.P
        size = m.msg.size
        alloc = max(size, 1)
        vectors = self.vectors(m) if m.sigs else [[] for _ in range(VECTORS_PER_MESSAGE)]
        name = f"{m.name}"
        w(f"static void test_{m.name}(void)")
        w("{")
        w(f"    static const uint8_t frames[{len(vectors)}][{alloc}] = {{")
        for raws in vectors:
            data = self.frame(m, raws) if size else b"\x00"
            w("        {" + ", ".join(f"0x{b:02X}u" for b in data) + "},")
        w("    };")
        w(f"    static const {m.type_name} values[{len(vectors)}] = {{")
        for raws in vectors:
            if m.sigs:
                vals = [self.value_literal(s, numerics.decode(s.conv, r)) for s, r in zip(m.sigs, raws)]
            else:
                vals = ["0"]
            w("        {" + ", ".join(vals) + "},")
        w("    };")
        floats = [s for s in m.sigs if s.is_float]
        for s in floats:
            exact = [float(r * s.conv.factor + s.conv.offset) for r in (row[m.sigs.index(s)] for row in vectors)]
            w(f"    static const double exact_{s.member}[{len(vectors)}] = {{"
              + ", ".join(c_double(x) for x in exact) + "};")
        w(f"    uint8_t buf[{alloc + 1}];")
        w(f"    {m.type_name} got;")
        w(f"    {p}_status_t st;")
        w("    size_t i;")
        w(f"    for (i = 0u; i < {len(vectors)}u; ++i) {{")
        w("        memset(buf, 0xA5, sizeof buf);")
        w(f"        st = {p}_{m.name}_pack(buf, {m.macro}_DLC, &values[i]);")
        w(f"        if (st != {P}_OK || memcmp(buf, frames[i], {m.macro}_DLC) != 0 || buf[{m.macro}_DLC] != 0xA5u) {{")
        w(f'            fail("{name}", "pack", i);')
        w("        }")
        w("        memset(&got, 0, sizeof got);")
        w(f"        st = {p}_{m.name}_unpack(&got, frames[i], {m.macro}_DLC);")
        w(f"        if (st != {P}_OK) {{")
        w(f'            fail("{name}", "unpack status", i);')
        w("        }")
        for s in m.sigs:
            if s.is_float:
                tol = float(s.conv.eps_lsb * abs(s.conv.factor)) * (1 + 1e-9)
                w(f"        if (!close_enough((double)got.{s.member}, exact_{s.member}[i], {c_double(tol)})) {{")
            else:
                w(f"        if (got.{s.member} != values[i].{s.member}) {{")
            w(f'            fail("{name}", "unpack {s.member}", i);')
            w("        }")
        w(f"        st = {p}_{m.name}_pack(buf, {m.macro}_DLC, &got);")
        w(f"        if (st != {P}_OK || memcmp(buf, frames[i], {m.macro}_DLC) != 0) {{")
        w(f'            fail("{name}", "pack(unpack(frame)) == frame", i);')
        w("        }")
        w("    }")
        if size >= 1:
            w("    memset(buf, 0x5A, sizeof buf);")
            w(f"    st = {p}_{m.name}_pack(buf, {m.macro}_DLC - 1u, &values[0]);")
            w(f"    if ((st & {P}_ERR_LENGTH) == 0u || !unchanged(buf, sizeof buf, 0x5Au)) {{")
            w(f'        fail("{name}", "pack into a short buffer", 0u);')
            w("    }")
            w(f"    if ({p}_{m.name}_unpack(&got, frames[0], {m.macro}_DLC - 1u) != {P}_ERR_LENGTH) {{")
            w(f'        fail("{name}", "unpack from a short buffer", 0u);')
            w("    }")
        for case, (s, value, expect, frame) in enumerate(self.edge_cases(m, vectors)):
            w("    got = values[0];")
            w(f"    got.{s.member} = {value};")
            w("    memset(buf, 0x5A, sizeof buf);")
            w(f"    st = {p}_{m.name}_pack(buf, {m.macro}_DLC, &got);")
            if expect:
                w(f"    if (st != {self.flags(expect)} || !unchanged(buf, sizeof buf, 0x5Au)) {{")
            else:
                data = ", ".join(f"0x{b:02X}u" for b in frame)
                w("    {")
                w(f"        static const uint8_t want[{alloc}] = {{{data}}};")
                w(f"        if (st != {P}_OK || memcmp(buf, want, {m.macro}_DLC) != 0) {{")
                w(f'            fail("{name}", "edge case {case} ({s.member})", {case}u);')
                w("        }")
                w("    }")
                continue
            w(f'        fail("{name}", "edge case {case} ({s.member})", {case}u);')
            w("    }")
        w("}")
        w()

    def flags(self, value: int) -> str:
        names = [(numerics.ERR_NOT_FINITE, "ERR_NOT_FINITE"), (numerics.ERR_RANGE, "ERR_RANGE"),
                 (numerics.ERR_QUANTIZATION, "ERR_QUANTIZATION")]
        return " | ".join(f"{self.P}_{n}" for bit, n in names if value & bit) or f"{self.P}_OK"

    def edge_cases(self, m: _Msg, vectors):
        """(signal, C value expression, expected error flags, expected frame) per case."""
        base_raws = vectors[0] if vectors else []
        out = []
        for idx, s in enumerate(m.sigs):
            conv = s.conv
            lo, hi = s.valid
            lsb = abs(conv.factor)
            candidates = []
            if s.is_float:
                candidates.append(("nan", None))
            for raw in (hi + 1, lo - 1):
                phys = raw * conv.factor + conv.offset
                candidates.append(("value", phys))
            mid = (lo + hi) // 2
            candidates.append(("value", mid * conv.factor + conv.offset + Fraction(3, 10) * lsb))
            if conv.kind is Kind.INTEGER and lsb >= 2:  # not a multiple of the factor: rounding
                candidates.append(("value", mid * conv.factor + conv.offset + 1))
            for what, phys in candidates:
                if what == "nan":
                    expr = "nan_f()" if conv.kind is Kind.FLOAT else "nan_d()"
                    out.append((s, expr, numerics.ERR_NOT_FINITE, None))
                    continue
                if s.is_float:
                    value = numerics.to_precision(phys, conv.precision)
                    expr = c_float(value) if conv.kind is Kind.FLOAT else c_double(value)
                else:
                    if phys.denominator != 1:
                        continue
                    value = int(phys)
                    tmin, tmax = TYPE_LIMITS[conv.c_type]
                    if not tmin <= value <= tmax:
                        continue
                    expr = c_int(value, conv.c_type)
                raw, flags = numerics.encode(conv, value, strict=self.strict)
                frame = None
                if not flags:
                    raws = list(base_raws)
                    raws[idx] = raw
                    frame = self.frame(m, raws)
                out.append((s, expr, flags, frame))
        return out
