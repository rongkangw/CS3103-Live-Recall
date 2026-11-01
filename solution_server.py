#!/usr/bin/env python3
"""CS3103 Assignment 4 - LiveRecall server implementation.

The server receives caption updates from the instructor's caption source,
maintains the latest state for each segment, and broadcasts to student clients.

Key features:
- Validates protocol messages from source and clients
- Tracks authoritative state (provisional + finalized captions)
- Filters recovery history based on client resume state
- Broadcasts captions to connected clients

Required evaluator command:
    python solution_server.py --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from protocol import (
    CaptionUpdate,
    ClientHello,
    ProtocolError,
    RevisionDecision,
    SourceHello,
    parse_caption_update,
    parse_hello,
)
from server_state import ClientState, SessionState

# Track which session each client subsribed to. Also stores state of client
CLIENTS: dict[Any, ClientState] = {}

SESSION_STATES: dict[str, SessionState] = {}

# After 4 messages, wait until the client confirms it has read them
SEND_WINDOW = 4


def get_session_state(session: str) -> SessionState:
    """Get or create state for a session (e.g., 'L1' for lecture 1)."""
    state = SESSION_STATES.get(session)
    if state is None:
        state = SessionState(session)
        SESSION_STATES[session] = state
    return state


def broadcast(update: CaptionUpdate, session: str) -> None:
    """Only enqueues one update into every client's buffer in a session.

    Does not sent nor wait for client.
    """
    if not CLIENTS:
        return

    raw_message = json.dumps(update.to_message())
    for client_state in CLIENTS.values():
        if client_state.session == session:
            client_state.enqueue(update, raw_message)


async def handle_source(ws, hello: SourceHello) -> None:
    """Reads CAPTION_UPDATE messages, validates, updates state, and broadcasts. Ignores any malformed messages.
    """
    session = hello.session
    state = get_session_state(session)
    print(f"source connected: session={session}", flush=True)

    try:
        async for raw in ws:
            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                continue

            if not isinstance(message, dict):
                continue
            msg_type = message.get("type")
            if msg_type == "CAPTION_UPDATE":
                try:
                    update = parse_caption_update(raw)
                except ProtocolError:
                    continue
                if update.session != session:
                    continue
                decision = state.accept(update)
                if decision is not RevisionDecision.ACCEPT:
                    continue
                broadcast(update, session)
            elif msg_type == "SOURCE_DONE":
                print("source finished", flush=True)
            else:
                continue
    finally:
        print("source disconnected", flush=True)

async def client_sender(client: ClientState) -> None:
    """Independent sender that waits until this client has a pending caption.

    Every SEND_WINDOW messages it pings and waits for the pong, which only comes
    back after the client has read everything sent so far. While it waits, newer
    provisionals overwrite older ones in the buffer, so coalescing happens only
    when the client actually lags at its side.
    """
    ws = client.websocket
    # number of messages sent since the previous pong ie client's ack
    unconfirmed = 0
    try:
        while True:
            await client.wakeup.wait()

            if client.overflowed:
                await ws.close(code=1013, reason="client too slow; reconnect to resume")
                return
            
            raw = client.next_pending()

            if raw is None:
                client.wakeup.clear()
                continue

            
            await ws.send(raw)
            unconfirmed += 1

            if unconfirmed >= SEND_WINDOW:
                #pending response to the ping
                pong = asyncio.ensure_future(
                    await ws.ping()
                    )

                while not pong.done() and not client.overflowed:
                    await asyncio.wait({pong}, timeout=0.1)
                if pong.done():
                    pong.result()

                unconfirmed = 0
    except ConnectionClosed:
        return


async def handle_client(ws, hello: ClientHello) -> None:
    """Sends filtered recovery history based on client's resume state, then registers client for live updates.
    """
    client_id = hello.client_id
    session = hello.session
    state = get_session_state(session)

    client_state = ClientState(
        client_id,
        session,
        ws,
    )

    # Get only captions the client hasn't seen yet
    history_items = state.filtered_finals(
        hello.history_seconds,
        hello.last_segment,
        hello.last_revision,
    )

    for update in state.active_snapshot():
        client_state.enqueue(update, json.dumps(update.to_message()))

    CLIENTS[ws] = client_state

    try:
        await ws.send(
            json.dumps({
                "type": "HISTORY",
                "items": [u.to_message() for u in history_items]
                })
        )
        client_state.sender_task = asyncio.create_task(
            client_sender(client_state)
            )
        print(f"client connected: id={client_id}; clients={len(CLIENTS)}", flush=True)

        # Keep the connection open until the client disconnects
        async for _ in ws:
            pass
    finally:
        CLIENTS.pop(ws, None)

        if client_state.sender_task is not None:
            client_state.sender_task.cancel()
            try:
                await client_state.sender_task
            except asyncio.CancelledError:
                pass

        print(
            f"client disconnected: id={client_id}; "
            f"clients={len(CLIENTS)}",
            flush=True
        )


async def handler(ws) -> None:
    """Parse hello and route to source or client handler."""
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        hello = parse_hello(raw)
    except (asyncio.TimeoutError, ProtocolError):
        await ws.close(code=1008, reason="Expected a valid hello message")
        return

    if isinstance(hello, SourceHello):
        await handle_source(ws, hello)
    elif isinstance(hello, ClientHello):
        await handle_client(ws, hello)


async def main_async(port: int) -> None:
    async with websockets.serve(
        handler,
        "0.0.0.0",
        port,
        max_size=2**20,
        ping_interval=20,
        ping_timeout=20,
    ):
        print(f"LiveRecall server listening on ws://0.0.0.0:{port}", flush=True)
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
