"""Authoritative in-memory caption state for the LiveRecall server.

This module tracks what captions the server has accepted for each session.
It maintains separate storage for active (provisional) and finalized captions.

Active captions: can be replaced by newer revisions
Finalized captions: immutable, kept for recovery
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from protocol import (
    CaptionUpdate,
    ProtocolError,
    RevisionDecision,
    select_revision,
)


# How far back to keep finalized captions for recovery
DEFAULT_HISTORY_RETENTION_SECONDS = 90.0

# Max number of finalized captions to keep
DEFAULT_MAX_FINALIZED_CAPTIONS = 4096


@dataclass(frozen=True, slots=True)
class FinalizedRecord:
    update: CaptionUpdate
    accepted_at: float

class SessionState:
    """Track the latest accepted state for every segment in one session.
    
    - segment = caption line number (1, 2, 3...)
    - revision = version number (1, 2, 3...) - instructor revises before finalizing
    - final = true means the instructor declares this complete
    
    Active = provisional captions
    Finalized = completed captions
    """

    def __init__(
        self,
        session: str,
        *,
        history_retention_seconds: float = DEFAULT_HISTORY_RETENTION_SECONDS,
        max_finalized_captions: int = DEFAULT_MAX_FINALIZED_CAPTIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not session:
            raise ValueError("session must not be empty")
        if history_retention_seconds < 60:
            raise ValueError("history retention must cover at least 60 seconds")
        if max_finalized_captions < 1:
            raise ValueError("max_finalized_captions must be >= 1")
        self.session = session
        self.history_retention_seconds = float(history_retention_seconds)
        self.max_finalized_captions = max_finalized_captions
        self._clock = clock
        self._active: dict[int, CaptionUpdate] = {}
        self._finalized: OrderedDict[int, FinalizedRecord] = OrderedDict()
        # Track highest finalized segment to prevent accepting new revisions for segments that have already been finalized
        self._highest_finalized_segment = 0

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else float(now)

    def _prune_finalized(self, now: float) -> None:
        # Remove captions older than retention window
        cutoff = now - self.history_retention_seconds
        while self._finalized:
            _, oldest = next(iter(self._finalized.items()))
            if oldest.accepted_at >= cutoff:
                break
            self._finalized.popitem(last=False)

        # Also remove if too many items
        while len(self._finalized) > self.max_finalized_captions:
            self._finalized.popitem(last=False)

    def current(self, segment: int, *, now: float | None = None) -> CaptionUpdate | None:
        self._prune_finalized(self._now(now))
        active = self._active.get(segment)
        if active is not None:
            return active
        record = self._finalized.get(segment)
        return record.update if record is not None else None

    def accept(self, incoming: CaptionUpdate, *, now: float | None = None) -> RevisionDecision:
        if incoming.session != self.session:
            raise ProtocolError("caption session does not match SessionState")

        accepted_at = self._now(now)
        self._prune_finalized(accepted_at)
        current = self.current(incoming.segment, now=accepted_at)
        if current is None and incoming.segment <= self._highest_finalized_segment:
            return RevisionDecision.FINAL_LOCKED
        selected, decision = select_revision(current, incoming)
        if decision is not RevisionDecision.ACCEPT:
            return decision

        if selected.final:
            # Move from active to finalized
            self._active.pop(selected.segment, None)
            self._finalized[selected.segment] = FinalizedRecord(
                update=selected,
                accepted_at=accepted_at,
            )
            
            self._highest_finalized_segment = max(
                self._highest_finalized_segment,
                selected.segment,
            )
            self._prune_finalized(accepted_at)
        else:
            self._active[selected.segment] = selected
        return decision

    def active_snapshot(self) -> tuple[CaptionUpdate, ...]:
        # Ordered by segment number, oldest to newest
        return tuple(self._active[key] for key in sorted(self._active))

    def finalized_snapshot(self, *, now: float | None = None) -> tuple[CaptionUpdate, ...]:
        # Ordered by segment number, oldest to newest
        self._prune_finalized(self._now(now))
        return tuple(record.update for record in self._finalized.values())

    def recent_finals(self, history_seconds: int, *, now: float | None = None) -> tuple[CaptionUpdate, ...]:
        """Return all finalized captions within the requested time window.
        
        Caller can then filter by client resume state (last_segment/last_revision).
        """
        if not 0 <= history_seconds <= 60:
            raise ValueError("history_seconds must be between 0 and 60")
        current_time = self._now(now)
        self._prune_finalized(current_time)
        if history_seconds == 0:
            return ()
        cutoff = current_time - history_seconds
        return tuple(
            record.update
            for record in self._finalized.values()
            if record.accepted_at >= cutoff
        )

    def filtered_finals(self, history_seconds: int, last_segment: int, last_revision: int, *, now: float | None = None) -> tuple[CaptionUpdate, ...]:
        """Return finalized captions the client needs based on resume state.
        
        Returns only captions that are:
        1. Within history_seconds window
        2. Newer than what client has (segment > last_segment OR same segment but revision > last_revision)
        
        """
        if not 0 <= history_seconds <= 60:
            raise ValueError("history_seconds must be between 0 and 60")
        if last_segment < 0:
            raise ValueError("last_segment must be >= 0")
        if last_revision < 0:
            raise ValueError("last_revision must be >= 0")

        current_time = self._now(now)
        self._prune_finalized(current_time)
        if history_seconds == 0:
            return ()

        cutoff = current_time - history_seconds
        return tuple(
            record.update
            for record in self._finalized.values()
            if record.accepted_at >= cutoff
            and (
                record.update.segment > last_segment
                or (record.update.segment == last_segment and record.update.revision > last_revision)
            )
        )

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def finalized_count(self) -> int:
        self._prune_finalized(self._clock())
        return len(self._finalized)
