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

from protocol import (
    ClientHello,
    ProtocolError,
    RevisionDecision,
    SourceHello,
    parse_caption_update,
    parse_hello,
)
from server_state import SessionState

# Track which session each client subscribed to
CLIENTS: dict[Any, str] = {}

SESSION_STATES: dict[str, SessionState] = {}


def get_session_state(session: str) -> SessionState:
    """Get or create state for a session (e.g., 'L1' for lecture 1)."""
    state = SESSION_STATES.get(session)
    if state is None:
        state = SessionState(session)
        SESSION_STATES[session] = state
    return state


async def broadcast(raw_message: str, session: str) -> None:
    """Send one message directly to every client in a session.

    The source handler currently waits for all sends to complete. 
    TODO: Per-client queues and backpressure have not been implemented yet.
    """
    if not CLIENTS:
        return

    # Filter clients to only those in the target session
    snapshot = [
        client
        for client, client_session in CLIENTS.items()
        if client_session == session
    ]
    results = await asyncio.gather(
        *(client.send(raw_message) for client in snapshot),
        return_exceptions=True,
    )
    for client, result in zip(snapshot, results):
        if isinstance(result, Exception):
            CLIENTS.pop(client, None)


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
                await broadcast(
                    json.dumps(update.to_message()),
                    session,
                )
            elif msg_type == "SOURCE_DONE":
                print("source finished", flush=True)
            else:
                continue
    finally:
        print("source disconnected", flush=True)


async def handle_client(ws, hello: ClientHello) -> None:
    """Sends filtered recovery history based on client's resume state, then registers client for live updates.
    """
    client_id = hello.client_id
    session = hello.session
    state = get_session_state(session)

    # Get only captions the client hasn't seen yet
    history_items = state.filtered_finals(
        hello.history_seconds,
        hello.last_segment,
        hello.last_revision,
    )

    # Send filtered history BEFORE registering for live updates
    # This prevents race where an update falls between history and live
    await ws.send(
        json.dumps({"type": "HISTORY", "items": [u.to_message() for u in history_items]})
    )

    CLIENTS[ws] = session
    print(f"client connected: id={client_id}; clients={len(CLIENTS)}", flush=True)
    try:
        # Keep the connection open until the client disconnects
        async for _ in ws:
            pass
    finally:
        CLIENTS.pop(ws, None)
        print(f"client disconnected: id={client_id}; clients={len(CLIENTS)}", flush=True)


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
