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
`hrf decode` prints loudly when they are not zero — then the layout is wrong
and we have learned something more valuable than a confirmation.

Its layout is not taken on faith. It already explains every button press we
have captured, with nothing left unexplained:

| we pressed | frames moved | layout says |
|------------|--------------|-------------|
| on, off | `cmd1` `0x01`↔`0x00` | `power` only ✓ |
| flame up, flame down | `cmd2` `0x32`→`0x33`→`0x34` | `flame` only ✓ |
| mode → smart | `cmd1` `0x01`→`0x03` | `thermostat` only ✓ |

Its checksum formula is also mathematically identical to the one we derived
independently from `tests/cmd.csv`, for all 256 command values, with
`K = (c_high << 4) | c_low`. Note that smartfire *hardcodes* its own remote's
constants (which work out to `K1 = 0xd0`, `K2 = 0x07`); ours are `0x0a` and
`0x86`, so that part of it would be wrong for any remote but theirs. We derive
`K` per remote from a single frame instead.

## What is already settled

| field | status |
|-------|--------|
| `power` | **Confirmed.** Controlled capture, on/off/on/off, nothing else touched. |
| `flame` | **Confirmed.** Watched step 2→3→4 and 5→4→3, and seen at 0, 1 and 6. |
| `thermostat` | **Corroborated.** Our capture correlates it with smart mode, and the independent layout agrees, but we never isolated it. Step 2 below settles it. |
| `light`, `fan`, `aux`, `front`, `pilot` | **Unverified.** smartfire's claim only. |
| `reserved` | Zero in every frame so far. |

One specific prediction worth testing early: **`fan` reads 3 in every capture we
have**, across days and both modes. Either the blower really is sitting at level
3, or that field is not the fan. Step 4 answers it in one press.

## Before you start

- **Put the handset in manual mode.** In thermostat/smart mode the remote
  changes the flame level on its own — we have watched it step 0x31 to 0x30
  unprompted — which would corrupt every single-variable capture in this list.
  This is also why step 2 comes before the rest.
- **Start the daemon with recording on**, so nothing depends on a client
  staying connected:

      hrf serve --rx-freq 315M --record ~/mapping.jsonl

- **Nothing here transmits.** The whole procedure is pressing buttons on the
  physical handset while we listen, so none of the project's rules about
  synthesising frames come into play.

## The procedure

Leave **at least 10 seconds** between steps. That is the only bookkeeping
required: the frames are self-labelling, because `hrf decode` reports which
field changed, so you do not have to record what you pressed. The gap is just
to keep two presses from blurring into one comparison.

If your handset does not have a given function, note it and move on — that is
information too.

| # | do this | expect to move |
|---|---------|----------------|
| 1 | Baseline: touch nothing for two minutes | nothing (see "echo" below) |
| 2 | Mode: smart → manual, then manual → smart, then back to manual | `thermostat` only |
| 3 | Flame: press down to minimum, then up to maximum, one step at a time | `flame`, 0…6 |
| 4 | Fan: press down to minimum, then up to maximum | `fan`, 0…6 |
| 5 | Light: press down to minimum, then up to maximum | `light`, 0…6 |
| 6 | Aux outlet: toggle on, then off | `aux` |
| 7 | Split/front flame: toggle on, then off | `front` |
| 8 | Pilot mode: continuous ↔ intermittent (often a settings menu, not a button) | `pilot` |
| 9 | Power: off, then on | `power` (re-confirmation) |

Steps 3 to 5 sweep the whole range deliberately. A single press only shows the
field moves; the full sweep shows it counts 0 to 6 linearly and where it
saturates, which is what an integration needs in order to map a percentage
onto it.

## Reading the results

    hrf decode --in ~/mapping.jsonl

The `appliance state` section prints each distinct frame with the fields
decoded and, next to it, what changed since the previous one. A clean run
looks like a single field name against each step.

Three outcomes are interesting rather than routine:

- **Two fields move on one press.** Either they are genuinely coupled — for
  instance, some models drop the fan when the flame goes out — or the layout
  splits a field in the wrong place.
- **`reserved` is not zero.** The layout is wrong. `hrf decode` says so
  explicitly rather than hiding it.
- **Nothing moves.** The handset has that function but it does not reach the
  air, which matters: it means the fireplace cannot be told to do it over RF
  either.

## The echo, and why step 1 exists

smartfire reports that **the fireplace echoes a successful command back
verbatim**. We have not confirmed that, and step 1 is where it would show:
sitting idle, any frame at all is either the thermostat regulating or the
appliance talking.

If the echo is real it matters twice over. It is a confirmation channel — after
transmitting we could listen for the appliance agreeing, rather than assuming.
And it is a hazard for M5: a received frame is then not necessarily a user
pressing anything, so a naive state sync would treat the appliance's own echo,
or the thermostat's own regulation, as a command and fight it.

## Afterwards

Keep the recording. Add it to `tests/frames/` with the step it came from, the
way the existing captures are kept, so the conclusions stay pinned by data
rather than by memory.

[smartfire]: https://github.com/johnellinwood/smartfire
