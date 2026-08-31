"""Plain-prose preprocessing for anything a synthesizer will read aloud.

A reply that reads well in Telegram may still be full of formatting symbols —
``**bold**``, ``*`` bullets, tables — and the speech layer would read those
symbols out loud: "*" becomes "asterisk", "24°C" becomes something stilted.
``sanitize_for_speech`` reduces such text to the prose a voice can read
naturally.

SOUL.md asks Stead to write plain prose in the first place; this is the
belt-and-braces pass for whichever synthesizer is selected (Sarvam's Bulbul
fallback today, and the same function is trivially reusable for a wrapper
around Hermes' built-in Edge provider if one is ever added here). The module
is deliberately free of the `agent.*` imports so these tests run on any
machine, with or without the Hermes source tree.
"""

from __future__ import annotations

import re

_SYMBOLS_FOR_SPEECH = re.compile(r"[*#`_~|\[\]()\\]")
_DASH_BULLET = re.compile(r"(?m)^\s*[-–—]\s+")
_NUMBERED_BULLET = re.compile(r"(?m)^\s*\d+[.)]\s+")
_RUN_WHITESPACE = re.compile(r"\s+")
_DEGREE_SIGN = "\u00b0"


def sanitize_for_speech(text: str) -> str:
    """Reduce reply text to the plain prose a synthesizer should read aloud.

    Drops markdown symbols (``**`` would otherwise be spoken as
    "asterisk asterisk"), turns list bullets into prose, converts the degree
    sign into the word "degrees" (so "24°C" reads naturally as
    "twenty-four degrees C"), and collapses whitespace and newlines.
    """
    if not text:
        return ""
    cleaned = text.replace(_DEGREE_SIGN, " degrees ")
    cleaned = _DASH_BULLET.sub("", cleaned)
    cleaned = _NUMBERED_BULLET.sub("", cleaned)
    # Remove symbols outright: replacing them with a space would leave a gap
    # before punctuation ("7pm ."), and words rarely need a separator here.
    cleaned = _SYMBOLS_FOR_SPEECH.sub("", cleaned)
    return _RUN_WHITESPACE.sub(" ", cleaned).strip()