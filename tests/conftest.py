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
import socket

import pytest

from stead_mcp.server import build_server
from stead_mcp.store import SteadStore


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
