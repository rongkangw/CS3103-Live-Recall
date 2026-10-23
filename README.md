# LiveRecall

A classroom caption companion that streams live captions and lets students revisit recent context. Built for a networking and distributed applications assignment.

## Environment

Ubuntu 24.04 LTS (native or Multipass) for development and performance experiments.

To create a Multipass VM, run on your host:

```bash
multipass launch 24.04 --name liverecall --cpus 2 --memory 4G --disk 15G
multipass shell liverecall
```

## Setup

Inside Ubuntu, from the repository root:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip iproute2
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Activate the virtual environment again whenever you open a new terminal:

```bash
source .venv/bin/activate
```

## Run the server

```bash
python solution_server.py --port 8765
```

Local WebSocket address: `ws://127.0.0.1:8765`.

See `START_HERE.md` for caption source and browser client instructions.

## Run a public test

Stop any manually started server first. The test runner starts its own server.

```bash
python run_demo.py --clients 20 --duration 20 --runs 3
```

Keep the generated results for the baseline comparison. See `PUBLIC_TESTS.md` for the full test suite.
