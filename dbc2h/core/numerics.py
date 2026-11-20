"""Numerics: raw ranges, C type selection, conversion formulas and the representability check.

The formulas (PLAN.md 3.3) are the same for the checker, the Python reference below
and the C generator:

    decode:  phys = (T)raw * F + O                      (T = float or double)
    encode:  raw  = round_half_away((v - O) / F)        or (v - O) * (1/F) if F is a power of two

F and O are literals of type T, i.e. the exact DBC values rounded once to T.

A float/double signal is accepted if, for every bit pattern, the decode error is at most
eps * LSB, and every raw value in the valid range survives decode followed by encode.
Compilers may contract ``raw * F + O`` into one fused multiply-add; that variant is not
emulated but bounded (error of the constants plus half an ulp of the result).

The check runs in three stages, each sound on its own:
1. dyadic factor and offset that fit the mantissa: exact by construction (EXACT);
2. an analytic error bound for both variants;
3. float only, if the bound is too coarse: every raw value is computed with emulated
   binary32 arithmetic (exact decision, with a counterexample on failure).
"""

from __future__ import annotations

import enum
import math
import struct
from array import array
from dataclasses import dataclass
from fractions import Fraction

from dbc2h.core import settings
from dbc2h.core.model import Network, Signal, ValueType


# --- binary32 / binary64 helpers ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Precision:
    name: str
    mantissa: int  # including the implicit bit
    emin: int


FLOAT32 = Precision("float", 24, -126)
FLOAT64 = Precision("double", 53, -1022)
FLT_MAX = float(Fraction(2**24 - 1) * Fraction(2) ** (127 - 23))


def ulp(x: float, prec: Precision) -> float:
    """Unit in the last place of |x| in the given precision (subnormals included)."""
    x = abs(x)
    e = math.frexp(x)[1] - 1 if x else prec.emin
    return 2.0 ** (max(e, prec.emin) - prec.mantissa + 1)


def round_binary32(q: Fraction) -> float:
    """Fraction -> nearest binary32 (ties to even), with a single rounding.

    Going through float(q) first would round twice and can be wrong for sums.
    """
    if q == 0:
        return 0.0
    sign = -1 if q < 0 else 1
    a = abs(q)
    e = a.numerator.bit_length() - a.denominator.bit_length()
    if a < Fraction(2) ** e:
        e -= 1
    e = max(e, FLOAT32.emin)
    step = Fraction(2) ** (e - FLOAT32.mantissa + 1)
    n, rest = divmod(a, step)
    n = int(n)
    if rest * 2 > step or (rest * 2 == step and n % 2 == 1):
        n += 1
    value = n * step
    if value > FLT_MAX:
        raise OverflowError(f"{float(q)} is outside the float range")
    return sign * float(value)


def to_precision(q: Fraction, prec: Precision) -> float:
    return round_binary32(q) if prec is FLOAT32 else float(q)  # int/int division rounds correctly


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def round_half_away(t: float) -> int:
    """Round to nearest, ties away from zero; the generated C helper behaves the same."""
    r = math.trunc(t)
    frac = t - r  # exact: t and r are close floats
    if frac >= 0.5:
        r += 1
    elif frac <= -0.5:
        r -= 1
    return r


def _is_power_of_two(q: Fraction) -> bool:
    a = abs(q)
    return a > 0 and a.numerator & (a.numerator - 1) == 0 and a.denominator & (a.denominator - 1) == 0


def _is_dyadic(q: Fraction) -> bool:
    return q.denominator & (q.denominator - 1) == 0


# --- ranges ------------------------------------------------------------------------------


def wire_range(sig: Signal) -> tuple[int, int]:
    """All bit patterns of the signal as raw integers."""
    n = sig.length
    if n < 1:
        return 0, 0
    if sig.is_signed:
        return -(1 << (n - 1)), (1 << (n - 1)) - 1
    return 0, (1 << n) - 1


@dataclass(frozen=True, slots=True)
class Ranges:
    wire: tuple[int, int]
    valid: tuple[int, int]  # [RAW_MIN, RAW_MAX]
    issue: str | None = None  # lint rule ID for an unusual Min/Max, with defined fallback
    detail: str = ""


def ranges(sig: Signal) -> Ranges:
    """Wire range and valid range (PLAN.md 3.1)."""
    wire = wire_range(sig)
    if sig.value_type is not ValueType.INTEGER:
        return Ranges(wire, wire)
    mn, mx = sig.minimum.value, sig.maximum.value
    f, o = sig.factor.value, sig.offset.value
    if (mn == 0 and mx == 0) or f == 0:
        return Ranges(wire, wire)
    if mn > mx:
        return Ranges(wire, wire, "W104", f"Min {sig.minimum.text} > Max {sig.maximum.text}; es gilt der volle Leitungsbereich")
    a, b = (mn - o) / f, (mx - o) / f
    if f < 0:
        a, b = b, a
    lo, hi = max(math.ceil(a), wire[0]), min(math.floor(b), wire[1])
    if lo > hi:
        return Ranges(wire, wire, "W103", f"[{sig.minimum.text}|{sig.maximum.text}] enthält keinen Rohwert; es gilt der volle Leitungsbereich")
    if a < wire[0] or b > wire[1]:
        return Ranges(wire, (lo, hi), "W103", f"[{sig.minimum.text}|{sig.maximum.text}] reicht über den Leitungsbereich hinaus; es gilt der Schnitt")
    return Ranges(wire, (lo, hi))


# --- conversion ----------------------------------------------------------------------------


class Kind(enum.StrEnum):
    INTEGER = "integer"   # integral factor and offset: integer field
    FLOAT = "float"       # fixed point -> float
    DOUBLE = "double"     # fixed point -> double
    IEEE32 = "ieee32"     # SIG_VALTYPE_ 1: IEEE float on the bus
    IEEE64 = "ieee64"     # SIG_VALTYPE_ 2: IEEE double on the bus


INT_TYPES = [(bits, signed) for bits in (8, 16, 32, 64) for signed in (False, True)]


def _int_type(lo: int, hi: int) -> str | None:
    for bits, signed in INT_TYPES:
        tmin, tmax = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
        if tmin <= lo and hi <= tmax:
            return f"{'' if signed else 'u'}int{bits}_t"
    return None


@dataclass(frozen=True, slots=True)
class Conversion:
    kind: Kind
    c_type: str | None  # None: integer range does not fit 64 bits
    factor: Fraction
    offset: Fraction
    factor_lit: float | int  # F and O as literals of the target type
    offset_lit: float | int
    inverse: float | None    # exact 1/F if F is a power of two (float path)
    ranges: Ranges
    eps_lsb: Fraction        # tolerance for the check and the strict quantization test

    @property
    def precision(self) -> Precision | None:
        return {Kind.FLOAT: FLOAT32, Kind.DOUBLE: FLOAT64, Kind.IEEE32: FLOAT32, Kind.IEEE64: FLOAT64}.get(self.kind)


def float_type(network: Network, sig: Signal) -> str:
    """'float' or 'double' for a fixed-point signal (signal override, else network default)."""
    choice = settings.get_setting(network, sig, "dbc2h_SigFloatType")
    if choice in ("float", "double"):
        return choice
    return settings.get_setting(network, network, "dbc2h_FloatType")


def conversion(network: Network, sig: Signal) -> Conversion:
    """How a signal is converted. Raises ValueError for factor 0."""
    f, o = sig.factor.value, sig.offset.value
    if f == 0:
        raise ValueError(f"{sig.name}: Faktor 0")
    rng = ranges(sig)
    eps = settings.get_setting(network, network, "dbc2h_FloatEpsLsb")
    if sig.value_type is not ValueType.INTEGER:
        kind = Kind.IEEE32 if sig.value_type is ValueType.FLOAT32 else Kind.IEEE64
        prec = FLOAT32 if kind is Kind.IEEE32 else FLOAT64
        return Conversion(kind, prec.name, f, o, to_precision(f, prec), to_precision(o, prec), None, rng, eps)
    if f.denominator == 1 and o.denominator == 1:
        lo, hi = rng.wire
        ends = (lo * f + o, hi * f + o)
        c_type = _int_type(int(min(ends)), int(max(ends)))
        return Conversion(Kind.INTEGER, c_type, f, o, int(f), int(o), None, rng, eps)
    prec = FLOAT32 if float_type(network, sig) == "float" else FLOAT64
    inverse = float(1 / f) if _is_power_of_two(f) else None
    kind = Kind.FLOAT if prec is FLOAT32 else Kind.DOUBLE
    return Conversion(kind, prec.name, f, o, to_precision(f, prec), to_precision(o, prec), inverse, rng, eps)


# --- Python reference of the generated C code ------------------------------------------------

ERR_NOT_FINITE = 1 << 1
ERR_RANGE = 1 << 2
ERR_QUANTIZATION = 1 << 3


def decode(conv: Conversion, raw: int) -> float | int:
    """Physical value exactly as the generated C code computes it (non-contracted)."""
    match conv.kind:
        case Kind.INTEGER:
            return raw * conv.factor_lit + conv.offset_lit
        case Kind.FLOAT:
            return f32(f32(float(raw) * conv.factor_lit) + conv.offset_lit)
        case Kind.DOUBLE:
            return float(raw) * conv.factor_lit + conv.offset_lit
        case Kind.IEEE32 | Kind.IEEE64:
            fmt, size = ("<f", 4) if conv.kind is Kind.IEEE32 else ("<d", 8)
            value = struct.unpack(fmt, (raw & ((1 << (8 * size)) - 1)).to_bytes(size, "little"))[0]
            if conv.factor == 1 and conv.offset == 0:
                return value
            if conv.kind is Kind.IEEE32:
                return f32(f32(value * conv.factor_lit) + conv.offset_lit)
            return value * conv.factor_lit + conv.offset_lit
    raise AssertionError(conv.kind)


def _phys(conv: Conversion, raw: int) -> Fraction:
    return raw * conv.factor + conv.offset


def guards(conv: Conversion) -> tuple[float, float]:
    """GUARD_LO/GUARD_HI: physical values two raw steps outside the valid range.

    Values outside are rejected before the float-to-integer conversion, so that
    conversion can never overflow. Everything inside rounds to a raw value that is at
    most a few steps outside the valid range.
    """
    lo, hi = conv.ranges.valid
    ends = sorted((_phys(conv, lo - 2), _phys(conv, hi + 2)))
    prec = conv.precision
    return to_precision(ends[0], prec), to_precision(ends[1], prec)


def encode(conv: Conversion, value: float | int, *, strict: bool = False) -> tuple[int | None, int]:
    """Raw value and error flags exactly as the generated pack() computes them."""
    lo, hi = conv.ranges.valid
    if conv.kind is Kind.INTEGER:
        num = value - conv.offset_lit
        f = conv.factor_lit
        if num % f == 0:
            raw = num // f
        else:
            if strict:
                return None, ERR_QUANTIZATION
            raw = round_half_away(Fraction(num, f))  # C: integer division with rounding
        return (raw, 0) if lo <= raw <= hi else (None, ERR_RANGE)
    if conv.kind in (Kind.IEEE32, Kind.IEEE64):
        raise NotImplementedError("IEEE signals are encoded bit for bit")
    rnd = f32 if conv.kind is Kind.FLOAT else float
    v = rnd(value)
    if not (-FLT_MAX <= v <= FLT_MAX) if conv.kind is Kind.FLOAT else not math.isfinite(v):
        return None, ERR_NOT_FINITE
    glo, ghi = guards(conv)
    if not glo <= v <= ghi:
        return None, ERR_RANGE
    s = rnd(v - conv.offset_lit)
    t = rnd(s * conv.inverse) if conv.inverse is not None else rnd(s / conv.factor_lit)
    raw = round_half_away(t)
    if not lo <= raw <= hi:
        return None, ERR_RANGE
    if strict:
        eps = to_precision(conv.eps_lsb * abs(conv.factor), conv.precision)
        diff = rnd(v - decode(conv, raw))
        if diff < -eps or diff > eps:
            return None, ERR_QUANTIZATION
    return raw, 0


# --- the check ---------------------------------------------------------------------------------


class Status(enum.StrEnum):
    EXACT = "EXACT"
    OK = "OK"
    LOSSY = "LOSSY"


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: Status
    conversion: Conversion | None
    max_error_lsb: float | None = None  # largest decode error found (or its bound), in LSB
    bound_only: bool = False            # max_error_lsb is an upper bound, not a measurement
    counterexample: int | None = None   # a raw value that violates the conditions
    reason: str = ""
    remedies: tuple[str, ...] = ()
    rule: str | None = None             # lint rule for LOSSY results

    def summary(self) -> str:
        if self.status is Status.EXACT:
            return "EXACT"
        if self.status is Status.OK:
            prefix = "≤ " if self.bound_only else ""
            return f"OK, max. Fehler {prefix}{self.max_error_lsb:.2g} LSB"
        return f"LOSSY: {self.reason}"


def _remedies(kind: Kind) -> tuple[str, ...]:
    if kind is Kind.FLOAT:
        return ("double wählen", "LSB vergrößern", "Länge reduzieren")
    return ("LSB vergrößern", "Länge reduzieren")


def check(network: Network, sig: Signal) -> CheckResult:
    """Classify a signal (PLAN.md 3.4). Raises ValueError for factor 0."""
    conv = conversion(network, sig)
    if conv.kind is Kind.INTEGER:
        if conv.c_type is None:
            return CheckResult(Status.LOSSY, conv, reason="physikalischer Bereich passt nicht in 64 Bit",
                               remedies=("Länge reduzieren", "Faktor oder Offset ändern"), rule="E202")
        return CheckResult(Status.EXACT, conv)
    if conv.kind in (Kind.IEEE32, Kind.IEEE64):
        if conv.factor == 1 and conv.offset == 0:
            return CheckResult(Status.EXACT, conv)
        return CheckResult(Status.OK, conv, reason="skaliertes IEEE-Signal, nicht geprüft")

    prec = conv.precision
    lo, hi = conv.ranges.wire
    r_max = max(abs(lo), abs(hi))
    if r_max > 2**prec.mantissa:
        return CheckResult(
            Status.LOSSY, conv, counterexample=2**prec.mantissa + 1,
            reason=f"{sig.length} Bit: Rohwerte bis {r_max} sind als {prec.name} nicht exakt darstellbar "
                   f"({prec.mantissa} Bit Mantisse)",
            remedies=_remedies(conv.kind), rule="E201",
        )
    if _exact(conv, r_max):
        return CheckResult(Status.EXACT, conv, max_error_lsb=0.0)
    threshold = conv.eps_lsb * abs(conv.factor)
    dec, rt = _bounds(conv, r_max)
    lsb = float(abs(conv.factor))
    if dec <= float(threshold) and rt < 0.5:
        return CheckResult(Status.OK, conv, max_error_lsb=dec / lsb, bound_only=True)
    if conv.kind is Kind.FLOAT:
        return _enumerate(conv, threshold)
    return CheckResult(
        Status.LOSSY, conv,
        reason=f"Fehlerschranke {dec / lsb:.3g} LSB > ε = {float(conv.eps_lsb):g} LSB"
        if dec > float(threshold) else "Round-Trip nicht nachweisbar",
        remedies=_remedies(conv.kind), rule="E201",
    )


def _exact(conv: Conversion, r_max: int) -> bool:
    """Dyadic factor and offset, and all products and sums fit the mantissa."""
    f, o = conv.factor, conv.offset
    if not (_is_dyadic(f) and _is_dyadic(o)) or Fraction(conv.factor_lit) != f or Fraction(conv.offset_lit) != o:
        return False
    scale = max(f.denominator, o.denominator)
    lo, hi = conv.ranges.wire
    limit = 2**conv.precision.mantissa
    products = max(abs(lo), abs(hi)) * abs(f) * scale
    sums = max(abs(lo * f + o), abs(hi * f + o)) * scale
    return products <= limit and sums <= limit


def _bounds(conv: Conversion, r_max: int) -> tuple[float, float]:
    """Upper bounds: decode error (both variants, absolute) and |t - raw| before rounding."""
    prec = conv.precision
    u = lambda x: ulp(x, prec)  # noqa: E731
    d_f = float(abs(Fraction(conv.factor_lit) - conv.factor))
    d_o = float(abs(Fraction(conv.offset_lit) - conv.offset))
    lo, hi = conv.ranges.wire
    p = r_max * float(abs(conv.factor))
    v = float(max(abs(_phys(conv, lo)), abs(_phys(conv, hi))))
    c = r_max * d_f + d_o
    separate = c + 0.5 * u(p + c) + 0.5 * u(v + c + u(p + c))
    fused = c + 0.5 * u(v + c)
    dec = max(separate, fused) * (1 + 2**-40)
    s = p + dec + d_o
    t_err = (r_max * d_f + d_o + dec + 0.5 * u(s)) / abs(conv.factor_lit)
    if conv.inverse is None:
        t_err += 0.5 * u(r_max + 1)
    return dec, t_err * (1 + 2**-40)


PROBE = 4096


def _enumerate(conv: Conversion, threshold: Fraction) -> CheckResult:
    """Exact check of a float signal over every raw value (emulated binary32).

    Errors grow with the magnitude of the raw value, so the ends of the range are
    probed first; a failing signal is then usually rejected in milliseconds.
    """
    lo, hi = conv.ranges.wire
    probes = [(max(lo, hi - PROBE + 1), hi)]
    if lo < 0:
        probes.append((lo, min(hi, lo + PROBE - 1)))
    for a, b in probes:
        result = _enumerate_range(conv, threshold, a, b)
        if result.status is Status.LOSSY:
            return result
    return _enumerate_range(conv, threshold, lo, hi)


def _enumerate_range(conv: Conversion, threshold: Fraction, lo: int, hi: int) -> CheckResult:
    vlo, vhi = conv.ranges.valid
    f_lit, o_lit, inv = conv.factor_lit, conv.offset_lit, conv.inverse
    f_d, o_d = float(conv.factor), float(conv.offset)
    df_s = float(Fraction(f_lit) - conv.factor)  # signed constant errors, for the FMA bound
    do_s = float(Fraction(o_lit) - conv.offset)
    thr = float(threshold)
    lsb = float(abs(conv.factor))
    margin_scale = 1 / abs(f_lit)
    worst = 0.0
    chunk = 1 << 16
    for a in range(lo, hi + 1, chunk):
        raws = range(a, min(a + chunk, hi + 1))
        prod = array("f", [r * f_lit for r in raws])        # exact product, one rounding to binary32
        phys = array("f", [x + o_lit for x in prod])
        for r, v in zip(raws, phys):
            err = abs(v - (r * f_d + o_d))
            fused = abs(r * df_s + do_s) + 0.5 * ulp(v, FLOAT32)
            e = err if err > fused else fused
            slack = 2.0**-50 * (abs(r * f_d) + abs(o_d) + abs(v))
            if e - slack > thr or (e + slack > thr and _exact_error(conv, r, v) > threshold):
                return _lossy_example(conv, r, e / lsb)
            if e > worst:
                worst = e
        rt_lo, rt_hi = max(raws.start, vlo), min(raws.stop - 1, vhi)
        if rt_lo > rt_hi:
            continue
        sub = range(rt_lo, rt_hi + 1)
        base = rt_lo - raws.start
        vals = phys[base : base + len(sub)]
        diff = array("f", [x - o_lit for x in vals])
        quot = array("f", [x * inv for x in diff]) if inv is not None else array("f", [x / f_lit for x in diff])
        for r, v, t in zip(sub, vals, quot):
            margin = ulp(v, FLOAT32) * margin_scale  # a fused decode may differ by about one ulp
            if round_half_away(t) != r or abs(t - r) + margin >= 0.5:
                return _lossy_example(conv, r, None, round_trip=True)
    return CheckResult(Status.OK, conv, max_error_lsb=worst / lsb)


def _exact_error(conv: Conversion, raw: int, v: float) -> Fraction:
    """Exact worst decode error of both variants for one raw value (slow path)."""
    exact = _phys(conv, raw)
    separate = abs(Fraction(v) - exact)
    const = abs(raw * (Fraction(conv.factor_lit) - conv.factor) + (Fraction(conv.offset_lit) - conv.offset))
    fused = const + Fraction(ulp(v, FLOAT32)) / 2
    return max(separate, fused)


def _lossy_example(conv: Conversion, raw: int, err_lsb: float | None, round_trip: bool = False) -> CheckResult:
    if round_trip:
        reason = f"Round-Trip nicht sicher bei raw={raw}"
    else:
        reason = f"{err_lsb:.6g} LSB bei raw={raw} (> ε = {float(conv.eps_lsb):g} LSB)"
    return CheckResult(Status.LOSSY, conv, max_error_lsb=err_lsb, counterexample=raw, reason=reason,
                       remedies=_remedies(conv.kind), rule="E201")
