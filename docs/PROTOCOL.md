# Proflame protocol notes

Status: **partial, derived from inherited data** (2026-08-16)

## Provenance and trust

`tests/cmd.csv` holds 220 decoded packets from five distinct remotes, inherited
from the earlier `proflame-mqtt` prototype on the XPS. The columns are
`serial1, serial2, version, cmd1, cmd2, checksum1, checksum2`.

That prototype's protocol conclusions are explicitly untrusted, and its byte
grouping in particular is suspect — see "What does not add up" below, which is
evidence the grouping does not match the on-air bit order. What follows is only
what the data proves about *itself*: every claim here was checked against all
220 packets, and `tools/analyze_cmd_csv.py` re-derives and re-verifies it from
scratch.

Nothing here has been confirmed against a signal we captured ourselves. Doing
that is M1's job.

## What the data proves

1. **The packet carries two independent checksums.** `checksum1` is a function
   of `cmd1` alone (given the remote), `checksum2` of `cmd2` alone. Neither
   depends on the other half. Zero violations in 220 packets.

2. **Both halves use the same function.** The map from command byte to
   checksum contribution is identical for the two halves; only an additive
   constant differs.

3. **The checksum is XOR-affine**, i.e. linear over GF(2) plus a constant.
   `cs(a) ^ cs(b)` depends only on `a ^ b`. There are no arithmetic carries, so
   this is a shift-register style checksum, not a sum.

4. **Closed form.** With `M` the linear map and `K` a per-remote constant:

       cs = M(cmd) ^ K

       M(byte)  = (byte & 0xF0) ^ nibble(byte & 0x0F) ^ nibble(byte >> 4)
       nibble(n) = (n ^ (n << 5)) & 0xFF

   `M` is a bijection on the 256 byte values. This reproduces **440 of 440**
   checksum bytes across all 220 packets and all five remotes.

5. **Per-remote constants** (`K` differs between the two halves):

   | remote | half 1 | half 2 | packets |
   |--------|--------|--------|---------|
   | 21dd   | 0x7d   | 0x8b   | 2       |
   | 47eb   | 0x61   | 0xa7   | 22      |
   | 7d14   | 0x7e   | 0x9e   | 1       |
   | a4ed   | 0xeb   | 0x28   | 176     |
   | bf39   | 0xdd   | 0x1c   | 19      |

### Why this is enough to transmit

The mapping from serial number to `K` is *not* solved, and with five samples it
cannot be. It also does not need to be: `K = cs ^ M(cmd)` falls straight out of
a single valid packet. **One captured packet from our own remote yields both
constants, and from there any command can be encoded.** Solving the general
serial derivation would only matter for supporting a remote we cannot hear.

## What does not add up

`M` is linear and invertible, which is what a CRC's "multiply by x⁸" step looks
like — but it is not one:

- A byte-wide CRC-8 was searched exhaustively (all 256 polynomials, both input
  and output reflections, all 24 orderings of the four header bytes). No fit.
- Within a nibble the map doubles cleanly (`0x21, 0x42, 0x84`), the signature of
  a shift register, but the pattern breaks across the nibble boundary: bit 3
  maps to `0x08` where a CRC would require `0x29`.
- No bit permutation of the byte (reversal, nibble swap, all rotations) turns
  `M` into a clean CRC multiply.
- The earlier bytes do not enter through powers of `M`, as they would in a CRC.

The consistent reading is that **the checksum operates on nibbles, and the
nibbles are not contiguous on air the way this CSV groups them**. That fits the
device: Proflame's fields (flame height, fan speed, light level) are all 0–6
values, i.e. nibble-sized.

Supporting observation: across the dataset `cmd1` only ever takes high nibbles
`0..6` with an optional `0x8` flag bit, and low nibbles `0..3`. A 0–6 field
packed in bits 4–6 with flags around it is exactly the expected shape.

## What M1 must confirm

Ordered by how much depends on it:

1. **Carrier and modulation.** 315 MHz, OOK. Read straight off the capture
   spectrum and the pulse-width histogram.
2. **Symbol clock and encoding.** Two clusters at t and 2t confirm Manchester;
   the histogram gives t.
3. **The real bit order**, by aligning a decoded bit stream against a packet
   whose fields we know because we pressed the button. This is the piece that
   should explain the nibble anomaly above.
4. **Whether the checksum model transfers.** Capture two packets with different
   commands from our remote, derive `K` from the first, and check it predicts
   the second. If it does, the model above is ours to use. If it does not, the
   inherited byte grouping is wrong and only findings 1–3 survive.
5. **Field semantics**, by controlled captures: press one button, record what
   changed.

Remember that a burst always ends on a mark, so a Manchester frame ending in a
one looks half a bit short — the decoder must restore that from the symbol
clock. See `proxyd/README.md`.

## Reference

- [smartfire](https://github.com/johnellinwood/smartfire) — Python Proflame 2
  controller, the origin of the inherited approach. Also unverified by us.
