"""Resolving config/digest.yaml into the objects the renderer walks.

config.py checks the file's shape; this checks the meanings only the
renderer knows — what a style is, and what a message limit can be.
"""

from __future__ import annotations

import datetime as dt

import pytest

from healthcare_newsfeed.config import ConfigError, load_digest_template
from healthcare_newsfeed.digest.template import (
    MESSAGE_LIMIT,
    MIN_MESSAGE_CHARS,
    STYLE_LENGTHS,
    resolve,
)


def template(**issue) -> dict:
    return {
        "issue": {"title": "This Week in Medicine", "timezone": "Asia/Singapore", **issue},
        "sections": [
            {"key": "story_of_week", "heading": "🔬 Story", "min": 1, "max": 1, "style": "long"},
            {"key": "also_reading", "heading": "📌 Also", "min": 3, "max": 6, "style": "headline"},
        ],
    }


def test_the_shipped_template_resolves():
    """The config in the repository is the one that has to work."""
    spec = resolve(load_digest_template("config/digest.yaml"))

    assert spec.title == "This Week in Medicine"
    assert [section.key for section in spec.sections][0] == "story_of_week"
    assert spec.section("explainer").style == "extract"
    assert spec.section("also_reading").body_chars == 0


def test_style_decides_how_much_body_a_section_asks_for():
    spec = resolve(template())

    assert spec.section("story_of_week").body_chars == STYLE_LENGTHS["long"]
    assert spec.section("also_reading").body_chars == 0      # headline: title + link


def test_an_unknown_style_is_an_error_not_a_default():
    """A typo would otherwise silently reformat a whole section."""
    document = template()
    document["sections"][0]["style"] = "extended"

    with pytest.raises(ConfigError, match="unknown style"):
        resolve(document)


def test_a_section_without_a_style_gets_the_default():
    document = template()
    del document["sections"][0]["style"]

    assert resolve(document).section("story_of_week").style == "short"


def test_a_missing_section_key_resolves_to_none():
    assert resolve(template()).section("ethics") is None


def test_the_message_limit_defaults_to_telegrams():
    assert resolve(template()).max_message_chars == MESSAGE_LIMIT


def test_a_smaller_message_limit_is_honoured():
    assert resolve(template(max_message_chars=1000)).max_message_chars == 1000


def test_a_limit_above_telegrams_is_refused_rather_than_clamped():
    """Clamping would leave the file reading as though 8,000 chars go out."""
    with pytest.raises(ConfigError, match="exceeds Telegram"):
        resolve(template(max_message_chars=8000))


def test_a_limit_too_small_for_one_item_is_refused():
    with pytest.raises(ConfigError, match="no room"):
        resolve(template(max_message_chars=MIN_MESSAGE_CHARS - 1))


def test_dates_read_in_the_issues_timezone():
    """A channel published at 19:00 SGT should not be headed with yesterday."""
    spec = resolve(template())
    midnight_sgt = dt.datetime(2026, 8, 24, 16, 0, tzinfo=dt.UTC)   # 2026-08-25 00:00 +08

    assert midnight_sgt.astimezone(spec.tz).date() == dt.date(2026, 8, 25)


def test_a_misspelled_timezone_is_an_error():
    with pytest.raises(ConfigError, match="unknown timezone"):
        resolve(template(timezone="Asia/Singapor"))
