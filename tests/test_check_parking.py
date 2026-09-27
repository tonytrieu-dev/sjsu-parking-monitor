import asyncio
import json
import os
import sys
import types
from argparse import Namespace
from io import BytesIO, StringIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from rich.console import Console

import check_parking


PAGE = """
<h2 class="garage__name">North Garage</h2>
<span class="garage__fullness">42%</span>
<h2 class="garage__name">South Garage</h2>
<span class="garage__fullness">Full</span>
"""


def test_fetch_success_uses_python_http_and_decodes_response(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"<html>caf\xc3\xa9</html>"

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr(check_parking, "urlopen", fake_urlopen)

    assert check_parking.fetch_parking_page("https://example.test", timeout=7) == "<html>café</html>"
    request, timeout = calls[0]
    assert request.full_url == "https://example.test"
    assert request.get_header("User-agent") == "Mozilla/5.0"
    assert timeout == 7


def test_fetch_failures_become_actionable_runtime_errors(monkeypatch):
    failures = [
        (HTTPError("https://example.test", 503, "Unavailable", {}, BytesIO()), "HTTP 503"),
        (URLError("offline"), "Could not reach the SJSU parking page: offline"),
    ]
    for failure, message in failures:
        monkeypatch.setattr(check_parking, "urlopen", lambda *args, failure=failure, **kwargs: (_ for _ in ()).throw(failure))
        with pytest.raises(RuntimeError, match=message):
            check_parking.fetch_parking_page()


def test_parser_supports_multiple_garages_and_full_status():
    assert check_parking.parse_garage_status(PAGE, "North Garage") == ("North Garage", 42)
    assert check_parking.parse_garage_status(PAGE, "South Garage") == ("South Garage", 100)


def test_parser_accepts_trimmed_case_insensitive_selection():
    assert check_parking.parse_garage_status(PAGE, "  south garage ") == ("south garage", 100)


def test_parser_rejects_missing_or_malformed_selection():
    cases = [
        ("", "SJSU_GARAGE is required"),
        (None, "SJSU_GARAGE is required"),
        ("West Garage", "Could not find fullness"),
        ("North Garage", "Could not find fullness"),
    ]
    malformed_page = '<div class="garage-name">North Garage</div>'
    for garage, message in cases:
        html = malformed_page if garage == "North Garage" else PAGE
        with pytest.raises(ValueError, match=message):
            check_parking.parse_garage_status(html, garage)


def test_parser_rejects_out_of_range_percentage():
    with pytest.raises(ValueError, match="Invalid fullness percentage"):
        check_parking.parse_garage_status(PAGE.replace("42%", "101%"), "North Garage")


def test_local_env_loads_values_without_overwriting_shell(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comment\nSJSU_GARAGE="North Garage"\nNEW_SETTING=value\nINVALID_LINE\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SJSU_GARAGE", "South Garage")
    monkeypatch.delenv("NEW_SETTING", raising=False)

    check_parking.load_local_env(env_file)

    assert os.environ["SJSU_GARAGE"] == "South Garage"
    assert os.environ["NEW_SETTING"] == "value"


def test_pipeline_owns_provider_configuration():
    pipeline = json.loads(Path(check_parking.PIPELINE).read_text(encoding="utf-8"))
    llm = next(item for item in pipeline["components"] if item["provider"] == "llm_gemini")

    assert llm["config"] == {
        "profile": "gemini-2_5-flash",
        "apikey": "${ROCKETRIDE_LLM_API_KEY}",
    }


class FakeRocketRideClient:
    connect_error = None
    chat_error = None
    answers = []
    instances = []

    def __init__(self, **options):
        self.options = options
        self.terminated = False
        self.disconnected = False
        self.__class__.instances.append(self)

    async def connect(self):
        if self.connect_error:
            raise self.connect_error

    async def use(self, filepath):
        self.pipeline_path = filepath
        return {"token": "test-token"}

    async def chat(self, token, question):
        if self.chat_error:
            raise self.chat_error
        self.question = question
        return {"answers": self.answers}

    async def terminate(self, token):
        self.terminated = True

    async def disconnect(self):
        self.disconnected = True


def configure_main_test(monkeypatch, answers=None, connect_error=None, chat_error=None):
    FakeRocketRideClient.instances.clear()
    FakeRocketRideClient.answers = answers or []
    FakeRocketRideClient.connect_error = connect_error
    FakeRocketRideClient.chat_error = chat_error
    monkeypatch.setattr(check_parking, "RocketRideClient", FakeRocketRideClient)
    monkeypatch.setattr(check_parking, "load_local_env", lambda: None)
    monkeypatch.setattr(check_parking, "fetch_parking_page", lambda: PAGE)


def test_main_handles_multiple_garages_and_empty_answer_fallback(monkeypatch):
    rendered = []
    configure_main_test(monkeypatch)
    monkeypatch.setattr(check_parking, "print_status", lambda *args: rendered.append(args))
    monkeypatch.setattr(check_parking, "send_notification", lambda *args: None)

    for garage, expected in (("North Garage", 42), ("South Garage", 100)):
        asyncio.run(
            check_parking.main(
                Namespace(garage=garage, notify="none", uri="ws://localhost:5565", debug=False)
            )
        )

    assert rendered == [
        ("North Garage", 42, "North Garage: 42% full"),
        ("South Garage", 100, "South Garage: 100% full"),
    ]
    assert FakeRocketRideClient.instances[-1].terminated
    assert FakeRocketRideClient.instances[-1].disconnected


def test_main_uses_local_handshake_and_llm_response(monkeypatch):
    rendered = []
    configure_main_test(monkeypatch, answers=["AI-formatted parking status"])
    monkeypatch.setattr(check_parking, "print_status", lambda *args: rendered.append(args))
    monkeypatch.setattr(check_parking, "send_notification", lambda *args: None)

    asyncio.run(
        check_parking.main(
            Namespace(garage="North Garage", notify="none", uri="ws://localhost:5565", debug=False)
        )
    )

    client = FakeRocketRideClient.instances[0]
    assert client.options == {"uri": "ws://localhost:5565", "auth": "MYAPIKEY"}
    assert rendered == [("North Garage", 42, "AI-formatted parking status")]


def test_main_propagates_rocketride_and_provider_failures(monkeypatch):
    args = Namespace(garage="North Garage", notify="none", uri="ws://localhost:5565", debug=False)
    for error in (RuntimeError("RocketRide engine unavailable"), RuntimeError("LLM API key rejected")):
        configure_main_test(monkeypatch, connect_error=error if "RocketRide" in str(error) else None, chat_error=error if "LLM" in str(error) else None)
        with pytest.raises(RuntimeError, match=str(error)):
            asyncio.run(check_parking.main(args))


def test_main_rejects_provider_error_returned_as_answer(monkeypatch):
    configure_main_test(monkeypatch, answers=["Error: Please enter your Gemini API key."])
    args = Namespace(garage="North Garage", notify="none", uri="ws://localhost:5565", debug=False)

    with pytest.raises(RuntimeError, match="Please enter your Gemini API key"):
        asyncio.run(check_parking.main(args))


def test_notification_delivery_failure_is_only_a_warning(monkeypatch, capsys):
    configure_main_test(monkeypatch)
    monkeypatch.setattr(check_parking, "print_status", lambda *args: None)
    monkeypatch.setattr(check_parking, "send_notification", lambda *args: (_ for _ in ()).throw(RuntimeError("toast unavailable")))

    asyncio.run(
        check_parking.main(
            Namespace(garage="North Garage", notify="desktop", uri="ws://localhost:5565", debug=False)
        )
    )

    assert "Warning: notification failed: toast unavailable" in capsys.readouterr().err


def test_cli_rejects_invalid_notification_configuration_before_delivery(monkeypatch, capsys):
    delivered = []
    configure_main_test(monkeypatch)
    monkeypatch.setenv("SJSU_NOTIFY", "email")
    monkeypatch.setattr(check_parking, "print_status", lambda *args: None)
    monkeypatch.setattr(check_parking, "send_notification", lambda *args: delivered.append(args))

    result = check_parking.cli(["--garage", "North Garage", "--uri", "ws://localhost:5565"])

    assert result == 1
    assert "SJSU_NOTIFY must be one of" in capsys.readouterr().err
    assert delivered == []


def test_cli_turns_expected_failure_into_short_error(monkeypatch, capsys):
    async def fail(_args):
        raise RuntimeError("SJSU parking page unavailable")

    monkeypatch.setattr(check_parking, "main", fail)

    assert check_parking.cli(["--garage", "North Garage"]) == 1
    assert capsys.readouterr().err.strip() == "Error: SJSU parking page unavailable"


def test_output_is_compact_and_does_not_repeat_default_response():
    console = Console(file=StringIO(), record=True, force_terminal=False)
    check_parking.print_status("North Garage", 42, "North Garage: 42% full", console)
    rendered = console.export_text()

    assert "SJSU Parking Status" in rendered
    assert rendered.count("North Garage") == 1
    assert "North Garage: 42% full" not in rendered


def test_output_keeps_meaningful_rocketride_response():
    console = Console(file=StringIO(), record=True, force_terminal=False)
    check_parking.print_status("North Garage", 42, "North Garage has plenty of space", console)
    rendered = console.export_text()

    assert "RocketRide" in rendered
    assert "North Garage has plenty of space" in rendered


def test_notification_modes_cover_portable_and_windows_paths(monkeypatch):
    check_parking.send_notification("status", "none")

    monkeypatch.setattr(check_parking.platform, "system", lambda: "Linux")
    check_parking.send_notification("status", "auto")
    with pytest.raises(RuntimeError, match="only on Windows"):
        check_parking.send_notification("status", "desktop")

    calls = []

    class FakeNotification:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def show(self):
            calls.append("shown")

    monkeypatch.setattr(check_parking.platform, "system", lambda: "Windows")
    monkeypatch.setitem(sys.modules, "winotify", types.SimpleNamespace(Notification=FakeNotification))
    check_parking.send_notification("status", "desktop")
    assert calls == [{"app_id": "SJSU Parking", "title": "SJSU Parking", "msg": "status"}, "shown"]


def test_cli_options_and_notification_configuration_are_validated():
    args = check_parking.parse_args(["--garage", "West Garage", "--notify", "none", "--uri", "ws://example"])
    assert (args.garage, args.notify, args.uri) == ("West Garage", "none", "ws://example")
    assert check_parking.resolve_notification_mode("none") == "none"
