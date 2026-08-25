"""Both dedupe passes.

Pass 1 is `canonical_url`, which the store keys every row on, so those cases
decide what counts as the same article. Pass 2 is `cluster`, which catches
the same story told twice — and the cases that matter most are the ones it
must leave alone, since a false merge silently drops an item from the issue.
"""

from __future__ import annotations

import datetime as dt

import pytest

from healthcare_newsfeed.pipeline.dedupe import (
    WINDOW,
    canonical_url,
    cluster,
    subjects,
)

# Real shapes from the configured sources, so a publisher changing its
# tracking scheme shows up here rather than as duplicate items in an issue.
TRACKED = [
    # BBC appends these to every link in the feed.
    ("https://www.bbc.co.uk/news/articles/c9w4jgg4y55o?at_medium=RSS&at_campaign=rss",
     "https://www.bbc.co.uk/news/articles/c9w4jgg4y55o"),
    # The journals' "available from" marker.
    ("https://www.nejm.org/doi/full/10.1056/NEJMoa2401?af=R",
     "https://www.nejm.org/doi/full/10.1056/NEJMoa2401"),
    # Elsevier, which serves The Lancet.
    ("https://www.thelancet.com/journals/lancet/article/PIIS01/fulltext?rss=yes&dgcid=raven",
     "https://www.thelancet.com/journals/lancet/article/PIIS01/fulltext"),
    # Anything shared onward picks up utm_*.
    ("https://kffhealthnews.org/article/x/?utm_source=rss&utm_medium=feed&utm_campaign=x",
     "https://kffhealthnews.org/article/x/"),
    ("https://www.statnews.com/2026/08/24/a/?utm_campaign=rss&fbclid=IwAR1&igshid=9",
     "https://www.statnews.com/2026/08/24/a/"),
]


@pytest.mark.parametrize("tracked,clean", TRACKED)
def test_tracking_parameters_are_stripped(tracked, clean):
    assert canonical_url(tracked) == clean


def test_meaningful_parameters_are_kept():
    """NEJM's own feed URL carries its journal code in the query string."""
    url = "https://www.nejm.org/action/showFeed?type=etoc&feed=rss&jc=nejm&utm_source=x"
    assert canonical_url(url) == "https://www.nejm.org/action/showFeed?type=etoc&feed=rss&jc=nejm"


def test_scheme_host_and_fragment_are_normalised():
    assert canonical_url("HTTPS://WWW.Example.TEST:443/Path/A?id=7#section-2") == \
        "https://www.example.test/Path/A?id=7"


def test_the_host_is_otherwise_left_alone():
    """This string is also the link the digest publishes, so www. stays."""
    assert canonical_url("https://www.who.int/news/item/x") == "https://www.who.int/news/item/x"


def test_the_path_is_left_alone():
    """A trailing slash is the publisher's choice; both forms are consistent
    within a source, and dropping one can turn a link into a redirect."""
    assert canonical_url("https://kffhealthnews.org/a/") == "https://kffhealthnews.org/a/"
    assert canonical_url("https://www.nature.com/articles/s41591") == \
        "https://www.nature.com/articles/s41591"


@pytest.mark.parametrize("value", ["", "   ", "not a url"])
def test_junk_is_returned_unchanged(value):
    assert canonical_url(value) == value.strip()


# --- pass 2: near-duplicate titles -------------------------------------------

MONDAY = dt.datetime(2026, 8, 24, tzinfo=dt.UTC)


def clusters(items) -> list[set[str]]:
    """The grouping, as sets of titles."""
    groups: dict[str, set[str]] = {}
    for item in items:
        groups.setdefault(item.cluster_id, set()).add(item.title)
    return sorted(groups.values(), key=len, reverse=True)


def test_every_item_lands_in_a_cluster(make_item):
    items = [make_item("Something"), make_item("Something else entirely")]

    cluster(items)

    assert all(item.cluster_id for item in items)
    assert len({item.cluster_id for item in items}) == 2


def test_the_same_story_from_two_sources_clusters(make_item):
    """The case the module exists for: one announcement, several outlets."""
    items = [
        make_item("WHO declares Ebola outbreak over in the DRC", source="who_news"),
        make_item("Ebola outbreak declared over in DRC, says WHO", source="bbc_health"),
    ]

    cluster(items)

    assert items[0].cluster_id == items[1].cluster_id


def test_a_journal_and_its_companion_paper_cluster(make_item):
    """NEJM ran this trial a fortnight before The Lancet's companion piece."""
    items = [
        make_item("Phase 3 Trial of Weekly Oral Islatravir\u2013Lenacapavir for HIV-1 Treatment",
                  source="nejm", published=MONDAY - dt.timedelta(days=14)),
        make_item("[Articles] Switch to once-weekly, single-tablet islatravir\u2013lenacapavir "
                  "from daily standard of care for HIV-1 (ISLEND-2): a multicentre, randomised, "
                  "open-label, active-controlled, phase 3 non-inferiority trial",
                  source="lancet", published=MONDAY),
    ]

    cluster(items)

    assert items[0].cluster_id == items[1].cluster_id


def test_unrelated_stories_stay_apart(make_item):
    items = [
        make_item("Doctors vote to take strike action over pay"),
        make_item("Continuous glucose monitors have transformed diabetes care"),
        make_item("Measles cases rise in Bangladesh"),
    ]

    cluster(items)

    assert len({item.cluster_id for item in items}) == 3


def test_a_short_title_does_not_swallow_a_longer_one(make_item):
    """Every word of a two-word title sits inside plenty of longer ones.

    Containment is what catches a wire headline inside a journal's fuller
    version, and without a floor on length it would merge "Antiretroviral
    Therapy" into any article that happened to mention it.
    """
    items = [
        make_item("Antiretroviral Therapy", source="nejm"),
        make_item("[Comment] Once-weekly oral antiretroviral therapy for HIV", source="lancet"),
    ]

    cluster(items)

    assert items[0].cluster_id != items[1].cluster_id


def test_reports_too_far_apart_are_separate_stories(make_item):
    items = [
        make_item("Measles - Bangladesh", published=MONDAY),
        make_item("Measles - Bangladesh", published=MONDAY - WINDOW - dt.timedelta(days=1)),
    ]

    cluster(items)

    assert items[0].cluster_id != items[1].cluster_id


def test_serial_reports_on_one_outbreak_chain_together(make_item):
    """WHO files these every week or two; the issue should carry one."""
    items = [
        make_item("Ebola disease caused by Bundibugyo virus - Democratic Republic of the Congo",
                  source="who_dons", published=MONDAY - dt.timedelta(days=step))
        for step in (0, 13, 26)
    ]

    cluster(items)

    assert len({item.cluster_id for item in items}) == 1


def test_the_cluster_is_named_after_its_lowest_member(make_item):
    """So the same week's candidates produce the same issue on a rerun."""
    first = make_item("Ebola outbreak declared over in the DRC", item_id="item0002")
    second = make_item("Ebola outbreak in DRC declared over", item_id="item0001")

    cluster([first, second])
    forwards = first.cluster_id
    cluster([second, first])

    assert forwards == first.cluster_id == second.cluster_id == "item0001"


def test_undated_items_fall_back_to_when_they_were_stored(make_item):
    """WHO news items carry no publication date at all."""
    items = [
        make_item("Ebola outbreak declared over in the DRC", published=None, first_seen=MONDAY),
        make_item("Ebola outbreak in the DRC declared over", published=None, first_seen=MONDAY),
    ]

    cluster(items)

    assert items[0].cluster_id == items[1].cluster_id


def test_cluster_returns_the_same_list(make_item):
    items = [make_item("A study")]

    assert cluster(items) is items


# --- section markers ---------------------------------------------------------

def test_a_publisher_section_marker_is_not_part_of_the_subject(make_item):
    """The Lancet prefixes everything with its section. Two Viewpoints on
    unrelated subjects must not look alike for sharing the word."""
    items = [
        make_item("[Viewpoint] What makes a meta-analysis believable?", source="lancet"),
        make_item("[Viewpoint] Ambient scribes as narrative technologies", source="lancet"),
    ]

    cluster(items)

    assert items[0].cluster_id != items[1].cluster_id


def test_a_marker_does_not_stop_a_genuine_match(make_item):
    """Stripping it must still let the same paper cluster across sources."""
    items = [
        make_item("[Articles] Switch to once-weekly islatravir-lenacapavir for HIV-1",
                  source="lancet"),
        make_item("Switch to once-weekly islatravir-lenacapavir for HIV-1", source="nejm"),
    ]

    cluster(items)

    assert items[0].cluster_id == items[1].cluster_id


# --- subjects ----------------------------------------------------------------

def test_a_subject_is_a_word_few_of_the_weeks_titles_carry(make_item):
    items = [make_item(f"Bundibugyo outbreak update {n}", source="who_dons")
             for n in range(3)]
    items += [make_item(f"Ordinary clinical item {n}", source="bbc_health")
              for n in range(60)]

    found = subjects(items)

    assert "bundibugyo" in found[items[0].id]
    assert "ordinary" not in found[items[3].id], "a word in most titles names nothing"


def test_a_field_of_medicine_is_not_a_subject(make_item):
    """Two items are not one running story for both concerning cancer."""
    items = [
        make_item("Cancer drug dosages challenged by patients", source="kff"),
        make_item("Sugar rationing and later cancer risk", source="conversation_uk"),
    ] + [make_item(f"Filler {n}", source="bbc_health") for n in range(40)]

    found = subjects(items)

    assert "cancer" not in found[items[0].id]
    assert "dosages" in found[items[0].id]


def test_headline_scaffolding_is_not_a_subject(make_item):
    """"How", "why" and "stop" build headlines; they do not identify one."""
    items = [
        make_item("How a loyalist got the nomination", source="statnews"),
        make_item("Why editors should stop policing authorship", source="jme_ethics"),
    ] + [make_item(f"Filler {n}", source="bbc_health") for n in range(40)]

    found = subjects(items)

    assert not {"how", "why", "stop", "should"} & found[items[0].id]
    assert not {"how", "why", "stop", "should"} & found[items[1].id]
