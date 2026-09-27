# SJSU Parking Monitor

A small cross-platform CLI that checks live SJSU garage occupancy, sends the result through a RocketRide pipeline, and prints a concise status.

## How it works

```text
SJSU Parking Status -> Python HTTP client -> Garage parser -> RocketRide -> Console status
                                                               |
                                                   Optional desktop notification
```

The monitor uses Python's standard-library HTTP client, so it does not require `curl.exe` or another operating-system executable. Terminal output is formatted with Rich, which works across Windows, macOS, and Linux. Desktop notifications are automatic on Windows when `winotify` is installed; on other systems the console output remains the portable default.

## Prerequisites

- Python 3.10 or newer
- A running RocketRide engine, normally at `ws://localhost:5565`
- An API key for the LLM configured in `sjsu-parking.pipe`

The local RocketRide development handshake is used automatically for localhost. A RocketRide engine key is not required in `.env`.

## Why RocketRide is included

Parking retrieval and parsing remain deterministic Python application code. RocketRide sits around that workflow as an inspectable AI harness: the application sends the live, parsed status into a small pipeline, and the pipeline owns the prompt, model profile, provider, and response formatting.

To change providers or models, edit `sjsu-parking.pipe` rather than rewriting the garage fetcher or parser. The current pipeline uses Gemini as a simple example, but the Python integration stays provider-neutral through `ROCKETRIDE_LLM_API_KEY`. This keeps the project useful as the workflow grows beyond a one-line status response without claiming that AI is required to read the parking page.

## Install

```bash
git clone https://github.com/tonytrieu-dev/sjsu-parking-monitor.git
cd sjsu-parking-monitor
python -m venv .venv
```

Activate the environment using the shell for your operating system, then install the package:

```bash
python -m pip install .
```

For development and tests:

```bash
python -m pip install -r requirements-dev.txt
```

On Windows, `winotify` is installed automatically by the platform marker in `pyproject.toml`. On macOS and Linux, the monitor runs without desktop notifications unless a future notifier adapter is added.

## Configuration

Copy `.env.example` to `.env` and set:

```env
ROCKETRIDE_LLM_API_KEY=your_llm_api_key
SJSU_GARAGE=North Garage
```

Supported garages currently include North Garage, South Garage, West Garage, and South Campus Garage. The selected garage is required; there is no implicit default.

## CLI

Run using the installed command:

```bash
parking-monitor
```

Example output:

```text
+- SJSU Parking Status -------+
|                             |
|  North Garage               |
|  8% full                    |
|                             |
+-----------------------------+
```

Command-line options override corresponding environment variables:

```bash
parking-monitor --garage "West Garage" --notify none
parking-monitor --uri ws://localhost:5565
```

Available options:

- `--garage NAME`: overrides `SJSU_GARAGE`.
- `--notify {auto,desktop,none}`: overrides `SJSU_NOTIFY`. `auto` is the default.
- `--uri URI`: overrides `ROCKETRIDE_URI`.

The original entry point remains supported:

```bash
python check_parking.py
```

Use `--notify none` when running in a log-only or headless environment.

## Scheduling

Use the scheduler native to your operating system:

- Windows: Task Scheduler, running `parking-monitor` or the virtual environment's Python executable.
- macOS: `launchd`.
- Linux: `cron` or a `systemd` timer.

Run the task from the repository directory so `.env` and `sjsu-parking.pipe` are found. Desktop notifications may require an interactive user session, depending on the operating system.

## Development

Run the parser and CLI tests with:

```bash
pytest
```

The tests do not contact the live SJSU site or require a running RocketRide engine.

## Security

`.env` and local RocketRide runtime files are ignored by Git. Never commit API keys. If a key is exposed, revoke and replace it.
