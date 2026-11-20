"""Compare the parsed model with cantools as an independent implementation.

Known differences in convention are normalised:
- cantools turns [0|0] into None (DBC convention: full range),
- cantools drops the placeholder receiver Vector__XXX,
- cantools uses the System*LongSymbol attributes as names (our long_name),
- cantools removes duplicate nodes and (inconsistently) Vector__XXX from the node list,
- cantools stores Min/Max as float,
- extended multiplexing (SG_MUL_VAL_) is not modelled by dbc2h 1.0.
"""

from __future__ import annotations

import warnings
import pytest

from conftest import CORPUS, corpus_id
from dbc2h.core import textio
from dbc2h.core.model import NO_NODE
from dbc2h.core.parser import parse

cantools = pytest.importorskip("cantools")


def _load_cantools(text: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return cantools.database.load_string(text, database_format="dbc", strict=False)
        except Exception as exc:  # cantools rejects some files that dbc2h accepts
            pytest.skip(f"cantools kann die Datei nicht laden: {str(exc)[:80]}")


def _minmax(n) -> float | None:
    return None if n is None else float(n)


@pytest.mark.parametrize("path", CORPUS, ids=corpus_id)
def test_structure_matches_cantools(path):
    text, _ = textio.decode(path.read_bytes())
    db = _load_cantools(text)
    net = parse(text).network
    extended_mux = {
        s.info["message_id"] for s in parse(text).statements if s.kind == "SG_MUL_VAL_"
    }

    long_names = {n.name: n.long_name for n in net.nodes}
    ours = {(m.frame_id, m.is_extended): m for m in net.messages if not m.is_independent_signals}
    theirs = {(m.frame_id, m.is_extended_frame): m for m in db.messages}
    assert set(ours) == set(theirs)
    for key, m in ours.items():
        cm = theirs[key]
        assert (m.long_name, m.size, m.comment or None) == (cm.name, cm.length, cm.comment or None)
        sigs = {s.long_name: s for s in m.signals}
        assert set(sigs) == {s.name for s in cm.signals}, m.name
        for cs in cm.signals:
            s = sigs[cs.name]
            byte_order = "little_endian" if s.byte_order == 1 else "big_endian"
            assert (s.start_bit, s.length, byte_order, s.is_signed) == (cs.start, cs.length, cs.byte_order, cs.is_signed)
            assert (float(s.factor.value), float(s.offset.value)) == (float(cs.scale), float(cs.offset))
            assert (s.value_type != 0) == cs.is_float
            mn, mx = float(s.minimum.value), float(s.maximum.value)
            expected = (None, None) if mn == mx == 0 else (mn, mx)
            assert (_minmax(cs.minimum), _minmax(cs.maximum)) in (expected, (mn, mx)), s.name
            receivers = [long_names.get(r, r) for r in s.receivers if r != NO_NODE]
            assert sorted(receivers) == sorted(cs.receivers)
            assert (s.unit or None, s.comment or None) == (cs.unit or None, cs.comment or None)
            choices = {int(v): d for v, d in s.values} if s.values else {}
            assert choices == {int(k): str(v) for k, v in (cs.choices or {}).items()}
            if m.raw_id not in extended_mux:
                ids = cs.multiplexer_ids[0] if cs.multiplexer_ids else None
                assert (s.mux.is_multiplexer, s.mux.multiplexed_by) == (cs.is_multiplexer, ids), s.name
    nodes = {n.long_name: n.comment or None for n in net.nodes if n.name != NO_NODE}
    their_nodes = {n.name: n.comment or None for n in db.nodes if n.name != NO_NODE}
    assert nodes == their_nodes
