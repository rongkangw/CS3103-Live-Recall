# Team 19 - LiveRecall

LiveRecall is a classroom caption companion for CS3103 Assignment 4. A caption
source sends provisional and final revisions to a WebSocket server. The server
validates and distributes them independently to many clients, while the browser
replaces revisions in place and provides local 15/30/60-second recall.

## Team members

- Rong Kang / A0306765X / Protocol, session state, bounded history, and recovery
- Jingya / A0302905M / Per-client delivery, concurrency, and overload handling
- Kenneth / A0360662E / Browser caption state, local recall, and user interface
- Shubh / A0360403U / Browser networking, reconnect, and client/server integration

## Ubuntu setup

From a fresh Ubuntu environment, install the mandatory system packages:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip iproute2
```

Node.js is needed only to run the browser unit tests:

```bash
sudo apt install -y nodejs npm
```

Run all remaining commands from the repository root.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Required server

Activate the environment and start the submitted WebSocket server:

```bash
source .venv/bin/activate
python solution_server.py --port 8765
```

The server listens on `ws://0.0.0.0:8765`. Stop it with `Ctrl+C`.

## Browser client

Keep the required server running. In a second terminal, serve the browser files:

```bash
source .venv/bin/activate
python -m http.server 8000 --bind 0.0.0.0 --directory client
```

If Ubuntu and the browser are on the same machine, open <http://127.0.0.1:8000>.
The status indicator should change from `connecting` to `connected`.

## Public deterministic tests

First run the unit and integration tests:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
node --test tests/test_caption_store.cjs
```

T1 - basic correctness:

```bash
python run_demo.py --clients 20 --duration 20 --runs 3 --output-stem t1
```

T2 - 100-client scale:

```bash
python run_demo.py --clients 100 --duration 40 --runs 3 --output-stem t2
```

T3 - 10% slow readers:

```bash
python run_demo.py --clients 100 --duration 60 --runs 3 \
  --slow-percent 10 --slow-ms 500 --output-stem t3
```

T4 - disconnect and recovery:

```bash
python run_demo.py --clients 100 --duration 60 --runs 3 \
  --disconnect-percent 20 --disconnect-at 15 --disconnect-seconds 5 \
  --output-stem t4
```

T5 - delay and packet loss on Ubuntu:

```bash
sudo ./network_profiles/delay_loss.sh lo
python run_demo.py --clients 100 --duration 40 --runs 3 --output-stem t5
sudo ./network_profiles/reset.sh lo
```

## Demo

From the repository root, use three terminals.

Terminal 1 - WebSocket server:

```bash
source .venv/bin/activate
python solution_server.py --port 8765
```

Terminal 2 - browser files:

```bash
source .venv/bin/activate
python -m http.server 8000 --bind 0.0.0.0 --directory client
```

Open the browser URL described above in two tabs or windows.

Terminal 3 - deterministic source:

```bash
source .venv/bin/activate
python caption_source.py --duration 120
```

Demonstrate the required behavior in this order:

1. Confirm that both open clients receive the same live caption stream.
2. Point out provisional and final captions and show that a newer revision
   replaces the same segment instead of creating a duplicate row.
3. Select **What did I miss?** and demonstrate the local 15, 30, and 60-second
   recall windows. The default is 30 seconds.
4. While the source continues, use one tab's browser developer tools to set its
   network state to **Offline** for several seconds. Restore **Online** and show
   `reconnecting` changing back to `connected`, followed by recovery of missed
   recent final captions without duplicates.
5. Open a new third tab while the source is still running. Show that it first
   receives recent finalized context through `HISTORY` and then continues with
   live `CAPTION_UPDATE` messages.

The browser retries after 1, 2, 4, and 8 seconds, then at intervals capped at
10 seconds. It requests up to 60 seconds of finalized history after every new or
re-established connection. `docs/DEMO_GUIDE.md` contains the shorter checklist.

## Design notes

Architecture:

```text
caption_source.py
        |
        v
solution_server.py -> SessionState -> one ClientState/sender per browser
        |
        +--> load_clients.py / run_demo.py
        +--> ConnectionManager -> CaptionStore -> CaptionUI
```

- **Revision and deduplication state:** `protocol.py` validates every message.
  `SessionState` keeps the latest accepted revision for each segment. Lower
  revisions, repeated revisions, and attempts to revise an already final
  segment are ignored. The browser applies the same revision/final checks so
  live and recovered data merge idempotently.
- **Bounded server history:** finalized captions are retained for 90 seconds,
  with an additional fixed cap of 4096 captions. A client may request at most
  the most recent 60 seconds.
- **Reconnect and late-join recovery:** a browser sends `CLIENT_HELLO` with its
  latest finalized segment/revision cursor and a 60-second history request. The
  server registers the client, sends one filtered `HISTORY` response before
  enabling live delivery, and then uses `CAPTION_UPDATE` for normal traffic.
- **Per-client buffering:** every client has its own sender and pending state,
  so one slow reader cannot block the source or other readers. Normal mode
  preserves every accepted revision in order.
- **Overload behavior:** if a send takes over 50 ms, a pong takes over 500 ms,
  or 256 normal updates become pending, only that client enters slow mode. Slow
  mode preserves every pending final and only the newest provisional revision
  per unfinished segment. Pending finals and provisional segments each have a
  fixed limit of 256. A client that exceeds a limit is closed with WebSocket
  code 1013 and can recover retained finals after reconnecting.
- **Return to normal:** after at least two seconds of final-priority delivery,
  the pending slow-mode state falls to four items or fewer, and three transport
  checks succeed, the client returns to normal mode.
- **Trade-off:** slow clients may miss obsolete provisional revisions, but
  final captions and the newest useful state are prioritised without an
  unbounded queue. Clients disconnected beyond the retained recovery window
  are not guaranteed older captions.

## Optional ASR bonus

The optional ASR bonus was not attempted.
