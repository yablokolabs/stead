"""Sarvam speech providers — behaviour at the process boundary.

These tests drive the providers against `fake_sarvam_mcp.py`, a real child
process speaking real JSON-RPC. Nothing about the client is stubbed: argv,
stdio framing, the initialize handshake, notification interleaving and process
teardown all run for real, so a protocol bug fails here rather than in Telegram.

The one thing not exercised is Sarvam's own API. `test_sarvam_live.py` covers
that, marked `integration` and excluded from the default run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from stead_voice.stt import SarvamTranscriptionProvider
from stead_voice.tts import SarvamTTSProvider

FAKE_SERVER = Path(__file__).parent / "fake_sarvam_mcp.py"


def fake_command(scenario: str = "ok") -> list[str]:
    return [sys.executable, str(FAKE_SERVER), scenario]


@pytest.fixture()
def voice_note(tmp_path: Path) -> Path:
    """A real Ogg/Opus file, the format Telegram delivers voice notes in."""
    path = tmp_path / "voice.ogg"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:a", "libopus", str(path)],
        check=True,
    )
    return path


def stt(scenario: str = "ok", **kwargs) -> SarvamTranscriptionProvider:
    return SarvamTranscriptionProvider(command=fake_command(scenario), **kwargs)


def tts(scenario: str = "ok", **kwargs) -> SarvamTTSProvider:
    return SarvamTTSProvider(command=fake_command(scenario), **kwargs)


# --------------------------------------------------------------------------
# Speech to text
# --------------------------------------------------------------------------

def test_transcribes_a_voice_note(voice_note: Path):
    result = stt().transcribe(str(voice_note))

    assert result["success"] is True
    assert result["transcript"]
    assert result["provider"] == "sarvam"


def test_sends_telegram_ogg_to_sarvam_without_transcoding(voice_note: Path):
    """Sarvam accepts Ogg/Opus directly, so a transcode would be wasted latency.

    The fake reports the extension it was handed, so re-encoding to wav on the
    way in shows up here instead of silently costing a second per voice note.
    """
    result = stt().transcribe(str(voice_note))

    assert result["transcript"] == "heard a ogg file"


def test_reports_a_refused_transcription_without_raising(voice_note: Path):
    """The provider contract is an error envelope; a raise breaks the gateway."""
    result = stt("tool_error").transcribe(str(voice_note))

    assert result["success"] is False
    assert result["transcript"] == ""
    assert result["error"]
    assert result["provider"] == "sarvam"


def test_reports_a_missing_audio_file_without_raising(tmp_path: Path):
    result = stt().transcribe(str(tmp_path / "gone.ogg"))

    assert result["success"] is False
    assert result["transcript"] == ""


def test_gives_up_on_an_unresponsive_server_and_leaves_no_process(voice_note: Path):
    """A hung Sarvam call must not wedge the turn or leak a child process."""
    provider = stt("hang", timeout=2.0)

    result = provider.transcribe(str(voice_note))

    assert result["success"] is False
    assert result["error"]
    assert _orphan_fake_servers() == [], "fake server survived the timeout"


def test_transcribes_despite_server_log_notifications(voice_note: Path):
    """The real server emits notifications mid-call (WebSocket 403 -> REST)."""
    result = stt("noisy").transcribe(str(voice_note))

    assert result["success"] is True
    assert result["transcript"] == "heard a ogg file"


def test_is_unavailable_without_an_api_key(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)

    assert stt().is_available() is False


def test_is_available_with_an_api_key(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")

    assert stt().is_available() is True


# --------------------------------------------------------------------------
# Text to speech
# --------------------------------------------------------------------------

def test_synthesizes_playable_audio(tmp_path: Path):
    out = tmp_path / "reply.wav"

    returned = tts().synthesize("Dinner is at seven.", str(out), format="wav")

    assert returned == str(out)
    assert out.exists()
    with wave.open(str(out)) as handle:
        assert handle.getnframes() > 0


def test_synthesizes_despite_the_broken_speak_tool(tmp_path: Path):
    """`sarvam_tools_tts_speak` always sends pitch/loudness, which bulbul:v3
    rejects. Synthesis has to work anyway, via the tool that does function."""
    out = tmp_path / "reply.wav"

    tts().synthesize("Dinner is at seven.", str(out), format="wav")

    assert out.exists()


def test_converts_to_the_requested_format(tmp_path: Path):
    """Sarvam only returns WAV; Telegram voice bubbles need Ogg/Opus."""
    out = tmp_path / "reply.ogg"

    tts().synthesize("Dinner is at seven.", str(out), format="ogg")

    assert _codec_of(out) == "opus"


def test_raises_when_sarvam_refuses_synthesis(tmp_path: Path):
    """TTS signals failure by raising — the dispatcher turns that into the
    error envelope, and the gateway falls back to sending text."""
    with pytest.raises(Exception):
        tts("tool_error").synthesize("Dinner is at seven.", str(tmp_path / "r.wav"))


def test_leaves_no_audio_behind_after_synthesis(tmp_path: Path):
    """Sarvam writes its own file next to ours; household audio must not pile up."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out = tmp_path / "reply.wav"

    tts(base_path=str(scratch)).synthesize("Dinner is at seven.", str(out), format="wav")

    assert list(scratch.iterdir()) == []


def test_leaves_no_audio_behind_after_a_failed_synthesis(tmp_path: Path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(Exception):
        tts("tool_error", base_path=str(scratch)).synthesize(
            "Dinner is at seven.", str(tmp_path / "r.wav")
        )

    assert list(scratch.iterdir()) == []


# --------------------------------------------------------------------------
# Plugin registration
# --------------------------------------------------------------------------

def test_registers_both_providers_with_hermes():
    """Hermes routes `stt.provider` / `tts.provider` by registered name."""
    pytest.importorskip("agent.transcription_registry")

    import stead_voice
    from agent import transcription_registry, tts_registry

    registered = []

    class Context:
        def register_transcription_provider(self, provider):
            registered.append(("stt", provider))

        def register_tts_provider(self, provider):
            registered.append(("tts", provider))

    stead_voice.register(Context())

    assert sorted(kind for kind, _ in registered) == ["stt", "tts"]
    assert {provider.name for _, provider in registered} == {"sarvam"}
    # Hermes rejects anything that isn't a real subclass at registration time.
    assert transcription_registry is not None and tts_registry is not None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _codec_of(path: Path) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _orphan_fake_servers() -> list[str]:
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True)
    return [
        line for line in out.stdout.splitlines()
        if str(FAKE_SERVER) in line and "defunct" not in line
    ]
