# AI / Coding-Assistant Usage

## Tools used

We used OpenAI Codex mainly for debugging, writing tests, and some agentic coding. This included inspecting related files, proposing and applying implementation changes, running checks, and refining changes in response to failures. AI assistance covered parts of the implementation as well as explanations and test suggestions.

The following breakdown is reconstructed from the team's responsibilities and the submitted implementation. The examples below describe relevant technical suggestions rather than reproduce a saved AI conversation.

| Member | Scope of AI assistance |
|---|---|
| Rong Kang | Debugging protocol validation, revision handling, bounded session history, and recovery filtering; assistance implementing changes and writing protocol, server-state, and recovery tests. |
| Jingya | Debugging asynchronous per-client delivery, slow-client isolation, and overload handling; assistance implementing bounded buffering and writing client-state and live-delivery tests. |
| Kenneth | Debugging caption replacement, duplicate handling, recall windows, and browser state; assistance implementing parts of the caption store and UI and writing browser store tests. |
| Shubh | Debugging WebSocket connection handling, reconnect timing, and recovery requests; assistance implementing parts of the connection lifecycle and client/server integration and preparing integration checks. |

## One useful suggestion

A useful approach was to make caption updates and recovered history merge idempotently: identify a caption by session and segment, accept only a newer revision, and prevent further changes once a segment is final. This lets overlapping history and live delivery update the same caption without creating duplicate rows or reverting finalized text.

The submitted tests check newer, repeated, stale, and post-final revisions in `tests/test_protocol.py` and `tests/test_server_state.py`. Browser tests in `tests/test_caption_store.cjs` check revision replacement, overlapping history, and preservation of the finalized resume cursor.

## One suggestion that was wrong, inefficient, or required modification

An oversimplified buffering approach would be to keep only the newest pending caption update for a slow client. Although this bounds memory, updates for different segments can overwrite one another, losing final captions that the client has not received. This is a reconstructed example of an approach requiring correction, rather than a verified historical AI output.

The submitted implementation instead uses a separate bounded pending state for each client. In slow mode, it retains pending finals in order and coalesces provisional updates to the newest revision per unfinished segment. If a client exceeds the configured limits, it is disconnected and can recover retained finals after reconnecting. `tests/test_client_state.py` checks provisional coalescing, final preservation and priority, buffer bounds, and overflow handling.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
