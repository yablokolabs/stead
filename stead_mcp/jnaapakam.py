"""Client for a jñāpakaṁ memory server (the Memory API of the jnaapakam
protocol, v0.2).

jnaapakam keeps an unbounded, content-addressable store: every `store` call
hands the text to its LLM for structured extraction (summary, entities, topics,
importance) and writes the row; `recall` searches the full-text index and ranks
by relevance, recency and importance. Nothing is pruned, so this is the layer
that "grows with the household and never forgets".

The household is bound at construction — the namespace column on every row is
the household id, and no method accepts a namespace from the caller.

Design constraints, matching the rest of `stead_mcp`:
  * stdlib only (`urllib.request`); the project pins exactly two dependencies
    and nothing here needs more.
  * Fail closed. If JNAAPAKAM_URL is unset, or the server is unreachable, or it
    returns an error, the caller gets a `MemoryUnavailable` exception — the MCP
    layer turns that into a structured `ok: false` result, never a silent miss.
  * The transport is injectable so tests can assert the exact request (path,
    namespace, body) without touching the network.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:8889"
REQUEST_TIMEOUT = 10.0

Transport = Callable[[str, str, Optional[bytes], Dict[str, str]],
                     "tuple[int, Dict[str, Any]]"]


class MemoryUnavailable(Exception):
    """The memory server is absent, unreachable, or refused the request."""


def _default_transport(method: str, url: str, body: Optional[bytes],
                       headers: Dict[str, str]) -> "tuple[int, Dict[str, Any]]":
    request = urllib.request.Request(url, data=body, method=method,
                                     headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            payload = resp.read()
            return int(resp.status), json.loads(payload or b"{}")
    except urllib.error.HTTPError as exc:
        raise MemoryUnavailable(f"memory server replied {exc.code} "
                                f"{exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MemoryUnavailable(f"cannot reach memory server: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MemoryUnavailable(f"memory server returned non-JSON: {exc}") from exc


class JnaapakamMemory:
    """A household-bound handle on a jnaapakam server.

    `transport` exists for tests and defaults to a real HTTP client. It must
    return `(status_code, decoded_json)` and may raise `MemoryUnavailable` for
    transport failures.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, namespace: str = "",
                 token: str = "", transport: Optional[Transport] = None):
        self.base_url = base_url.rstrip("/")
        self.namespace = namespace
        self.token = token
        self._transport = transport or _default_transport

    @classmethod
    def from_environment(cls, household_id: str) -> "JnaapakamMemory":
        """Bind to the configured server. Raises MemoryUnavailable if unset."""
        url = os.environ.get("JNAAPAKAM_URL", "").strip()
        if not url:
            raise MemoryUnavailable(
                "JNAAPAKAM_URL is not set; no long-term memory server configured"
            )
        return cls(
            base_url=url,
            namespace=household_id,
            token=os.environ.get("JNAAPAKAM_TOKEN", "").strip(),
        )

    # -- plumbing ------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, query: Dict[str, str] = {},
                 body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        payload = json.dumps(body).encode() if body is not None else None
        headers = self._headers()
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, decoded = self._transport(method, url, payload, headers)
        if not 200 <= status < 300:
            raise MemoryUnavailable(f"memory server replied HTTP {status}")
        return decoded

    # -- the two operations the household needs ------------------------------

    def store(self, text: str, source: str = "conversation") -> Dict[str, Any]:
        """Persist a durable memory. Raises MemoryUnavailable on any failure."""
        return self._request("POST", "/ingest", body={
            "text": text,
            "source": source,
            "namespace": self.namespace,
        })

    def recall(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Return the memories most relevant to `query`, newest-first ties.

        Query text is URL-encoded, so search-engine syntax in user input is
        treated as literal content, never executed (protocol rule).
        """
        capped = max(1, min(int(limit), 8))
        decoded = self._request(
            "GET", "/search",
            query={"q": query, "limit": capped, "namespace": self.namespace},
        )
        return list(decoded.get("memories", []))