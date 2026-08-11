"""Suite-wide guards.

Stead now reaches the network: the `web` toolset queries a local SearXNG,
which in turn queries upstream engines. A test that quietly succeeds against
either proves nothing and puts a household query on the wire.

Scope, precisely: this blocks in-process TCP and hostname resolution, which
covers httpx, anyio and asyncio since they all connect through a Python
socket object. It does NOT constrain subprocesses — `test_scheduler_gate`
and `test_credential_isolation` shell out, and a child process has its own
interpreter. Those rely on `EXEC_GUARD` and a fake cron runner instead.
"""
import os
import socket
import sys
from pathlib import Path

import pytest

from stead_mcp.server import build_server
from stead_mcp.store import SteadStore

# The speech providers subclass Hermes' provider ABCs, which live in the Hermes
# checkout rather than on the path. Same variable the launcher uses. Without a
# Hermes source tree those tests cannot run at all, so skip collecting them
# instead of failing the suite on an unrelated machine.
HERMES_SOURCE = Path(
    os.environ.get("STEAD_HERMES_SOURCE", Path.home() / ".hermes" / "hermes-agent")
)
if (HERMES_SOURCE / "agent" / "tts_provider.py").is_file():
    sys.path.insert(0, str(HERMES_SOURCE))
else:  # pragma: no cover - depends on the host
    collect_ignore = ["test_sarvam_voice.py", "test_sarvam_live.py"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("tests must not reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    # DNS goes out before any connect() is attempted.
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "gethostbyname", refuse)


@pytest.fixture()
def server(tmp_path):
    store = SteadStore(tmp_path / "stead.sqlite")
    store.migrate()
    return build_server(store=store)
