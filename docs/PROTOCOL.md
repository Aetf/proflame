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

| field | observation | confidence |
|-------|-------------|------------|
| `cmd1` bit 0 | **On/off.** `1` is on, `0` is off. | Confirmed by a controlled capture |
| `cmd1` bit 1 | **Thermostat ("smart") mode**, probably. | Correlation only — see below |
| `cmd2` | `0x3N`, where `N` is the flame level, `0x30`–`0x36` seen. | Confirmed |

The on/off bit was isolated on 2026-08-16 by a capture of the power button
pressed on, off, on, off with nothing else touched
(`tests/frames/power_on_off.timings.json`, 25 of 25 frames clean). Exactly one
field moved: `cmd1` alternated `0x01 → 0x00 → 0x01 → 0x00`.

`cmd2` stayed at `0x36` across both states, which says something useful: **off
is not "flame level zero"**. The level rides along unchanged and the appliance
resumes at it, exactly as the physical remote behaves. The corollary is that
turning the fireplace off at some *other* flame level would be a frame no
remote has sent, and the project does not synthesise those.

The inherited `cmd.csv` does not corroborate any of this, and should not be
read as if it did: it is a systematic sweep of command values, not a log of
presses with known meaning. Semantics come only from captures where the button
is known.

### Thermostat mode, and why `cmd1` bit 1 is not yet confirmed

`tests/frames/smart_mode.frames.jsonl` was recorded by the daemon on
2026-08-17 with the appliance in thermostat mode, and it is the first capture
containing `cmd1 = 0x03`. Bit 0 is set (on, which matches), and bit 1 is set,
which no earlier capture ever showed.

That is suggestive, not proof. Several things changed between the last known
state and this capture — the appliance was turned on, the flame adjusted, and
the mode changed — so bit 1 is *correlated* with thermostat mode rather than
isolated to it. Confirming it needs a capture that toggles only the mode. The
on/off bit, by contrast, was isolated properly and is confirmed.

Worth having anyway: the checksum model predicted `cmd1 = 0x03`'s checksum
correctly, a command byte that appears in no earlier capture and in no row of
`cmd.csv` for this remote. That is independent evidence the model generalises
rather than merely fitting the data it came from.

**In thermostat mode the remote transmits on its own.** These ten frames
arrived with nobody touching it: the handset holds the temperature sensor, and
it stepped the flame from `0x31` to `0x30` three seconds apart to regulate.
Two consequences. It is a free source of live frames for testing. And it is a
hazard for M5 — received frames are not all user intent, so a naive state sync
would treat the thermostat's own regulation as a command and fight it.

Each level was transmitted as five identical frames before the next step, so
the remote emits the whole state repeatedly rather than sending an increment.
This matches the inherited table, where `cmd1` only ever took high nibbles
`0..6` with an optional `0x8` flag and low nibbles `0..3` — the shape of a 0–6
level field packed alongside flags.

Mapping the rest needs controlled captures: press one button, note what
changed. Fan, accent light and aux are unmapped, and thermostat mode has only
the correlation above.

The daemon makes that easier than the bench tools did — `hrf serve --record
frames.jsonl` keeps every frame it hears, across restarts and disconnects, and
`hrf decode --in` reads the file directly.

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

## Safety: the asymmetry is resolved (2026-08-16)

The project ran for a while able to ignite the fireplace but not extinguish it,
because every frame captured encoded *on* and the off bit was unknown. The
power capture closes that: a verbatim off frame is now on file, so both
directions are reachable by replaying frames the remote itself has sent.

The rule that made this safe still stands and still binds. **Replaying a
captured frame is safe** — it can only reproduce a state the remote just asked
for. **Synthesising a frame that has never been observed means guessing bits on
a gas appliance**, and is not something to settle by experiment. That
distinction is why the off bit was worth waiting for a capture rather than
inferring from `cmd.csv`.

Thermostat mode still deserves particular care whenever it is mapped, since it
makes the appliance cycle on its own and will fight Home Assistant.

## Still open

1. Confirm `cmd1` bit 1 = thermostat mode with a capture that toggles only
   that, since the existing evidence is correlation.
2. Field semantics beyond on/off and the flame level: fan, accent light, aux.
3. The serial-to-`K` derivation, which is not needed for our own remote and may
   not be worth solving.

## Reference

- [smartfire](https://github.com/johnellinwood/smartfire) — Python Proflame 2
  controller; the origin of the inherited approach.
