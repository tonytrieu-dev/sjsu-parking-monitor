import sys
import types
from io import BytesIO
from io import StringIO
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


def test_fetch_parking_page_uses_python_http_client(monkeypatch):
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


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (HTTPError("https://example.test", 503, "Unavailable", {}, BytesIO()), "HTTP 503"),
        (URLError("offline"), "Could not reach the SJSU parking page: offline"),
    ],
)
def test_fetch_parking_page_reports_network_root_cause(monkeypatch, error, expected):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(check_parking, "urlopen", fail)

    with pytest.raises(RuntimeError, match=expected):
        check_parking.fetch_parking_page()


def test_parse_numeric_fullness_happy_path():
    assert check_parking.parse_garage_status(PAGE, "North Garage") == ("North Garage", 42)


def test_parse_full_is_normalized_to_100_percent():
    assert check_parking.parse_garage_status(PAGE, "South Garage") == ("South Garage", 100)


def test_parse_is_case_insensitive_and_trims_requested_name():
    html = PAGE.replace("Full", "fUlL")
    assert check_parking.parse_garage_status(html, "  south garage ") == ("south garage", 100)


@pytest.mark.parametrize("garage", ["", "   ", None])
def test_missing_garage_is_actionable(garage):
    with pytest.raises(ValueError, match="SJSU_GARAGE is required"):
        check_parking.parse_garage_status(PAGE, garage)


def test_unknown_garage_does_not_fall_through_to_another_garage():
    with pytest.raises(ValueError, match="configured garage 'West Garage'"):
        check_parking.parse_garage_status(PAGE, "West Garage")


def test_invalid_percentage_is_rejected():
    html = PAGE.replace("42%", "101%")
    with pytest.raises(ValueError, match="Invalid fullness percentage"):
        check_parking.parse_garage_status(html, "North Garage")


@pytest.mark.parametrize(
    ("percent", "color"),
    [(0, "green"), (69, "green"), (70, "yellow"), (89, "yellow"), (90, "red"), (100, "red")],
)
def test_status_color_has_explicit_occupancy_boundaries(percent, color):
    assert check_parking.status_color(percent) == color


def test_print_status_renders_panel_without_losing_response_text():
    console = Console(file=StringIO(), record=True, force_terminal=False)

    check_parking.print_status("North Garage", 42, "North Garage: 42% full", console)

    rendered = console.export_text()
    assert "SJSU Parking" in rendered
    assert "North Garage" in rendered
    assert "42% full" in rendered


def test_cli_options_are_parsed_together():
    args = check_parking.parse_args(
        ["--garage", "West Garage", "--notify", "none", "--uri", "ws://example"]
    )
    assert (args.garage, args.notify, args.uri) == ("West Garage", "none", "ws://example")


def test_notification_cli_value_overrides_environment(monkeypatch):
    monkeypatch.setenv("SJSU_NOTIFY", "desktop")
    assert check_parking.resolve_notification_mode("none") == "none"


def test_invalid_notification_environment_is_actionable(monkeypatch):
    monkeypatch.setenv("SJSU_NOTIFY", "email")
    with pytest.raises(ValueError, match="SJSU_NOTIFY must be one of"):
        check_parking.resolve_notification_mode()


def test_auto_notification_is_safe_on_non_windows(monkeypatch):
    monkeypatch.setattr(check_parking.platform, "system", lambda: "Linux")
    check_parking.send_notification("North Garage: 42% full", "auto")


def test_desktop_notification_uses_windows_notifier(monkeypatch):
    calls = []

    class FakeNotification:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def show(self):
            calls.append("shown")

    monkeypatch.setattr(check_parking.platform, "system", lambda: "Windows")
    monkeypatch.setitem(sys.modules, "winotify", types.SimpleNamespace(Notification=FakeNotification))

    check_parking.send_notification("North Garage: 42% full", "desktop")

    assert calls == [
        {"app_id": "SJSU Parking", "title": "SJSU Parking", "msg": "North Garage: 42% full"},
        "shown",
    ]


def test_none_notification_never_imports_or_sends(monkeypatch):
    class UnexpectedNotification:
        def __init__(self, *args, **kwargs):
            raise AssertionError("none mode must not construct a notification")

    monkeypatch.setattr(check_parking.platform, "system", lambda: "Windows")
    monkeypatch.setitem(
        sys.modules,
        "winotify",
        types.SimpleNamespace(Notification=UnexpectedNotification),
    )
    check_parking.send_notification("North Garage: 42% full", "none")


def test_desktop_notification_reports_missing_dependency(monkeypatch):
    monkeypatch.setattr(check_parking.platform, "system", lambda: "Windows")
    monkeypatch.setitem(sys.modules, "winotify", None)

    with pytest.raises(RuntimeError, match="optional winotify dependency"):
        check_parking.send_notification("North Garage: 42% full", "desktop")


def test_desktop_notification_requires_windows(monkeypatch):
    monkeypatch.setattr(check_parking.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="only on Windows"):
        check_parking.send_notification("North Garage: 42% full", "desktop")
