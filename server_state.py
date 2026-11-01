"""Authoritative in-memory caption state for the LiveRecall server.

This module tracks what captions the server has accepted for each session.
It maintains separate storage for active (provisional) and finalized captions.

Active captions: can be replaced by newer revisions
Finalized captions: immutable, kept for recovery
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

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

# Max finals queued for one client before it is disconnected.
DEFAULT_MAX_PENDING_FINALS = 64

# In lag mode, send a provisional only after this long with nothing sent
LAG_PROVISIONAL_IDLE_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class FinalizedRecord:
    update: CaptionUpdate
    accepted_at: float

@dataclass(slots=True)
class ClientState:
    """Per client record that server maintains each client's caption delivery independently.
    """
    client_id: int | str
    session: str
    websocket: Any
    pending_finals: deque = field(default_factory=deque)
    pending_provisional: dict = field(default_factory=dict)
    
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    sender_task: asyncio.Task | None = None

    # Set when finals backlog exceeds max. client closed.
    overflowed: bool = False

    #timer for lag mode
    lag_until: float = 0.0
    last_send_at: float = 0.0


    def enqueue(self, update: CaptionUpdate, raw: str) -> None:
        if self.overflowed:
            return
        
        if update.final:
            self.pending_provisional.pop(update.segment, None)
            self.pending_finals.append(raw)
            if len(self.pending_finals) > DEFAULT_MAX_PENDING_FINALS:
                #Too far behind, drop
                self.overflowed = True
                self.pending_finals.clear()
                self.pending_provisional.clear()
            
        else:
            # Coalesce: overwrite an unsent older revision of this segment
            self.pending_provisional[update.segment] = raw
        self.wakeup.set()

    @property
    def has_pending(self) -> bool:
        return bool(self.pending_finals or self.pending_provisional)

    """send provisional if not in lag mode or nothing sent in 2 seconds"""
    def may_send_provisional(self, now: float) -> bool:
        return now >= self.lag_until or now - self.last_send_at >= LAG_PROVISIONAL_IDLE_SECONDS

    def next_pending(self, allow_provisional: bool = True) -> str | None:
        # Finals first (oldest to newest), then provisionals by lowest segment
        if self.pending_finals:
            return self.pending_finals.popleft()
        if self.pending_provisional and allow_provisional:
            return self.pending_provisional.pop(min(self.pending_provisional))
        return None

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
