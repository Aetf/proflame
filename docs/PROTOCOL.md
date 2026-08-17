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

The full bit layout, from [smartfire][smartfire] and checked against every
capture we have:

    cmd1:  pilot(1)  light(3)  reserved(2)  thermostat(1)  power(1)
    cmd2:  front(1)  fan(3)    aux(1)       flame(3)

| field | meaning | confidence |
|-------|---------|------------|
| `cmd1` bit 0 | **Power.** `1` is on, `0` is off. | Confirmed by a controlled capture |
| `cmd1` bit 1 | **Thermostat ("smart") mode.** `1` is smart, `0` is manual. | Confirmed by a controlled capture |
| `cmd1` bits 3–2 | Reserved; zero in every frame seen. | — |
| `cmd1` bits 6–4 | **Accent light, 0–6.** | Confirmed by a controlled sweep |
| `cmd1` bit 7 | Continuous pilot. | smartfire's claim; adjustable from the handset via a key combination, not yet captured |
| `cmd2` bits 2–0 | **Main flame level, 0–6.** | Confirmed |
| `cmd2` bit 3 | Auxiliary outlet. | smartfire's claim; this handset has no separate control for it |
| `cmd2` bits 6–4 | **Blower, 0–6.** Reaches 0, so it can be switched off. | Confirmed by a controlled sweep |
| `cmd2` bit 7 | Front flame / split. | smartfire's claim; **this appliance does not have the feature** |

All sixteen bits are accounted for, which is what makes a complete
verification possible; `docs/MAPPING.md` is the procedure.

Three independent things agree on this layout, which is why the field
*boundaries* are treated as settled even where the *labels* are not:

1. smartfire derived it from its own remote.
2. It explains every press we captured, with nothing left over: power moved
   only `power`, the flame buttons only `flame`, switching to smart mode only
   `thermostat`.
3. The inherited `cmd.csv` obeys it. Across 220 packets from five other
   remotes, every nibble that would encode a 3-bit level of 7 is **absent** —
   `0x7` and `0xf` never appear as `cmd1`'s high nibble, nor as either of
   `cmd2`'s — and `cmd1`'s low nibble never exceeds 3. That is exactly the
   constraint "three-bit fields count 0 to 6, and the reserved bits are never
   set", holding across a table collected by someone else years earlier. It
   also retroactively explains the shape those notes recorded without
   explanation: "high nibbles 0–6 with an optional 0x8 flag" is `light` with
   `pilot` above it.

What that does *not* settle is which three-bit field is which. `cmd2`'s upper
field could be the blower or something else entirely; only pressing the button
says. That is what `docs/MAPPING.md` is for.

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

### Five fields confirmed on our own remote (2026-08-17)

`tests/frames/manual_sweeps.frames.jsonl` is a 73-minute session recorded by
the daemon: the handset was switched from smart to manual mode, and then the
blower, the accent light and the flame were each swept from off up to high and
back down.

Across the whole session there is **exactly one** transition that moves more
than one field, and it is the deliberate mode switch. Every other step moves a
single field. Three sweeps landing in three different places is a much stronger
result than any one of them: a layout that merely fit the earlier data would
not survive that.

So `power`, `flame`, `thermostat`, `fan` and `light` are now confirmed on this
remote. `aux`, `front` and `pilot` remain smartfire's claim, and the two
reserved bits have been zero in every frame ever received.

Two open questions closed. **The blower reaches 0**, so it can be switched off
over RF rather than only turned down. And `fan` reading 3 in every earlier
capture was simply the blower sitting at level 3 — the field was right all
along.

One more detail worth keeping, because it will matter for Home Assistant: the
final power-off moved **only** the `power` bit, leaving flame, fan and light
where they were. Combined with the earlier capture, where switching off left
`cmd2 = 0x36` untouched, the appliance clearly remembers its levels across a
power cycle rather than zeroing them.

Reception over the session: 236 of 266 frames decoded. The shortfall is not
weak signal — all but eleven frames arrived saturated — but fragmentary bursts,
with edge counts running down to 15 where a whole frame is 131 to 143. The
signal dropped mid-frame. It cost nothing: five identical frames carry each
state, and every step of every sweep survived. Frame loss is not information
loss here.

Holding a button does step faster than five frames per level, though. Some
levels appear only once or twice in the recording, and a few are skipped
entirely, so a receiver must not assume it sees every intermediate state.

### The appliance is stateless; the handset holds the state

This is the most consequential thing learned so far, and it shapes the whole
Home Assistant design.

Every frame carries the complete state of every field — never a delta, never an
increment. The handset repeats that whole state five times per press. Switching
off leaves the flame, blower and light levels intact in the frame, and turning
back on restores them. Nothing in the protocol ever asks the appliance what it
is currently doing.

The reading that fits all of it: **the appliance simply obeys the last complete
state it was told, and the handset is the thing that remembers.** The levels
that "survive" a power cycle survive in the handset, not in the fireplace.

The handset's own label supports this from the other side. It offers three
thermostat choices — SMART ("fire modulates up/down and on/off"), ON ("standard
thermostat — fire will turn on/off") and OFF ("thermostat is off — manual
operation") — but the protocol has only **one** thermostat bit, and across 314
frames covering all of this the two spare bits of `cmd1` were never once set.
So the difference between "modulates the flame" and "only switches on and off"
is not transmitted at all. It is behaviour inside the handset, which decides
what complete state to send and when.

We watched it do exactly that: in smart mode the handset drives `power` as well
as `flame`, unprompted, and `cmd1 = 0x02` — thermostat set, power clear — is the
state where it had decided the room was warm enough.

#### What this costs the integration

**Two state holders that cannot hear each other.** Home Assistant will hold a
model and transmit from it; the handset holds its own and cannot receive. After
Home Assistant changes anything, the handset's model is stale, and the next
press on it will transmit that stale state and silently undo the change.

Listening (M5) fixes one direction only. Home Assistant can track every frame it
hears, including the handset's, so its model stays right. The handset has no
such option — it is a transmitter.

There is no protocol-level fix for this; it is a property of the appliance. What
an integration can do is make the failure legible rather than surprising: treat
the handset as authoritative whenever it speaks, and expect a press after a
Home Assistant command to revert things.

### Thermostat mode

`tests/frames/smart_mode.frames.jsonl` was recorded by the daemon on
2026-08-17 with the appliance in thermostat mode, and it is the first capture
containing `cmd1 = 0x03`. Bit 0 is set (on, which matches), and bit 1 is set,
which no earlier capture ever showed.

On its own that was suggestive rather than proof: several things changed
between the last known state and this capture — the appliance was turned on,
the flame adjusted, and the mode changed — so bit 1 was only *correlated* with
thermostat mode. The independent layout naming that same bit `thermostat` is
what settles it. A capture toggling only the mode would still be worth having,
and is step 2 of `docs/MAPPING.md`.

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

- [smartfire] — Python Proflame 2 controller; the origin of the inherited
  approach, and the source of the command-byte bit layout. Its frame structure
  was derived independently of ours and agrees exactly: 7 words of 13 bits,
  sync `11`, a start guard bit, 8 data bits, a padding bit set only in the
  first word, parity, an end guard bit, Manchester `10`/`01`.

  Its checksum formula is the same function as ours for all 256 command
  values, with `K = (c_high << 4) | c_low`. It hardcodes its own remote's
  constants, which come to `K1 = 0xd0` and `K2 = 0x07` — not ours — so that
  part of it does not transfer between remotes, which is why we derive `K`
  from a frame instead.

  It also reports that the fireplace echoes a successful command back
  verbatim. We have not confirmed that; see `docs/MAPPING.md`.

[smartfire]: https://github.com/johnellinwood/smartfire
