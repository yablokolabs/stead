"""Shared helpers for driving the MCP tool surface from tests."""
import asyncio


def run(coro):
    return asyncio.run(coro)


def call(server, tool, /, **kwargs):
    """Invoke an MCP tool and return its structured payload.

    `tool` is positional-only so a tool's own `name` argument cannot collide
    with this helper's parameters.
    """
    result = run(server.call_tool(tool, kwargs))
    # FastMCP returns (content_blocks, structured_result)
    payload = result[1] if isinstance(result, tuple) else result
    if isinstance(payload, dict) and "result" in payload and len(payload) == 1:
        return payload["result"]
    return payload
