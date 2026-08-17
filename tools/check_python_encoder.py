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
"""

from __future__ import annotations

import json
import pathlib
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

    if failures:
        print("FAILED")
        for failure in failures[:8]:
            print(f"  {failure}")
        return 1

    print(f"{len(frames)} captured states encoded by Python, all decoded by Rust")
    print("states round-trip, framing intact, checksum constants as this handset's")
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
