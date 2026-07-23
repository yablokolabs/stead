"""Deployment rendering and local SearXNG safety contracts."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_systemd_dropin_persists_custom_runtime_paths(tmp_path: Path) -> None:
    from stead_mcp.install import render_systemd_dropin

    output = tmp_path / "override.conf"
    launcher = tmp_path / "checkout" / "scripts" / "stead-launch.sh"
    demo_home = tmp_path / "private-state"
    hermes_bin = tmp_path / "custom-bin" / "hermes"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    hermes_bin.parent.mkdir(parents=True)
    hermes_bin.write_text("#!/bin/sh\n")

    render_systemd_dropin(
        output=output,
        launcher=launcher,
        demo_home=demo_home,
        hermes_bin=hermes_bin,
    )
    content = output.read_text()
    assert f'Environment="STEAD_DEMO_HOME={demo_home}"' in content
    assert f'Environment="STEAD_HERMES_BIN={hermes_bin}"' in content


def _fake_docker(tmp_path: Path) -> tuple[Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "docker-started"
    inspect_json = tmp_path / "inspect.json"
    executable = fake_bin / "docker"
    executable.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  inspect) cat \"$FAKE_DOCKER_INSPECT\" ;;\n"
        "  start|run) : > \"$FAKE_DOCKER_MARKER\" ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    executable.chmod(0o755)
    return fake_bin, marker, inspect_json


def test_searxng_uses_custom_workspace_and_refuses_unsafe_existing_container(
    tmp_path: Path,
) -> None:
    fake_bin, marker, inspect_json = _fake_docker(tmp_path)
    home = tmp_path / "home"
    demo_home = tmp_path / "custom-private"
    home.mkdir()
    environment = os.environ.copy()
    environment.update(
        HOME=str(home),
        STEAD_DEMO_HOME=str(demo_home),
        PATH=f"{fake_bin}:{environment['PATH']}",
        FAKE_DOCKER_INSPECT=str(inspect_json),
        FAKE_DOCKER_MARKER=str(marker),
    )
    inspect_json.write_text("[]\n")

    configured = subprocess.run(
        [str(REPO / "scripts" / "setup-searxng.sh")],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert configured.returncode == 0, configured.stderr
    config_dir = demo_home / "searxng"
    assert (config_dir / "settings.yml").is_file()
    assert not (home / ".stead-demo" / "searxng").exists()

    image = "searxng/searxng@sha256:b8ca38ba06eea544d7555e88321e212ddc0d5c3c7de055419cfb2e5c6bf30812"
    inspect_json.write_text(
        json.dumps(
            [
                {
                    "Config": {
                        "Image": image,
                        "Env": ["SEARXNG_BASE_URL=http://localhost:8080/"],
                    },
                    "HostConfig": {
                        "PortBindings": {
                            "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]
                        },
                        "RestartPolicy": {"Name": "unless-stopped"},
                    },
                    "Mounts": [
                        {
                            "Destination": "/etc/searxng",
                            "Source": str(config_dir),
                            "RW": True,
                        }
                    ],
                    "State": {"Running": False},
                }
            ]
        )
    )
    started = subprocess.run(
        [str(REPO / "scripts" / "setup-searxng.sh"), "--start"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert started.returncode != 0
    assert "unsafe existing stead-searxng container" in started.stderr
    assert not marker.exists()
