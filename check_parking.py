import argparse
import asyncio
import os
import platform
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
except ModuleNotFoundError as error:
    if error.name == "rich":
        raise SystemExit(
            "Missing dependency 'rich'. Activate the project environment or run: "
            "python -m pip install ."
        ) from None
    raise
from rocketride import RocketRideClient
from rocketride.schema import Question
PIPELINE = Path(__file__).with_name("sjsu-parking.pipe")
URL = "https://sjsuparkingstatus.sjsu.edu/"
GARAGE_NAMES = (
    "North Garage",
    "South Garage",
    "West Garage",
    "South Campus Garage",
)


def load_local_env(env_file=None):
    """Load simple KEY=VALUE entries from .env without overwriting the shell."""
    env_file = env_file or Path(__file__).with_name(".env")
    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def fetch_parking_page(url=URL, timeout=30):
    """Fetch the live parking page without requiring an OS-specific executable."""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        raise RuntimeError(f"SJSU parking page returned HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach the SJSU parking page: {error.reason}") from error


def parse_args(args=None):
    parser = argparse.ArgumentParser(description="Report live SJSU garage fullness.")
    parser.add_argument(
        "--garage",
        default=None,
        help="Garage name (defaults to SJSU_GARAGE).",
    )
    parser.add_argument(
        "--notify",
        choices=("auto", "desktop", "none"),
        default=None,
        help="Notification mode (defaults to SJSU_NOTIFY or auto).",
    )
    parser.add_argument(
        "--uri",
        default=None,
        help="RocketRide WebSocket URI (defaults to ROCKETRIDE_URI or localhost).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show a traceback for unexpected errors.",
    )
    return parser.parse_args(args)


def send_notification(message, mode):
    """Send a desktop notification when supported, otherwise remain console-safe."""
    if mode == "none":
        return
    if platform.system() != "Windows":
        if mode == "desktop":
            raise RuntimeError("Desktop notifications are currently supported only on Windows")
        return

    try:
        from winotify import Notification
    except ImportError as error:
        if mode == "desktop":
            raise RuntimeError(
                "Desktop notifications require the optional winotify dependency"
            ) from error
        return
    Notification(app_id="SJSU Parking", title="SJSU Parking", msg=message).show()


def resolve_notification_mode(cli_mode=None):
    mode = cli_mode or os.getenv("SJSU_NOTIFY", "auto").strip().lower()
    if mode not in {"auto", "desktop", "none"}:
        raise ValueError("SJSU_NOTIFY must be one of: auto, desktop, none")
    return mode


def status_color(percent):
    """Return the display color for a garage fullness percentage."""
    if percent >= 90:
        return "red"
    if percent >= 70:
        return "yellow"
    return "green"


def print_status(garage, percent, status, console=None):
    """Render a readable status without repeating the default LLM response."""
    console = console or Console()
    color = status_color(percent)
    content = Text()
    content.append(f"{garage}\n", style="bold")
    content.append(f"{percent}% full", style=f"bold {color}")
    default_status = f"{garage}: {percent}% full"
    if status.strip().casefold() != default_status.casefold():
        content.append("\n\nRocketRide\n", style="bold cyan")
        content.append(status)
    console.print(
        Panel(
            content,
            title="SJSU Parking Status",
            border_style=color,
            padding=(1, 2),
            expand=False,
        )
    )


def parse_garage_status(html, garage_name):
    """Return (garage name, fullness percentage) for the selected garage."""
    requested = (garage_name or "").strip()
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
    percent = 100 if fullness.lower() == "full" else int(fullness)
    if not 0 <= percent <= 100:
        raise ValueError(f"Invalid fullness percentage for {requested!r}: {percent}%")
    return requested, percent


async def main(args=None):
    load_local_env()
    args = args or parse_args()
    garage = (args.garage or os.getenv("SJSU_GARAGE", "")).strip()
    html = fetch_parking_page()
    garage, percent = parse_garage_status(html, garage)

    rocketride_uri = args.uri or os.getenv("ROCKETRIDE_URI", "ws://localhost:5565")
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
        if status.strip().casefold().startswith("error:"):
            raise RuntimeError(status.split(":", 1)[1].strip())

        print_status(garage, percent, status)
        notification_mode = resolve_notification_mode(args.notify)
        try:
            send_notification(status, notification_mode)
        except Exception as error:
            Console(stderr=True).print(Text(f"Warning: notification failed: {error}", style="yellow"))
    finally:
        if token is not None:
            try:
                await client.terminate(token)
            except Exception:
                pass
        await client.disconnect()


def cli(args=None):
    parsed_args = parse_args(args)
    try:
        asyncio.run(main(parsed_args))
    except Exception as error:
        if parsed_args.debug:
            raise
        Console(stderr=True).print(Text(f"Error: {error}", style="red"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
