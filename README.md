# proflame

The SIT Proflame 2 fireplace remote protocol, in Python: encode a complete
appliance state into OOK timings, decode received timings back into a state
and the identity of the handset that sent them, and derive the per-remote
checksum constants from a single captured frame.

Transceiver-agnostic by design: this library deals in signed-microsecond
timing lists, and a radio is whatever turns those into RF and back.

See `docs/PROTOCOL.md` for the protocol derivation and `docs/MAPPING.md` for
the field-confirmation procedure.

## License

MIT OR Apache-2.0, at your option.
