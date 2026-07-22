"""Transport-neutral JSON framing for Voice Input Protocol v1 messages."""

from __future__ import annotations

import json
from typing import Any

from voice_input_protocol import ProtocolError, ProtocolMessage


MAX_FRAME_BYTES = 1_048_576


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def encode_message(message: ProtocolMessage) -> bytes:
    """Encode one validated message as canonical UTF-8 JSON bytes."""
    if not isinstance(message, ProtocolMessage):
        raise TypeError("message must be a ProtocolMessage")
    try:
        frame = json.dumps(
            message.to_mapping(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ProtocolError(
            "message cannot be encoded as canonical UTF-8 JSON"
        ) from error
    if len(frame) > MAX_FRAME_BYTES:
        raise ProtocolError("protocol JSON frame exceeds size limit")
    return frame


def decode_message(frame: bytes) -> ProtocolMessage:
    """Decode and validate one canonical UTF-8 JSON message frame."""
    if not isinstance(frame, bytes):
        raise TypeError("frame must be bytes")
    if len(frame) > MAX_FRAME_BYTES:
        raise ProtocolError("protocol JSON frame exceeds size limit")
    try:
        value: Any = json.loads(
            frame.decode("utf-8"),
            parse_constant=_reject_nonstandard_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ProtocolError("invalid protocol JSON frame") from error

    # ProtocolMessage remains the single owner of schema, version, kind,
    # payload, and unknown-field validation.
    message = ProtocolMessage.from_mapping(value)
    if encode_message(message) != frame:
        raise ProtocolError("protocol JSON frame is not canonical")
    return message


__all__ = ["MAX_FRAME_BYTES", "decode_message", "encode_message"]
