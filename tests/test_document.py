"""Statement placement, minimal diffs after edits, settings."""

from __future__ import annotations

import difflib
from fractions import Fraction

import pytest

from conftest import CORPUS_DIR
from dbc2h.core import settings, textio
from dbc2h.core.dbcfile import DbcFile, new_file_text
from dbc2h.core.model import ByteOrder, Message, Number, Signal
from dbc2h.core.parser import parse

SMALL = """VERSION ""

BU_: A B

BO_ 1 First: 8 A
 SG_ s1 : 0|8@1+ (1,0) [0|0] "" B

BO_ 2 Second: 8 A
 SG_ s2 : 0|8@1+ (1,0) [0|0] "" B

CM_ BO_ 1 "first";
BA_DEF_ BO_  "GenMsgCycleTime" INT 0 65535;
BA_ "GenMsgCycleTime" BO_ 1 10;
"""


def changed_lines(before: str, after: str) -> list[str]:
    return [line for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line[:1] in "+-"]


def make_signal(msg: Message, name: str, start: int) -> Signal:
    return Signal(name, start, 8, ByteOrder.INTEL, False, Number.of(1), Number.of(0), Number.of(0), Number.of(0),
                  receivers=["B"], message=msg)


def test_edit_changes_only_the_affected_statement():
    doc = parse(SMALL)
    msg = doc.network.message(1)
    msg.comment = "edited"
    doc.mark_dirty(msg.src["CM_"])
    assert changed_lines(SMALL, doc.text()) == ['- CM_ BO_ 1 "first";', '+ CM_ BO_ 1 "edited";']


def test_new_signal_goes_after_the_last_signal_of_its_message():
    doc = parse(SMALL)
    msg = doc.network.message(1)
    sig = make_signal(msg, "s1b", 8)
    msg.signals.append(sig)
    doc.new_statement("SG_", sig, sig, "SG_")
    lines = doc.text().splitlines()
    assert lines[lines.index(" SG_ s1 : 0|8@1+ (1,0) [0|0] \"\" B") + 1] == ' SG_ s1b : 8|8@1+ (1,0) [0|0] "" B'


def test_new_message_goes_after_the_signals_of_the_last_message():
    doc = parse(SMALL)
    msg = Message(3, "Third", 8, "A")
    doc.network.messages.append(msg)
    doc.new_statement("BO_", msg, msg, "BO_")
    assert 'SG_ s2 : 0|8@1+ (1,0) [0|0] "" B\n\nBO_ 3 Third: 8 A\n\nCM_' in doc.text()


def test_new_kind_starts_a_section_after_earlier_kinds():
    doc = parse(SMALL)
    sig = doc.network.message(2).signals[0]
    sig.values = [(Number.of(0), "off")]
    doc.new_statement("VAL_", sig, sig, "VAL_")
    assert doc.text().endswith('BA_ "GenMsgCycleTime" BO_ 1 10;\n\nVAL_ 2 s2 0 "off" ;\n')


def test_set_and_remove_attribute():
    doc = parse(SMALL)
    msg2 = doc.network.message(2)
    doc.set_attribute(msg2, "GenMsgCycleTime", Number.of(20))
    assert 'BA_ "GenMsgCycleTime" BO_ 1 10;\nBA_ "GenMsgCycleTime" BO_ 2 20;' in doc.text()
    doc.remove_attribute(msg2, "GenMsgCycleTime")
    assert doc.text() == SMALL


def test_new_file_skeleton():
    f = DbcFile.new()
    assert f.document.text() == new_file_text()
    assert f.document.newline == "\r\n"
    assert f.format == textio.FileFormat("cp1252")
    assert "SG_MUL_VAL_" in f.document.network.new_symbols


# --- settings ------------------------------------------------------------------------


def test_reading_settings_does_not_change_the_file():
    text = (CORPUS_DIR / "cantools" / "motohawk.dbc").read_text()
    doc = parse(text)
    msg = doc.network.messages[0]
    assert settings.get_setting(doc.network, doc.network, "dbc2h_FloatType") == "float"
    assert settings.get_setting(doc.network, msg, "dbc2h_MsgIgnore") == 0
    assert settings.get_setting(doc.network, msg.signals[0], "dbc2h_SigFloatType") == "default"
    assert settings.get_setting(doc.network, doc.network, "dbc2h_FloatEpsLsb") == Fraction(1, 16)
    assert doc.text() == text


def test_setting_a_value_creates_definitions_once():
    doc = parse(SMALL)
    sig1 = doc.network.message(1).signals[0]
    sig2 = doc.network.message(2).signals[0]
    settings.set_setting(doc, sig1, "dbc2h_SigFloatType", "double")
    settings.set_setting(doc, sig2, "dbc2h_SigFloatType", "float")
    text = doc.text()
    assert text.count('BA_DEF_ SG_  "dbc2h_SigFloatType" ENUM "default","float","double";') == 1
    assert text.count('BA_DEF_DEF_  "dbc2h_SigFloatType" "default";') == 1
    assert 'BA_ "dbc2h_SigFloatType" SG_ 1 s1 2;' in text  # ENUM values are stored as index
    reparsed = parse(text)
    s1 = reparsed.network.message(1).signals[0]
    assert settings.get_setting(reparsed.network, s1, "dbc2h_SigFloatType") == "double"


def test_setting_back_to_default_removes_the_value():
    doc = parse(SMALL)
    settings.set_setting(doc, doc.network, "dbc2h_Prefix", "pt")
    assert 'BA_ "dbc2h_Prefix" "pt";' in doc.text()
    settings.set_setting(doc, doc.network, "dbc2h_Prefix", "")
    assert 'BA_ "dbc2h_Prefix"' not in doc.text()
    assert settings.get_setting(doc.network, doc.network, "dbc2h_Prefix") == ""


def test_setting_to_default_on_untouched_file_changes_nothing():
    doc = parse(SMALL)
    settings.set_setting(doc, doc.network, "dbc2h_FloatType", "float")
    assert doc.text() == SMALL


@pytest.mark.parametrize(("name", "obj", "value"), [
    ("dbc2h_FloatType", "network", "half"),
    ("dbc2h_FloatEpsLsb", "network", Fraction(1)),
    ("dbc2h_MsgIgnore", "network", 1),          # wrong object type
    ("dbc2h_SigIgnore", "signal", 2),
    ("dbc2h_Unknown", "network", 1),
])
def test_invalid_settings_are_rejected(name, obj, value):
    doc = parse(SMALL)
    target = doc.network if obj == "network" else doc.network.message(1).signals[0]
    with pytest.raises(settings.SettingError):
        settings.set_setting(doc, target, name, value)


def test_setting_names_fit_dbc_limits_and_object_types_are_unique():
    for name in settings.SETTINGS:
        assert len(name) <= settings.MAX_NAME_LENGTH
    # A DBC attribute definition is identified by its name: one object type per name.
    assert len(settings.SETTINGS) == len({s.name for s in settings.SETTINGS.values()})


def test_incompatible_definition_in_file_is_reported_and_not_overwritten():
    doc = parse(SMALL + 'BA_DEF_  "dbc2h_FloatType" STRING ;\n')
    assert settings.conflicts(doc.network)
    assert settings.get_setting(doc.network, doc.network, "dbc2h_FloatType") == "float"
    with pytest.raises(settings.SettingError):
        settings.set_setting(doc, doc.network, "dbc2h_FloatType", "double")
