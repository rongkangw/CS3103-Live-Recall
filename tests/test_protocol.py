import json
import math
import unittest

from protocol import (
    CaptionUpdate,
    ClientHello,
    ProtocolError,
    RevisionDecision,
    SourceHello,
    compare_revision,
    parse_caption_update,
    parse_hello,
    select_revision,
)


def update(revision=1, *, final=False, segment=1, text="caption"):
    return CaptionUpdate(
        session="L1",
        segment=segment,
        revision=revision,
        source_ts=1760000000.0 + revision,
        final=final,
        text=text,
    )


class HelloParsingTests(unittest.TestCase):
    def test_source_hello_ignores_unknown_fields(self):
        hello = parse_hello(json.dumps({
            "type": "SOURCE_HELLO",
            "session": "L1",
            "test_phase": "burst",
        }))
        self.assertEqual(hello, SourceHello(session="L1"))

    def test_client_hello_is_normalized(self):
        hello = parse_hello(json.dumps({
            "type": "CLIENT_HELLO",
            "client_id": 17,
            "session": "L1",
            "last_segment": 38,
            "last_revision": 3,
            "history_seconds": 30,
            "extra": "ignored",
        }))
        self.assertEqual(hello, ClientHello(17, "L1", 38, 3, 30))

    def test_non_object_json_is_rejected(self):
        with self.assertRaises(ProtocolError):
            parse_hello("[]")

    def test_unknown_hello_type_is_rejected(self):
        with self.assertRaises(ProtocolError):
            parse_hello('{"type":"OTHER"}')

    def test_boolean_is_not_accepted_as_integer(self):
        with self.assertRaises(ProtocolError):
            parse_hello(json.dumps({
                "type": "CLIENT_HELLO",
                "client_id": 17,
                "session": "L1",
                "last_segment": True,
                "last_revision": 0,
                "history_seconds": 30,
            }))

    def test_history_window_must_be_at_most_sixty_seconds(self):
        with self.assertRaises(ProtocolError):
            parse_hello(json.dumps({
                "type": "CLIENT_HELLO",
                "client_id": 17,
                "session": "L1",
                "last_segment": 0,
                "last_revision": 0,
                "history_seconds": 61,
            }))


class CaptionParsingTests(unittest.TestCase):
    def test_caption_is_normalized_and_unknown_fields_are_ignored(self):
        parsed = parse_caption_update(json.dumps({
            "type": "CAPTION_UPDATE",
            "session": "L1",
            "segment": 42,
            "revision": 3,
            "source_ts": 1760000000,
            "final": True,
            "text": "TCP uses cumulative acknowledgements.",
            "test_phase": "normal",
        }))
        self.assertEqual(parsed.segment, 42)
        self.assertEqual(parsed.source_ts, 1760000000.0)
        self.assertNotIn("test_phase", parsed.to_message())

    def test_required_positive_integers_are_enforced(self):
        message = {
            "type": "CAPTION_UPDATE",
            "session": "L1",
            "segment": 0,
            "revision": 1,
            "source_ts": 1760000000.0,
            "final": False,
            "text": "caption",
        }
        with self.assertRaises(ProtocolError):
            parse_caption_update(json.dumps(message))

    def test_final_must_be_a_json_boolean(self):
        message = {
            "type": "CAPTION_UPDATE",
            "session": "L1",
            "segment": 1,
            "revision": 1,
            "source_ts": 1760000000.0,
            "final": 1,
            "text": "caption",
        }
        with self.assertRaises(ProtocolError):
            parse_caption_update(json.dumps(message))

    def test_non_finite_source_timestamp_is_rejected(self):
        message = {
            "type": "CAPTION_UPDATE",
            "session": "L1",
            "segment": 1,
            "revision": 1,
            "source_ts": math.inf,
            "final": False,
            "text": "caption",
        }
        with self.assertRaises(ProtocolError):
            parse_caption_update(json.dumps(message))


class RevisionRuleTests(unittest.TestCase):
    def test_first_revision_is_accepted(self):
        self.assertIs(compare_revision(None, update()), RevisionDecision.ACCEPT)

    def test_newer_revision_is_accepted(self):
        self.assertIs(
            compare_revision(update(1), update(2)),
            RevisionDecision.ACCEPT,
        )

    def test_duplicate_does_not_replace_current_state(self):
        current = update(2, text="accepted")
        selected, decision = select_revision(current, update(2, text="different"))
        self.assertIs(selected, current)
        self.assertIs(decision, RevisionDecision.DUPLICATE)

    def test_stale_revision_does_not_replace_current_state(self):
        current = update(3)
        selected, decision = select_revision(current, update(2))
        self.assertIs(selected, current)
        self.assertIs(decision, RevisionDecision.STALE)

    def test_final_state_rejects_even_a_newer_revision(self):
        current = update(3, final=True)
        selected, decision = select_revision(current, update(4, final=False))
        self.assertIs(selected, current)
        self.assertIs(decision, RevisionDecision.FINAL_LOCKED)

    def test_different_segments_cannot_be_compared(self):
        with self.assertRaises(ProtocolError):
            compare_revision(update(segment=1), update(segment=2))


if __name__ == "__main__":
    unittest.main()
