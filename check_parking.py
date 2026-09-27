import asyncio
import os
import re
import subprocess
from pathlib import Path

from rocketride import RocketRideClient
from rocketride.schema import Question
from winotify import Notification


PIPELINE = Path(__file__).with_name("sjsu-parking.pipe")
URL = "https://sjsuparkingstatus.sjsu.edu/"
GARAGE_NAMES = (
    "North Garage",
    "South Garage",
    "West Garage",
    "South Campus Garage",
)


def load_local_env():
    """Load simple KEY=VALUE entries from .env without overwriting the shell."""
    env_file = Path(__file__).with_name(".env")
    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def fetch_parking_page():
    result = subprocess.run(
        [
            "curl.exe",
            "-f",
            "-sS",
            "-L",
            "-A",
            "Mozilla/5.0",
            URL,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def parse_garage_status(html, garage_name):
    """Return (garage name, fullness percentage) for the selected garage."""
    requested = garage_name.strip()
    if not requested:
        raise ValueError(
            "SJSU_GARAGE is required. Choose one of: "
            + ", ".join(GARAGE_NAMES)
        )
    garage_heading = (
        rf'<h2\s+class=["\']garage__name["\']>\s*'
        rf'{re.escape(requested)}\s*</h2>'
    )
    match = re.search(
        garage_heading
        + rf'.*?<span\s+class=["\']garage__fullness["\']>\s*'
        + rf'(?:(?P<fullness>\d+)\s*%|(?P<full>Full))',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    if not match:
        raise ValueError(
            f"Could not find fullness for configured garage {requested!r}. "
            "Check SJSU_GARAGE against the garage names on the SJSU status page."
        )

    fullness = match.group("fullness") or match.group("full")
    return requested, 100 if fullness.lower() == "full" else int(fullness)


async def main():
    load_local_env()
    garage = os.getenv("SJSU_GARAGE", "").strip()
    html = fetch_parking_page()
    garage, percent = parse_garage_status(html, garage)

    rocketride_uri = os.getenv("ROCKETRIDE_URI", "ws://localhost:5565")
    client_options = {"uri": rocketride_uri}
    if rocketride_uri.startswith(("ws://localhost", "ws://127.0.0.1")):
        # Local RocketRide development engines use this built-in handshake value.
        client_options["auth"] = "MYAPIKEY"
    client = RocketRideClient(**client_options)
    await client.connect()

    token = None
    try:
        result = await client.use(filepath=str(PIPELINE))
        token = result["token"]

        question = Question()
        question.addQuestion(
            f"The current live SJSU {garage} fullness is {percent}%. "
            f"Return exactly one concise line: {garage}: {percent}% full"
        )

        response = await client.chat(token=token, question=question)
        answers = response.get("answers") or []
        status = str(answers[0]) if answers else f"{garage}: {percent}% full"

        print(status)
        Notification(app_id="SJSU Parking", title="SJSU Parking", msg=status).show()
    finally:
        if token is not None:
            try:
                await client.terminate(token)
            except Exception:
                pass
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
