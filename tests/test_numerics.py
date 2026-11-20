from __future__ import annotations

import math
import random
import struct
from fractions import Fraction

import pytest

from dbc2h.core import numerics, settings
from dbc2h.core.numerics import Kind, Status
from dbc2h.core.parser import parse


def signals(*specs: str, extra: str = ""):
    """specs: 'name|start|len@order sign (f,o) [min|max]' in DBC SG_ syntax after the colon."""
    text = "BO_ 1 M: 64 N\n" + "".join(f' SG_ {s.split(" ", 1)[0]} : {s.split(" ", 1)[1]} "" N\n' for s in specs)
    doc = parse(text + extra)
    return doc, doc.network.messages[0].signals


def check(spec: str, extra: str = ""):
    doc, (sig,) = signals(spec, extra=extra)
    return numerics.check(doc.network, sig)


# --- helpers -------------------------------------------------------------------------------------


def test_round_binary32_single_rounding():
    rnd = random.Random(3)
    for _ in range(2000):
        x = rnd.uniform(-1e6, 1e6) * 10 ** rnd.randint(-30, 30)
        assert numerics.round_binary32(Fraction(x)) == numerics.f32(x)
    # going through double first rounds twice and lands on the wrong float
    q = 1 + Fraction(1, 2**24) + Fraction(1, 2**60)
    assert numerics.f32(float(q)) == 1.0
    assert numerics.round_binary32(q) == 1 + 2**-23


def test_ulp():
    assert numerics.ulp(1.0, numerics.FLOAT32) == 2**-23
    assert numerics.ulp(16384.0, numerics.FLOAT32) == 2**-9
    assert numerics.ulp(1.0, numerics.FLOAT64) == math.ulp(1.0)
    assert numerics.ulp(0.0, numerics.FLOAT32) == 2**-149


@pytest.mark.parametrize(("t", "r"), [(2.5, 3), (-2.5, -3), (2.4999998, 2), (0.49999997, 0), (-0.5, -1), (7.0, 7)])
def test_round_half_away(t, r):
    assert numerics.round_half_away(t) == r


# --- ranges --------------------------------------------------------------------------------------


def test_ranges():
    doc, (a, b, c, d, e, f) = signals(
        "a 0|8@1+ (1,0) [0|0]",
        "b 0|8@1- (0.5,10) [0|20]",
        "c 0|3@1+ (1,0) [0|12]",
        "d 0|8@1+ (1,0) [5|1]",
        "e 0|8@1+ (-0.5,0) [-10|0]",
        "f 0|8@1+ (1,0) [300|400]",
    )
    assert numerics.ranges(a) == numerics.Ranges((0, 255), (0, 255))
    assert numerics.ranges(b).valid == (-20, 20)
    assert (numerics.ranges(c).valid, numerics.ranges(c).issue) == ((0, 7), "W103")
    assert (numerics.ranges(d).valid, numerics.ranges(d).issue) == ((0, 255), "W104")
    assert numerics.ranges(e).valid == (0, 20)  # negative factor swaps min and max
    assert (numerics.ranges(f).valid, numerics.ranges(f).issue) == ((0, 255), "W103")


# --- classification ----------------------------------------------------------------------------


def test_known_classifications():
    assert check("speed 0|16@1+ (0.25,0) [0|16383.75]").status is Status.EXACT
    temp = check("temp 0|8@1- (0.1,-40) [-52.8|-27.3]")
    assert temp.status is Status.OK and temp.max_error_lsb < 1e-4
    trip = check("trip 0|20@1+ (0.001,0) [0|0]")
    assert trip.status is Status.LOSSY and trip.counterexample is not None and trip.rule == "E201"
    assert "double wählen" in trip.remedies
    trip19 = check("trip 0|19@1+ (0.001,0) [0|0]")
    assert trip19.status is Status.OK and 0.05 < trip19.max_error_lsb < 0.0625
    odo = check("odo 0|32@1+ (0.001,0) [0|0]")
    assert odo.status is Status.LOSSY and "Mantisse" in odo.reason


def test_double_override_and_network_default():
    double_sig = 'BA_DEF_ SG_ "dbc2h_SigFloatType" ENUM "default","float","double";\nBA_ "dbc2h_SigFloatType" SG_ 1 odo 2;\n'
    odo = check("odo 0|32@1+ (0.001,0) [0|0]", extra=double_sig)
    assert (odo.status, odo.conversion.c_type) == (Status.OK, "double")
    net_double = 'BA_DEF_ "dbc2h_FloatType" ENUM "float","double";\nBA_ "dbc2h_FloatType" 1;\n'
    assert check("x 0|16@1+ (0.1,0) [0|0]", extra=net_double).conversion.kind is Kind.DOUBLE
    assert check("x 0|54@1+ (0.5,0) [0|0]", extra=net_double).status is Status.LOSSY  # > 53 bit


@pytest.mark.parametrize(("spec", "c_type"), [
    ("a 0|8@1+ (1,0) [0|0]", "uint8_t"),
    ("b 0|11@1+ (1,-500) [0|0]", "int16_t"),
    ("c 0|16@1- (2,0) [0|0]", "int32_t"),
    ("d 0|32@1+ (1,0) [0|0]", "uint32_t"),
    ("e 0|64@1- (1,0) [0|0]", "int64_t"),
    ("f 0|64@1+ (1,0) [0|0]", "uint64_t"),
    ("g 0|8@1+ (-1,0) [0|0]", "int16_t"),
])
def test_integer_path(spec, c_type):
    result = check(spec)
    assert (result.status, result.conversion.kind, result.conversion.c_type) == (Status.EXACT, Kind.INTEGER, c_type)


def test_integer_path_beyond_64_bit():
    result = check("x 0|64@1+ (3,0) [0|0]")
    assert (result.status, result.rule) == (Status.LOSSY, "E202")


def test_ieee_signals():
    text = 'SIG_VALTYPE_ 1 x : 1;\n'
    result = check("x 0|32@1- (1,0) [0|0]", extra=text)
    assert (result.status, result.conversion.c_type) == (Status.EXACT, "float")
    assert numerics.decode(result.conversion, struct.unpack("<i", struct.pack("<f", 1.5))[0]) == 1.5


# --- Python reference of encode/decode ---------------------------------------------------------------


def conv_of(spec: str, extra: str = ""):
    doc, (sig,) = signals(spec, extra=extra)
    return numerics.conversion(doc.network, sig)


def test_encode_decode_round_trip_and_errors():
    conv = conv_of("t 0|8@1- (0.1,-40) [-52.8|-27.3]")
    lo, hi = conv.ranges.valid
    for raw in range(lo, hi + 1):
        assert numerics.encode(conv, numerics.decode(conv, raw)) == (raw, 0)
    assert numerics.encode(conv, float("nan"))[1] == numerics.ERR_NOT_FINITE
    assert numerics.encode(conv, float("inf"))[1] == numerics.ERR_NOT_FINITE
    assert numerics.encode(conv, -20.0)[1] == numerics.ERR_RANGE        # above Max
    assert numerics.encode(conv, 1e30)[1] == numerics.ERR_RANGE         # outside the guards
    assert numerics.encode(conv, -30.04) == (100, 0)                     # rounds to -30.0
    assert numerics.encode(conv, -30.04, strict=True)[1] == numerics.ERR_QUANTIZATION
    assert numerics.encode(conv, -30.001, strict=True) == (100, 0)       # within 1/16 LSB


def test_encode_integer_path():
    conv = conv_of("x 0|8@1+ (2,10) [0|0]")
    assert numerics.encode(conv, 14) == (2, 0)
    assert numerics.encode(conv, 15) == (3, 0)                          # 2.5 rounds away from zero
    assert numerics.encode(conv, 15, strict=True)[1] == numerics.ERR_QUANTIZATION
    assert numerics.encode(conv, 1000)[1] == numerics.ERR_RANGE


def test_guards_enclose_the_valid_range():
    conv = conv_of("s 0|16@1+ (0.25,0) [0|16383.75]")
    glo, ghi = numerics.guards(conv)
    assert glo < 0 and ghi > 16383.75
    assert numerics.encode(conv, 16383.75) == (65535, 0)
    assert numerics.encode(conv, 16383.9)[1] == numerics.ERR_RANGE


# --- soundness against an independent brute force ----------------------------------------------------


def _fl32(x: Fraction) -> Fraction:
    return Fraction(numerics.round_binary32(x))


def _round_away(q: Fraction) -> int:
    r = math.floor(abs(q) + Fraction(1, 2))
    return r if q >= 0 else -r


def brute_force_ok(spec_factor: str, spec_offset: str, bits: int, signed: bool, eps: Fraction) -> bool:
    """Exact rational emulation of both variants, without any of the shortcuts in numerics."""
    f, o = Fraction(spec_factor), Fraction(spec_offset)
    f32, o32 = _fl32(f), _fl32(o)
    power_of_two = abs(f).numerator & (abs(f).numerator - 1) == 0 and abs(f).denominator & (abs(f).denominator - 1) == 0
    lo, hi = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    threshold = eps * abs(f)
    for raw in range(lo, hi + 1):
        exact = raw * f + o
        separate = _fl32(_fl32(raw * f32) + o32)
        fused = _fl32(raw * f32 + o32)
        for v in (separate, fused):
            if abs(v - exact) > threshold:
                return False
            s = _fl32(v - o32)
            t = _fl32(s * (1 / f)) if power_of_two else _fl32(s / f32)
            if _round_away(t) != raw:
                return False
    return True


GRID_FACTORS = ["0.1", "0.01", "0.001", "0.05", "0.3", "0.007", "0.25", "0.000025"]
GRID_OFFSETS = ["0", "-40", "-327.68", "100.1"]


@pytest.mark.parametrize("eps", [Fraction(1, 16), Fraction(1, 4096)], ids=["eps=1/16", "eps=1/4096"])
def test_check_is_sound_against_brute_force(eps):
    """Whatever numerics accepts must pass the exact emulation of both variants."""
    accepted = rejected = conservative = 0
    extra = f'BA_DEF_ "dbc2h_FloatEpsLsb" FLOAT 0 0.5;\nBA_ "dbc2h_FloatEpsLsb" {float(eps)!r};\n'
    for f in GRID_FACTORS:
        for o in GRID_OFFSETS:
            for bits in (7, 10):
                for signed in (False, True):
                    sign = "-" if signed else "+"
                    result = check(f"x 0|{bits}@1{sign} ({f},{o}) [0|0]", extra=extra)
                    truth = brute_force_ok(f, o, bits, signed, eps)
                    if result.status is not Status.LOSSY:
                        assert truth, (f, o, bits, signed, result.summary())
                        accepted += 1
                    else:
                        rejected += 1
                        conservative += truth
    print(f"accepted={accepted} rejected={rejected} unnecessary={conservative}")
    assert accepted and rejected
    assert conservative == 0, f"{conservative} of {rejected} rejections were unnecessary"


def test_settings_feed_the_check():
    doc, (sig,) = signals("x 0|16@1+ (0.1,0) [0|0]")
    settings.set_setting(doc, doc.network, "dbc2h_FloatEpsLsb", Fraction(1, 2**20))
    assert numerics.check(doc.network, sig).status is Status.LOSSY
    settings.set_setting(doc, sig, "dbc2h_SigFloatType", "double")
    assert numerics.check(doc.network, sig).status is Status.OK
