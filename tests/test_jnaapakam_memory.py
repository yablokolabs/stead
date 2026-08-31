"""Tests for the jnaapakam long-term memory layer.

The store is exercised two ways:
  * directly against `JnaapakamMemory` with a recording fake transport, so the
    exact request shape (method, path, namespace, body) is asserted without a
    network, and
  * through the MCP tool surface, mirroring how the model reaches it.
"""
import json

import pytest

from helpers import call, run
from stead_mcp.jnaapakam import JnaapakamMemory, MemoryUnavailable
from stead_mcp.server import build_server
from stead_mcp.store import SteadStore

HOUSEHOLD = "stead-demo-household"


class RecordingTransport:
    """Records every request and answers from a script.

    `responses` is a list of (status, dict) consumed in order; the last entry
    repeats for any further request.
    """

    def __init__(self, *responses):
        self.calls = []
        self.responses = list(responses) or [(200, {})]

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url, body, headers))
        status, payload = self.responses[min(len(self.calls) - 1,
                                             len(self.responses) - 1)]
        return status, payload


def mem(transport, namespace=HOUSEHOLD):
    return JnaapakamMemory(base_url="http://127.0.0.1:8889",
                           namespace=namespace, transport=transport)


# -- direct client: request shape ---------------------------------------------

def test_store_posts_ingest_with_household_namespace():
    transport = RecordingTransport((200, {"status": "stored", "memory_id": 3,
                                          "summary": "Kerstin prefers mornings"}))
    memory = mem(transport)

    result = memory.store("Kerstin prefers morning reminders",
                          source="conversation")

    assert result["memory_id"] == 3
    method, url, body, headers = transport.calls[0]
    assert method == "POST"
    assert url == "http://127.0.0.1:8889/ingest"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {
        "text": "Kerstin prefers morning reminders",
        "source": "conversation",
        "namespace": HOUSEHOLD,
    }


def test_recall_searches_with_household_namespace():
    transport = RecordingTransport((200, {
        "query": "coffee", "count": 1,
        "memories": [{"id": 1, "source": "conversation",
                      "summary": "Dad drinks strong black coffee, no sugar",
                      "raw_text": "...", "entities": ["dad"],
                      "topics": ["coffee"], "importance": 0.7,
                      "created_at": "2026-08-30T09:00:00Z", "score": 0.9}],
    }))
    memory = mem(transport)

    results = memory.recall("what does Dad drink?", limit=3)

    assert len(results) == 1
    assert results[0]["summary"].startswith("Dad drinks")
    method, url, body, headers = transport.calls[0]
    assert method == "GET"
    assert body is None
    assert "q=what+does+Dad+drink%3F" in url
    assert "limit=3" in url
    assert f"namespace={HOUSEHOLD}" in url


def test_query_text_is_url_encoded_never_executed_as_syntax():
    transport = RecordingTransport((200, {"memories": []}))
    memory = mem(transport)

    memory.recall("milk OR eggs; DROP TABLE", limit=2)
    url = transport.calls[0][1]

    assert "OR" in url  # literal text survived encoding
    assert "DROP+TABLE" in url
    assert "%3B" in url  # the semicolon travelled as data, not syntax


def test_limit_is_clamped_to_the_household_window():
    transport = RecordingTransport((200, {"memories": []}))
    memory = mem(transport)

    memory.recall("anything", limit=-5)
    memory.recall("anything", limit=999)
    memory.recall("anything", limit=4)

    limits = [dict(p.split("=") for p in c[1].split("?")[1].split("&"))["limit"]
              for c in transport.calls]
    assert limits == ["1", "8", "4"]


def test_memory_server_error_becomes_memory_unavailable():
    memory = mem(RecordingTransport((503, {"error": "llm down"})))

    with pytest.raises(MemoryUnavailable, match="503"):
        memory.store("anything")

    with pytest.raises(MemoryUnavailable, match="503"):
        memory.recall("anything")


def test_unset_url_fails_closed(monkeypatch):
    monkeypatch.delenv("JNAAPAKAM_URL", raising=False)

    with pytest.raises(MemoryUnavailable, match="JNAAPAKAM_URL"):
        JnaapakamMemory.from_environment(HOUSEHOLD)


# -- through the MCP tool surface ---------------------------------------------

def build(transport):
    store = SteadStore(":memory:")
    store.migrate()
    return build_server(store=store,
                        memory=mem(transport, namespace=store.household_id))


def test_remember_and_recall_round_trip_via_mcp():
    transport = RecordingTransport(
        (200, {"status": "stored", "memory_id": 1,
               "summary": "Grandma avoids garlic"}),
        (200, {"memories": [{"id": 1, "source": "conversation",
                             "summary": "Grandma avoids garlic",
                             "created_at": "2026-08-30T09:00:00Z",
                             "score": 0.95}], "count": 1}),
    )
    server = build(transport)

    stored = call(server, "remember", text="Grandma avoids garlic")
    recalled = call(server, "recall", query="cooking for Grandma")

    assert stored["ok"] is True and stored["memory_id"] == 1
    assert recalled["ok"] is True
    assert recalled["memories"][0]["summary"] == "Grandma avoids garlic"
    # The namespace never came from the caller — it is the household id.
    stored_url = transport.calls[0][1]
    assert stored_url == "http://127.0.0.1:8889/ingest"


def test_empty_remember_and_recall_are_rejected(server):
    assert call(server, "remember", text="   ")["ok"] is False
    assert call(server, "recall", query="")["ok"] is False


def test_unavailable_memory_server_fails_closed_via_mcp():
    def refuse(method, url, body, headers):
        raise MemoryUnavailable("JNAAPAKAM_URL is not set")
    server = build(refuse)

    stored = call(server, "remember", text="anything")
    recalled = call(server, "recall", query="anything")

    assert stored["ok"] is False and stored["error"] == "MemoryUnavailable"
    assert recalled["ok"] is False and recalled["error"] == "MemoryUnavailable"


def test_jnaapakam_tools_accept_no_household_identifier(server):
    names = {t.name: t for t in run(server.list_tools())}
    for tool in ("remember", "recall"):
        params = set((names[tool].inputSchema or {}).get("properties", {}))
        assert params == ({"text", "source"} if tool == "remember"
                          else {"query", "limit"})