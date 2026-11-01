"""End-to-end tests: real server, real WebSocket clients (requirements 9-10)."""

import asyncio
import json
import unittest
import solution_server
import server_state
try:
    import websockets
except ImportError:
    websockets = None
from unittest import mock


TIMEOUT = 10.0


def caption(segment, revision, final=False):
    return json.dumps({
        "type": "CAPTION_UPDATE",
        "session": "L1",
        "segment": segment,
        "revision": revision,
        "source_ts": 1760000000.0 + segment + revision / 10,
        "final": final,
        "text": f"segment {segment} revision {revision}",
    })


@unittest.skipIf(websockets is None, "websockets is not installed")
class LiveDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # IsolatedAsyncioTestCase turns on asyncio debug mode, which slows the loop a lot
        asyncio.get_running_loop().set_debug(False)
        solution_server.CLIENTS.clear()
        solution_server.SESSION_STATES.clear()
        self.server = await websockets.serve(solution_server.handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.uri = f"ws://127.0.0.1:{port}"
        self.connections = []

    async def asyncTearDown(self):
        await asyncio.gather(*(ws.close() for ws in self.connections))
        self.server.close()
        await self.server.wait_closed()

    async def connect(self, **kwargs):
        ws = await websockets.connect(self.uri, close_timeout=0.5, **kwargs)
        self.connections.append(ws)
        return ws

    async def source(self):
        ws = await self.connect()
        await ws.send(json.dumps({"type": "SOURCE_HELLO", "session": "L1"}))
        return ws

    async def client(self, client_id, **kwargs):
        ws = await self.connect(**kwargs)
        await ws.send(json.dumps({
            "type": "CLIENT_HELLO",
            "client_id": client_id,
            "session": "L1",
            "last_segment": 0,
            "last_revision": 0,
            "history_seconds": 60,
        }))
        history = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
        self.assertEqual(history["type"], "HISTORY")
        return ws

    async def wait_registered(self, count):
        while len(solution_server.CLIENTS) < count:
            await asyncio.sleep(0.01)

    async def send_lecture(self, source, segments, revisions, gap=0.005):
        for segment in range(1, segments + 1):
            for revision in range(1, revisions + 1):
                await source.send(caption(segment, revision, revision == revisions))
                await asyncio.sleep(gap)

    async def read_until_final(self, ws, last_segment):
        """Read (segment, revision, final) tuples until last_segment's final arrives."""
        received = []
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
            item = (msg["segment"], msg["revision"], msg["final"])
            received.append(item)
            if item[0] == last_segment and item[2]:
                return received

    async def read_until(self, ws, done):
        """Read (segment, revision, final) tuples until done(item) is true."""
        received = []
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
            item = (msg["segment"], msg["revision"], msg["final"])
            received.append(item)
            if done(item):
                return received

    async def test_normal_client_receives_every_revision_in_order(self):
        source = await self.source()
        client = await self.client(1)
        await self.wait_registered(1)

        # Read while the source sends, so this client never falls behind
        sending = asyncio.create_task(self.send_lecture(source, segments=10, revisions=3))
        received = await self.read_until_final(client, last_segment=10)
        await sending

        expected = [(s, r, r == 3) for s in range(1, 11) for r in range(1, 4)]
        self.assertEqual(received, expected)

    async def test_many_clients_all_receive_every_revision(self):
        source = await self.source()
        clients = [await self.client(i) for i in range(50)]
        await self.wait_registered(50)

        sending = asyncio.create_task(self.send_lecture(source, segments=5, revisions=3))
        results = await asyncio.gather(*(
            self.read_until_final(ws, last_segment=5) for ws in clients
        ))
        await sending

        expected = [(s, r, r == 3) for s in range(1, 6) for r in range(1, 4)]
        for received in results:
            self.assertEqual(received, expected)

    async def test_stalled_client_does_not_delay_normal_client(self):
        source = await self.source()
        normal = await self.client(1)
        # max_queue=1 and never reading: this client stalls almost immediately
        await self.client(2, max_queue=1)
        await self.wait_registered(2)

        sending = asyncio.create_task(self.send_lecture(source, segments=50, revisions=3, gap=0.002))
        received = await self.read_until_final(normal, last_segment=50)
        await sending

        self.assertEqual(len(received), 150)

    async def test_lagging_client_gets_every_final_and_skips_obsolete_revisions(self):
        source = await self.source()
        # max_queue=1 and not reading yet: this client stalls almost immediately
        stalled = await self.client(1, max_queue=1)
        await self.wait_registered(1)

        await self.send_lecture(source, segments=50, revisions=3, gap=0)
        await asyncio.sleep(0.2)
        received = await self.read_until(stalled, lambda item: item == (50, 3, True))

        finals = [s for s, _, final in received if final]
        self.assertEqual(finals, list(range(1, 51)), "every final exactly once, in order")
        highest = {}
        for segment, revision, _ in received:
            self.assertGreater(revision, highest.get(segment, 0), "stale or duplicate revision")
            highest[segment] = revision
        self.assertLess(len(received), 150, "obsolete provisionals should be coalesced")

    async def test_lagging_client_gets_newest_revision_of_active_segment(self):
        source = await self.source()
        stalled = await self.client(1, max_queue=1)
        await self.wait_registered(1)

        for revision in range(1, 41):
            await source.send(caption(1, revision))
        await asyncio.sleep(0.2)
        received = await self.read_until(stalled, lambda item: item == (1, 40, False))

        self.assertEqual(received[-1], (1, 40, False))
        self.assertLess(len(received), 40, "obsolete provisionals should be coalesced")

    async def test_hopelessly_behind_client_is_closed_and_recovers_via_history(self):
        source = await self.source()
        with mock.patch.object(server_state, "DEFAULT_MAX_PENDING_FINALS", 5):
            stalled = await self.client(1, max_queue=1)
            await self.wait_registered(1)
            for segment in range(1, 41):
                await source.send(caption(segment, 1, final=True))
            await asyncio.sleep(0.3)

            last_final = 0
            with self.assertRaises(websockets.exceptions.ConnectionClosed) as ctx:
                while True:
                    msg = json.loads(await asyncio.wait_for(stalled.recv(), TIMEOUT))
                    last_final = msg["segment"]
            self.assertEqual(ctx.exception.rcvd.code, 1013)

        # Reconnect with the resume state: HISTORY fills exactly the gap
        resumed = await self.connect()
        await resumed.send(json.dumps({
            "type": "CLIENT_HELLO",
            "client_id": 1,
            "session": "L1",
            "last_segment": last_final,
            "last_revision": 1,
            "history_seconds": 60,
        }))
        history = json.loads(await asyncio.wait_for(resumed.recv(), TIMEOUT))
        self.assertEqual([h["segment"] for h in history["items"]], list(range(last_final + 1, 41)))


if __name__ == "__main__":
    unittest.main()