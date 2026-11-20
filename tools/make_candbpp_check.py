#!/usr/bin/env python3
"""Generate the DBC files for the manual CANdb++ check (docs/candbpp-check.md).

Both files are built through the dbc2h API, so they show exactly what dbc2h writes:
  tests/data/candbpp/dbc2h_check_cp1252.dbc   default encoding for new files
  tests/data/candbpp/dbc2h_check_utf8.dbc     same content in UTF-8 plus a character
                                              that cp1252 cannot encode

A test checks that the committed files match this script's output.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dbc2h.core import settings, textio  # noqa: E402
from dbc2h.core.dbcfile import DbcFile  # noqa: E402
from dbc2h.core.model import ByteOrder, Message, Multiplexing, Node, Number, Signal, ValueTable  # noqa: E402

OUT_DIR = REPO / "tests" / "data" / "candbpp"
BACKSLASH_COMMENT = 'Pfad C:\\temp\\neu und "Anführungszeichen"'


def build(utf8: bool) -> DbcFile:
    f = DbcFile.new()
    if utf8:
        f.format = textio.FileFormat("utf-8")
    doc = f.document
    net = doc.network

    for name in ("ECU_Engine", "ECU_Brake"):
        node = Node(name)
        net.nodes.append(node)
        doc.network.src["BU_"].info["nodes"].append(node)
    doc.mark_dirty(net.src["BU_"])

    table = ValueTable("Gear", [(Number.of(0), "Park"), (Number.of(1), "Reverse"), (Number.of(2), "Neutral"),
                                (Number.of(3), "Drive")])
    net.value_tables.append(table)
    doc.new_statement("VAL_TABLE_", table, table, "VAL_TABLE_")

    def add_message(msg: Message) -> Message:
        net.messages.append(msg)
        doc.new_statement("BO_", msg, msg, "BO_")
        return msg

    def add_signal(msg: Message, sig: Signal) -> Signal:
        sig.message = msg
        msg.signals.append(sig)
        doc.new_statement("SG_", sig, sig, "SG_")
        return sig

    def num(x) -> Number:
        return Number.of(x)

    status = add_message(Message(0x123, "VehicleStatus", 8, "ECU_Engine"))
    speed = add_signal(status, Signal("speed", 0, 16, ByteOrder.INTEL, False, num("0.25"), num(0), num(0),
                                      num("16383.75"), "km/h", ["ECU_Brake"]))
    temp = add_signal(status, Signal("coolant_temp", 23, 8, ByteOrder.MOTOROLA, False, num("0.75"), num(-48),
                                     num(-48), num("143.25"), "\u00b0C", ["ECU_Brake"]))
    gear = add_signal(status, Signal("gear", 24, 2, ByteOrder.INTEL, False, num(1), num(0), num(0), num(3), "",
                                     ["ECU_Brake"]))
    gear.values = list(table.entries)
    doc.new_statement("VAL_", gear, gear, "VAL_")

    ext = add_message(Message(0x80000000 | 0x18FEF100, "ExtendedMsg", 8, "ECU_Brake"))
    add_signal(ext, Signal("mode", 0, 2, ByteOrder.INTEL, False, num(1), num(0), num(0), num(3), "",
                           ["ECU_Engine"], mux=Multiplexing.of(is_multiplexer=True)))
    add_signal(ext, Signal("pressure", 8, 16, ByteOrder.INTEL, False, num("0.1"), num(0), num(0), num("6553.5"),
                           "bar", ["ECU_Engine"], mux=Multiplexing.of(0)))
    add_signal(ext, Signal("flow", 8, 16, ByteOrder.INTEL, True, num("0.01"), num(0), num("-327.68"),
                           num("327.67"), "l/min", ["ECU_Engine"], mux=Multiplexing.of(1)))

    status.comment = BACKSLASH_COMMENT
    doc.new_statement("CM_", status, status, "CM_")
    speed.comment = "Geschwindigkeit über Grund, LSB 0,25 km/h"
    doc.new_statement("CM_", speed, speed, "CM_")
    if utf8:
        temp.comment = "Nur in UTF-8 darstellbar: \u2192 \u2264 \u00b5"
        doc.new_statement("CM_", temp, temp, "CM_")

    settings.set_setting(doc, net, "dbc2h_Prefix", "pt")
    settings.set_setting(doc, net, "dbc2h_FloatType", "double")
    settings.set_setting(doc, net, "dbc2h_StrictQuantization", 1)
    settings.set_setting(doc, net, "dbc2h_FloatEpsLsb", Fraction(1, 32))
    settings.set_setting(doc, net, "dbc2h_OutputMode", "split")
    settings.set_setting(doc, net, "dbc2h_OutputDir", "gen/can")
    settings.set_setting(doc, net, "dbc2h_Lint", "E103=warning,W405=off")
    settings.set_setting(doc, ext, "dbc2h_MsgIgnore", 1)
    settings.set_setting(doc, speed, "dbc2h_SigFloatType", "float")
    settings.set_setting(doc, temp, "dbc2h_SigIgnore", 1)
    settings.set_setting(doc, gear, "dbc2h_SigEnumComplete", 1)
    return f


def outputs() -> dict[Path, bytes]:
    return {
        OUT_DIR / "dbc2h_check_cp1252.dbc": build(utf8=False).encoded(),
        OUT_DIR / "dbc2h_check_utf8.dbc": build(utf8=True).encoded(),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, data in outputs().items():
        path.write_bytes(data)
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
