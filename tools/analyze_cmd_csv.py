#!/usr/bin/env python3
"""Re-derive the Proflame checksum from the captured packet table and verify it.

Input is tests/cmd.csv, a table of decoded packets inherited from the earlier
proflame-mqtt prototype. That decoder's field extraction is not trusted, so
nothing here assumes the byte grouping is right; the script only asks what
relation holds within the data, and says so when it cannot explain something.

Run: python3 tools/analyze_cmd_csv.py [path/to/cmd.csv]
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_CSV = Path(__file__).resolve().parent.parent / "tests" / "cmd.csv"


def load(path):
    with open(path, newline="") as handle:
        return [{k: int(v, 16) for k, v in row.items()} for row in csv.DictReader(handle)]


def low_nibble_map(n):
    """The per-nibble contribution: n ^ (n << 5), truncated to 8 bits."""
    return (n ^ (n << 5)) & 0xFF


def checksum_map(byte):
    """The linear map from a command byte to its checksum contribution.

    Derived from the data, then simplified: the high nibble passes through,
    and both nibbles are folded through the same per-nibble map. Notably this
    is *not* a byte-wide CRC — see the module docstring in docs/PROTOCOL.md.
    """
    return (byte & 0xF0) ^ low_nibble_map(byte & 0x0F) ^ low_nibble_map(byte >> 4)


def check_field_dependence(rows):
    """Each checksum byte must be a function of its own command byte alone."""
    seen = defaultdict(set)
    for row in rows:
        key = (row["serial1"], row["serial2"], row["version"])
        seen[("cs1", key, row["cmd1"])].add(row["checksum1"])
        seen[("cs2", key, row["cmd2"])].add(row["checksum2"])
    violations = [k for k, v in seen.items() if len(v) > 1]
    print(f"checksum1 depends only on cmd1, checksum2 only on cmd2: "
          f"{not violations} ({len(violations)} violations)")
    return not violations


def check_affine(rows):
    """XOR-affinity: cs(a) ^ cs(b) must depend only on a ^ b, per serial."""
    ok = True
    for half, cmd_field, cs_field in (("1", "cmd1", "checksum1"), ("2", "cmd2", "checksum2")):
        by_serial = defaultdict(dict)
        for row in rows:
            by_serial[(row["serial1"], row["serial2"])][row[cmd_field]] = row[cs_field]
        for serial, table in by_serial.items():
            diffs = {}
            for a, ca in table.items():
                for b, cb in table.items():
                    delta, value = a ^ b, ca ^ cb
                    if diffs.setdefault(delta, value) != value:
                        ok = False
                        print(f"  half {half} serial {serial[0]:02x}{serial[1]:02x}: "
                              f"not affine at delta 0x{delta:02x}")
    print(f"both halves are XOR-affine in the command byte: {ok}")
    return ok


def derive_constants(rows):
    """Per-serial, per-half constant: cs ^ checksum_map(cmd)."""
    constants = defaultdict(lambda: defaultdict(set))
    for row in rows:
        serial = (row["serial1"], row["serial2"])
        constants[serial]["cs1"].add(row["checksum1"] ^ checksum_map(row["cmd1"]))
        constants[serial]["cs2"].add(row["checksum2"] ^ checksum_map(row["cmd2"]))
    print("\nper-remote constants (cs ^ checksum_map(cmd)):")
    print("  serial   half1  half2   packets")
    resolved = True
    for serial in sorted(constants):
        k1, k2 = constants[serial]["cs1"], constants[serial]["cs2"]
        count = sum(1 for r in rows if (r["serial1"], r["serial2"]) == serial)
        if len(k1) != 1 or len(k2) != 1:
            resolved = False
        print(f"  {serial[0]:02x}{serial[1]:02x}     "
              f"{'0x%02x' % next(iter(k1)) if len(k1) == 1 else 'AMBIGUOUS'}   "
              f"{'0x%02x' % next(iter(k2)) if len(k2) == 1 else 'AMBIGUOUS'}   {count}")
    print(f"every remote has a single constant per half: {resolved}")
    return {s: (next(iter(v["cs1"])), next(iter(v["cs2"]))) for s, v in constants.items()}


def verify(rows, constants):
    """Reproduce every checksum byte in the table."""
    failures = 0
    for row in rows:
        k1, k2 = constants[(row["serial1"], row["serial2"])]
        if checksum_map(row["cmd1"]) ^ k1 != row["checksum1"]:
            failures += 1
        if checksum_map(row["cmd2"]) ^ k2 != row["checksum2"]:
            failures += 1
    print(f"\nreproduced {2 * len(rows) - failures}/{2 * len(rows)} checksum bytes "
          f"across {len(rows)} packets")
    return failures == 0


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    rows = load(path)
    print(f"{len(rows)} packets, "
          f"{len({(r['serial1'], r['serial2']) for r in rows})} distinct remotes\n")

    ok = check_field_dependence(rows)
    ok &= check_affine(rows)
    constants = derive_constants(rows)
    ok &= verify(rows, constants)

    print("\nRESULT:", "model holds" if ok else "MODEL BROKEN")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
