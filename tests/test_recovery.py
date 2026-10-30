"""Tests for client recovery and late join (Step 4)."""

import unittest

from protocol import CaptionUpdate
from server_state import SessionState


def update(segment=1, revision=1, *, final=False, session="L1", text=None):
    """Create a CaptionUpdate with sensible defaults."""
    return CaptionUpdate(
        session=session,
        segment=segment,
        revision=revision,
        source_ts=1760000000.0 + segment + revision / 10,
        final=final,
        text=text or f"segment {segment} revision {revision}",
    )


class FilteredFinalsTests(unittest.TestCase):
    """Test the filtered_finals method for recovery filtering."""

    def setUp(self):
        self.state = SessionState("L1", clock=lambda: 100, history_retention_seconds=60)

    def test_filtered_finals_returns_empty_when_no_finals(self):
        """No finals in state means empty filtered result."""
        result = self.state.filtered_finals(60, 0, 0)
        self.assertEqual(result, ())

    def test_filtered_finals_returns_all_when_no_resume_state(self):
        """Client with last_segment=0, last_revision=0 should get all finals."""
        self.state.accept(update(segment=1, revision=1, final=True), now=40)
        self.state.accept(update(segment=2, revision=1, final=True), now=50)
        self.state.accept(update(segment=3, revision=1, final=True), now=60)

        result = self.state.filtered_finals(60, 0, 0)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].segment, 1)
        self.assertEqual(result[1].segment, 2)
        self.assertEqual(result[2].segment, 3)

    def test_filtered_finals_excludes_recovered_segment(self):
        """Client that already has segment 1 should not receive it again."""
        self.state.accept(update(segment=1, revision=1, final=True), now=40)
        self.state.accept(update(segment=2, revision=1, final=True), now=50)

        # Client has segment 1, revision 1
        result = self.state.filtered_finals(60, 1, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].segment, 2)

    def test_filtered_finals_excludes_exact_resume_state(self):
        """Client that has segment 2, revision 3 should not receive segment 2, revision 3."""
        self.state.accept(update(segment=1, revision=1, final=True), now=30)
        self.state.accept(update(segment=2, revision=3, final=True), now=40)
        self.state.accept(update(segment=3, revision=1, final=True), now=50)

        # Client has segment 2, revision 3 - should get segment 3 but not segment 2
        result = self.state.filtered_finals(60, 2, 3)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].segment, 3)

    def test_filtered_finals_includes_newer_revision(self):
        """Client should receive newer revisions of segments it partially has."""
        # Use a fresh segment (4) for the "newer revision" part since segment 1 is already final
        # and we can't have higher revisions of a finalized segment
        self.state.accept(update(segment=1, revision=1, final=True), now=40)
        self.state.accept(update(segment=2, revision=1, final=True), now=50)
        self.state.accept(update(segment=3, revision=1, final=True), now=60)
        self.state.accept(update(segment=4, revision=1, final=False), now=65)  # Provisional
        self.state.accept(update(segment=4, revision=2, final=True), now=70)  # Final revision of segment 4

        # Client has segment 1, revision 1 - should get all segments 2, 3, 4
        # Segment 4 has revision 2 (the latest, since rev 1 was provisional)
        result = self.state.filtered_finals(60, 1, 1)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].segment, 2)
        self.assertEqual(result[1].segment, 3)
        self.assertEqual(result[2].segment, 4)
        self.assertEqual(result[2].revision, 2)  # Latest revision of segment 4

    def test_filtered_finals_ignores_time_window(self):
        """Client with resume state in old time window should get filtered results."""
        # At clock=100, history_retention=60, cutoff=40
        # Use times that ensure some items are within window and some are not
        self.state.accept(update(segment=1, revision=1, final=True), now=30)  # Outside cutoff (30<40)
        self.state.accept(update(segment=2, revision=1, final=True), now=50)  # Inside (50>=40)
        self.state.accept(update(segment=3, revision=1, final=True), now=60)  # Inside (60>=40)

        # Client wants 30 seconds (cutoff=70), has segment 1
        # Items: seg1(t=30), seg2(t=50), seg3(t=60)
        # cutoff=70: all items have t<70, so none pass time filter
        # Wait, that's wrong. Let me recalculate.
        # cutoff = 100 - 30 = 70
        # seg1(t=30): 30 < 70, EXCLUDED by time
        # seg2(t=50): 50 < 70, EXCLUDED by time  
        # seg3(t=60): 60 < 70, EXCLUDED by time
        # Result: empty! This test needs adjustment.
        # 
        # Let me use a larger time window:
        # cutoff = 100 - 60 = 40 (using 60-second window)
        # seg1(t=30): 30 < 40, EXCLUDED by time
        # seg2(t=50): 50 >= 40, INCLUDED
        # seg3(t=60): 60 >= 40, INCLUDED
        # Resume state (1,1) excludes seg1
        # Result: seg2, seg3
        result = self.state.filtered_finals(60, 1, 1)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].segment, 2)
        self.assertEqual(result[1].segment, 3)

    def test_filtered_finals_respects_time_cutoff(self):
        """Older finals outside time window should not be returned."""
        state = SessionState("L1", clock=lambda: 100, history_retention_seconds=60)
        state.accept(update(segment=1, revision=1, final=True), now=10)  # Too old
        state.accept(update(segment=2, revision=1, final=True), now=40)  # Inside
        state.accept(update(segment=3, revision=1, final=True), now=70)  # Inside

        # Client has nothing, should get only 2 and 3
        result = state.filtered_finals(60, 0, 0)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].segment, 2)
        self.assertEqual(result[1].segment, 3)

    def test_filtered_finals_excludes_provisional(self):
        """Provisional updates should not be in filtered results."""
        self.state.accept(update(segment=1, revision=1, final=False), now=40)
        self.state.accept(update(segment=2, revision=1, final=True), now=50)

        # Only final should be returned
        result = self.state.filtered_finals(60, 0, 0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].segment, 2)
        self.assertTrue(result[0].final)

    def test_filtered_finals_ordering(self):
        """Filtered results should be in insertion order (oldest-to-newest by insertion)."""
        # Use sequential segment numbers so segment order = insertion order = time order
        self.state.accept(update(segment=1, revision=1, final=True), now=40)
        self.state.accept(update(segment=2, revision=1, final=True), now=50)
        self.state.accept(update(segment=3, revision=1, final=True), now=60)
        self.state.accept(update(segment=4, revision=1, final=True), now=70)

        # With no resume, should be in insertion order: 1, 2, 3, 4
        result = self.state.filtered_finals(60, 0, 0)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0].segment, 1)  # First inserted
        self.assertEqual(result[1].segment, 2)
        self.assertEqual(result[2].segment, 3)
        self.assertEqual(result[3].segment, 4)  # Last inserted

    def test_filtered_finals_excludes_resume_segment_with_equal_revision(self):
        """Client with segment 5, revision 3 should not receive segment 5, revision 3."""
        self.state.accept(update(segment=5, revision=3, final=True), now=50)
        self.state.accept(update(segment=6, revision=1, final=True), now=60)

        result = self.state.filtered_finals(60, 5, 3)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].segment, 6)

    def test_filtered_finals_includes_resume_segment_with_newer_revision(self):
        """Client with segment 5, revision 3 should receive segment 5, revision 5."""
        # revision 3 of segment 5 is NOT final, so revision 5 is accepted
        self.state.accept(update(segment=5, revision=3, final=False), now=40)
        self.state.accept(update(segment=5, revision=5, final=True), now=50)
        self.state.accept(update(segment=6, revision=1, final=True), now=60)

        result = self.state.filtered_finals(60, 5, 3)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].segment, 5)
        self.assertEqual(result[0].revision, 5)
        self.assertEqual(result[1].segment, 6)


class RecoveryIntegrationTests(unittest.TestCase):
    """Integration tests simulating recovery scenarios."""

    def test_late_join_gets_recent_finals(self):
        """A new client (no resume state) should get all recent finals."""
        state = SessionState("L1", clock=lambda: 100, history_retention_seconds=60)
        state.accept(update(segment=1, revision=1, final=True), now=40)
        state.accept(update(segment=2, revision=1, final=True), now=50)
        state.accept(update(segment=3, revision=1, final=True), now=60)

        # New client with last_segment=0, last_revision=0
        result = state.filtered_finals(60, 0, 0)
        self.assertEqual(len(result), 3)

    def test_reconnect_gets_missed_finals(self):
        """A reconnecting client should get only missed finals."""
        state = SessionState("L1", clock=lambda: 100, history_retention_seconds=60)
        state.accept(update(segment=1, revision=1, final=True), now=40)
        state.accept(update(segment=2, revision=1, final=True), now=50)
        state.accept(update(segment=3, revision=1, final=True), now=60)

        # Client reconnects having received up to segment 2
        result = state.filtered_finals(60, 2, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].segment, 3)

    def test_reconnect_with_gap_gets_only_missing(self):
        """Client should get only what it missed, even with gaps."""
        state = SessionState("L1", clock=lambda: 100, history_retention_seconds=60)
        for i in range(1, 11):
            state.accept(update(segment=i, revision=1, final=True), now=20 + i * 3)

        # Client reconnects having received up to segment 5
        # Time cutoff is 40 (100-60), so segments 1-6 are excluded by time (t=23-38)
        # Segments 7-10 (t=41-50) are within time window
        # Resume state (5,1) excludes segments 1-5
        # Result: segments 7-10, which is 4 items
        result = state.filtered_finals(60, 5, 1)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0].segment, 7)
        self.assertEqual(result[-1].segment, 10)

    def test_empty_history_when_client_up_to_date(self):
        """Client that has everything should get empty history."""
        state = SessionState("L1", clock=lambda: 100, history_retention_seconds=60)
        state.accept(update(segment=1, revision=1, final=True), now=40)
        state.accept(update(segment=2, revision=1, final=True), now=50)

        # Client has both segments
        result = state.filtered_finals(60, 2, 1)
        self.assertEqual(result, ())

    def test_zero_history_seconds_returns_empty(self):
        """Requesting 0 seconds history should return empty."""
        state = SessionState("L1", clock=lambda: 100)
        state.accept(update(segment=1, revision=1, final=True), now=40)

        result = state.filtered_finals(0, 0, 0)
        self.assertEqual(result, ())


if __name__ == "__main__":
    unittest.main()
