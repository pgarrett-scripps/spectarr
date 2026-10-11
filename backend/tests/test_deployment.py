"""Exercise the actual Compose interpolation used by self-hosted installs."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is required")


@pytest.fixture(params=["compose.yaml", "release/compose.yaml"])
def compose_service(request, tmp_path):
    def resolve(settings=None, env_file=None, native=False):
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("SPECTARR_", "COMPOSE_")) and key != "DOCKER_SOCKET"
        }
        environment.update(settings or {})
        command = ["docker", "compose", "--project-directory", str(tmp_path),
                   "--env-file", str(env_file or os.devnull), "-f", str(ROOT / request.param)]
        if native:
            command.extend(["-f", str(ROOT / "release/compose.native.yaml")])
        result = subprocess.run(
            [*command, "config", "--format", "json"],
            env=environment, check=True, capture_output=True, text=True, timeout=10,
        )
        return json.loads(result.stdout)["services"]["spectarr"]
    return resolve


def test_native_override_requires_explicit_binary_and_mounts_it_read_only(compose_service, tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        compose_service(native=True)
    binary = tmp_path / "msconvert-rs"
    binary.write_text("fixture")
    service = compose_service({"SPECTARR_MSCONVERT_RS_BINARY": str(binary)}, native=True)
    assert service["environment"]["SPECTARR_MSCONVERT_RS_BINARY"] == "/opt/native/msconvert-rs"
    mount = next(item for item in service["volumes"] if item["target"] == "/opt/native/msconvert-rs")
    assert mount["source"] == str(binary)
    assert mount["read_only"] is True
    assert mount["bind"]["create_host_path"] is False


def test_new_install_has_private_ports_and_generated_secrets(compose_service):
    service = compose_service()
    assert all(port["host_ip"] == "127.0.0.1" for port in service["ports"])
    assert service["environment"]["SPECTARR_AUTH_MODE"] == "password"
    assert service["environment"]["SPECTARR_SECRET_KEY"] == ""
    assert service["environment"]["SPECTARR_WORKER_TOKEN"] == ""
    socket = next(mount for mount in service["volumes"] if mount["target"] == "/var/run/docker.sock")
    assert socket["bind"]["create_host_path"] is False


def test_dashboard_network_access_does_not_publish_mcp(compose_service):
    service = compose_service({"SPECTARR_BIND_ADDRESS": "0.0.0.0"})
    ports = {port["target"]: port["host_ip"] for port in service["ports"]}
    assert ports == {8000: "0.0.0.0", 8001: "127.0.0.1"}


def test_proxy_origin_and_rootless_socket_reach_the_container(compose_service):
    service = compose_service({
        "SPECTARR_CORS_ORIGINS": '["https://massspec.example.org"]',
        "SPECTARR_TRUSTED_HOSTS": '["localhost","massspec.example.org"]',
        "DOCKER_SOCKET": "/run/user/1000/docker.sock",
    })
    assert json.loads(service["environment"]["SPECTARR_CORS_ORIGINS"]) == ["https://massspec.example.org"]
    assert "massspec.example.org" in json.loads(service["environment"]["SPECTARR_TRUSTED_HOSTS"])
    socket = next(mount for mount in service["volumes"] if mount["target"] == "/var/run/docker.sock")
    assert socket["source"] == "/run/user/1000/docker.sock"


@pytest.mark.parametrize("env_file", [".env.example", "release/.env.example"])
def test_copying_an_example_keeps_automatic_secrets(compose_service, env_file):
    service = compose_service(env_file=ROOT / env_file)
    assert service["environment"]["SPECTARR_SECRET_KEY"] == ""
    assert service["environment"]["SPECTARR_WORKER_TOKEN"] == ""
