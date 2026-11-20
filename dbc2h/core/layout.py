"""Bit layout of signals in a CAN frame.

DBC numbers the bits of a frame as bit (k % 8) of byte (k // 8), least significant
bit first. The start bit of an Intel (little-endian) signal is its least significant
bit; the signal then counts upwards. The start bit of a Motorola (big-endian) signal
is its most significant bit; the signal continues towards lower bits and jumps to
bit 7 of the next byte at a byte boundary ("sawtooth" numbering).
"""

from __future__ import annotations

from collections.abc import Iterable

from dbc2h.core.model import ByteOrder, Message, Signal

MAX_SIGNAL_LENGTH = 64


def signal_bits(start_bit: int, length: int, byte_order: ByteOrder) -> list[int]:
    """Frame bit positions of a signal; index i holds the position of raw bit i (LSB first)."""
    if length < 1:
        return []
    if byte_order is ByteOrder.INTEL:
        return [start_bit + i for i in range(length)]
    msb_first = []
    pos = start_bit
    for _ in range(length):
        msb_first.append(pos)
        pos = pos + 15 if pos % 8 == 0 else pos - 1
    return msb_first[::-1]


def bits(sig: Signal) -> list[int]:
    return signal_bits(sig.start_bit, sig.length, sig.byte_order)


def bytes_needed(sig: Signal) -> int:
    """Frame length in bytes that the signal needs."""
    positions = bits(sig)
    return max(positions) // 8 + 1 if positions else 0


def fits(sig: Signal, size: int) -> bool:
    positions = bits(sig)
    return bool(positions) and min(positions) >= 0 and max(positions) < size * 8


def _mux_coexist(ma: int | None, mb: int | None) -> bool:
    return not (ma is not None and mb is not None and ma != mb)


def can_coexist(a: Signal, b: Signal) -> bool:
    """False if two signals can never be in the same frame (different mux values)."""
    return _mux_coexist(a.mux.multiplexed_by, b.mux.multiplexed_by)


def overlaps(signals: Iterable[Signal]) -> list[tuple[Signal, Signal, list[int]]]:
    """Pairs of signals that share bits and can be active at the same time."""
    sigs = list(signals)
    occupied = [set(bits(s)) for s in sigs]
    out = []
    for i, a in enumerate(sigs):
        for j in range(i + 1, len(sigs)):
            b = sigs[j]
            common = occupied[i] & occupied[j]
            if common and can_coexist(a, b):
                out.append((a, b, sorted(common)))
    return out


def find_free_start(message: Message, length: int, byte_order: ByteOrder,
                    multiplexed_by: int | None = None) -> int | None:
    """First start bit where a new signal fits without overlap, or None.

    A signal with ``multiplexed_by`` set only has to avoid signals that can be active
    together with it. Positions are tried from the start of the frame: Intel from bit 0
    upwards, Motorola from bit 7 of byte 0 in sawtooth order.
    """
    taken: set[int] = set()
    for s in message.signals:
        if _mux_coexist(s.mux.multiplexed_by, multiplexed_by):
            taken.update(bits(s))
    total = message.size * 8
    if byte_order is ByteOrder.INTEL:
        candidates = range(total)
    else:
        candidates = [byte * 8 + bit for byte in range(message.size) for bit in range(7, -1, -1)]
    for start in candidates:
        positions = signal_bits(start, length, byte_order)
        if max(positions) < total and min(positions) >= 0 and taken.isdisjoint(positions):
            return start
    return None


# --- raw values in a frame ---------------------------------------------------------------


def insert_raw(buf: bytearray, sig: Signal, raw: int) -> None:
    """Write a raw value (two's complement for negative values) into the frame."""
    unsigned = raw & ((1 << sig.length) - 1)
    for i, pos in enumerate(bits(sig)):
        byte, bit = divmod(pos, 8)
        if unsigned >> i & 1:
            buf[byte] |= 1 << bit
        else:
            buf[byte] &= ~(1 << bit) & 0xFF


def extract_raw(buf: bytes, sig: Signal) -> int:
    """Read a raw value from the frame; signed signals are sign-extended."""
    value = 0
    for i, pos in enumerate(bits(sig)):
        byte, bit = divmod(pos, 8)
        value |= (buf[byte] >> bit & 1) << i
    if sig.is_signed and sig.length and value >> (sig.length - 1) & 1:
        value -= 1 << sig.length
    return value
