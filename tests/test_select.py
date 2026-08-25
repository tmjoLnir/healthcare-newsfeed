"""Section allocation.

The rules worth guarding are the ones that protect a thin section from a
strong news week, and the ones that stop an issue repeating itself — within
a single issue, and across consecutive ones.
"""

from __future__ import annotations

import datetime as dt

import pytest

from healthcare_newsfeed.pipeline.select import select

MONDAY = dt.datetime(2026, 8, 24, tzinfo=dt.UTC)
WEEK = (MONDAY, MONDAY + dt.timedelta(days=7))


def template(*sections: dict) -> dict:
    return {"issue": {"title": "This Week in Medicine"}, "sections": list(sections)}


def section(key: str, *, low: int = 0, high: int = 2, **extra) -> dict:
    return {"key": key, "heading": key.title(), "min": low, "max": high, **extra}


@pytest.fixture
def scored(make_item):
    """Items already ranked, highest first — score() runs before select()."""
    def _scored(*specs) -> list:
        items = []
        for index, (title, source, value) in enumerate(specs):
            item = make_item(title, source=source, item_id=f"item{index:04d}")
            item.score = value
            item.cluster_id = item.id
            items.append(item)
        return items
    return _scored


@pytest.fixture
def padding(make_item):
    """Unrelated items, to make a candidate pool the size of a real week.

    `subjects()` measures a word against the week's other titles, so a
    handful of items is not a corpus it can say anything about, and the
    proportion it applies needs a realistic denominator: four items on one
    story is 4% of this pool, close to the 5.5% the outbreak that prompted
    the cap actually reached. These come from a source with no sections, so
    they count towards the measurement and can never take a slot.
    """
    def _pad(items: list, count: int = 100) -> list:
        return items + [make_item(f"Unrelated bulletin {n} on assorted matters",
                                  source="filler", item_id=f"pad{n:04d}")
                        for n in range(count)]
    return _pad


@pytest.fixture
def sources(make_source):
    return {
        "filler": make_source("filler", sections=()),
        "jme_ethics": make_source("jme_ethics", sections=("ethics",)),
        "conversation_uk": make_source("conversation_uk",
                                       sections=("explainer", "ethics", "also_reading")),
        "nejm": make_source("nejm", sections=("journals",)),
        "lancet": make_source("lancet", sections=("journals", "global_health")),
        "bbc_health": make_source("bbc_health", sections=("story_of_week", "also_reading")),
        # One section only, and one that fills late: the shape that decides
        # whether the cap spends a slot on the best item or the first to ask.
        "who_dons": make_source("who_dons", sections=("global_health",)),
    }


def titles(digest, key: str) -> list[str]:
    for block in digest.sections:
        if block.key == key:
            return [item.title for item in block.items]
    return []


# --- quotas ------------------------------------------------------------------

def test_a_section_takes_its_best_up_to_max(scored, sources):
    items = scored(("A", "nejm", 9.0), ("B", "nejm", 8.0), ("C", "nejm", 7.0))

    digest = select(items, template(section("journals", low=1, high=2)), 3, sources)

    assert titles(digest, "journals") == ["A", "B"]


def test_a_section_short_on_supply_shrinks(scored, sources):
    """It does not borrow from another, and it does not pad."""
    items = scored(("A", "nejm", 9.0))

    digest = select(items, template(section("journals", low=2, high=4)), 3, sources)

    assert titles(digest, "journals") == ["A"]


def test_a_section_with_nothing_to_carry_is_omitted(scored, sources):
    """min: 0 means an absent block, never a weak filler item."""
    items = scored(("A", "nejm", 9.0))

    digest = select(items, template(section("journals", low=1, high=2),
                                    section("ethics", low=0, high=1)), 3, sources)

    assert [block.key for block in digest.sections] == ["journals"]


def test_selection_marks_each_item_with_its_section(scored, sources):
    items = scored(("A", "nejm", 9.0))

    select(items, template(section("journals", low=1, high=2)), 3, sources)

    assert items[0].section == "journals"


def test_an_item_only_fills_a_section_its_source_may_fill(scored, sources):
    """Eligibility is the source's `sections:` list, not the score."""
    items = scored(("A", "nejm", 9.9))

    digest = select(items, template(section("ethics", low=0, high=1)), 3, sources)

    assert digest.sections == []


# --- the rules that stop an issue repeating itself ---------------------------

def test_only_one_item_per_cluster_survives(scored, sources):
    items = scored(("A", "nejm", 9.0), ("Same story", "nejm", 8.0))
    items[1].cluster_id = items[0].cluster_id

    digest = select(items, template(section("journals", low=1, high=4)), 3, sources)

    assert titles(digest, "journals") == ["A"]


def test_a_cluster_used_in_one_section_is_spent_for_the_others(scored, sources):
    items = scored(("A", "lancet", 9.0), ("Same story", "lancet", 8.0))
    items[1].cluster_id = items[0].cluster_id

    digest = select(items, template(section("journals", low=1, high=2),
                                    section("global_health", low=0, high=2)), 3, sources)

    assert titles(digest, "journals") == ["A"]
    assert titles(digest, "global_health") == []


def test_an_item_an_earlier_issue_carried_is_never_republished(scored, sources):
    items = scored(("Already out", "nejm", 9.9), ("Fresh", "nejm", 1.0))
    items[0].published_in_issue = 11

    digest = select(items, template(section("journals", low=1, high=4)), 12, sources)

    assert titles(digest, "journals") == ["Fresh"]


def test_an_item_taken_by_one_section_does_not_appear_in_another(scored, sources):
    items = scored(("A", "bbc_health", 9.0), ("B", "bbc_health", 8.0))

    digest = select(items, template(section("story_of_week", low=1, high=1),
                                    section("also_reading", low=1, high=6)), 3, sources)

    assert titles(digest, "story_of_week") == ["A"]
    assert titles(digest, "also_reading") == ["B"]


# --- protecting a thin section -----------------------------------------------

def test_a_greedy_earlier_section_cannot_take_a_later_sections_floor(scored, sources):
    """The crowding-out the per-section quotas exist to prevent.

    The Conversation feeds both blocks. Filling explainer to its max first
    would leave ethics — the section this audience benefits from most —
    empty, so every section gets its min before any gets its second choice.
    """
    items = scored(("Best", "conversation_uk", 9.0), ("Next", "conversation_uk", 8.0))

    digest = select(items, template(section("explainer", low=1, high=2),
                                    section("ethics", low=1, high=1)), 3, sources)

    assert titles(digest, "explainer") == ["Best"]
    assert titles(digest, "ethics") == ["Next"]


def test_a_section_still_tops_up_once_the_floors_are_met(scored, sources):
    items = scored(("Best", "conversation_uk", 9.0), ("Next", "conversation_uk", 8.0),
                   ("Third", "conversation_uk", 7.0))

    digest = select(items, template(section("explainer", low=1, high=2),
                                    section("ethics", low=1, high=1)), 3, sources)

    assert titles(digest, "explainer") == ["Best", "Third"]
    assert titles(digest, "ethics") == ["Next"]


# --- diversification ----------------------------------------------------------

def test_diversify_by_source_spreads_a_section_across_publishers(scored, sources):
    """Four journal slots should not all go to NEJM."""
    items = scored(("N1", "nejm", 9.0), ("N2", "nejm", 8.5), ("L1", "lancet", 8.0))

    digest = select(items, template(section("journals", low=1, high=2,
                                            diversify_by="source")), 3, sources)

    assert titles(digest, "journals") == ["N1", "L1"]


def test_diversifying_still_beats_leaving_the_section_short(scored, sources):
    """A second item from one source is better than an empty slot."""
    items = scored(("N1", "nejm", 9.0), ("N2", "nejm", 8.5))

    digest = select(items, template(section("journals", low=2, high=2,
                                            diversify_by="source")), 3, sources)

    assert titles(digest, "journals") == ["N1", "N2"]


def test_without_diversify_the_best_two_win(scored, sources):
    items = scored(("N1", "nejm", 9.0), ("N2", "nejm", 8.5), ("L1", "lancet", 8.0))

    digest = select(items, template(section("journals", low=1, high=2)), 3, sources)

    assert titles(digest, "journals") == ["N1", "N2"]


# --- the digest itself --------------------------------------------------------

def test_the_digest_carries_the_issue_number_and_week(scored, sources):
    items = scored(("A", "nejm", 9.0))

    digest = select(items, template(section("journals", low=1, high=1)), 12, sources, week=WEEK)

    assert digest.issue == 12
    assert (digest.week_start, digest.week_end) == WEEK
    assert digest.sections[0].heading == "Journals"


def test_the_week_falls_back_to_the_span_of_the_candidates(scored, sources, make_item):
    early = make_item("A", source="nejm", first_seen=MONDAY)
    late = make_item("B", source="nejm", first_seen=MONDAY + dt.timedelta(days=3))
    for item in (early, late):
        item.score, item.cluster_id = 1.0, item.id

    digest = select([early, late], template(section("journals", low=1, high=2)), 3, sources)

    assert (digest.week_start, digest.week_end) == (MONDAY, MONDAY + dt.timedelta(days=3))


def test_an_empty_week_still_produces_a_digest(sources):
    digest = select([], template(section("journals", low=1, high=2)), 3, sources)

    assert digest.sections == []
    assert digest.week_start < digest.week_end


# --- subject cap -------------------------------------------------------------
#
# Clusters catch a story filed twice. This catches a story that runs all week:
# the DRC Bundibugyo outbreak reached one real week's candidates as 23 items
# whose titles share a subject and almost no vocabulary, and four of them
# reached the issue.

def test_one_running_story_cannot_fill_the_issue(scored, sources, padding):
    """The shape that prompted this: distinct reports on one outbreak."""
    items = scored(
        ("Ebola disease caused by Bundibugyo virus in the Congo", "bbc_health", 9.0),
        ("Congo Ebola outbreak on track to surpass the largest", "bbc_health", 8.0),
        ("Scientists deploy Merck Ebola vaccine in Congo", "bbc_health", 7.0),
        ("Communities as essential partners in Ebola response", "bbc_health", 6.0),
        ("Hydroxyurea for children with sickle cell anaemia", "bbc_health", 5.0),
    )

    digest = select(padding(items), template(section("also_reading", low=3, high=5)), 1, sources)

    carried = titles(digest, "also_reading")
    assert sum("ebola" in title.lower() for title in carried) == 2
    assert "Hydroxyurea for children with sickle cell anaemia" in carried


def test_the_cap_reaches_across_sections(scored, sources, padding):
    """Two slots for a subject in the issue, not two in every block."""
    items = scored(
        ("Ebola disease caused by Bundibugyo virus in the Congo", "lancet", 9.0),
        ("Congo Ebola outbreak surpasses the largest on record", "lancet", 8.0),
        ("Ebola vaccine deployed across the Congo", "bbc_health", 7.0),
        ("Hydroxyurea for children with sickle cell anaemia", "bbc_health", 6.0),
    )

    digest = select(padding(items), template(section("journals", low=1, high=2),
                                    section("also_reading", low=1, high=2)), 1, sources)

    carried = titles(digest, "journals") + titles(digest, "also_reading")
    assert sum("ebola" in title.lower() for title in carried) == 2


def test_the_cap_does_not_chain(scored, sources, padding):
    """It counts against what is already chosen, never across the pool.

    A rule loose enough to link every report of one story transitively pulls
    unrelated items in behind them — measured at 142 of one week's 361
    candidates. Two items sharing a subject with a third, but nothing with
    each other, are two subjects and not one.
    """
    items = scored(
        ("Bundibugyo outbreak spreads across the Congo", "bbc_health", 9.0),
        ("Congo announces new mining concessions", "bbc_health", 8.0),
        ("Bundibugyo antibody cocktail shows promise", "bbc_health", 7.0),
    )

    digest = select(padding(items), template(section("also_reading", low=1, high=3)), 1, sources)

    assert len(titles(digest, "also_reading")) == 3


def test_the_cap_spends_its_slots_on_the_best_items(scored, sources, padding):
    """Its two slots belong to the subject's best items, not the first askers.

    Measured on a real week: `journals` has a min of 2 and `global_health` a
    min of 0, so journals filled first and spent both Ebola slots on a Lancet
    comment and a MedPage summary. The WHO outbreak report — the highest
    scoring item on the subject, and the reason global_health exists — was
    refused. The cap held and threw away the best item to do it.
    """
    items = scored(
        ("Ebola outbreak situation report from the Congo", "who_dons", 9.0),
        ("Ebola vaccine trial begins across the Congo", "lancet", 8.0),
        ("Communities as essential partners in the Ebola response", "lancet", 7.0),
        ("Hydroxyurea for children with sickle cell anaemia", "lancet", 6.0),
    )

    digest = select(padding(items), template(section("journals", low=2, high=4),
                                    section("global_health", low=0, high=2)), 1, sources)

    carried = titles(digest, "journals") + titles(digest, "global_health")
    assert "Ebola outbreak situation report from the Congo" in carried
    assert "Communities as essential partners in the Ebola response" not in carried
    assert sum("ebola" in title.lower() for title in carried) == 2


def test_a_capped_section_shrinks_rather_than_pads(scored, sources, padding):
    """Same contract as thin supply: an absent item beats a repeated subject."""
    items = scored(
        ("Ebola disease caused by Bundibugyo virus in the Congo", "nejm", 9.0),
        ("Congo Ebola outbreak surpasses the largest on record", "nejm", 8.0),
        ("Ebola vaccine deployed across the Congo", "nejm", 7.0),
    )

    digest = select(padding(items), template(section("journals", low=3, high=3)), 1, sources)

    assert len(titles(digest, "journals")) == 2


def test_unrelated_items_are_untouched(scored, sources, padding):
    """The cap must cost nothing in a week with no running story."""
    items = scored(
        ("Hydroxyurea for children with sickle cell anaemia", "nejm", 9.0),
        ("Psilocybin therapy for treatment-resistant depression", "nejm", 8.0),
        ("Rural-urban disparities in hospice delivery", "nejm", 7.0),
    )

    digest = select(padding(items), template(section("journals", low=1, high=3)), 1, sources)

    assert len(titles(digest, "journals")) == 3
