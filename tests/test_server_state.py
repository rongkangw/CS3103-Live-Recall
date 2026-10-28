import unittest

from protocol import CaptionUpdate, ProtocolError, RevisionDecision
from server_state import (
    DEFAULT_HISTORY_RETENTION_SECONDS,
    DEFAULT_MAX_FINALIZED_CAPTIONS,
    SessionState,
)


def update(segment=1, revision=1, *, final=False, session="L1", text=None):
    return CaptionUpdate(
        session=session,
        segment=segment,
        revision=revision,
        source_ts=1760000000.0 + segment + revision / 10,
        final=final,
        text=text or f"segment {segment} revision {revision}",
    )


class SessionStateTests(unittest.TestCase):
    def setUp(self):
        self.state = SessionState("L1")

    def test_first_update_becomes_active_state(self):
        incoming = update()
        decision = self.state.accept(incoming)
        self.assertIs(decision, RevisionDecision.ACCEPT)
        self.assertEqual(self.state.active_snapshot(), (incoming,))
        self.assertEqual(self.state.finalized_snapshot(), ())

    def test_newer_revision_replaces_active_state(self):
        self.state.accept(update(revision=1))
        newer = update(revision=2)
        self.assertIs(self.state.accept(newer), RevisionDecision.ACCEPT)
        self.assertIs(self.state.current(1), newer)
        self.assertEqual(self.state.active_count, 1)

    def test_duplicate_does_not_mutate_state(self):
        accepted = update(revision=2, text="accepted")
        self.state.accept(accepted)
        decision = self.state.accept(update(revision=2, text="different"))
        self.assertIs(decision, RevisionDecision.DUPLICATE)
        self.assertIs(self.state.current(1), accepted)

    def test_stale_revision_does_not_mutate_state(self):
        accepted = update(revision=3)
        self.state.accept(accepted)
        self.assertIs(
            self.state.accept(update(revision=2)),
            RevisionDecision.STALE,
        )
        self.assertIs(self.state.current(1), accepted)

    def test_final_moves_segment_out_of_active_state(self):
        self.state.accept(update(revision=1))
        final = update(revision=2, final=True)
        self.assertIs(self.state.accept(final), RevisionDecision.ACCEPT)
        self.assertEqual(self.state.active_count, 0)
        self.assertEqual(self.state.finalized_snapshot(), (final,))
        self.assertIs(self.state.current(1), final)

    def test_final_state_rejects_newer_revision(self):
        final = update(revision=3, final=True)
        self.state.accept(final)
        decision = self.state.accept(update(revision=4))
        self.assertIs(decision, RevisionDecision.FINAL_LOCKED)
        self.assertIs(self.state.current(1), final)

    def test_segments_advance_independently(self):
        first = update(segment=1, revision=2, final=True)
        second = update(segment=2, revision=1)
        self.state.accept(first)
        self.state.accept(second)
        self.assertEqual(self.state.finalized_snapshot(), (first,))
        self.assertEqual(self.state.active_snapshot(), (second,))

    def test_wrong_session_is_rejected_without_mutation(self):
        with self.assertRaises(ProtocolError):
            self.state.accept(update(session="other"))
        self.assertEqual(self.state.active_count, 0)
        self.assertEqual(self.state.finalized_count, 0)


class BoundedHistoryTests(unittest.TestCase):
    def test_default_bounds_cover_required_window(self):
        self.assertGreaterEqual(DEFAULT_HISTORY_RETENTION_SECONDS, 60)
        self.assertGreater(DEFAULT_MAX_FINALIZED_CAPTIONS, 0)

    def test_retention_shorter_than_sixty_seconds_is_rejected(self):
        with self.assertRaises(ValueError):
            SessionState("L1", history_retention_seconds=59)

    def test_time_window_evicts_only_items_older_than_retention(self):
        state = SessionState(
            "L1",
            history_retention_seconds=60,
            clock=lambda: 0,
        )
        first = update(segment=1, final=True)
        second = update(segment=2, final=True)
        state.accept(first, now=0)
        state.accept(second, now=30)

        # The boundary is inclusive: exactly 60 seconds old is retained.
        self.assertEqual(state.finalized_snapshot(now=60), (first, second))
        self.assertEqual(state.finalized_snapshot(now=61), (second,))

    def test_capacity_limit_evicts_oldest_final(self):
        state = SessionState(
            "L1",
            history_retention_seconds=60,
            max_finalized_captions=2,
            clock=lambda: 0,
        )
        finals = [update(segment=i, final=True) for i in range(1, 4)]
        for timestamp, final in enumerate(finals):
            state.accept(final, now=timestamp)
        self.assertEqual(state.finalized_snapshot(now=3), tuple(finals[1:]))

    def test_evicted_final_cannot_be_resurrected(self):
        state = SessionState(
            "L1",
            history_retention_seconds=60,
            max_finalized_captions=1,
            clock=lambda: 0,
        )
        state.accept(update(segment=1, revision=3, final=True), now=0)
        state.accept(update(segment=2, revision=3, final=True), now=1)
        self.assertIsNone(state.current(1, now=1))
        self.assertIs(
            state.accept(update(segment=1, revision=4), now=1),
            RevisionDecision.FINAL_LOCKED,
        )

    def test_recent_finals_are_oldest_to_newest(self):
        state = SessionState(
            "L1",
            history_retention_seconds=90,
            clock=lambda: 0,
        )
        finals = [update(segment=i, final=True) for i in range(1, 4)]
        state.accept(finals[0], now=10)
        state.accept(finals[1], now=40)
        state.accept(finals[2], now=70)
        self.assertEqual(state.recent_finals(60, now=80), tuple(finals[1:]))

    def test_zero_second_history_is_empty(self):
        state = SessionState("L1", clock=lambda: 10)
        state.accept(update(final=True), now=10)
        self.assertEqual(state.recent_finals(0, now=10), ())

    def test_source_timestamp_does_not_control_retention(self):
        state = SessionState(
            "L1",
            history_retention_seconds=60,
            clock=lambda: 100,
        )
        old_source_time = CaptionUpdate(
            session="L1",
            segment=1,
            revision=1,
            source_ts=1.0,
            final=True,
            text="accepted now despite old source timestamp",
        )
        state.accept(old_source_time, now=100)
        self.assertEqual(state.recent_finals(60, now=101), (old_source_time,))

    def test_active_state_is_not_removed_by_history_pruning(self):
        state = SessionState(
            "L1",
            history_retention_seconds=60,
            clock=lambda: 0,
        )
        active = update(segment=2)
        state.accept(update(segment=1, final=True), now=0)
        state.accept(active, now=1)
        self.assertEqual(state.finalized_snapshot(now=100), ())
        self.assertEqual(state.active_snapshot(), (active,))


if __name__ == "__main__":
    unittest.main()
