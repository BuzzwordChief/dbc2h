from __future__ import annotations

from fractions import Fraction

import pytest

from dbc2h.core.lexer import DbcSyntaxError, T, escape, tokenize, unescape
from dbc2h.core.model import ByteOrder, Multiplexing, Number, ObjectType, ValueType, format_fraction
from dbc2h.core.parser import parse
from dbc2h.core.statements import AttributeTarget, RefKind, StatementClass

BASE = """VERSION "1.0"

NS_ :
\tCM_
\tBA_

BS_:

BU_: ECU1 ECU2

BO_ 291 VehicleStatus: 8 ECU1
 SG_ speed : 0|16@1+ (0.25,0) [0|16383.75] "km/h" ECU2
 SG_ temp : 23|8@0- (0.1,-40) [-52.8|-27.3] "\xb0C" ECU2,ECU1
 SG_ mode M : 32|2@1+ (1,0) [0|3] "" ECU2
 SG_ a m0 : 40|8@1+ (1,0) [0|0] "" ECU2
 SG_ b m1M : 40|8@1+ (1,0) [0|0] "" ECU2

BO_ 2147484160 Ext: 8 Vector__XXX
 SG_ x : 0|32@1- (1,0) [0|0] "" Vector__XXX

CM_ "Network comment";
CM_ BO_ 291 "Message comment with \\"quotes\\" and \\\\ backslash";
CM_ SG_ 291 speed "multi
line";
BA_DEF_ BO_  "GenMsgCycleTime" INT 0 65535;
BA_DEF_  "BusType" STRING ;
BA_DEF_ SG_  "Mode" ENUM  "A","B";
BA_DEF_DEF_  "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 291 20;
BA_ "BusType" "CAN";
VAL_ 291 mode 0 "Off" 1 "On" -1 "Err" ;
SIG_VALTYPE_ 2147484160 x : 1;
"""


def test_base_model():
    doc = parse(BASE)
    net = doc.network
    assert net.version == "1.0"
    assert net.new_symbols == ["CM_", "BA_"]
    assert [n.name for n in net.nodes] == ["ECU1", "ECU2"]
    msg = net.message(291)
    assert (msg.name, msg.size, msg.transmitter, msg.is_extended) == ("VehicleStatus", 8, "ECU1", False)
    speed, temp, mode, a, b = msg.signals
    assert (speed.start_bit, speed.length, speed.byte_order, speed.is_signed) == (0, 16, ByteOrder.INTEL, False)
    assert speed.factor == Number("0.25", Fraction(1, 4))
    assert (temp.byte_order, temp.is_signed, temp.offset.value, temp.unit) == (ByteOrder.MOTOROLA, True, -40, "\xb0C")
    assert temp.receivers == ["ECU2", "ECU1"]
    assert (mode.mux.is_multiplexer, mode.mux.multiplexed_by) == (True, None)
    assert (a.mux.is_multiplexer, a.mux.multiplexed_by) == (False, 0)
    assert (b.mux.is_multiplexer, b.mux.multiplexed_by) == (True, 1)
    assert mode.values == [(Number.parse("0"), "Off"), (Number.parse("1"), "On"), (Number.parse("-1"), "Err")]
    ext = net.message_by_name("Ext")
    assert (ext.is_extended, ext.frame_id, ext.transmitter) == (True, 0x200, "Vector__XXX")
    assert ext.signals[0].value_type is ValueType.FLOAT32
    assert net.comment == "Network comment"
    assert msg.comment == 'Message comment with "quotes" and \\ backslash'
    assert speed.comment == "multi\nline"
    cycle = net.attribute_definition("GenMsgCycleTime")
    assert (cycle.object_type, cycle.minimum.value, cycle.maximum.value, cycle.default) == (
        ObjectType.MESSAGE, 0, 65535, Number.parse("0"))
    assert net.attribute_definition("Mode").choices == ["A", "B"]
    assert msg.attributes["GenMsgCycleTime"] == Number.parse("20")
    assert net.attributes["BusType"] == "CAN"
    assert doc.warnings == []


def test_statement_classes_and_targets():
    doc = parse(BASE)
    kinds = [(s.kind, s.cls) for s in doc.statements]
    assert all(cls is StatementClass.MODELLED for _, cls in kinds)
    ba = next(s for s in doc.statements if s.kind == "BA_" and "291" in s.text)
    assert isinstance(ba.target, AttributeTarget) and ba.target.name == "GenMsgCycleTime"
    assert doc.network.message(291).src["BA_:GenMsgCycleTime"] is ba


def test_line_comments_and_trailer_are_kept():
    text = '// head\nVERSION "2.0" // tail\n\nBS_:\n// end\n'
    doc = parse(text)
    assert doc.network.version == "2.0"
    assert doc.statements[0].prefix == "// head\n"
    assert doc.trailer == "\n// end\n"
    assert doc.text() == text


def test_crlf_is_detected_and_kept():
    text = BASE.replace("\n", "\r\n")
    doc = parse(text)
    assert doc.newline == "\r\n"
    assert doc.text() == text
    assert doc.network.message(291).signals[0].comment == "multi\r\nline"


def test_unquoted_attribute_values_and_plus_sign():
    doc = parse('BU_: N\nBO_ 1 M: 1 N\n SG_ s : 0|8@1- (1,-128) [-128|+127] "" N\n'
                'BA_DEF_ BO_ "T" STRING ;\nBA_ "T" BO_ 1 Bare_Value;\n')
    msg = doc.network.message(1)
    assert msg.attributes["T"] == "Bare_Value"
    assert msg.signals[0].maximum == Number("+127", Fraction(127))


def test_missing_semicolon_before_next_statement():
    doc = parse('CM_ "no semicolon"\nBO_ 1 M: 8 N\n')
    assert doc.network.comment == "no semicolon"
    assert doc.network.message(1).name == "M"


def test_names_starting_with_digits_are_accepted_with_warning():
    doc = parse('BO_ 1 2017_5: 8 XXX\n SG_ 0_COUNTER : 0|4@1+ (1,0) [0|15] "" XXX\n')
    assert doc.network.message(1).signals[0].name == "0_COUNTER"
    assert len(doc.warnings) == 2 and "2017_5" in doc.warnings[0]
    assert [t.type for t in tokenize("1e5 2017_5 0x")] == [T.NUMBER, T.IDENT, T.IDENT, T.EOF]


def test_invalid_mux_indicator_is_kept_verbatim():
    text = 'BO_ 1 M: 8 N\n SG_ s m : 0|2@1+ (1,0) [0|3] "" N\n'
    doc = parse(text)
    mux = doc.network.message(1).signals[0].mux
    assert (mux.is_valid, mux.is_multiplexer, mux.multiplexed_by, mux.text) == (False, False, None, "m")
    assert "Multiplex" in doc.warnings[0]
    stmt = doc.statements[-1]
    doc.mark_dirty(stmt)
    assert doc.text() == text


def test_independent_signals_message():
    doc = parse('BO_ 3221225472 VECTOR__INDEPENDENT_SIG_MSG: 0 Vector__XXX\n'
                ' SG_ lost : 0|8@1+ (1,0) [0|0] "" Vector__XXX\n')
    msg = doc.network.messages[0]
    assert msg.is_independent_signals and msg.is_extended


@pytest.mark.parametrize(("text", "position", "fragment"), [
    ('BO_ 1 M: 8 N\n SG_ s : 0|8@2+ (1,0) [0|0] "" N\n', (2, 14), "Byte-Order"),
    ('BO_ 1 M: 8 N\n SG_ s : 0|8@1+ (1,0) [0|0] N\n', (2, 29), "Einheit"),
    ('BO_ x M: 8 N\n', (1, 5), "Nachrichten-ID"),
    (' SG_ s : 0|8@1+ (1,0) [0|0] "" N\n', (1, 2), "ohne vorangehendes BO_"),
    ('CM_ "open\n', (1, 5), "String ohne"),
    ('BU_: A\nBO_ 1 M: 8 N ?\n', (2, 14), "unerwartetes Zeichen"),
    ('BA_DEF_ "X" BOOL;\n', (1, 13), "Attributtyp"),
])
def test_syntax_errors_have_positions(text, position, fragment):
    with pytest.raises(DbcSyntaxError) as info:
        parse(text)
    assert (info.value.line, info.value.col) == position
    assert fragment in str(info.value)


def test_orphans_become_referencing():
    doc = parse('BO_ 1 M: 8 N\n SG_ s : 0|8@1+ (1,0) [0|0] "" N\n'
                'CM_ SG_ 1 missing "x";\nBA_ "A" BO_ 99 1;\nVAL_ 1 missing 0 "a" ;\nSIG_VALTYPE_ 2 s : 1;\n')
    orphans = [s for s in doc.statements if s.info.get("orphan")]
    assert [s.kind for s in orphans] == ["CM_", "BA_", "VAL_", "SIG_VALTYPE_"]
    assert all(s.cls is StatementClass.REFERENCING for s in orphans)
    assert [r.kind for r in orphans[0].refs] == [RefKind.MESSAGE, RefKind.SIGNAL]


def test_referencing_statements_and_refs():
    text = (
        'BU_: A B\nBO_ 5 M: 8 A\n SG_ s : 0|8@1+ (1,0) [0|0] "" B\n SG_ t M : 8|8@1+ (1,0) [0|0] "" B\n'
        'BO_TX_BU_ 5 : A,B;\n'
        'EV_ Env: 0 [0|1] "" 0 1 DUMMY_NODE_VECTOR0 A,B;\n'
        'SIG_GROUP_ 5 G 1 : s t;\n'
        'SG_MUL_VAL_ 5 s t 1-1, 3-5;\n'
        'BA_REL_ "R" BU_SG_REL_ A SG_ 5 s 3;\n'
        'VAL_ Env 0 "off" ;\n'
    )
    doc = parse(text)
    by_kind = {s.kind: s for s in doc.statements if s.cls is StatementClass.REFERENCING}
    names = lambda s: [(r.kind.value, r.name) for r in s.refs]  # noqa: E731
    assert names(by_kind["BO_TX_BU_"]) == [("message", "5"), ("node", "A"), ("node", "B")]
    assert names(by_kind["EV_"]) == [("envvar", "Env"), ("node", "A"), ("node", "B")]
    assert names(by_kind["SIG_GROUP_"]) == [("message", "5"), ("signal", "s"), ("signal", "t")]
    assert by_kind["SG_MUL_VAL_"].info["ranges"] == [(1, 1), (3, 5)]
    assert names(by_kind["BA_REL_"]) == [("attribute", "R"), ("node", "A"), ("message", "5"), ("signal", "s")]
    assert names(by_kind["VAL_"]) == [("envvar", "Env")]


def test_replace_ref_shifts_later_references():
    doc = parse('BU_: A B\nBO_ 5 M: 8 A\nBO_TX_BU_ 5 : A,B;\n')
    stmt = doc.statements[-1]
    stmt.replace_ref(stmt.refs[1], "LongerName")
    assert stmt.text == "BO_TX_BU_ 5 : LongerName,B;"
    assert [stmt.text[r.start : r.end] for r in stmt.refs] == ["5", "LongerName", "B"]


def test_escape_round_trip():
    s = 'a "quoted" \\ path'
    assert unescape(escape(s)) == s


@pytest.mark.parametrize(("value", "text"), [
    (Fraction(1, 4), "0.25"), (Fraction(-3, 8), "-0.375"), (Fraction(100), "100"), (Fraction(1, 16), "0.0625"),
])
def test_format_fraction(value, text):
    assert format_fraction(value) == text


def test_format_fraction_rejects_non_terminating():
    with pytest.raises(ValueError):
        format_fraction(Fraction(1, 3))


def test_multiplexing_constructor():
    assert Multiplexing.of(3, True).text == "m3M"
    assert Multiplexing.of(is_multiplexer=True).text == "M"
    assert Multiplexing.of().text == ""
