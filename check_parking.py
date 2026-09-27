"""Compatibility wrapper for running the monitor from a source checkout."""

from sjsu_parking_monitor import cli


if __name__ == "__main__":
    raise SystemExit(cli())
