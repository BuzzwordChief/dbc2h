"""Round-trip tests over real-world DBC files (tests/corpus, see README there)."""

from __future__ import annotations

import pytest

from conftest import CORPUS, corpus_id
from dbc2h.core import textio
from dbc2h.core.model import snapshot
from dbc2h.core.parser import parse
from dbc2h.core.statements import RefKind, StatementClass


def test_corpus_is_present():
    assert len(CORPUS) >= 140


@pytest.mark.parametrize("path", CORPUS, ids=corpus_id)
def test_load_save_is_byte_identical(path):
    data = path.read_bytes()
    text, fmt = textio.decode(data)
    assert textio.encode(parse(text).text(), fmt) == data


@pytest.mark.parametrize("path", CORPUS, ids=corpus_id)
def test_canonical_rendering_keeps_the_model(path):
    """Re-render every modelled statement from the model and parse again."""
    text, _ = textio.decode(path.read_bytes())
    doc = parse(text)
    before = snapshot(doc.network)
    for stmt in doc.statements:
        if stmt.cls is StatementClass.MODELLED:
            doc.mark_dirty(stmt)
    assert snapshot(parse(doc.text()).network) == before


@pytest.mark.parametrize("path", CORPUS, ids=corpus_id)
def test_reference_positions(path):
    """Every recorded reference points at exactly its name in the statement text."""
    text, _ = textio.decode(path.read_bytes())
    for stmt in parse(text).statements:
        for ref in stmt.refs:
            assert stmt.text[ref.start : ref.end] == ref.name, (stmt.line, ref)
            if ref.kind is RefKind.SIGNAL:
                assert ref.message_id is not None
