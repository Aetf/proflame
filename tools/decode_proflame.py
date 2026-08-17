#!/usr/bin/env python3
"""Decode Proflame frames from demodulated timings.

Consumes the JSON written by `hrf demod --out-all`, which is a list of bursts,
each a Flipper-RAW timing array. Signal processing stays in the Rust tool; this
is the protocol layer, and doubles as the reference the M4 Rust encoder gets
checked against.

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

import json
import sys
from collections import Counter

SYMBOL_US = 450.0
BLOCK_SYMBOLS = 26
SYNC_SYMBOLS = 4
FIELDS = ["serial1", "serial2", "version", "cmd1", "cmd2", "checksum1", "checksum2"]


def nibble_map(n):
    return (n ^ (n << 5)) & 0xFF


def checksum_map(byte):
    """The linear map shared by both checksum halves."""
    return (byte & 0xF0) ^ nibble_map(byte & 0x0F) ^ nibble_map(byte >> 4)


def to_symbols(timings):
    """Quantise signed microseconds into 450 us symbols."""
    out = []
    for value in timings:
        count = max(1, round(abs(value) / SYMBOL_US))
        out.append(("1" if value > 0 else "0") * count)
    return "".join(out)


def decode_frame(timings):
    """Return (fields, problems) for one burst."""
    symbols = to_symbols(timings)
    # A frame's final space has no falling edge to end it, so the last block
    # comes back short; restore it from the known block length.
    while len(symbols) % BLOCK_SYMBOLS:
        symbols += "0"

    problems = []
    values = []
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


def report(bursts):
    decoded = []
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

    clean = [v for _, v, p in decoded if not p and len(v) == len(FIELDS)]
    if not clean:
        print("\nno cleanly decoded frames")
        return 1

    print(f"\n{len(clean)}/{len(bursts)} frames decoded with parity, stop and framing intact")

    distinct = Counter(tuple(v) for v in clean)
    print(f"{len(distinct)} distinct frame value(s):")
    for values, count in distinct.most_common():
        print("   " + "  ".join(f"{n}=0x{v:02x}" for n, v in zip(FIELDS, values)) + f"   x{count}")

    # The checksum model: cs = M(cmd) ^ K, with K constant per remote and half.
    k1 = {v[5] ^ checksum_map(v[3]) for v in clean}
    k2 = {v[6] ^ checksum_map(v[4]) for v in clean}
    print("\nchecksum model  cs = M(cmd) ^ K")
    print(f"   half 1: K = {sorted('0x%02x' % k for k in k1)}  {'consistent' if len(k1) == 1 else 'INCONSISTENT'}")
    print(f"   half 2: K = {sorted('0x%02x' % k for k in k2)}  {'consistent' if len(k2) == 1 else 'INCONSISTENT'}")
    return 0 if len(k1) == 1 and len(k2) == 1 else 1


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    status = 0
    for path in sys.argv[1:]:
        print(f"=== {path}")
        status |= report(json.load(open(path)))
        print()
    return status


if __name__ == "__main__":
    sys.exit(main())
