"""Round trip against the real Sarvam API.

Excluded from the default run — needs `SARVAM_API_KEY`, the network, and about
ten seconds. Run it after changing anything about the request shape::

    pytest tests/test_sarvam_live.py -m integration

It exists because the fake server can only prove Stead speaks MCP correctly.
Whether Sarvam accepts these particular arguments is a question only Sarvam can
answer, and it has already changed once: `sarvam_tools_tts_speak` sends pitch
and loudness that bulbul:v3 now rejects.
"""

from __future__ import annotations

import os
import subprocess
import wave
from pathlib import Path

import pytest

from stead_voice.stt import SarvamTranscriptionProvider
from stead_voice.tts import SarvamTTSProvider

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def allow_network(monkeypatch):
    """Undo the suite-wide network guard; this test is the exception."""
    import socket
    monkeypatch.undo()
    assert socket.getaddrinfo


@pytest.fixture(autouse=True)
def require_key():
    if not os.environ.get("SARVAM_API_KEY", "").strip():
        pytest.skip("SARVAM_API_KEY is not set")


def test_speaks_then_understands_itself(tmp_path: Path):
    """Synthesize a sentence, hand the audio back to Sarvam, read it again."""
    spoken = tmp_path / "spoken.wav"
    SarvamTTSProvider().synthesize(
        "Dinner is at seven o'clock.", str(spoken), format="wav",
    )
    with wave.open(str(spoken)) as handle:
        assert handle.getnframes() > 0

    # Re-encode to what Telegram actually delivers, then transcribe that.
    voice_note = tmp_path / "voice.ogg"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(spoken),
         "-c:a", "libopus", str(voice_note)],
        check=True,
    )

    result = SarvamTranscriptionProvider().transcribe(str(voice_note))

    assert result["success"] is True, result.get("error")
    assert "dinner" in result["transcript"].lower()


def test_long_reply_is_synthesized_in_full(tmp_path: Path):
    """Bulbul caps a call near 500 characters; a real reply can exceed that."""
    reply = " ".join(
        f"Item number {n} is scheduled for the afternoon." for n in range(1, 25)
    )
    assert len(reply) > 500
    out = tmp_path / "long.wav"

    SarvamTTSProvider().synthesize(reply, str(out), format="wav")

    with wave.open(str(out)) as handle:
        seconds = handle.getnframes() / handle.getframerate()
    assert seconds > 20, f"only {seconds:.1f}s of audio for a {len(reply)}-char reply"
