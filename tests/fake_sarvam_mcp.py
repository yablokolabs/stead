#!/usr/bin/env python3
"""A stand-in for the ``sarvam-mcp`` server, speaking real JSON-RPC over stdio.

This is a fake *process*, not a mock object: the code under test spawns it with
the same argv/stdio contract it uses for the real server, so framing, protocol
handshake, notification interleaving and timeout handling all execute for real.

Every response shape here was copied from a live ``uvx sarvam-mcp`` session
against the real API, including fields the tests never read — a partial shape
would pass here and fail against the real server.

Usage::

    python fake_sarvam_mcp.py <scenario>

Scenarios:
    ok          successful transcription / synthesis
    tool_error  isError envelopes, including the real bulbul:v3 rejection of
                pitch/loudness that makes ``sarvam_tools_tts_speak`` unusable
    hang        accepts the handshake, then never answers a tools/call
    noisy       emits log notifications before the real response
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import wave

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "ok"


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _notify(level: str, msg: str) -> None:
    _send({
        "jsonrpc": "2.0",
        "method": "notifications/message",
        "params": {"level": level, "data": {"msg": msg, "extra": None}},
    })


def _observability() -> dict:
    return {
        "latency_ms": 1159.1,
        "upstream_calls": 1,
        "request_ids": ["20260811_c300c669-cf0c-481a-be28-c8743e4f8dad"],
    }


def _ok(structured: dict) -> dict:
    """Mirror the real server: JSON-as-text in content, plus structuredContent."""
    return {
        "content": [{"type": "text", "text": json.dumps(structured)}],
        "structuredContent": structured,
        "isError": False,
    }


def _err(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _write_wav(path: str, seconds: float = 0.2) -> int:
    """Write a real 24 kHz mono PCM WAV, matching bulbul:v3 output."""
    rate = 24000
    frames = int(rate * seconds)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", 0) for _ in range(frames)))
    return os.path.getsize(path)


def _handle_stt(args: dict) -> dict:
    audio_path = args.get("audio_path") or ""
    if not audio_path or not os.path.exists(audio_path):
        return _err(f"Error calling tool 'sarvam_tools_stt_transcribe': no such file {audio_path!r}")
    # Echo the extension actually received so a caller that silently transcoded
    # its input is visible to the test rather than indistinguishable.
    extension = os.path.splitext(audio_path)[1].lstrip(".").lower()
    return _ok({
        "transcript": f"heard a {extension} file",
        "language_code": args.get("language_code") or "unknown",
        "language_probability": None,
        "diarized_transcript": None,
        "timestamps": None,
        "observability": _observability(),
    })


def _handle_tts_stream(args: dict) -> dict:
    base = os.environ.get("SARVAM_MCP_BASE_PATH") or "/tmp"
    os.makedirs(base, exist_ok=True)
    out = os.path.join(base, f"sarvam-tts-stream-{os.getpid()}-{int(time.time()*1000)}.wav")
    size = _write_wav(out)
    return _ok({
        "file_path": out,
        "resource_uri": None,
        "size_bytes": size,
        "completed_at": time.time(),
        "observability": _observability(),
    })


def _dispatch(name: str, args: dict) -> dict:
    if SCENARIO == "tool_error":
        if name == "sarvam_tools_tts_speak":
            # Verbatim from the live API — this is why tts_speak is unusable.
            return _err(
                "Error calling tool 'sarvam_tools_tts_speak': Pitch and loudness "
                "parameters are currently not supported for the Bulbul V3 model. "
                "Please do not pass these values."
            )
        return _err(f"Error calling tool {name!r}: upstream refused the request")

    if name == "sarvam_tools_stt_transcribe":
        return _handle_stt(args)
    if name == "sarvam_tools_tts_stream":
        return _handle_tts_stream(args)
    if name == "sarvam_tools_tts_speak":
        # Even on the happy path the real server rejects this call.
        return _err(
            "Error calling tool 'sarvam_tools_tts_speak': Pitch and loudness "
            "parameters are currently not supported for the Bulbul V3 model. "
            "Please do not pass these values."
        )
    return _err(f"Unknown tool {name!r}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method")
        message_id = message.get("id")

        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "sarvam-mcp", "version": "3.4.7"},
                },
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/call":
            if SCENARIO == "hang":
                # Stay alive and silent: exercises the caller's timeout, not EOF.
                while True:
                    time.sleep(3600)
            if SCENARIO == "noisy":
                _notify("warning", "WebSocket streaming failed (403); falling back to REST.")
                _notify("info", "using REST transport")
            params = message.get("params") or {}
            _send({
                "jsonrpc": "2.0",
                "id": message_id,
                "result": _dispatch(params.get("name", ""), params.get("arguments") or {}),
            })
        elif message_id is not None:
            _send({
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            })


if __name__ == "__main__":
    main()
