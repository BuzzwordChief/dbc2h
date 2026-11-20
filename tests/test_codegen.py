"""C generator: Python-level checks, cantools comparison, and real compilers.

The compile tests build the generated test_<prefix>.c with every compiler found on
PATH (gcc, clang, cc, MSVC's cl), as C11 and C++17, and run it. clang additionally
runs with -ffp-contract=fast (fused multiply-add, the case PLAN.md 3.3 bounds).
Set DBC2H_FULL_C_TESTS=1 to compile every error-free corpus file instead of a sample.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import warnings
from fractions import Fraction
from pathlib import Path

import pytest

from conftest import CORPUS, REPO, corpus_id
from dbc2h.core import codegen_c, compilers, layout, lint, numerics, settings, textio
from dbc2h.core.dbcfile import DbcFile
from dbc2h.core.parser import parse

SYNTHETIC = REPO / "tests" / "data" / "codegen" / "synthetic.dbc"
SAMPLE = ["cantools/motohawk.dbc", "cantools/signed.dbc", "cantools/abs.dbc", "cantools/foobar.dbc",
          "opendbc/toyota_adas.dbc", "opendbc/tesla_powertrain.dbc", "opendbc/cadillac_ct6_powertrain.dbc",
          "opendbc/ford_fusion_2018_pt.dbc"]


def load(path: Path):
    return DbcFile.load(path).document


def error_free_corpus() -> list[Path]:
    out = []
    for path in CORPUS:
        if not lint.has_errors(lint.lint(load(path))):
            out.append(path)
    return out


# --- literals ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "text"), [
    (numerics.f32(0.1), "0.1f"), (40.0, "40.0f"), (-48.0, "-48.0f"), (16384.25, "16384.25f"),
    (numerics.f32(1e-30), "1e-30f"), (0.0, "0.0f"), (numerics.f32(0.001), "0.001f"),
])
def test_c_float(value, text):
    assert codegen_c.c_float(value) == text


def test_c_float_is_exact():
    rnd = random.Random(5)
    for _ in range(3000):
        x = numerics.f32(rnd.uniform(-1, 1) * 10 ** rnd.randint(-20, 20))
        literal = codegen_c.c_float(x)
        assert numerics.round_binary32(Fraction(literal[:-1])) == x, literal


@pytest.mark.parametrize(("value", "c_type", "text"), [
    (5, "int32_t", "5"), (5, "uint8_t", "5u"), (-(1 << 31), "int32_t", "(-2147483647 - 1)"),
    (-(1 << 63), "int64_t", "(-INT64_C(9223372036854775807) - 1)"), ((1 << 64) - 1, "uint64_t",
                                                                      "UINT64_C(18446744073709551615)"),
    (1 << 40, "int32_t", "INT64_C(1099511627776)"),
])
def test_c_int(value, c_type, text):
    assert codegen_c.c_int(value, c_type) == text


# --- generation ---------------------------------------------------------------------------------------


def test_header_basics():
    out = codegen_c.generate(load(SYNTHETIC), "synthetic.dbc")
    assert (out.header_name, out.test_name) == ("synthetic.h", "test_synthetic.c")
    for text in (out.header, out.test):
        assert text.isascii() and "\r" not in text and text.endswith("\n")
    h = out.header
    assert "SYNTHETIC_STATIC_ASSERT(FLT_RADIX == 2 && FLT_MANT_DIG == 24" in h
    assert 'extern "C" {' in h
    assert "#define SYNTHETIC_VEHICLE_STATUS_CYCLE_MS     20u" in h
    assert "#define SYNTHETIC_EXTENDED_MSG_ID           0x18FEF100u" in h
    assert "#define SYNTHETIC_EXTENDED_MSG_IS_EXTENDED  1" in h
    assert "    uint8_t   class_;" in h            # C/C++ keyword
    assert "    uint8_t   _0_counter;" in h        # name starting with a digit
    assert "    double    odo;" in h               # dbc2h_SigFloatType = double
    assert "degC" in h and "Fahrzeugstatus mit Umlaut a und * / Kommentarende" in h
    assert "strict" not in h.lower() or "Strict quantization is off" in h


def test_generation_is_deterministic():
    a = codegen_c.generate(load(SYNTHETIC), "synthetic.dbc")
    b = codegen_c.generate(load(SYNTHETIC), "synthetic.dbc")
    assert (a.header, a.test) == (b.header, b.test)


def test_prefix_setting_and_hash_ignores_line_endings():
    doc = load(SYNTHETIC)
    settings.set_setting(doc, doc.network, "dbc2h_Prefix", "pt")
    out = codegen_c.generate(doc, "synthetic.dbc")
    assert out.header_name == "pt.h" and "pt_vehicle_status_pack" in out.header
    text = SYNTHETIC.read_text(encoding="utf-8")
    crlf = codegen_c.generate(parse(text.replace("\n", "\r\n")), "synthetic.dbc").header
    lf = codegen_c.generate(parse(text), "synthetic.dbc").header
    assert crlf == lf


def test_errors_refuse_generation():
    doc = parse('BU_: A\nBO_ 1 M: 8 A\n SG_ a : 0|32@1+ (0.001,0) [0|0] "" A\n')
    with pytest.raises(codegen_c.GenerationError) as info:
        codegen_c.generate(doc, "x.dbc")
    assert [f.rule for f in info.value.findings] == ["E201"]


def test_ignored_and_unsupported_messages_are_left_out():
    doc = parse('BU_: A\nBO_ 1 M: 8 A\n SG_ a : 0|32@1+ (0.001,0) [0|0] "" A\n'
                'BO_ 2 Mux: 8 A\n SG_ s M : 0|8@1+ (1,0) [0|0] "" A\n SG_ x m1 : 8|8@1+ (1,0) [0|0] "" A\n'
                'BO_ 3 Ieee: 8 A\n SG_ f : 0|32@1- (1,0) [0|0] "" A\nSIG_VALTYPE_ 3 f : 1;\n')
    settings.set_setting(doc, doc.network.message(1), "dbc2h_MsgIgnore", 1)
    out = codegen_c.generate(doc, "x.dbc")
    assert [name for name, _ in out.skipped] == ["Mux", "Ieee"]
    assert "x_m_pack" not in out.header and "Message Mux skipped" in out.header


def test_strict_quantization_changes_the_code():
    doc = load(SYNTHETIC)
    assert "ERR_QUANTIZATION;" not in codegen_c.generate(doc, "s.dbc").header
    settings.set_setting(doc, doc.network, "dbc2h_StrictQuantization", 1)
    assert "st |= S_ERR_QUANTIZATION;" in codegen_c.generate(doc, "s.dbc").header


def test_identifier_collisions_are_errors():
    doc = parse('BU_: A\nBO_ 1 Frame: 8 A\n SG_ Static : 0|8@1+ (1,0) [0|0] "" A\n'
                ' SG_ Static_ : 8|8@1+ (1,0) [0|0] "" A\n')
    with pytest.raises(codegen_c.GenerationError) as info:
        codegen_c.generate(doc, "x.dbc")
    assert [f.rule for f in info.value.findings] == ["E301"]


# --- Python reference against cantools ------------------------------------------------------------------


@pytest.mark.parametrize("path", SAMPLE, ids=str)
def test_frames_and_values_match_cantools(path):
    cantools = pytest.importorskip("cantools")
    text, _ = textio.decode((REPO / "tests" / "corpus" / path).read_bytes())
    doc = parse(text)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        db = cantools.database.load_string(text, database_format="dbc", strict=False)
    rnd = random.Random(path)
    checked = 0
    for msg in doc.network.messages:
        if any(s.mux.text or s.value_type for s in msg.signals) or not msg.signals:
            continue
        cmsg = db.get_message_by_frame_id(msg.frame_id)
        for _ in range(5):
            buf = bytearray(msg.size)
            raws = {}
            for s in msg.signals:
                lo, hi = numerics.ranges(s).valid
                raws[s] = rnd.randint(lo, hi)
                layout.insert_raw(buf, s, raws[s])
            decoded = cmsg.decode(bytes(buf), scaling=True, decode_choices=False)
            for s, raw in raws.items():
                conv = numerics.conversion(doc.network, s)
                ours = numerics.decode(conv, raw)
                tol = float(conv.eps_lsb * abs(conv.factor)) * 1.0001 + 1e-9
                assert abs(float(ours) - float(decoded[s.long_name])) <= tol, (msg.name, s.name, raw)
                assert numerics.encode(conv, ours) == (raw, 0)
            checked += 1
    assert checked


# --- real compilers -----------------------------------------------------------------------------------------

COMPILERS = compilers.find_compilers()


def _configs():
    out = []
    for c in COMPILERS:
        out.append(pytest.param((c, False, ()), id=f"{c.name}-c11"))
        out.append(pytest.param((c, True, ()), id=f"{c.name}-c++17"))
        if c.name == "clang":
            out.append(pytest.param((c, False, ("-std=gnu11", "-ffp-contract=fast")), id="clang-gnu11-fpcontract"))
        if c.name == "gcc":
            out.append(pytest.param((c, False, ("-std=gnu11", "-ffp-contract=fast")), id="gcc-gnu11-fpcontract"))
    return out


def _dbc_files():
    if os.environ.get("DBC2H_FULL_C_TESTS"):
        return [pytest.param(p, id=corpus_id(p)) for p in error_free_corpus()]
    return [pytest.param(SYNTHETIC, id="synthetic")] + [
        pytest.param(REPO / "tests" / "corpus" / s, id=s) for s in SAMPLE]


@pytest.mark.skipif(not COMPILERS, reason="kein C-Compiler gefunden")
@pytest.mark.parametrize("config", _configs())
@pytest.mark.parametrize("dbc", _dbc_files())
def test_generated_tests_pass_on_real_compilers(tmp_path, dbc, config):
    compiler, cpp, extra = config
    out = codegen_c.generate(load(dbc), dbc.name)
    (tmp_path / out.header_name).write_text(out.header, encoding="ascii", newline="\n")
    (tmp_path / out.test_name).write_text(out.test, encoding="ascii", newline="\n")
    result = compilers.build_and_run(compiler, tmp_path / out.test_name, tmp_path, tmp_path, cpp=cpp, extra=extra)
    assert result.ok, f"{result.stage} failed: {' '.join(result.command)}\n{result.output[-3000:]}"
    assert "messages ok" in result.output


@pytest.mark.skipif(not COMPILERS, reason="kein C-Compiler gefunden")
def test_strict_mode_compiles_and_passes(tmp_path):
    doc = load(SYNTHETIC)
    settings.set_setting(doc, doc.network, "dbc2h_StrictQuantization", 1)
    out = codegen_c.generate(doc, "synthetic.dbc")
    (tmp_path / out.header_name).write_text(out.header, encoding="ascii", newline="\n")
    (tmp_path / out.test_name).write_text(out.test, encoding="ascii", newline="\n")
    for cpp in (False, True):
        result = compilers.build_and_run(COMPILERS[0], tmp_path / out.test_name, tmp_path, tmp_path, cpp=cpp)
        assert result.ok, result.output[-3000:]


# --- command line ------------------------------------------------------------------------------------------


def run_gen(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "dbc2h", "gen", *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=REPO)


def test_gen_command(tmp_path):
    dbc = tmp_path / "net.dbc"
    dbc.write_bytes(SYNTHETIC.read_bytes())
    out = tmp_path / "gen"
    assert run_gen(str(dbc), "-o", str(out), "--check").returncode == 1        # nothing generated yet
    proc = run_gen(str(dbc), "-o", str(out), "--tests")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sorted(p.name for p in out.iterdir()) == ["net.h", "test_net.c"]
    assert b"\r" not in (out / "net.h").read_bytes()
    assert run_gen(str(dbc), "-o", str(out), "--tests", "--check").returncode == 0
    dbc.write_bytes(dbc.read_bytes().replace(b"(0.25,0)", b"(0.5,0)").replace(b"16383.75", b"32767.5"))
    proc = run_gen(str(dbc), "-o", str(out), "--check")
    assert proc.returncode == 1 and "veraltet" in proc.stdout


def test_gen_command_errors(tmp_path):
    bad = tmp_path / "bad.dbc"
    bad.write_text('BU_: A\nBO_ 1 M: 8 A\n SG_ a : 0|32@1+ (0.001,0) [0|0] "" A\n', encoding="cp1252")
    proc = run_gen(str(bad), "-o", str(tmp_path / "gen"))
    assert proc.returncode == 1 and "E201" in proc.stdout and not (tmp_path / "gen").exists()
    assert run_gen(str(tmp_path / "missing.dbc")).returncode == 2


def test_gen_default_output_dir_from_settings(tmp_path):
    doc = load(SYNTHETIC)
    settings.set_setting(doc, doc.network, "dbc2h_OutputDir", "build/can")
    (tmp_path / "net.dbc").write_text(doc.text(), encoding="utf-8")
    assert run_gen(str(tmp_path / "net.dbc")).returncode == 0
    assert (tmp_path / "build" / "can" / "net.h").is_file()


@pytest.mark.skipif(not COMPILERS, reason="kein C-Compiler gefunden")
def test_gen_run_tests(tmp_path):
    dbc = tmp_path / "net.dbc"
    dbc.write_bytes(SYNTHETIC.read_bytes())
    proc = run_gen(str(dbc), "-o", str(tmp_path / "gen"), "--run-tests")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "6 messages ok" in proc.stdout


def test_documented_example_is_current():
    """docs/example shows real generator output; regenerate with
    python -m dbc2h gen docs/example/synthetic.dbc -o docs/example --tests"""
    example = REPO / "docs" / "example"
    out = codegen_c.generate(load(example / "synthetic.dbc"), "synthetic.dbc")
    assert (example / out.header_name).read_text(encoding="ascii") == out.header
    assert (example / out.test_name).read_text(encoding="ascii") == out.test
