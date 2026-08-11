"""Speech to text through the Sarvam MCP server."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Sequence

from agent.transcription_provider import TranscriptionProvider

from .mcp_client import DEFAULT_TIMEOUT, SarvamMCPClient, SarvamMCPError

logger = logging.getLogger(__name__)

TOOL = "sarvam_tools_stt_transcribe"
DEFAULT_MODEL = "saaras:v3"

# Saaras only speaks Indian locales. Every English variant has to arrive as
# `en-IN` — the tool validates `language_code` against a closed enum, so a
# well-formed `en` or `en-GB` is rejected outright rather than approximated.
ENGLISH = "en-IN"
AUTO_DETECT = "unknown"


class SarvamTranscriptionProvider(TranscriptionProvider):
    """Transcribe household voice notes with Sarvam's Saaras model.

    Sarvam accepts Ogg/Opus, which is what Telegram delivers, so a voice note
    goes to the API byte-for-byte as recorded.
    """

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        language: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._command = list(command) if command else None
        self._language = language
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "sarvam"

    @property
    def display_name(self) -> str:
        return "Sarvam (Saaras)"

    def is_available(self) -> bool:
        return bool(os.environ.get("SARVAM_API_KEY", "").strip())

    def list_models(self) -> list[dict[str, Any]]:
        return [{
            "id": DEFAULT_MODEL,
            "display": "Saaras v3",
            "languages": ["en-IN", "hi-IN", "bn-IN", "ta-IN", "te-IN", "gu-IN",
                          "kn-IN", "ml-IN", "mr-IN", "pa-IN", "od-IN"],
        }]

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "paid",
            "tag": "Indic + Indian-English speech recognition",
            "env_vars": [{
                "key": "SARVAM_API_KEY",
                "prompt": "Sarvam API key",
                "url": "https://dashboard.sarvam.ai",
            }],
        }

    def transcribe(
        self,
        file_path: str,
        *,
        model: str | None = None,
        language: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        started = time.monotonic()
        logger.info("stt_started provider=sarvam")
        try:
            result = self._client().call_tool(TOOL, {
                "audio_path": file_path,
                "language_code": _language_code(language or self._language),
                "model": model or DEFAULT_MODEL,
                "mode": "transcribe",
            })
        except SarvamMCPError as exc:
            logger.warning(
                "voice_pipeline_failed stage=stt provider=sarvam elapsed_ms=%d reason=%s",
                _elapsed_ms(started), exc,
            )
            return {"success": False, "transcript": "", "error": str(exc), "provider": self.name}

        transcript = str(result.get("transcript") or "").strip()
        # Sarvam answers 200 with an empty transcript for silence or noise.
        # Reported as a failure so the gateway asks the user to try again
        # instead of handing Stead an empty turn.
        if not transcript:
            logger.info("stt_completed provider=sarvam elapsed_ms=%d empty=true", _elapsed_ms(started))
            return {
                "success": False,
                "transcript": "",
                "error": "Sarvam returned an empty transcript.",
                "provider": self.name,
            }

        logger.info(
            "stt_completed provider=sarvam elapsed_ms=%d chars=%d",
            _elapsed_ms(started), len(transcript),
        )
        return {"success": True, "transcript": transcript, "provider": self.name}

    def _client(self) -> SarvamMCPClient:
        return SarvamMCPClient(self._command, timeout=self._timeout)


def _language_code(requested: str | None) -> str:
    """Map a BCP-47 hint onto a code Saaras will accept."""
    if not requested:
        return ENGLISH
    code = requested.strip().replace("_", "-")
    if not code or code.lower() in {"auto", AUTO_DETECT}:
        return AUTO_DETECT
    if code.lower().startswith("en"):
        return ENGLISH
    if "-" in code:
        return code
    return f"{code.lower()}-IN"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
