#!/usr/bin/env python3
"""Check the Home Assistant encoder against the Rust implementation.

The Python encoder in `integrations/proflame/protocol.py` and the Rust one in
`proxyd/src/proflame.rs` have to agree, or Home Assistant will put something
different on the air than everything else in this project was verified with.

The check is end to end and uses real data: take every distinct appliance state
the daemon has ever recorded off the air, encode each with the Python encoder,
and hand the result to `hrf decode`. Every frame must come back as the state it
started as, with parity, sync, framing and both checksums intact.

    tools/check_python_encoder.py

Runs outside Home Assistant — `rf_protocols` is stubbed, since the encoder only
needs its base class.

Two things this learned the hard way, both about the check being real rather
than merely green. Counting what the decoder accepts off the captures cannot
show that it still validates anything: the frames it should reject there are
corrupt in several ways at once, so dropping any single check leaves the count
unchanged. Frames broken in exactly one way have to be built for that. And
when mutating this code to prove the check catches things, clear
`__pycache__` — `>> 4` and `>> 5` are the same length, so a restored file can
keep the mutant's bytecode and the whole exercise measures nothing.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
HRF = ROOT / "proxyd" / "target" / "release" / "hrf"
CAPTURES = [
    ROOT / "tests" / "frames" / "manual_sweeps.frames.jsonl",
    ROOT / "tests" / "frames" / "smart_mode.frames.jsonl",
]

#: This house's handset, as derived from its own captures.
REMOTE = dict(serial1=0x00, serial2=0x86, version=0x02, key1=0x0A, key2=0x86)


SYMBOL_US = 450
BLOCK_SYMBOLS = 26
SYNC_SYMBOLS = 4


def _to_symbols(timings: list[int]) -> list[bool]:
    """Expand timings into one entry per symbol, for surgical corruption."""
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
    """Make the last bit of the first block a zero instead of the stop bit."""
    corrupted = list(symbols)
    stop = BLOCK_SYMBOLS - 2
    corrupted[stop], corrupted[stop + 1] = False, True
    return _to_timings(corrupted)


def _break_manchester(symbols: list[bool]) -> list[int]:
    """Two equal symbols in a row where a bit should be."""
    corrupted = list(symbols)
    corrupted[SYNC_SYMBOLS] = corrupted[SYNC_SYMBOLS + 1] = True
    return _to_timings(corrupted)


def _stub_rf_protocols() -> None:
    """Provide just enough of Home Assistant's rf_protocols to import."""
    stub = types.ModuleType("rf_protocols")

    class ModulationType:
        OOK = "OOK"

    class RadioFrequencyCommand:
        def __init__(self, **kwargs: object) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    stub.ModulationType = ModulationType  # type: ignore[attr-defined]
    stub.RadioFrequencyCommand = RadioFrequencyCommand  # type: ignore[attr-defined]
    sys.modules["rf_protocols"] = stub


def _states_from_captures() -> list[dict[str, int]]:
    """Every distinct state `hrf decode` reports across the captures."""
    states: set[tuple[tuple[str, int], ...]] = set()
    for capture in CAPTURES:
        report = subprocess.run(
            [str(HRF), "decode", "--in", str(capture)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for line in report.splitlines():
            if not line.strip().startswith("power="):
                continue
            fields = {
                key: int(value)
                for key, value in (
                    part.split("=") for part in line.split() if "=" in part
                )
            }
            states.add(tuple(sorted(fields.items())))
    return [dict(state) for state in sorted(states)]


def main() -> int:
    """Encode every captured state and check the Rust decoder agrees."""
    if not HRF.exists():
        print(f"build hrf first: cargo build --release --manifest-path proxyd/Cargo.toml")
        return 2

    _stub_rf_protocols()
    sys.path.insert(0, str(ROOT / "integrations" / "proflame"))
    from protocol import ProflameCommand, Remote, State  # noqa: PLC0415

    remote = Remote(**REMOTE)
    states = _states_from_captures()
    if not states:
        print("no states found in the captures")
        return 1

    frames = [
        ProflameCommand(
            remote,
            State(
                power=bool(state["power"]),
                flame=state["flame"],
                fan=state["fan"],
                light=state["light"],
                thermostat=bool(state["thermostat"]),
                aux=bool(state["aux"]),
                front=bool(state["front"]),
                pilot=bool(state["pilot"]),
            ),
        ).get_raw_timings()
        for state in states
    ]

    encoded = ROOT / "target-python-encoded.json"
    encoded.write_text(json.dumps(frames))
    try:
        report = subprocess.run(
            [str(HRF), "decode", "--in", str(encoded)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    finally:
        encoded.unlink(missing_ok=True)

    failures: list[str] = []

    # 1. Everything decodes. `hrf decode` annotates framing violations after an
    #    arrow, and annotates state changes after the same arrow — matching on
    #    the words is a trap, since the success line itself reads "decoded with
    #    parity, stop and framing intact". The timeline is the only arrow that
    #    says "changed:", so any other arrow is a fault.
    clean = f"{len(frames)}/{len(frames)} frames decoded"
    if clean not in report:
        failures.append(f"not every frame decoded; expected '{clean}'")
    failures += [
        f"framing: {line.strip()}"
        for line in report.splitlines()
        if "<- " in line and "changed:" not in line
    ]

    # 2. It decodes back to the states that went in. Without this the check is
    #    vacuous against the mistake that matters most — packing a field into
    #    the wrong bits still produces a perfectly well-formed frame, just one
    #    that means something else.
    decoded = _states_from_report(report)
    intended = {tuple(sorted(state.items())) for state in states}
    if decoded != intended:
        for missing in sorted(intended - decoded)[:3]:
            failures.append(f"encoded but came back as something else: {dict(missing)}")
        for extra in sorted(decoded - intended)[:3]:
            failures.append(f"decoded a state that was never encoded: {dict(extra)}")

    # 3. The checksums are the ones *this handset's* receiver expects. The
    #    decoder derives the constants from the frame rather than knowing them,
    #    so a checksum that is wrong but consistently wrong looks perfectly
    #    healthy to it — the constants have to be named to be checked.
    for half, key in ((1, REMOTE["key1"]), (2, REMOTE["key2"])):
        if f"half {half}: K = 0x{key:02x}  consistent" not in report:
            failures.append(f"checksum constant for half {half} is not 0x{key:02x}")

    # 4. The Python decoder reads what the daemon really recorded off the air,
    #    and agrees with the Rust decoder about it. Home Assistant depends on
    #    this to learn the handset's identity and to follow it.
    from protocol import decode_frame  # noqa: PLC0415

    heard: set[tuple[tuple[str, int], ...]] = set()
    remotes: set[tuple[int, ...]] = set()
    total = clean_count = 0
    for capture in CAPTURES:
        # How many frames the Rust decoder accepted from this file. Comparing
        # counts, not just the states, is what catches a decoder that skips a
        # validity check: being *more* permissive than the reference would let
        # corrupted frames through, and Home Assistant would act on them.
        report_for_capture = subprocess.run(
            [str(HRF), "decode", "--in", str(capture)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        match = re.search(r"(\d+)/(\d+) frames decoded", report_for_capture)
        assert match is not None, f"no decode summary for {capture.name}"
        rust_clean = int(match.group(1))
        python_clean = 0

        for line in capture.read_text().splitlines():
            if not line.strip():
                continue
            total += 1
            if (decoded := decode_frame(json.loads(line)["timings"])) is None:
                continue
            clean_count += 1
            python_clean += 1
            remotes.add(
                (
                    decoded.remote.serial1,
                    decoded.remote.serial2,
                    decoded.remote.version,
                    decoded.remote.key1,
                    decoded.remote.key2,
                )
            )
            heard.add(
                tuple(
                    sorted(
                        {
                            "power": int(decoded.state.power),
                            "flame": decoded.state.flame,
                            "fan": decoded.state.fan,
                            "light": decoded.state.light,
                            "thermostat": int(decoded.state.thermostat),
                            "aux": int(decoded.state.aux),
                            "front": int(decoded.state.front),
                            "pilot": int(decoded.state.pilot),
                        }.items()
                    )
                )
            )

        if python_clean != rust_clean:
            failures.append(
                f"{capture.name}: Python accepted {python_clean} frames, "
                f"Rust accepted {rust_clean}"
            )

    if heard != intended:
        failures.append(
            f"the Python decoder read {len(heard)} states off the captures, "
            f"the Rust one read {len(intended)}"
        )
    expected_remote = (
        REMOTE["serial1"],
        REMOTE["serial2"],
        REMOTE["version"],
        REMOTE["key1"],
        REMOTE["key2"],
    )
    if remotes != {expected_remote}:
        failures.append(f"handset identity came out as {remotes}, expected {expected_remote}")

    # 5. It rejects frames that are wrong in exactly one way.
    #
    #    Counting what the decoder accepts off the captures cannot show this:
    #    the frames it should reject there are corrupt in several ways at once,
    #    so dropping any single check leaves the count unchanged. A decoder
    #    that had quietly stopped checking parity would look perfect right up
    #    until it handed Home Assistant a garbled state. So the bad frames are
    #    built here, each breaking one rule and nothing else.
    good = ProflameCommand(remote, State(power=True, flame=4, light=2)).get_raw_timings()
    for description, corrupt in (
        ("a flipped data bit, which only parity can catch", _flip_first_data_bit),
        ("a broken sync pattern", _break_sync),
        ("a cleared stop bit", _clear_stop_bit),
        ("a Manchester violation", _break_manchester),
    ):
        if decode_frame(corrupt(_to_symbols(good))) is not None:
            failures.append(f"accepted a frame with {description}")
    if decode_frame(good) is None:
        failures.append("rejected a frame it built itself")

    if failures:
        print("FAILED")
        for failure in failures[:8]:
            print(f"  {failure}")
        return 1

    print(f"{len(frames)} captured states encoded by Python, all decoded by Rust")
    print("states round-trip, framing intact, checksum constants as this handset's")
    print(
        f"Python decoder reads {clean_count}/{total} recorded frames, "
        f"same states and handset identity as Rust"
    )
    return 0


def _states_from_report(report: str) -> set[tuple[tuple[str, int], ...]]:
    """The distinct states `hrf decode` reports, as comparable tuples."""
    states = set()
    for line in report.splitlines():
        if not line.strip().startswith("power="):
            continue
        fields = {
            key: int(value)
            for key, value in (part.split("=") for part in line.split() if "=" in part)
        }
        states.add(tuple(sorted(fields.items())))
    return states


if __name__ == "__main__":
    sys.exit(main())
