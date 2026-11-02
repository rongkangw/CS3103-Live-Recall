"""Tests for the per-client send buffer (requirements 10-11)."""

import unittest

from protocol import CaptionUpdate
from server_state import ClientState
from unittest import mock

import server_state

def update(segment=1, revision=1, *, final=False):
    return CaptionUpdate(
        session="L1",
        segment=segment,
        revision=revision,
        source_ts=1760000000.0 + segment + revision / 10,
        final=final,
        text=f"segment {segment} revision {revision}",
    )


def push(client, segment, revision, final=False):
    # Use a short readable string as the "raw" message so assertions are easy to read
    raw = f"{segment}.{revision}{'F' if final else ''}"
    client.enqueue(update(segment, revision, final=final), raw)


def drain(client):
    sent = []
    while (raw := client.next_pending()) is not None:
        sent.append(raw)
    return sent


class ClientStateTests(unittest.TestCase):
    def setUp(self):
        self.client = ClientState(1, "L1", None)

    def test_provisionals_coalesce_to_newest_revision(self):
        for revision in (1, 2, 3):
            push(self.client, 1, revision)
        self.assertEqual(drain(self.client), ["1.3"])

    def test_final_replaces_pending_provisional(self):
        push(self.client, 1, 1)
        push(self.client, 1, 2, final=True)
        self.assertEqual(drain(self.client), ["1.2F"])

    def test_every_final_is_kept_in_order(self):
        for segment in range(1, 11):
            push(self.client, segment, 1)
            push(self.client, segment, 2, final=True)
        self.assertEqual(drain(self.client), [f"{s}.2F" for s in range(1, 11)])

    def test_finals_are_sent_before_provisionals(self):
        push(self.client, 1, 3, final=True)
        push(self.client, 2, 1)
        push(self.client, 2, 2)
        self.assertEqual(drain(self.client), ["1.3F", "2.2"])

    def test_provisional_buffer_stays_bounded(self):
        for revision in range(1, 1001):
            push(self.client, 1, revision)
        self.assertEqual(len(self.client.pending_provisional), 1)

    def test_enqueue_wakes_the_sender(self):
        self.assertFalse(self.client.wakeup.is_set())
        push(self.client, 1, 1)
        self.assertTrue(self.client.wakeup.is_set())

    def test_empty_buffer_returns_none(self):
        self.assertIsNone(self.client.next_pending())

    def test_finals_backlog_over_cap_marks_overflow_and_frees_memory(self):
        with mock.patch.object(server_state, "DEFAULT_MAX_PENDING_FINALS", 5):
            for segment in range(1, 7):
                push(self.client, segment, 1, final=True)
        self.assertTrue(self.client.overflowed)
        self.assertEqual(drain(self.client), [])

    def test_enqueue_is_ignored_after_overflow(self):
        self.client.overflowed = True
        push(self.client, 1, 1, final=True)
        self.assertEqual(drain(self.client), [])

    def test_provisional_allowed_when_not_lagging(self):
        self.assertTrue(self.client.may_send_provisional(now=100.0))

    def test_lag_mode_holds_provisionals_until_idle(self):
        self.client.lag_until = 115.0
        self.client.last_send_at = 100.0
        self.assertFalse(self.client.may_send_provisional(now=101.0))
        self.assertTrue(self.client.may_send_provisional(now=102.0))

    def test_lag_mode_ends_after_hold(self):
        self.client.lag_until = 115.0
        self.client.last_send_at = 114.9
        self.assertTrue(self.client.may_send_provisional(now=115.0))

    def test_held_back_provisional_does_not_block_finals(self):
        push(self.client, 1, 3, final=True)
        push(self.client, 2, 1)
        self.assertEqual(self.client.next_pending(allow_provisional=False), "1.3F")
        self.assertIsNone(self.client.next_pending(allow_provisional=False))
        self.assertTrue(self.client.has_pending)
        self.assertEqual(self.client.next_pending(), "2.1")


if __name__ == "__main__":
    unittest.main()