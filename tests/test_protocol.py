"""Golden tests against real captures, plus single-fault rejection.

The fixtures are recordings of a real handset (and, for cmd.csv, an
inherited table of five others). The counts asserted here are the ones the
original Rust reference implementation produced on the same data; the Python
implementation was validated against it frame-for-frame before that
implementation was retired, and these tests pin that agreement.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from proflame import (
    BLOCK_SYMBOLS,
    SYMBOL_US,
    Remote,
    State,
    checksum,
    decode_frame,
    derive_key,
    encode_timings,
    frame_blocks,
)

FIXTURES = Path(__file__).parent

#: The handset all the frame captures came from, keys derived from its own frames.
REMOTE = Remote(serial1=0x00, serial2=0x86, version=0x02, key1=0x0A, key2=0x86)

SYNC_SYMBOLS = 4


def _jsonl_frames(name: str) -> list[list[int]]:
    lines = (FIXTURES / "frames" / f"{name}.frames.jsonl").read_text().splitlines()
    return [json.loads(line)["timings"] for line in lines if line.strip()]


def _timing_bursts(name: str) -> list[list[int]]:
    return json.loads((FIXTURES / "frames" / f"{name}.timings.json").read_text())


def test_checksum_reproduces_the_inherited_table() -> None:
    """All 440 checksum bytes of cmd.csv, across five remotes, from derived keys."""
    rows = list(csv.DictReader((FIXTURES / "cmd.csv").open()))
    assert len(rows) == 220

    keys: dict[tuple[str, str, str], tuple[int, int]] = {}
    for row in rows:
        ident = (row["serial1"], row["serial2"], row["version"])
        cmd1, cmd2, cs1, cs2 = (
            int(row[k], 16) for k in ("cmd1", "cmd2", "checksum1", "checksum2")
        )
        if ident not in keys:
            keys[ident] = (derive_key(cmd1, cs1), derive_key(cmd2, cs2))
        key1, key2 = keys[ident]
        assert checksum(cmd1, key1) == cs1, f"{ident} cmd1={cmd1:#x}"
        assert checksum(cmd2, key2) == cs2, f"{ident} cmd2={cmd2:#x}"
    assert len(keys) == 5


@pytest.mark.parametrize(
    ("capture", "expected_clean", "expected_total", "expected_states"),
    [("manual_sweeps", 298, 349, 31), ("smart_mode", 10, 10, 2)],
)
def test_decodes_the_recorded_frames(
    capture: str, expected_clean: int, expected_total: int, expected_states: int
) -> None:
    """Exactly as many frames decode as the reference accepted, all one handset.

    Pinning the count both ways matters: accepting more would mean a validity
    check was dropped, accepting fewer that a good frame is being refused.
    """
    frames = _jsonl_frames(capture)
    assert len(frames) == expected_total

    decoded = [d for d in (decode_frame(f) for f in frames) if d is not None]
    assert len(decoded) == expected_clean
    assert {d.remote for d in decoded} == {REMOTE}
    assert len({d.state for d in decoded}) == expected_states


@pytest.mark.parametrize(
    ("capture", "expected_pairs"),
    [
        # One button at a time: each sweep moves a single field.
        ("flame_up", {(0x01, 0x32), (0x01, 0x33), (0x01, 0x34)}),
        ("flame_down", {(0x01, 0x33), (0x01, 0x34), (0x01, 0x35)}),
        # Off is not "flame level zero": cmd2 rides along unchanged.
        ("power_on_off", {(0x00, 0x36), (0x01, 0x36)}),
    ],
)
def test_bench_captures_decode_to_the_documented_fields(
    capture: str, expected_pairs: set[tuple[int, int]]
) -> None:
    decoded = [d for d in (decode_frame(b) for b in _timing_bursts(capture)) if d is not None]
    assert decoded, capture
    assert {d.state.to_commands() for d in decoded} == expected_pairs
    assert {d.remote for d in decoded} == {REMOTE}


def test_every_captured_state_round_trips() -> None:
    """Encode each state ever heard off the air; decoding returns it exactly."""
    states = {
        d.state
        for name in ("manual_sweeps", "smart_mode")
        for d in (decode_frame(f) for f in _jsonl_frames(name))
        if d is not None
    }
    assert len(states) == 31
    for state in states:
        decoded = decode_frame(encode_timings(REMOTE, state))
        assert decoded is not None
        assert decoded.state == state
        assert decoded.remote == REMOTE


def test_levels_out_of_range_are_rejected() -> None:
    with pytest.raises(ValueError):
        State(flame=7)
    with pytest.raises(ValueError):
        State(fan=-1)


# --- Single-fault rejection -------------------------------------------------
#
# Counting what the decoder accepts off the captures cannot show that it still
# validates anything: the frames it should reject there are corrupt in several
# ways at once, so dropping any single check leaves the count unchanged. The
# bad frames are built here, each breaking one rule and nothing else.


def _to_symbols(timings: list[int]) -> list[bool]:
    symbols: list[bool] = []
    for value in timings:
        symbols += [value > 0] * (abs(value) // SYMBOL_US)
    return symbols


def _to_timings(symbols: list[bool]) -> list[int]:
    return [SYMBOL_US if symbol else -SYMBOL_US for symbol in symbols]


def _flip_first_data_bit(symbols: list[bool]) -> list[int]:
    """Swap one Manchester pair, which stays legal but breaks parity."""
    corrupted = list(symbols)
    first = SYNC_SYMBOLS
    corrupted[first], corrupted[first + 1] = corrupted[first + 1], corrupted[first]
    return _to_timings(corrupted)


def _break_sync(symbols: list[bool]) -> list[int]:
    """Turn the deliberate code violation into ordinary Manchester."""
    corrupted = list(symbols)
    corrupted[2] = False
    return _to_timings(corrupted)


def _clear_stop_bit(symbols: list[bool]) -> list[int]:
    corrupted = list(symbols)
    stop = BLOCK_SYMBOLS - 2
    corrupted[stop], corrupted[stop + 1] = False, True
    return _to_timings(corrupted)


def _break_manchester(symbols: list[bool]) -> list[int]:
    """Two equal symbols in a row where a bit should be."""
    corrupted = list(symbols)
    corrupted[SYNC_SYMBOLS] = corrupted[SYNC_SYMBOLS + 1] = True
    return _to_timings(corrupted)


@pytest.mark.parametrize(
    "corrupt", [_flip_first_data_bit, _break_sync, _clear_stop_bit, _break_manchester]
)
def test_rejects_frames_broken_in_exactly_one_way(
    corrupt: Callable[[list[bool]], list[int]],
) -> None:
    good = encode_timings(REMOTE, State(power=True, flame=4, light=2))
    assert decode_frame(good) is not None
    assert decode_frame(corrupt(_to_symbols(good))) is None


def test_frame_blocks_layout() -> None:
    """Seven blocks in air order: identity, commands, per-half checksums."""
    state = State(power=True, flame=4, light=2)
    cmd1, cmd2 = state.to_commands()
    assert frame_blocks(REMOTE, state) == [
        0x00,
        0x86,
        0x02,
        cmd1,
        cmd2,
        checksum(cmd1, REMOTE.key1),
        checksum(cmd2, REMOTE.key2),
    ]
