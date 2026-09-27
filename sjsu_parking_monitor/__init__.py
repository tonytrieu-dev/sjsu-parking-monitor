import argparse
import asyncio
import os
import platform
import re
import shutil
import sys
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory
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

PIPELINE = resources.files(__package__).joinpath("sjsu-parking.pipe")
URL = "https://sjsuparkingstatus.sjsu.edu/"
GARAGE_NAMES = (
    "North Garage",
    "South Garage",
    "West Garage",
    "South Campus Garage",
)


def load_local_env(env_file=None):
    """Load simple KEY=VALUE entries from the working directory without overwrites."""
    env_file = Path.cwd() / ".env" if env_file is None else Path(env_file)
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
    parser.add_argument("--garage", default=None, help="Garage name (defaults to SJSU_GARAGE).")
    parser.add_argument(
        "--notify",
        choices=("desktop", "none"),
        default=None,
        help="Notification mode (defaults to SJSU_NOTIFY or none).",
    )
    parser.add_argument(
        "--uri",
        default=None,
        help="RocketRide WebSocket URI (defaults to ROCKETRIDE_URI or localhost).",
    )
    parser.add_argument("--debug", action="store_true", help="Show a traceback for unexpected errors.")
    return parser.parse_args(args)


def send_desktop_notification(message):
    """Send a desktop notification or raise an actionable error."""
    if platform.system() != "Windows":
        raise RuntimeError("Desktop notifications are currently supported only on Windows")

    try:
        from winotify import Notification
    except ImportError as error:
        raise RuntimeError(
            "Desktop notifications require the optional winotify dependency"
        ) from error
    Notification(app_id="SJSU Parking", title="SJSU Parking", msg=message).show()


def resolve_notification_mode(cli_mode=None):
    mode = cli_mode or os.getenv("SJSU_NOTIFY", "none").strip().lower()
    if mode not in {"desktop", "none"}:
        raise ValueError("SJSU_NOTIFY must be one of: desktop, none")
    return mode


def status_color(percent):
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
            "SJSU_GARAGE is required. Choose one of: " + ", ".join(GARAGE_NAMES)
        )
    garage_heading = (
        rf'<h2\s+class=["\']garage__name["\']>\s*'
        rf'{re.escape(requested)}\s*</h2>'
    )
    selected_heading = re.search(garage_heading, html, re.DOTALL | re.IGNORECASE)
    if not selected_heading:
        raise ValueError(
            f"Could not find fullness for configured garage {requested!r}. "
            "Check SJSU_GARAGE against the garage names on the SJSU status page."
        )

    html_after_heading = html[selected_heading.end() :]
    next_heading = re.search(
        r'<h2\s+class=["\']garage__name["\']>',
        html_after_heading,
        re.IGNORECASE,
    )
    selected_garage_html = (
        html_after_heading[: next_heading.start()] if next_heading else html_after_heading
    )
    fullness_match = re.search(
        r'<span\s+class=["\']garage__fullness["\']>\s*'
        r'(?:(?P<fullness>\d+)\s*%|(?P<full>Full))',
        selected_garage_html,
        re.DOTALL | re.IGNORECASE,
    )
    if not fullness_match:
        raise ValueError(
            f"Could not find fullness for configured garage {requested!r}. "
            "Check SJSU_GARAGE against the garage names on the SJSU status page."
        )

    fullness = fullness_match.group("fullness") or fullness_match.group("full")
    percent = 100 if fullness.lower() == "full" else int(fullness)
    if not 0 <= percent <= 100:
        raise ValueError(f"Invalid fullness percentage for {requested!r}: {percent}%")
    return requested, percent


async def close_client_after_pipeline(client, token, preserve_workflow_error):
    """Release RocketRide resources while preserving an active workflow failure."""
    if token is not None:
        try:
            await client.terminate(token)
        except Exception:
            if not preserve_workflow_error:
                raise
    try:
        await client.disconnect()
    except Exception:
        if not preserve_workflow_error:
            raise


async def run_pipeline(garage, percent, rocketride_uri):
    client_options = {"uri": rocketride_uri}
    if rocketride_uri.startswith(("ws://localhost", "ws://127.0.0.1")):
        client_options["auth"] = "MYAPIKEY"
    client = RocketRideClient(**client_options)
    await client.connect()

    token = None
    try:
        with resources.as_file(PIPELINE) as bundled_pipeline, TemporaryDirectory() as temp_dir:
            pipeline_path = Path(temp_dir) / bundled_pipeline.name
            shutil.copyfile(bundled_pipeline, pipeline_path)
            result = await client.use(filepath=str(pipeline_path))
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
        return status
    finally:
        await close_client_after_pipeline(
            client,
            token,
            preserve_workflow_error=sys.exc_info()[0] is not None,
        )


async def main(args=None, env_file=None):
    load_local_env(env_file)
    args = args or parse_args()
    notification_mode = resolve_notification_mode(args.notify)
    garage = (args.garage or os.getenv("SJSU_GARAGE", "")).strip()
    html = fetch_parking_page()
    garage, percent = parse_garage_status(html, garage)
    rocketride_uri = args.uri or os.getenv("ROCKETRIDE_URI", "ws://localhost:5565")
    status = await run_pipeline(garage, percent, rocketride_uri)
    print_status(garage, percent, status)
    if notification_mode == "desktop":
        send_desktop_notification(status)


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
