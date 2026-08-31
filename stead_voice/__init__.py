"""Sarvam speech providers for Stead.

Voice is an I/O modality, not a second agent. A Telegram voice note is
transcribed here and handed to the same Hermes turn a typed message would
have produced, so identity, household context, memory, tools and the
confirmation rules are shared by construction — there is nothing in this
package that knows what a calendar or a reminder is.

Registering against Hermes' provider ABCs (rather than reaching into the
gateway) is what keeps the seam replaceable: swapping in realtime streaming
later means writing another provider, not touching Stead.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)

try:
    from .stt import SarvamTranscriptionProvider
    from .tts import SarvamTTSProvider
    _PROVIDERS_AVAILABLE = True
except ModuleNotFoundError:  # the agent.* ABCs only exist inside a Hermes install
    logger.debug("stead_voice: Hermes provider ABCs not importable; providers disabled")
    SarvamTranscriptionProvider = None  # type: ignore[assignment]
    SarvamTTSProvider = None            # type: ignore[assignment]
    _PROVIDERS_AVAILABLE = False

__all__ = ["SarvamTranscriptionProvider", "SarvamTTSProvider", "register"]


def register(ctx: Any) -> None:
    """Register Sarvam under the name ``sarvam`` for both `stt` and `tts`."""
    if not _PROVIDERS_AVAILABLE:
        raise RuntimeError(
            "stead_voice: Hermes provider ABCs are not importable here, cannot register"
        )
    stt_config = _section("stt")
    tts_config = _section("tts")

    ctx.register_transcription_provider(SarvamTranscriptionProvider(
        command=stt_config.get("command"),
        language=stt_config.get("language"),
        **_timeout(stt_config),
    ))
    ctx.register_tts_provider(SarvamTTSProvider(
        command=tts_config.get("command"),
        speaker=tts_config.get("speaker"),
        language=tts_config.get("language"),
        base_path=tts_config.get("base_path"),
        **_timeout(tts_config),
    ))


def _section(kind: str) -> Mapping[str, Any]:
    """Read ``<kind>.sarvam`` from config.yaml, tolerating its absence."""
    try:
        from hermes_cli.config import load_config

        section = (load_config().get(kind) or {}).get("sarvam")
    except Exception as exc:  # noqa: BLE001 — config is optional, defaults are fine
        logger.debug("stead_voice: no %s.sarvam config (%s)", kind, exc)
        return {}
    return section if isinstance(section, dict) else {}


def _timeout(config: Mapping[str, Any]) -> dict[str, float]:
    try:
        return {"timeout": float(config["timeout"])}
    except (KeyError, TypeError, ValueError):
        return {}
