from __future__ import annotations

import pytest

from dbc2h.core import textio
from dbc2h.core.dbcfile import DbcFile
from dbc2h.core.edit import EditError, History, ObjectRef, SetComment
from dbc2h.core.parser import parse

TEXT = 'VERSION ""\n\nBU_: A\n\nBO_ 1 M: 8 A\n SG_ s : 0|8@1+ (1,0) [0|0] "" A\n\nCM_ BO_ 1 "old";\n'


def history() -> History:
    return History(DbcFile(parse(TEXT)))


def test_set_comment_modify_add_remove():
    h = history()
    msg_ref = ObjectRef("message", raw_id=1)
    h.execute(SetComment(msg_ref, "new"))
    assert 'CM_ BO_ 1 "new";' in h.dbc.document.text()
    h.execute(SetComment(ObjectRef("signal", raw_id=1, name="s"), "sig"))
    assert 'CM_ SG_ 1 s "sig";' in h.dbc.document.text()
    h.execute(SetComment(msg_ref, ""))
    assert "CM_ BO_ 1" not in h.dbc.document.text()
    assert h.dbc.document.network.message(1).comment is None


def test_undo_redo_and_modified():
    h = history()
    assert not h.modified
    h.execute(SetComment(ObjectRef("network"), "net"))
    assert h.modified and h.dbc.document.network.comment == "net"
    assert h.undo() == "Kommentar ändern"
    assert not h.modified and h.dbc.document.text() == TEXT
    assert h.undo() is None
    assert h.redo() == "Kommentar ändern"
    assert h.dbc.document.network.comment == "net"
    h.mark_saved()
    assert not h.modified


def test_new_command_clears_redo():
    h = history()
    h.execute(SetComment(ObjectRef("network"), "a"))
    h.undo()
    h.execute(SetComment(ObjectRef("network"), "b"))
    assert h.redo() is None


def test_unencodable_text_is_rejected_and_document_unchanged():
    h = history()
    h.dbc.format = textio.FileFormat("cp1252")
    with pytest.raises(EditError, match="cp1252"):
        h.execute(SetComment(ObjectRef("network"), "Pfeil →"))
    assert h.dbc.document.text() == TEXT and not h.undo_steps


def test_missing_object():
    with pytest.raises(EditError):
        history().execute(SetComment(ObjectRef("message", raw_id=99), "x"))


def test_object_ref_round_trip():
    net = parse(TEXT).network
    sig = net.message(1).signals[0]
    for obj in (net, net.nodes[0], net.message(1), sig):
        assert ObjectRef.of(obj).resolve(net) is obj
