import json
import os
import site
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def installed_package(tmp_path_factory):
    build_dir = tmp_path_factory.mktemp("wheel")
    install_dir = tmp_path_factory.mktemp("installed")
    project_dir = Path(__file__).parents[1]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(build_dir),
            str(project_dir),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    wheel = next(build_dir.glob("*.whl"))
    subprocess.run(
        [sys.executable, "-m", "venv", str(install_dir)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    installed_python = install_dir / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    installed_site = subprocess.run(
        [str(installed_python), "-c", "import site; print(site.getsitepackages()[-1])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    Path(installed_site, "test-dependencies.pth").write_text(
        site.getsitepackages()[-1], encoding="utf-8"
    )
    subprocess.run(
        [
            str(installed_python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            str(wheel),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return install_dir


def installed_environment():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for key in ("SJSU_GARAGE", "SJSU_NOTIFY", "ROCKETRIDE_URI"):
        environment.pop(key, None)
    return environment


def test_wheel_contains_pipeline_with_provider_configuration(installed_package, tmp_path):
    script = (
        "import json, sjsu_parking_monitor as m; "
        "pipeline=json.loads(m.PIPELINE.read_text(encoding='utf-8')); "
        "llm=next(c for c in pipeline['components'] if c['provider']=='llm_gemini'); "
        "print(json.dumps({'name': pipeline['name'], 'config': llm['config']}))"
    )
    result = subprocess.run(
        [
            str(
                installed_package
                / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            ),
            "-c",
            script,
        ],
        cwd=tmp_path,
        env=installed_environment(),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "name": "SJSU Garage Status Monitor",
        "config": {
            "profile": "gemini-2_5-flash",
            "apikey": "${ROCKETRIDE_LLM_API_KEY}",
        },
    }


def test_installed_command_reads_env_from_working_directory(installed_package, tmp_path):
    (tmp_path / ".env").write_text(
        "SJSU_GARAGE=North Garage\nSJSU_NOTIFY=invalid\n",
        encoding="utf-8",
    )
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    command = installed_package / scripts_dir / (
        "parking-monitor.exe" if os.name == "nt" else "parking-monitor"
    )
    result = subprocess.run(
        [str(command)],
        cwd=tmp_path,
        env=installed_environment(),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "SJSU_NOTIFY must be one of" in result.stderr
