# SJSU Parking Monitor

A small Windows demo that checks live SJSU garage occupancy, sends the result through a RocketRide pipeline powered by a configurable LLM, and shows the response as a Windows notification.

Recommended repository name: `sjsu-parking-monitor`

## What problem it solves

Finding an open SJSU garage can take time. This monitor turns the live parking status page into a concise notification for the garage you choose.

## Architecture

```text
SJSU Parking Status
        ↓
      curl
        ↓
  Python Parser
        ↓
   RocketRide
        ↓
 Configured LLM
        ↓
Windows Notification
```

The script fetches live HTML with Windows `curl.exe`, parses the selected garage, and passes the live garage name and percentage into RocketRide. RocketRide is the AI orchestration layer after the live parking data is retrieved; the configured LLM produces the concise human-facing status line.

## Technologies

- Python 3.10+
- Windows `curl.exe`
- RocketRide Python client and pipeline engine
- Configurable RocketRide LLM provider (Gemini is the default example)
- `winotify` Windows toast notifications

## Prerequisites

1. Windows with `curl.exe` available on `PATH`.
2. Python 3.10 or newer.
3. A running RocketRide engine. The default local endpoint is `ws://localhost:5565`.
4. An API key if your selected LLM provider requires one. Local providers such as Ollama can run without a cloud key.

## Setup

```powershell
git clone https://github.com/your-user/sjsu-parking-monitor.git
cd sjsu-parking-monitor
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set your Gemini key and preferred garage:

```env
ROCKETRIDE_LLM_API_KEY=your_llm_api_key
SJSU_GARAGE=your_garage_name_here
```

The Python client connects to the local RocketRide engine at `ws://localhost:5565` using its built-in local development handshake; no RocketRide engine key is required in `.env`. The included pipeline uses the direct `llm_gemini` provider and the provider-neutral `ROCKETRIDE_LLM_API_KEY`; no LLM URL is needed. Users of another LLM can replace that single LLM component in `sjsu-parking.pipe` while keeping the same key variable.

The RocketRide engine/runtime is intentionally not included in this repository. Install or run it separately, then import `sjsu-parking.pipe` into that engine if your setup requires an explicit pipeline import.

## Choose a garage

Set the required `SJSU_GARAGE` variable to one of the garage names currently listed by SJSU:

- `North Garage`
- `South Garage`
- `West Garage`
- `South Campus Garage`

For example:

```env
SJSU_GARAGE=West Garage
```

If the value is missing, the script stops with a helpful error listing the valid garage names. If it cannot find the requested garage, it stops with an error naming the requested value.

## Run

Start the RocketRide engine, confirm the selected LLM credentials are available, and run:

```powershell
python check_parking.py
```

Example console output:

```text
West Garage: 35% full
```

The same status is shown in a Windows notification titled `SJSU Parking`.

## Windows Task Scheduler

Create a task that runs `python.exe` with `check_parking.py` as its argument, using the repository as the **Start in** directory. For example:

```text
Program: C:\path\to\sjsu-parking-monitor\.venv\Scripts\python.exe
Arguments: C:\path\to\sjsu-parking-monitor\check_parking.py
Start in: C:\path\to\sjsu-parking-monitor
```

The current scheduling example is Mondays and Wednesdays at 3:00 PM and 4:00 PM, but the script has no time-of-day restriction. Add any Task Scheduler triggers you want—for example, hourly, daily, or at different times on different days. The task must run in a logged-in Windows session for toast notifications to appear.

## Project structure

```text
check_parking.py    Fetches, parses, orchestrates, and notifies
sjsu-parking.pipe   RocketRide chat → configurable LLM provider → response pipeline
.env.example        Safe configuration template
requirements.txt    Runtime Python dependencies
```

## Security

API keys and engine credentials are supplied through environment variables in `.env`. `.env`, virtual environments, downloaded wheels, logs, caches, IDE files, and the RocketRide runtime directory are ignored by Git. Never commit real credentials; if a key was previously exposed, revoke and replace it before publishing the repository.

## License

Add the license that fits your presentation or project before publishing.
