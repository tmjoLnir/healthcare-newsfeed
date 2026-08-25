"""Ranking.

Each signal is tested on its own, because when an issue carries the wrong
story the question is always which signal put it there. The combined tests
check the parts add up, not any particular ordering of real headlines.
"""

from __future__ import annotations

import datetime as dt

import pytest

from healthcare_newsfeed.pipeline.score import (
    corroboration,
    durability,
    readability,
    recency,
    score,
    topic_fit,
)

NOW = dt.datetime(2026, 8, 25, tzinfo=dt.UTC)


@pytest.fixture
def sources(make_source):
    return {
        "bbc_health": make_source("bbc_health", weight=0.7),
        "statnews": make_source("statnews", weight=1.0),
        "jme_ethics": make_source("jme_ethics", weight=1.0),
    }


# --- topic fit ---------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Assisted dying and the limits of autonomy", 1.00),        # ethics
    ("NHS waiting list reform stalls again", 0.85),             # policy
    ("Measles outbreak spreads across the region", 0.85),       # global health
    ("Phase 3 trial of a new malaria vaccine", 0.85),           # strongest wins
    ("Can AI read a chest X-ray better than a registrar?", 0.70),
])
def test_a_headline_on_theme_scores_its_theme(make_item, title, expected):
    assert topic_fit(make_item(title)) == pytest.approx(expected, abs=0.11)


def test_an_off_topic_headline_scores_nothing(make_item):
    assert topic_fit(make_item("Local hospital opens new car park")) == 0.0


def test_the_title_counts_for_more_than_the_body(make_item):
    """Every long article mentions everything; the headline says what it is."""
    headline = make_item("The ethics of assisted dying")
    buried = make_item("A car park opens", summary="Later the piece turns to ethics.")

    assert topic_fit(headline) > topic_fit(buried) > 0


def test_a_match_beyond_the_lede_is_not_counted(make_item):
    """A 6,000-character explainer name-checks every subject going."""
    padded = make_item("A car park opens", summary="filler " * 200 + "assisted dying")

    assert topic_fit(padded) == 0.0


def test_the_sources_own_categories_count(make_item):
    """KFF tags its items; WHO gives a NewsType."""
    tagged = make_item("A quiet week", categories=("Health Policy",))

    assert topic_fit(tagged) > 0


def test_breadth_adds_a_little_but_focus_still_wins(make_item):
    focused = make_item("Assisted dying and the limits of autonomy")
    scattered = make_item("Trial of a new drug and an algorithm for triage")

    assert focused.title and topic_fit(focused) >= topic_fit(scattered)
    assert topic_fit(scattered) > topic_fit(make_item("Trial of a new drug"))


# --- durability --------------------------------------------------------------

def test_an_explainer_holds_its_value(make_item):
    assert durability(make_item("Why long gaps between meals may not be healthy")) > 0


def test_breaking_news_is_penalised(make_item):
    assert durability(make_item("Minister announces funding review")) < 0


def test_long_form_counts_as_analysis(make_item):
    assert durability(make_item("A study", summary="x" * 2000)) > durability(make_item("A study"))


def test_durability_stays_in_range(make_item):
    assert durability(make_item("Why the latest update explained everything")) <= 1.0
    assert durability(make_item("Minister announces update, warns of latest")) >= -0.5


# --- corroboration -----------------------------------------------------------

def test_corroboration_needs_more_than_one_source():
    assert corroboration(1) == 0.0
    assert corroboration(2) > 0
    assert corroboration(5) == 1.0


def test_one_publisher_repeating_itself_is_not_corroboration(make_item, sources):
    """BBC files seven episodes of one column under an identical title.

    Counting cluster members would rank that boilerplate above a story two
    independent outlets both thought mattered.
    """
    repeats = [make_item("Inside Health", source="bbc_health") for _ in range(7)]
    for item in repeats:
        item.cluster_id = "shared"

    score(repeats, sources, now=NOW)

    assert corroboration(len({item.source_key for item in repeats})) == 0.0


# --- readability -------------------------------------------------------------

def test_a_plain_headline_beats_a_citation_line(make_item):
    plain = make_item("New pill cuts stroke risk", summary="A summary")
    citation = make_item(
        "Post-Exposure Prophylaxis with a monoclonal antibody cocktail Following "
        "Bundibugyo Ebolavirus Exposure in Adults and Children", summary="A summary")

    assert readability(plain) > readability(citation)


def test_an_item_with_no_summary_is_harder_to_follow(make_item):
    """NEJM ships an 87-character citation where the description belongs."""
    assert readability(make_item("A study", summary="")) < \
        readability(make_item("A study", summary="What it found"))


def test_an_empty_title_scores_nothing(make_item):
    assert readability(make_item("")) == 0.0


# --- recency -----------------------------------------------------------------

def test_this_week_is_fresh(make_item):
    assert recency(make_item("A study", published=NOW - dt.timedelta(days=3)), NOW) == 1.0


def test_an_archived_report_decays(make_item):
    """WHO's outbreak collection arrives months deep on the first poll."""
    middling = recency(make_item("A study", published=NOW - dt.timedelta(days=30)), NOW)
    ancient = recency(make_item("A study", published=NOW - dt.timedelta(days=90)), NOW)

    assert 0 < middling < 1.0
    assert ancient == 0.0


def test_an_undated_item_falls_back_to_when_it_was_stored(make_item):
    """WHO news items carry no publication date."""
    item = make_item("A study", published=None, first_seen=NOW - dt.timedelta(days=1))

    assert recency(item, NOW) == 1.0


# --- the whole score ---------------------------------------------------------

def test_every_item_gets_a_score_and_the_list_comes_back_sorted(make_item, sources):
    items = [make_item("Assisted dying and autonomy"), make_item("A car park opens"),
             make_item("NHS reform stalls")]

    ranked = score(items, sources, now=NOW)

    assert all(item.score is not None for item in ranked)
    assert [item.score for item in ranked] == sorted((i.score for i in ranked), reverse=True)


def test_source_weight_multiplies(make_item, sources):
    """Same headline, different mastheads."""
    trusted = make_item("Assisted dying and autonomy", source="statnews")
    lighter = make_item("Assisted dying and autonomy", source="bbc_health")

    score([trusted, lighter], sources, now=NOW)

    assert trusted.score > lighter.score
    assert trusted.score == pytest.approx(lighter.score / 0.7, rel=1e-6)


def test_an_unconfigured_source_is_not_a_crash(make_item, sources):
    """A source can leave the config while its items are still in the store."""
    orphan = make_item("Assisted dying and autonomy", source="retired_source")

    score([orphan], sources, now=NOW)

    assert orphan.score > 0


def test_ties_break_on_id_so_a_rerun_gives_the_same_issue(make_item, sources):
    first = make_item("A car park opens", item_id="item0002")
    second = make_item("A car park opens", item_id="item0001")

    ranked = score([first, second], sources, now=NOW)

    assert [item.id for item in ranked] == ["item0001", "item0002"]


def test_the_score_stays_positive_when_every_signal_is_against(make_item, sources):
    """Source weight has to stay a multiplier, not a sign."""
    worst = make_item(
        "Minister announces update on the latest supercalifragilistic development",
        source="bbc_health", published=NOW - dt.timedelta(days=200), summary="")

    score([worst], sources, now=NOW)

    assert worst.score > 0
