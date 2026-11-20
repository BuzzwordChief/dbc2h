from __future__ import annotations

import random
import warnings

import pytest

from conftest import CORPUS
from dbc2h.core import layout, textio
from dbc2h.core.model import ByteOrder, Message, Multiplexing, Number, Signal
from dbc2h.core.parser import parse

INTEL, MOTOROLA = ByteOrder.INTEL, ByteOrder.MOTOROLA


def sig(start: int, length: int, order: ByteOrder, signed: bool = False, mux: str = "", name: str = "s") -> Signal:
    one, zero = Number.of(1), Number.of(0)
    return Signal(name, start, length, order, signed, one, zero, zero, zero, mux=Multiplexing(mux))


def test_known_layouts():
    assert layout.signal_bits(0, 16, INTEL) == list(range(16))
    # Motorola, start bit = MSB 7 of byte 0, continues with byte 1 (sawtooth)
    assert layout.signal_bits(7, 16, MOTOROLA) == [8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7]
    assert layout.signal_bits(3, 4, MOTOROLA) == [0, 1, 2, 3]
    # start 4: bits 4..0 of byte 0, then bit 7 of byte 1 (= frame bit 15) is the LSB
    assert layout.signal_bits(4, 6, MOTOROLA) == [15, 0, 1, 2, 3, 4]
    assert layout.signal_bits(1, 4, MOTOROLA) == [14, 15, 0, 1]


@pytest.mark.parametrize("size", [8, 64])
@pytest.mark.parametrize("order", [INTEL, MOTOROLA])
def test_exhaustive_insert_extract(size, order):
    """Every start/length combination that fits: distinct bits, lossless round-trip."""
    rnd = random.Random(1)
    total = size * 8
    lengths = range(1, 65)
    for start in range(total):
        for length in lengths:
            positions = layout.signal_bits(start, length, order)
            if max(positions) >= total or min(positions) < 0:
                continue
            assert len(set(positions)) == length
            s = sig(start, length, order, signed=length > 1 and start % 2 == 0)
            lo = -(1 << (length - 1)) if s.is_signed else 0
            hi = (1 << (length - 1)) - 1 if s.is_signed else (1 << length) - 1
            outside = ~sum(1 << pos for pos in positions)
            for raw in (lo, hi, rnd.randint(lo, hi)):
                buf = bytearray(rnd.randbytes(size))
                before = int.from_bytes(buf, "little")
                layout.insert_raw(buf, s, raw)
                assert layout.extract_raw(bytes(buf), s) == raw
                assert int.from_bytes(buf, "little") & outside == before & outside  # rest untouched


def _corpus_layouts():
    seen = set()
    for path in CORPUS:
        text, _ = textio.decode(path.read_bytes())
        for msg in parse(text).network.messages:
            for s in msg.signals:
                key = (s.start_bit, s.length, s.byte_order, s.is_signed, msg.size)
                if 1 <= s.length <= 64 and layout.fits(s, msg.size) and key not in seen:
                    seen.add(key)
    return sorted(seen)


def test_layouts_match_cantools():
    cantools = pytest.importorskip("cantools")
    from cantools.database.can import Message as CMessage, Signal as CSignal

    rnd = random.Random(7)
    layouts = _corpus_layouts()
    assert len(layouts) > 500
    for start, length, order, signed, size in layouts:
        s = sig(start, length, order, signed)
        csig = CSignal("s", start, length, "little_endian" if order is INTEL else "big_endian", signed)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cmsg = CMessage(frame_id=1, name="m", length=size, signals=[csig], strict=False)
        lo = -(1 << (length - 1)) if signed else 0
        hi = (1 << (length - 1)) - 1 if signed else (1 << length) - 1
        raw = rnd.randint(lo, hi)
        buf = bytearray(size)
        layout.insert_raw(buf, s, raw)
        assert bytes(buf) == cmsg.encode({"s": raw}, scaling=False, strict=False), (start, length, order)


def test_overlap_is_mux_aware():
    msg = Message(1, "m", 8, None)
    msg.signals = [
        sig(0, 8, INTEL, mux="M", name="sel"),
        sig(8, 16, INTEL, mux="m0", name="a"),
        sig(8, 16, INTEL, mux="m1", name="b"),   # same bits as a, different mux value: fine
        sig(16, 8, INTEL, name="plain"),         # always active: overlaps a and b
    ]
    pairs = {(a.name, b.name) for a, b, _ in layout.overlaps(msg.signals)}
    assert pairs == {("a", "plain"), ("b", "plain")}


def test_find_free_start():
    msg = Message(1, "m", 2, None)
    msg.signals = [sig(0, 4, INTEL, name="x"), sig(8, 4, INTEL, mux="m1", name="y")]
    assert layout.find_free_start(msg, 4, INTEL) == 4
    # an always-active signal must also avoid y (m1): bits 4..11 collide, 12..19 do not fit
    assert layout.find_free_start(msg, 8, INTEL) is None
    assert layout.find_free_start(msg, 8, INTEL, multiplexed_by=2) == 4  # y (m1) does not matter for m2
    assert layout.find_free_start(msg, 8, INTEL, multiplexed_by=1) is None
    assert layout.find_free_start(msg, 4, MOTOROLA) == 7  # bits 7..4 of byte 0
