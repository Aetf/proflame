# Proflame protocol notes

Status: **solved and verified against our own captures** (2026-08-16)

The frame format below was derived from scratch from two captures of the
fireplace remote taken in the living room at 315 MHz, and independently
reproduces the checksum relation that had been derived earlier from an
inherited packet table. Test data lives in `tests/`, and
`tools/decode_proflame.py` is the reference decoder.

## Physical layer

| property | value |
|----------|-------|
| carrier | 315.000 MHz, OOK |
| symbol | 450 µs |
| coding | Manchester, `10` = 1, `01` = 0 |
| frame | 7 blocks × 26 symbols = 81.1 ms |
| repeats | frame repeated while the button is held, ~4.15 ms between frames |

315 MHz is the FCC variant; SIT also ships a 433.92 MHz CE variant, so the band
is regional rather than fixed.

Measured pulse widths cluster at 400/850/1300 µs for marks and 500/950 µs for
spaces. Marks read consistently ~100 µs shorter than spaces because the slicing
threshold sits above the envelope's midpoint; the underlying symbol is 450 µs
either way, which is why quantising to 450 µs recovers the structure exactly.

## Block format

Each block is 26 symbols:

    3 symbols mark + 1 symbol space     sync; three consecutive equal symbols
                                        cannot occur in Manchester, so this is
                                        a deliberate code violation
    8 data bits, Manchester, MSB first
    1 start-of-frame flag               set in block 0 only
    1 parity bit                        even parity over the preceding 9 bits
    1 stop bit                          always 1

Verified across 29 frames: parity, stop bit and start-of-frame flag hold in
every block of every cleanly received frame.

## Frame fields

The seven blocks are, in order:

    serial1  serial2  version  cmd1  cmd2  checksum1  checksum2

For the remote in this house: `serial1 = 0x00`, `serial2 = 0x86`,
`version = 0x02`.

That is worth pausing on. The inherited notes recorded a "Remote ID" of
`008602` with no explanation of its structure; decoding the air interface from
scratch produced exactly those three bytes as three separate fields. The two
independent derivations agree.

## Checksums

Each half is covered independently, `checksum1` by `cmd1` and `checksum2` by
`cmd2`, with the same function and a per-remote constant:

    cs = M(cmd) ^ K
    M(byte)   = (byte & 0xF0) ^ nibble(byte & 0x0F) ^ nibble(byte >> 4)
    nibble(n) = (n ^ (n << 5)) & 0xFF

For this remote, **K = 0x0a for half 1 and 0x86 for half 2**, constant across
all 29 captured frames spanning six distinct command values.

This model came from `tests/cmd.csv`, a table of 220 packets from five other
remotes inherited from the earlier prototype, where it reproduced 440 of 440
checksum bytes. It then predicted ours correctly on the first try. Deriving `K`
needs only one valid frame, so no part of the serial-to-`K` mapping has to be
solved to transmit.

## Command semantics

Known so far, from captures where the button pressed is known:

| field | observation |
|-------|-------------|
| `cmd2` | `0x3N`, where `N` is the flame level. Flame-up stepped `0x32 → 0x33 → 0x34`, flame-down stepped `0x35 → 0x34 → 0x33`. |
| `cmd1` | `0x01` throughout both captures, so it carries state that neither button changed. |

Each level was transmitted as five identical frames before the next step, so
the remote emits the whole state repeatedly rather than sending an increment.
This matches the inherited table, where `cmd1` only ever took high nibbles
`0..6` with an optional `0x8` flag and low nibbles `0..3` — the shape of a 0–6
level field packed alongside flags.

Mapping the rest needs controlled captures: press one button, note what
changed. Fan, accent light, aux and thermostat are all unmapped.

## Reproducing

    hrf demod --in flame_up.cs8 --gap-us 3000 --threshold 0.3 --out-all up.json
    hrf decode --in up.json

`hrf decode` is the Rust port of the protocol (`proxyd/src/proflame.rs`,
2026-08-16); `tools/decode_proflame.py` remains as the independent reference
and the two agree byte for byte on the captures below. The Rust side also
carries the encoder (frame -> timings) and regression tests that freeze this
document's numbers in place.

`--gap-us 3000` matters: the default 10 ms is longer than the 4.15 ms
inter-frame gap, so frames get merged into one 429 ms blob and the histogram
smears. `tests/frames/*.timings.json` are the demodulated captures, kept as
regression data since the raw IQ is 20 MB per capture.

## Transmission is accepted (2026-08-16)

Replaying a captured frame verbatim — `cmd1 = 0x01`, `cmd2 = 0x32`, ten
repetitions 4.15 ms apart, 315 MHz, TX VGA 30 dB with the amplifier off —
**ignited the fireplace from the cold state**. The receive and transmit paths
are both proven end to end, which closes M1.

Nothing was synthesised for that test. The frame was a byte-for-byte reproduction
of one the remote itself had sent minutes earlier, which is what made it safe to
send before the command semantics were mapped.

## Safety note: we can ignite but not extinguish

Every frame captured so far encodes the fireplace *on* at some flame level, so
the bit that means "off" is unknown. Until an off press is captured, the RF path
can only start the appliance, and stopping it depends on the physical remote.
Capturing off is therefore the next thing to do, ahead of any other field.

## Still open

1. The off command, for the reason above.
2. Field semantics beyond the flame level: fan, accent light, aux, thermostat.
   Thermostat mode deserves care, since it makes the appliance cycle on its own.
3. The serial-to-`K` derivation, which is not needed for our own remote and may
   not be worth solving.

## Reference

- [smartfire](https://github.com/johnellinwood/smartfire) — Python Proflame 2
  controller; the origin of the inherited approach.
