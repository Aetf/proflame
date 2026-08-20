# Mapping the remaining Proflame fields

A procedure for confirming what every bit of the two command bytes does, with
an argument for why it cannot miss anything.

## Why this is a bounded job, not a hunt

[smartfire] — an independent Proflame 2 reverse engineering effort — publishes
a bit layout for both command bytes, and it accounts for all sixteen bits:

    cmd1:  pilot(1)  light(3)  reserved(2)  thermostat(1)  power(1)
    cmd2:  front(1)  fan(3)    aux(1)       flame(3)

Two things follow. First, there is nothing left over, so a button that does
anything must move one of these fields — verifying all eight is provably
complete, in a way that "press every button we can find" never is. Second, if
a button moves something *else* — in particular the two reserved bits, which
`tools/decode_proflame.py` prints loudly when they are not zero — then the layout is wrong,
and we have learned something more valuable than a confirmation.

Its layout is not taken on faith. Two other things agree with it
independently, which is why the field *boundaries* below are treated as
settled even where the *labels* are not.

It explains every button press we have captured, with nothing left over:

| we pressed | frames moved | layout says |
|------------|--------------|-------------|
| on, off | `cmd1` `0x01`↔`0x00` | `power` only ✓ |
| flame up, flame down | `cmd2` `0x32`→`0x33`→`0x34` | `flame` only ✓ |
| mode → smart | `cmd1` `0x01`→`0x03` | `thermostat` only ✓ |

And the community packet table `tests/cmd.csv` obeys it. Across 220 packets
from five *other* remotes, recorded by [rtl_433 users][rtl-433-1905] years
before this project existed, no nibble ever
encodes a three-bit level of 7 — `0x7` and `0xf` never appear as `cmd1`'s high
nibble, nor as either of `cmd2`'s — and `cmd1`'s low nibble never exceeds 3.
That is precisely the constraint "three-bit fields count 0 to 6, reserved bits
are never set", and it is not something that happens by accident.

Its checksum formula is also mathematically identical to the one we derived
from that same table, for all 256 command values, with
`K = (c_high << 4) | c_low`. Note that smartfire *hardcodes* its own remote's
constants (which work out to `K1 = 0xd0`, `K2 = 0x07`); ours are `0x0a` and
`0x86`, so that part of it would be wrong for any remote but theirs. We derive
`K` per remote from a single frame instead.

None of it settles **which three-bit field is which**. The evidence
above pins the boundaries; only pressing a button says whether `cmd2`'s upper
field is really the blower. That is what the procedure below is for.

## What is already settled

| field | status |
|-------|--------|
| `power` | **Confirmed.** Controlled capture, on/off/on/off, nothing else touched. |
| `flame` | **Confirmed.** Watched step 2→3→4 and 5→4→3, and seen at 0, 1 and 6. |
| `thermostat` | **Confirmed.** Smart→manual moved it and the flame stopped drifting. |
| `fan` | **Confirmed.** Swept off→high→off; `fan` and nothing else each time. |
| `light` | **Confirmed.** Swept off→high→off; `light` and nothing else each time. |
| `front` | **Not present on this appliance.** The handset has no split-flame control. |
| `aux` | No separate control on this handset — its MODE cycle is Flame / Blower / "Lights (AUX)", so the light level is the only thing reaching that part of the frame. |
| `pilot` | **Confirmed.** Cycled IPI→CPI→IPI; `pilot` moved and nothing else. |
| `reserved` | Zero in every frame so far. |

**Nothing remaining that this hardware can reach.** Six fields are confirmed by
controlled captures; split flame does not exist on this appliance and aux has
no separate control on this handset, so those two rest on smartfire's word and
would need different hardware to settle. The two reserved bits have never been
set in any frame.

The handset's MODE button cycles through exactly three adjustable things —
Flame Adjust, Blower, and "Lights (AUX)" — which is why the sweeps came out so
cleanly: MODE picks the target and up/down moves it, so one press can only ever
touch one field. It also explains the label's aside that Flame Adjust is "not
for smart thermostat": in smart mode the handset is driving the flame itself.

## Before you start

- **Put the handset in manual mode.** In thermostat/smart mode the remote
  changes the flame level on its own — captures show it stepping 0x31 to 0x30
  unprompted — which would corrupt every single-variable capture in this list.
  This is also why step 2 comes before the rest.
- **Start the daemon with recording on**, so nothing depends on a client
  staying connected:

      hrf serve --rx-freq 315M --record ~/mapping.jsonl

- **Nothing here transmits.** The whole procedure is pressing buttons on the
  physical handset while we listen, so none of the project's rules about
  synthesizing frames come into play.

## The procedure

Leave **at least 10 seconds** between steps. That is the only bookkeeping
required: the frames are self-labelling, because `tools/decode_proflame.py` reports which
field changed, so you do not have to record what you pressed. The gap is just
to keep two presses from blurring into one comparison.

If your handset does not have a given function, note it and move on — that is
information too.

| # | do this | expect to move |
|---|---------|----------------|
| 1 | Baseline: touch nothing for two minutes | nothing (see "echo" below) |
| 2 | Mode: smart ↔ manual | `thermostat` only |
| 3 | Flame: sweep 0…6 and back | `flame` |
| 4 | Fan: sweep, including off | `fan` |
| 5 | Light: sweep, including off | `light` |
| 6 | Aux, if the handset controls it separately | `aux` |
| 7 | Split flame, if the appliance has it | `front` |
| 8 | Pilot mode: IPI ↔ CPI | `pilot` |
| 9 | Power: on, off, on, off | `power` |

Steps 3 to 5 sweep the whole range deliberately. A single press only shows the
field moves; the full sweep shows it counts 0 to 6 linearly and where it
saturates, which is what an integration needs in order to map a percentage
onto it.

## Reading the results

    uv run python tools/decode_proflame.py ~/mapping.jsonl

The `appliance state` section prints each distinct frame with the fields
decoded and, next to it, what changed since the previous one. A clean run
looks like a single field name against each step.

Three outcomes are interesting rather than routine:

- **Two fields move on one press.** Either they are genuinely coupled — for
  instance, some models drop the fan when the flame goes out — or the layout
  splits a field in the wrong place.
- **`reserved` is not zero.** The layout is wrong. `tools/decode_proflame.py` says so
  explicitly rather than hiding it.
- **Nothing moves.** The handset has that function, but it does not reach the
  air, which matters: it means the fireplace cannot be told to do it over RF
  either.
- **A field that never moves is not evidence of a wrong label.** A level
  genuinely parked at one value reads exactly like a mislabelled field; only
  sweeping the control tells them apart, which is why steps 3–5 sweep.

## The echo: still open

smartfire reports that the fireplace echoes a successful command back verbatim.
Transmitting and listening finds nothing, but a half-duplex radio is deaf
during its own transmission, which is precisely when a reply would come — so
that test cannot answer the question. Malformed frames during handset presses
point weakly the other way. `docs/PROTOCOL.md` has the detail; settling it
needs a second receiver that never transmits.

## Afterward

Keep the recording. Add it to `tests/frames/` with the step it came from, the
way the existing captures are kept, so the conclusions stay pinned by data
rather than by memory.

[smartfire]: https://github.com/johnellinwood/smartfire
[rtl-433-1905]: https://github.com/merbanan/rtl_433/issues/1905
