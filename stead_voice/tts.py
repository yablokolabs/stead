"""Text to speech through the Sarvam MCP server.

Not Stead's default voice. Bulbul only speaks the eleven Indic locales, so its
English is `en-IN` — Sarvam confirmed by email on 2026-08-11 that no
British-accent voice exists in the catalogue. Stead therefore speaks through
Hermes' built-in `edge` provider, and this provider stays here so switching
back is a one-line config change rather than a rewrite.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import wave
from typing import Any, Sequence

from agent.tts_provider import TTSProvider

from .mcp_client import DEFAULT_TIMEOUT, SarvamMCPClient, SarvamMCPError
from .plain_prose import sanitize_for_speech

logger = logging.getLogger(__name__)

TOOL = "sarvam_tools_tts_stream"
DEFAULT_MODEL = "bulbul:v3"
DEFAULT_LANGUAGE = "en-IN"

# Sarvam names shubh, ratan, aditya and anand as its English male voices.
DEFAULT_SPEAKER = "ratan"
MALE_ENGLISH_SPEAKERS = ("ratan", "shubh", "aditya", "anand", "kabir", "rahul", "vijay", "gokul")

# The API caps a call at roughly 500 characters; leave room for the
# preprocessor expanding "10" into "ten".
CHUNK_LIMIT = 420

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class SarvamTTSProvider(TTSProvider):
    """Synthesize Stead's replies with Sarvam's Bulbul model."""

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        speaker: str | None = None,
        language: str | None = None,
        base_path: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._command = list(command) if command else None
        self._speaker = speaker or DEFAULT_SPEAKER
        self._language = language or DEFAULT_LANGUAGE
        self._base_path = base_path
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "sarvam"

    @property
    def display_name(self) -> str:
        return "Sarvam (Bulbul)"

    def is_available(self) -> bool:
        return bool(os.environ.get("SARVAM_API_KEY", "").strip())

    def list_voices(self) -> list[dict[str, Any]]:
        return [
            {"id": speaker, "display": f"{speaker.title()} — male, Indian English",
             "language": DEFAULT_LANGUAGE, "gender": "male"}
            for speaker in MALE_ENGLISH_SPEAKERS
        ]

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": DEFAULT_MODEL, "display": "Bulbul v3", "max_text_length": CHUNK_LIMIT}]

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "paid",
            "tag": "Indian-English and Indic voices (no British accent)",
            "env_vars": [{
                "key": "SARVAM_API_KEY",
                "prompt": "Sarvam API key",
                "url": "https://dashboard.sarvam.ai",
            }],
        }

    def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        voice: str | None = None,
        model: str | None = None,
        speed: float | None = None,
        format: str = "mp3",
        **extra: Any,
    ) -> str:
        started = time.monotonic()
        logger.info("tts_started provider=sarvam chars=%d", len(text or ""))

        root = self._base_path or tempfile.gettempdir()
        os.makedirs(root, exist_ok=True)
        # Per-call directory: cleanup is one rmtree on every exit path, and two
        # household members' replies never share a directory.
        workdir = tempfile.mkdtemp(prefix="stead-tts-", dir=root)
        client = SarvamMCPClient(
            self._command,
            env={"SARVAM_MCP_BASE_PATH": workdir},
            timeout=self._timeout,
        )
        try:
            # Read the reply as plain prose, not as the markdown Telegram shows:
            # "**bold**" must not come out as "asterisk asterisk".
            spoken = sanitize_for_speech(text)
            parts = [
                client.call_tool(TOOL, {
                    "text": chunk,
                    "target_language_code": self._language,
                    "speaker": voice or self._speaker,
                }).get("file_path")
                for chunk in _chunk(spoken)
            ]
            if not parts or not all(parts):
                raise SarvamMCPError("Sarvam returned no audio")
            joined = _join_wavs([str(part) for part in parts], os.path.join(workdir, "joined.wav"))
            _encode(joined, output_path, format)
        except SarvamMCPError:
            logger.warning("voice_pipeline_failed stage=tts provider=sarvam elapsed_ms=%d",
                           _elapsed_ms(started))
            raise
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        logger.info("tts_completed provider=sarvam elapsed_ms=%d", _elapsed_ms(started))
        return output_path


def _chunk(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Split on sentence boundaries so a long reply stays synthesizable."""
    text = (text or "").strip()
    if not text:
        raise SarvamMCPError("nothing to synthesize")
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > limit:  # a single runaway sentence
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:limit])
            sentence = sentence[limit:]
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= limit:
            current = f"{current} {sentence}"
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _join_wavs(paths: list[str], destination: str) -> str:
    """Concatenate Sarvam's WAV chunks; they share rate, width and channels."""
    if len(paths) == 1:
        return paths[0]
    with wave.open(paths[0], "rb") as first:
        params = first.getparams()
    with wave.open(destination, "wb") as out:
        out.setparams(params)
        for path in paths:
            with wave.open(path, "rb") as part:
                out.writeframes(part.readframes(part.getnframes()))
    return destination


def _encode(source: str, destination: str, fmt: str) -> None:
    """Re-encode to the format Hermes asked for; WAV needs no ffmpeg."""
    if (fmt or "").lower() == "wav":
        shutil.copyfile(source, destination)
        return
    codec = ["-c:a", "libopus"] if (fmt or "").lower() in {"ogg", "opus"} else []
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", source, *codec, destination],
            check=True, capture_output=True, timeout=120,
        )
    except FileNotFoundError as exc:
        raise SarvamMCPError("ffmpeg is required to convert Sarvam audio") from exc
    except subprocess.SubprocessError as exc:
        raise SarvamMCPError(f"could not convert Sarvam audio to {fmt}: {exc}") from exc


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
