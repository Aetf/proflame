# proflame

The SIT Proflame 2 fireplace remote protocol, in Python: encode a complete
appliance state into OOK timings, decode received timings back into a state
and the identity of the handset that sent them, and derive the per-remote
checksum constants from a single captured frame.

Transceiver-agnostic by design: this library deals in signed-microsecond
timing lists, and a radio is whatever turns those into RF and back. The
first consumers are the [hass-proflame](https://github.com/Aetf/hass-proflame)
Home Assistant integration and the
[hackrf-proxy](https://github.com/Aetf/hackrf-proxy) daemon ecosystem, but
nothing here depends on either.

```python
from proflame import Remote, State, encode_timings, decode_frame

# One captured frame is enough to learn a handset, checksum constants and all.
heard = decode_frame(received_timings)
remote = heard.remote

# A frame always carries the complete appliance state, never a delta.
timings = encode_timings(remote, State(power=True, flame=4, light=2))
```

Two things worth knowing before building on it:

- **The checksum constants are per handset.** They fall out of any one valid
  frame (`Remote.from_frame`, `derive_key`); hardcoding one remote's
  constants produces frames every other receiver rejects.
- **The appliance is stateless and answers no questions.** Every frame
  carries the whole state, so a consumer must hold its own belief; nothing in
  the protocol confirms what the appliance did.

## Documentation and data

- `docs/PROTOCOL.md` — the protocol derivation: physical layer, framing,
  field layout, checksum model, open questions, acknowledgments.
- `docs/MAPPING.md` — the field-confirmation procedure and its results.
- `tests/` — real captures: an inherited five-remote packet table and frames
  recorded off the air, used as golden test data.
- `tools/` — bench scripts: the standalone reference decoder and the
  checksum analysis that first solved the relation.

## License

MIT OR Apache-2.0, at your option.
