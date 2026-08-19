"""SIT Proflame 2 fireplace remote protocol."""

from .protocol import (
    BLOCK_SYMBOLS,
    CE_FREQUENCY,
    DEFAULT_REPEATS,
    FCC_FREQUENCY,
    FRAME_BLOCKS,
    INTER_FRAME_GAP_US,
    MAX_LEVEL,
    SYMBOL_US,
    DecodedFrame,
    Remote,
    State,
    checksum,
    decode_frame,
    derive_key,
    encode_timings,
    frame_blocks,
)

__all__ = [
    "BLOCK_SYMBOLS",
    "CE_FREQUENCY",
    "DEFAULT_REPEATS",
    "FCC_FREQUENCY",
    "FRAME_BLOCKS",
    "INTER_FRAME_GAP_US",
    "MAX_LEVEL",
    "SYMBOL_US",
    "DecodedFrame",
    "Remote",
    "State",
    "checksum",
    "decode_frame",
    "derive_key",
    "encode_timings",
    "frame_blocks",
]
