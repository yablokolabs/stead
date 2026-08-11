"""A small stdio MCP client for the Sarvam MCP server.

One short-lived server process per call. Measured cold start for
``uvx sarvam-mcp`` is ~0.6 s against ~1.2 s for a transcription, and a voice
turn makes at most two calls, so a persistent connection would buy under a
second in exchange for reconnect logic, health checks and a process to leak.
Household audio also stays isolated per turn this way.

Sarvam's MCP is not reachable through the agent's own MCP client here: that one
is owned by the async agent loop, while Hermes calls speech providers
synchronously from a worker thread.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence

DEFAULT_COMMAND: tuple[str, ...] = ("uvx", "sarvam-mcp")
DEFAULT_TIMEOUT = 120.0
_PROTOCOL_VERSION = "2024-11-05"
_STDERR_TAIL_LINES = 5


class SarvamMCPError(RuntimeError):
    """The Sarvam MCP server could not service a call."""


class SarvamMCPClient:
    """Call one Sarvam MCP tool over stdio, then tear the server down."""

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._command = list(command) if command else list(DEFAULT_COMMAND)
        self._env_overrides = dict(env or {})
        self._timeout = float(timeout)

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Run *name* and return its ``structuredContent``.

        Raises :class:`SarvamMCPError` if the server cannot be started, breaks
        protocol, reports a tool error, or does not answer within the timeout.
        """
        try:
            process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self._environment(),
                # Own process group: `uvx` execs a child interpreter, so killing
                # only the parent on timeout would strand the real server.
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            raise SarvamMCPError(
                f"could not start the Sarvam MCP server ({self._command[0]}): {exc}"
            ) from exc

        messages: queue.Queue = queue.Queue()
        stderr_tail: list[str] = []
        reader = threading.Thread(target=self._read_stdout, args=(process, messages), daemon=True)
        drainer = threading.Thread(target=self._drain_stderr, args=(process, stderr_tail), daemon=True)
        reader.start()
        drainer.start()

        try:
            self._send(process, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "stead-voice", "version": "1"},
                },
            })
            self._await(messages, 1, stderr_tail)
            self._send(process, {
                "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
            })
            self._send(process, {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments)},
            })
            response = self._await(messages, 2, stderr_tail)
        finally:
            self._terminate(process, (reader, drainer))

        return self._unwrap(name, response)

    # -- protocol ----------------------------------------------------------

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self._env_overrides)
        return env

    @staticmethod
    def _send(process: subprocess.Popen, message: Mapping[str, Any]) -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, ValueError, AssertionError) as exc:
            raise SarvamMCPError(f"the Sarvam MCP server closed its input: {exc}") from exc

    @staticmethod
    def _read_stdout(process: subprocess.Popen, messages: queue.Queue) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                continue  # the server also prints non-JSON banners
        messages.put(None)  # EOF

    @staticmethod
    def _drain_stderr(process: subprocess.Popen, tail: list[str]) -> None:
        # Drained rather than DEVNULL'd: a full stderr pipe would deadlock the
        # server, and the last few lines explain startup failures.
        assert process.stderr is not None
        for line in process.stderr:
            tail.append(line.rstrip())
            del tail[:-_STDERR_TAIL_LINES]

    def _await(self, messages: queue.Queue, message_id: int, stderr_tail: list[str]) -> dict[str, Any]:
        """Return the response to *message_id*, skipping notifications."""
        remaining = self._timeout
        while remaining > 0:
            started = time.monotonic()
            try:
                message = messages.get(timeout=remaining)
            except queue.Empty:
                break
            remaining -= time.monotonic() - started
            if message is None:
                detail = "; ".join(stderr_tail) or "no output"
                raise SarvamMCPError(f"the Sarvam MCP server exited early ({detail})")
            if message.get("id") == message_id:
                if "error" in message:
                    raise SarvamMCPError(str(message["error"].get("message", message["error"])))
                return message.get("result") or {}
            # Anything else is a notification (the server logs its WebSocket
            # fallback mid-call) — keep waiting for the answer.
        raise SarvamMCPError(f"the Sarvam MCP server did not respond within {self._timeout:.0f}s")

    @staticmethod
    def _unwrap(name: str, result: Mapping[str, Any]) -> dict[str, Any]:
        if result.get("isError"):
            raise SarvamMCPError(_first_text(result) or f"{name} failed")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        text = _first_text(result)
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(parsed, dict):
                    return parsed
        raise SarvamMCPError(f"{name} returned no usable content")

    @staticmethod
    def _terminate(process: subprocess.Popen, readers: tuple[threading.Thread, ...]) -> None:
        """Kill the server, then close its pipes — strictly in that order.

        Closing a pipe while a reader thread is blocked on it deadlocks: both
        sides want the same buffer lock. Killing first ends the read at EOF.
        """
        if process.poll() is None:
            _signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_group(process, signal.SIGKILL)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for reader in readers:
            reader.join(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    """Signal the whole group: `uvx` execs the real server as a child."""
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass


def _first_text(result: Mapping[str, Any]) -> str:
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")
    return ""
