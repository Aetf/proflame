#!/usr/bin/env python3
"""Decode Proflame frames from demodulated timings.

Consumes demodulated timings in any of the shapes the hackrf-proxy tools
produce: `hrf serve --record` JSON Lines, a `demod --out-all` burst list, or a
single `demod --out` Flipper-RAW burst. Signal processing stays in the daemon's
tools; this is the protocol layer, kept as a standalone reference with
framing diagnostics the library's all-or-nothing decoder deliberately omits.

Frame format, derived from captures of remote 0086 on 2026-08-16 and detailed
in docs/PROTOCOL.md:

    frame  = 7 blocks, each 26 symbols of 450 us
    block  = sync (3 symbols mark, 1 symbol space)
             + 8 data bits, Manchester coded, most significant first
             + start-of-frame flag, set only in block 0
             + even parity over those 9 bits
             + stop bit, always 1
    blocks = serial1, serial2, version, cmd1, cmd2, checksum1, checksum2

Run: python3 tools/decode_proflame.py captures/flame_up.all.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import cast

from proflame import State

SYMBOL_US = 450.0
BLOCK_SYMBOLS = 26
SYNC_SYMBOLS = 4
FIELDS = ["serial1", "serial2", "version", "cmd1", "cmd2", "checksum1", "checksum2"]


def nibble_map(n: int) -> int:
    return (n ^ (n << 5)) & 0xFF


def checksum_map(byte: int) -> int:
    """The linear map shared by both checksum halves."""
    return (byte & 0xF0) ^ nibble_map(byte & 0x0F) ^ nibble_map(byte >> 4)


def to_symbols(timings: list[int]) -> str:
    """Quantize signed microseconds into 450 us symbols."""
    out: list[str] = []
    for value in timings:
        count = max(1, round(abs(value) / SYMBOL_US))
        out.append(("1" if value > 0 else "0") * count)
    return "".join(out)


def decode_frame(timings: list[int]) -> tuple[list[int | None], list[str]]:
    """Return (fields, problems) for one burst."""
    symbols = to_symbols(timings)
    # A frame's final space has no falling edge to end it, so the last block
    # comes back short; restore it from the known block length.
    while len(symbols) % BLOCK_SYMBOLS:
        symbols += "0"

    problems: list[str] = []
    values: list[int | None] = []
    for index in range(0, len(symbols), BLOCK_SYMBOLS):
        block = symbols[index : index + BLOCK_SYMBOLS]
        payload = block[SYNC_SYMBOLS:]
        bits = ""
        for pair_index in range(len(payload) // 2):
            pair = payload[2 * pair_index : 2 * pair_index + 2]
            if pair == "10":
                bits += "1"
            elif pair == "01":
                bits += "0"
            else:
                bits += "?"
        position = index // BLOCK_SYMBOLS
        if "?" in bits:
            problems.append(f"block {position}: Manchester violation")
            values.append(None)
            continue
        if bits[10] != "1":
            problems.append(f"block {position}: stop bit not set")
        if (bits[:9] + bits[9]).count("1") % 2:
            problems.append(f"block {position}: parity")
        expected_sof = "1" if position == 0 else "0"
        if bits[8] != expected_sof:
            problems.append(f"block {position}: start-of-frame flag is {bits[8]}")
        values.append(int(bits[:8], 2))

    if len(values) != len(FIELDS):
        problems.append(f"{len(values)} blocks, expected {len(FIELDS)}")
    return values, problems


def report(bursts: list[list[int]]) -> int:
    decoded: list[tuple[int, list[int | None], list[str]]] = []
    for number, timings in enumerate(bursts, start=1):
        values, problems = decode_frame(timings)
        decoded.append((number, values, problems))

    print(f"{len(bursts)} frame(s)\n")
    header = "  frame  " + "".join(f"{name:>11}" for name in FIELDS)
    print(header)
    for number, values, problems in decoded:
        cells = "".join(f"{('0x%02x' % v) if v is not None else '--':>11}" for v in values)
        flag = "" if not problems else "   <- " + "; ".join(problems)
        print(f"  {number:>5}  {cells}{flag}")

    clean = [
        cast("list[int]", v)
        for _, v, p in decoded
        if not p and len(v) == len(FIELDS) and all(x is not None for x in v)
    ]
    if not clean:
        print("\nno cleanly decoded frames")
        return 1

    print(f"\n{len(clean)}/{len(bursts)} frames decoded with parity, stop and framing intact")

    distinct = Counter(tuple(v) for v in clean)
    print(f"{len(distinct)} distinct frame value(s):")
    for values, count in distinct.most_common():
        print("   " + "  ".join(f"{n}=0x{v:02x}" for n, v in zip(FIELDS, values)) + f"   x{count}")

    # The appliance-state timeline, consecutive repeats collapsed: mapping a
    # field means pressing one button and reading which field moved, so the
    # frames label themselves. Reserved bits are printed loudly — if one is
    # ever set, the field layout is wrong, which is worth more than any
    # confirmation.
    print("\nappliance state (timeline, consecutive repeats collapsed):")
    previous: State | None = None
    for cmd1, cmd2 in _collapsed_commands(clean):
        state = State.from_commands(cmd1, cmd2)
        line = "   " + "  ".join(f"{k}={v:d}" for k, v in _state_fields(state).items())
        if reserved := cmd1 & 0x0C:
            line += f"   RESERVED BITS SET (cmd1 & 0x{reserved:02x}) — layout is wrong"
        if previous is not None:
            changed = [
                k for k, v in _state_fields(state).items() if _state_fields(previous)[k] != v
            ]
            line += "   <- changed: " + (", ".join(changed) if changed else "nothing")
        print(line)
        previous = state

    # The checksum model: cs = M(cmd) ^ K, with K constant per remote and half.
    k1 = {v[5] ^ checksum_map(v[3]) for v in clean}
    k2 = {v[6] ^ checksum_map(v[4]) for v in clean}
    print("\nchecksum model  cs = M(cmd) ^ K")
    print(f"   half 1: K = {sorted('0x%02x' % k for k in k1)}  {'consistent' if len(k1) == 1 else 'INCONSISTENT'}")
    print(f"   half 2: K = {sorted('0x%02x' % k for k in k2)}  {'consistent' if len(k2) == 1 else 'INCONSISTENT'}")
    return 0 if len(k1) == 1 and len(k2) == 1 else 1


def _collapsed_commands(clean: list[list[int]]) -> list[tuple[int, int]]:
    """The (cmd1, cmd2) timeline with consecutive repeats collapsed."""
    timeline: list[tuple[int, int]] = []
    for values in clean:
        pair = (values[3], values[4])
        if not timeline or timeline[-1] != pair:
            timeline.append(pair)
    return timeline


def _state_fields(state: State) -> dict[str, int]:
    return {
        "power": int(state.power),
        "flame": state.flame,
        "fan": state.fan,
        "light": state.light,
        "thermostat": int(state.thermostat),
        "aux": int(state.aux),
        "front": int(state.front),
        "pilot": int(state.pilot),
    }


def read_bursts(path: str) -> list[list[int]]:
    """Read a file of bursts, whichever shape it is in.

    Accepts the daemon's `serve --record` JSON Lines (one `{"timings": [...]}`
    object per line), a `demod --out-all` list of bursts, or a single
    `demod --out` burst.
    """
    with open(path) as handle:
        text = handle.read()
    if text.lstrip().startswith("{"):
        return [json.loads(line)["timings"] for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if data and isinstance(data[0], list):
        return cast("list[list[int]]", data)
    return [cast("list[int]", data)]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    status = 0
    for path in sys.argv[1:]:
        print(f"=== {path}")
        status |= report(read_bursts(path))
        print()
    return status


if __name__ == "__main__":
    sys.exit(main())
