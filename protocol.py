"""Protocol parsing and revision-state rules.

This module handles incoming messages from the caption source and student clients.
It validates messages and decides whether a new caption revision should be accepted.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any



class ProtocolError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class SourceHello:
    session: str


@dataclass(frozen=True, slots=True)
class ClientHello:
    client_id: int | str
    session: str
    last_segment: int
    last_revision: int
    history_seconds: int


@dataclass(frozen=True, slots=True)
class CaptionUpdate:
    session: str
    segment: int
    revision: int
    source_ts: float
    final: bool
    text: str

    def to_message(self) -> dict[str, Any]:
        return {
            "type": "CAPTION_UPDATE",
            "session": self.session,
            "segment": self.segment,
            "revision": self.revision,
            "source_ts": self.source_ts,
            "final": self.final,
            "text": self.text,
        }


class RevisionDecision(str, Enum):
    ACCEPT = "accept"           # Accept this new revision
    DUPLICATE = "duplicate"     # Same as what we have, ignore
    STALE = "stale"             # Older than what we have, ignore
    FINAL_LOCKED = "final_locked"  # Can't replace a final caption


def _decode_object(raw: str | bytes) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise ProtocolError("message must be valid JSON") from exc
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    return message


def _required_string(message: dict[str, Any], field: str) -> str:
    value = message.get(field)
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{field} must be a non-empty string")
    return value


def _required_int(
    message: dict[str, Any], field: str, *, minimum: int | None = None
) -> int:
    value = message.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ProtocolError(f"{field} must be >= {minimum}")
    return value


def parse_hello(raw: str | bytes) -> SourceHello | ClientHello:
    message = _decode_object(raw)
    message_type = message.get("type")

    if message_type == "SOURCE_HELLO":
        return SourceHello(session=_required_string(message, "session"))

    if message_type == "CLIENT_HELLO":
        client_id = message.get("client_id")
        if isinstance(client_id, bool) or not isinstance(client_id, (int, str)):
            raise ProtocolError("client_id must be an integer or string")
        if isinstance(client_id, str) and not client_id:
            raise ProtocolError("client_id must not be empty")

        history_seconds = _required_int(message, "history_seconds", minimum=0)
        if history_seconds > 60:
            raise ProtocolError("history_seconds must be <= 60")

        return ClientHello(
            client_id=client_id,
            session=_required_string(message, "session"),
            last_segment=_required_int(message, "last_segment", minimum=0),
            last_revision=_required_int(message, "last_revision", minimum=0),
            history_seconds=history_seconds,
        )

    raise ProtocolError("expected SOURCE_HELLO or CLIENT_HELLO")


def parse_caption_update(raw: str | bytes) -> CaptionUpdate:
    message = _decode_object(raw)
    if message.get("type") != "CAPTION_UPDATE":
        raise ProtocolError("expected CAPTION_UPDATE")

    source_ts = message.get("source_ts")
    if isinstance(source_ts, bool) or not isinstance(source_ts, (int, float)):
        raise ProtocolError("source_ts must be a number")
    source_ts = float(source_ts)
    if not math.isfinite(source_ts):
        raise ProtocolError("source_ts must be finite")

    final = message.get("final")
    if not isinstance(final, bool):
        raise ProtocolError("final must be a boolean")

    text = message.get("text")
    if not isinstance(text, str):
        raise ProtocolError("text must be a string")

    return CaptionUpdate(
        session=_required_string(message, "session"),
        segment=_required_int(message, "segment", minimum=1),
        revision=_required_int(message, "revision", minimum=1),
        source_ts=source_ts,
        final=final,
        text=text,
    )


def compare_revision(
    current: CaptionUpdate | None, incoming: CaptionUpdate
) -> RevisionDecision:
    if current is None:
        return RevisionDecision.ACCEPT
    if current.session != incoming.session or current.segment != incoming.segment:
        raise ProtocolError("revision comparison requires the same session and segment")
    if incoming.revision < current.revision:
        return RevisionDecision.STALE
    if incoming.revision == current.revision:
        return RevisionDecision.DUPLICATE
    if current.final:
        return RevisionDecision.FINAL_LOCKED
    return RevisionDecision.ACCEPT


def select_revision(
    current: CaptionUpdate | None, incoming: CaptionUpdate
) -> tuple[CaptionUpdate, RevisionDecision]:
    """Return the authoritative caption update and the decision.
    
    If incoming is ACCEPT, return incoming.
    Otherwise, return current (unchanged) with the decision.
    """
    decision = compare_revision(current, incoming)
    if decision is RevisionDecision.ACCEPT:
        return incoming, decision
    assert current is not None
    return current, decision
