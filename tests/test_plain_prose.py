"""Plain-prose preprocessing shared by the speech providers.

Hermes-free on purpose: ``stead_voice/plain_prose.py`` imports nothing from
the ``agent.*`` tree, so these tests run on a machine without a Hermes source
checkout — unlike ``test_sarvam_voice.py``, which the suite skips then.
"""

from __future__ import annotations

from stead_voice.plain_prose import sanitize_for_speech


def test_strips_markdown_symbols():
    cleaned = sanitize_for_speech("**Dinner** is at *7pm*. Don't forget the milk.")

    assert "*" not in cleaned
    assert cleaned == "Dinner is at 7pm. Don't forget the milk."


def test_flattens_dash_bullets_into_prose():
    cleaned = sanitize_for_speech("- buy milk\n- buy eggs\n- buy rice")

    assert cleaned == "buy milk buy eggs buy rice"


def test_flattens_numbered_bullets_into_prose():
    cleaned = sanitize_for_speech("1. buy milk\n2. buy eggs\n3. buy rice")

    assert cleaned == "buy milk buy eggs buy rice"


def test_turns_degree_sign_into_the_word():
    cleaned = sanitize_for_speech("It's 24°C outside, lovely.")

    assert cleaned == "It's 24 degrees C outside, lovely."


def test_collapses_newlines_and_repeated_spaces():
    cleaned = sanitize_for_speech("One  sentence.\n\n\nAnother,   later.")

    assert cleaned == "One sentence. Another, later."


def test_empty_input_stays_empty():
    assert sanitize_for_speech("") == ""