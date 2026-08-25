"""Rendering a Digest into Telegram messages.

The rules under test are the ones the README promises a reader and a
publisher: how much of each item may go out, what a paywall looks like from
the channel, and that an issue never arrives as broken markup or a message
the API will reject.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import feedparser
import pytest

from healthcare_newsfeed.config import load_digest_template, load_sources
from healthcare_newsfeed.digest.render import (
    LICENCE_CEILINGS,
    RenderError,
    escape,
    render,
    strip_citation,
    strip_image_credit,
    summary_text,
    trim,
)
from healthcare_newsfeed.digest.template import resolve
from healthcare_newsfeed.models import Digest, Licence, Section
from healthcare_newsfeed.sources.base import clean_text
from healthcare_newsfeed.telegram import length

from .conftest import FIXTURES

WEEK = (dt.datetime(2026, 8, 18, tzinfo=dt.UTC), dt.datetime(2026, 8, 25, tzinfo=dt.UTC))

PROSE = ("Health workers are continuing to treat patients and train caregivers in the "
         "Democratic Republic of Congo, where the second-deadliest Ebola outbreak on "
         "record continues to spread through dense urban districts. ")


@pytest.fixture
def spec():
    return resolve(load_digest_template("config/digest.yaml"))


@pytest.fixture
def extract_spec():
    """A template whose explainer still asks for a full extract.

    The shipped config asks `short` of every prose section, and 180 sits
    below the link-only ceiling of 200 — so a section that asks for more
    than the ceiling is the only place the licence rule and the splitter can
    be observed at all. How much text a section asks for is an editorial
    setting; that the renderer honours the smaller of ask and ceiling, and
    that it splits on a section boundary, are code. These tests are about
    the second, so they bring their own template rather than borrowing
    whatever quota `config/digest.yaml` happens to carry this week.
    """
    return resolve({
        "issue": {"title": "This Week in Medicine", "timezone": "Asia/Singapore"},
        "sections": [{"key": "explainer", "heading": "💡 Explainer",
                      "min": 1, "max": 12, "style": "extract"}],
    })


@pytest.fixture
def shipped_sources():
    return {source.key: source for source in load_sources("config/sources.yaml")}


@pytest.fixture
def digest(make_item):
    """One issue, assembled by hand so each test can restate just its own part."""
    def _digest(*sections: tuple[str, str, list], issue: int = 12) -> Digest:
        return Digest(issue=issue, week_start=WEEK[0], week_end=WEEK[1],
                      sections=[Section(key, heading, items) for key, heading, items in sections])
    return _digest


def only(messages: list[str]) -> str:
    assert len(messages) == 1, f"expected one message, got {len(messages)}"
    return messages[0]


# --- licence limits ---------------------------------------------------------

def test_a_link_only_source_carries_a_quotation_not_an_article(digest, spec, make_source,
                                                               make_item):
    """The README's rule: headline, a short summary, and a link."""
    item = make_item("A long read", source="statnews", summary=PROSE * 5)
    sources = {"statnews": make_source("statnews", sections=("story_of_week",))}

    body = only(render(digest(("story_of_week", "🔬 Story", [item])), sources, spec))
    summary = _summary_line(body)

    assert summary, "no summary rendered — the cap below would pass vacuously"
    assert len(summary) <= LICENCE_CEILINGS[Licence.LINK_ONLY]
    assert body.count(item.canonical_url) == 1


def test_a_cc_source_may_carry_the_extract_its_section_asks_for(digest, extract_spec, make_source,
                                                                make_item):
    item = make_item("An explainer", source="conversation_uk", summary=PROSE * 12)
    sources = {"conversation_uk": make_source("conversation_uk", sections=("explainer",),
                                              licence=Licence.CC_REPUBLISHABLE)}

    body = only(render(digest(("explainer", "💡 Explainer", [item])), sources, extract_spec))
    summary = _summary_line(body)

    # `extract` asks for 900; the CC ceiling of 1200 does not get in the way.
    assert 800 < len(summary) <= 900
    assert len(summary) > LICENCE_CEILINGS[Licence.LINK_ONLY]


def test_the_licence_caps_the_section_not_the_other_way_round(digest, spec, make_source,
                                                              make_item):
    """The same section renders shorter for a link-only source than a CC one.

    On the shipped template, deliberately: a section has to ask for more
    than the link-only ceiling of 200 before the two licences can render
    differently at all, and `story_of_week` asking `long` is what makes that
    true today. If every section were dropped to `short`, the whole licence
    mechanism would go quiet without a single test noticing — so this one
    reads the real config rather than a template of its own.
    """
    sections = ("story_of_week",)
    linked = make_item("Same slot", source="link", summary=PROSE * 12)
    freed = make_item("Same slot", source="cc", summary=PROSE * 12)
    sources = {"link": make_source("link", sections=sections),
               "cc": make_source("cc", sections=sections, licence=Licence.CC_REPUBLISHABLE)}

    short = _summary_line(only(render(digest(("story_of_week", "🔬 S", [linked])), sources, spec)))
    long = _summary_line(only(render(digest(("story_of_week", "🔬 S", [freed])), sources, spec)))

    assert short and long
    assert len(short) <= LICENCE_CEILINGS[Licence.LINK_ONLY] < len(long)


def test_public_domain_is_treated_as_republishable(digest, spec, make_source, make_item):
    item = make_item("An outbreak report", source="who_dons", summary=PROSE * 12)
    sources = {"who_dons": make_source("who_dons", sections=("story_of_week",),
                                       licence=Licence.PUBLIC_DOMAIN)}

    summary = _summary_line(only(render(digest(("story_of_week", "🔬 S", [item])), sources, spec)))

    assert len(summary) > LICENCE_CEILINGS[Licence.LINK_ONLY]


def test_an_unconfigured_source_is_treated_as_link_only(digest, spec, make_item):
    """A source dropped from the config must not widen what may be republished."""
    item = make_item("Orphan", source="gone", summary=PROSE * 5)

    body = only(render(digest(("explainer", "💡 E", [item])), {}, spec))
    summary = _summary_line(body)

    assert summary, "no summary rendered — the cap below would pass vacuously"
    assert len(summary) <= LICENCE_CEILINGS[Licence.LINK_ONLY]
    assert "gone" in body                       # still attributed, by key


# --- what each section style looks like -------------------------------------

def test_a_headline_section_carries_no_body_text(digest, spec, make_source, make_item):
    item = make_item("Worth a look", source="bbc_health", summary=PROSE)
    sources = {"bbc_health": make_source("bbc_health")}

    body = only(render(digest(("also_reading", "📌 Also", [item])), sources, spec))

    assert "Health workers" not in body
    assert item.canonical_url in body and "Worth a look" in body


def test_headlines_are_listed_not_spaced_apart(digest, spec, make_source, make_item):
    items = [make_item(f"Headline {n}", source="bbc_health") for n in range(3)]
    sources = {"bbc_health": make_source("bbc_health")}

    body = only(render(digest(("also_reading", "📌 Also", items)), sources, spec))

    assert "\n\n•" not in body.split("📌 Also</b>\n\n", 1)[1]


def test_a_summary_too_short_to_be_useful_is_dropped(digest, spec, make_source, make_item):
    """Three words and an ellipsis is worse than a headline and a link."""
    item = make_item("Terse", source="statnews", summary="A brief note about the trial.")
    sources = {"statnews": make_source("statnews", sections=("journals",))}

    body = only(render(digest(("journals", "📊 Journals", [item])), sources, spec))

    assert "…" not in body


# --- publisher boilerplate --------------------------------------------------

def test_a_journal_citation_is_stripped_but_the_abstract_is_kept():
    """Nature Medicine ships the citation and then the abstract."""
    parsed = feedparser.parse((FIXTURES / "nature_med_rss1.xml").read_bytes())
    summary = clean_text(parsed.entries[0]["summary"])

    assert summary.startswith("Nature Medicine, Published online")
    assert strip_citation(summary).startswith("In a case report")


@pytest.mark.parametrize("citation", [
    "N Engl J Med, Volume 395, Issue 8, Page 723-733, August 2026.",
    "N Engl J Med 2026;395(8):723-733",
    "New England Journal of Medicine, Volume 395, Issue 8, August 20, 2026.",
])
def test_a_summary_that_is_only_a_citation_leaves_nothing(citation):
    """NEJM's feed carries one where the description belongs — see README."""
    assert strip_citation(citation) == ""


def test_an_nejm_item_renders_as_title_and_link(digest, spec, make_source, make_item):
    item = make_item("A trial of something", source="nejm",
                     summary="N Engl J Med, Volume 395, Issue 8, Page 723-733, August 2026.")
    sources = {"nejm": make_source("nejm", sections=("journals",))}

    body = only(render(digest(("journals", "📊 Journals", [item])), sources, spec))

    assert "Volume 395" not in body and "N Engl J Med" not in body
    assert item.canonical_url in body and "A trial of something" in body


@pytest.mark.parametrize("prose", [
    "A policy issue 5 years in the making finally reaches the committee stage.",
    "Turn to page 4 of the guidance for the revised consent wording.",
    "Volume 3 of the inquiry report examines maternity failings in detail.",
])
def test_prose_that_merely_mentions_a_number_is_not_a_citation(prose):
    """One weak marker is a sentence; a citation carries several, or a doi."""
    assert strip_citation(prose) == prose


@pytest.mark.parametrize("citation", [
    "The Lancet, Volume 408, Issue 10500, Pages 512-524, 23 August 2026.",
    "JAMA. 2026;336(7):604-612. doi:10.1001/jama.2026.11234",
])
def test_the_other_journals_citations_are_stripped_too(citation):
    assert strip_citation(citation) == ""


def test_a_summary_with_no_citation_is_untouched():
    """Every non-journal source in the configured set lands here."""
    summary = "Nearly 4 million women in England can order a self-testing kit."

    assert strip_citation(summary) == summary


def test_a_doi_inside_an_abstract_is_not_read_as_a_preamble():
    summary = ("Researchers report a new treatment pathway. " * 8) + "See doi:10.1038/example."

    assert strip_citation(summary) == summary


def test_a_hero_image_credit_is_stripped_from_the_article():
    """Every Conversation article opens with one, and explainers carry the most text."""
    parsed = feedparser.parse((FIXTURES / "conversation_uk_atom.xml").read_bytes())
    article = clean_text(parsed.entries[0]["content"][0]["value"])

    assert "Shutterstock" in article[:80]
    assert "Shutterstock" not in summary_text(article)[:80]


@pytest.mark.parametrize("text", [
    "Reuters reported that the outbreak has spread to three provinces this week.",
    "Getty Images faces a lawsuit over its use of medical photography in adverts.",
    "The EPA has revised its guidance on airborne particulates in hospitals.",
])
def test_an_agency_named_in_the_prose_is_not_a_photo_credit(text):
    """The `/` separator is what makes a credit a credit."""
    assert strip_image_credit(text) == text


# --- labelling --------------------------------------------------------------

def test_a_paywalled_source_is_labelled_before_the_reader_clicks(digest, spec, make_item):
    from healthcare_newsfeed.models import Source
    source = Source(key="lancet", name="The Lancet", url="https://lancet.test/feed",
                    adapter="rss", licence=Licence.LINK_ONLY, weight=1.0,
                    sections=("journals",), paywalled=True)
    item = make_item("A trial", source="lancet", summary=PROSE)

    body = only(render(digest(("journals", "📊 Journals", [item])), {"lancet": source}, spec))

    assert "🔒 paywalled" in body and "The Lancet" in body


def test_a_free_source_carries_no_paywall_mark(digest, spec, make_source, make_item):
    item = make_item("Free to read", source="bbc_health", summary=PROSE)
    sources = {"bbc_health": make_source("bbc_health", sections=("journals",))}

    body = only(render(digest(("journals", "📊 J", [item])), sources, spec))

    assert "paywalled" not in body


def test_the_header_names_the_issue_and_the_week_it_covers(digest, spec, make_source,
                                                           make_item):
    sources = {"bbc_health": make_source("bbc_health")}
    item = make_item("Anything", source="bbc_health")

    body = only(render(digest(("also_reading", "📌 Also", [item]), issue=12), sources, spec))

    assert "This Week in Medicine" in body and "Issue 12" in body
    assert "August 2026" in body


# --- markup safety ----------------------------------------------------------

def test_markup_in_a_title_cannot_break_the_message(digest, spec, make_source, make_item):
    """Telegram rejects a whole message with an unparseable entity."""
    item = make_item("Trials of <b>drug</b> & the ethics of it", source="bbc_health")
    sources = {"bbc_health": make_source("bbc_health")}

    body = only(render(digest(("also_reading", "📌 Also", [item])), sources, spec))

    assert "&lt;b&gt;drug&lt;/b&gt; &amp; the ethics" in body
    assert body.count("<b>") == body.count("</b>")


def test_an_ampersand_in_a_url_is_escaped(digest, spec, make_source, make_item):
    item = make_item("Tracked", source="bbc_health")
    item.canonical_url = "https://bbc.test/a?utm_source=rss&utm_medium=feed"
    sources = {"bbc_health": make_source("bbc_health")}

    body = only(render(digest(("also_reading", "📌 Also", [item])), sources, spec))

    assert "utm_source=rss&amp;utm_medium=feed" in body


def test_escaping_is_applied_once():
    assert escape("a & b") == "a &amp; b"
    assert escape("<i>") == "&lt;i&gt;"


def test_trim_keeps_the_ellipsis_inside_the_budget():
    assert len(trim(PROSE, 40)) <= 40
    assert trim(PROSE, 40).endswith("…")
    assert trim("short", 40) == "short"


# --- splitting --------------------------------------------------------------

def test_a_long_issue_is_split_into_an_ordered_burst(digest, extract_spec, make_source, make_item):
    items = [make_item(f"Explainer {n}", source="cc", summary=PROSE * 12) for n in range(12)]
    sources = {"cc": make_source("cc", sections=("explainer",),
                                 licence=Licence.CC_REPUBLISHABLE)}

    messages = render(digest(("explainer", "💡 Explainer", items)), sources, extract_spec)

    assert len(messages) > 1
    assert all(length(message) <= extract_spec.max_message_chars for message in messages)


def test_a_split_section_repeats_its_heading(digest, extract_spec, make_source, make_item):
    """A reader landing mid-block still needs to know which section it is."""
    items = [make_item(f"Explainer {n}", source="cc", summary=PROSE * 12) for n in range(12)]
    sources = {"cc": make_source("cc", sections=("explainer",),
                                 licence=Licence.CC_REPUBLISHABLE)}

    messages = render(digest(("explainer", "💡 Explainer", items)), sources, extract_spec)

    assert "(cont.)" in messages[1]
    assert all("💡 Explainer" in message for message in messages[1:])


def test_no_item_is_ever_split_across_messages(digest, extract_spec, make_source, make_item):
    items = [make_item(f"Explainer {n}", source="cc", summary=PROSE * 12) for n in range(12)]
    sources = {"cc": make_source("cc", sections=("explainer",),
                                 licence=Licence.CC_REPUBLISHABLE)}

    for message in render(digest(("explainer", "💡 E", items)), sources, extract_spec):
        assert message.count("<a href") == message.count("</a>")
        assert message.count("<b>") == message.count("</b>")
        assert message.count("<i>") == message.count("</i>")


@pytest.mark.parametrize("limit", [4096, 2000, 900, 400, 280])
def test_messages_stay_within_the_limit_at_any_configured_size(digest, spec, make_source,
                                                               make_item, limit):
    items = [make_item(f"Explainer {n}", source="cc", summary=PROSE * 12) for n in range(8)]
    heads = [make_item(f"Headline {n}", source="cc") for n in range(6)]
    sources = {"cc": make_source("cc", sections=("explainer", "also_reading"),
                                 licence=Licence.CC_REPUBLISHABLE)}
    issue = digest(("explainer", "💡 Explainer", items), ("also_reading", "📌 Also", heads))

    messages = render(issue, sources, replace(spec, max_message_chars=limit))

    assert messages and all(length(message) <= limit for message in messages)


def test_a_short_issue_fits_one_message(digest, spec, shipped_sources, make_item):
    items = [make_item(f"Headline {n}", source="bbc_health") for n in range(4)]

    assert len(render(digest(("also_reading", "📌 Also", items)), shipped_sources, spec)) == 1


def test_length_counts_the_way_telegram_counts():
    """Section headings all open with an emoji, and those are two units each."""
    assert length("🔬") == 2
    assert length("abc") == 3


# --- guards -----------------------------------------------------------------

def test_a_section_the_template_dropped_is_reported(digest, spec, make_source, make_item):
    item = make_item("Orphaned", source="bbc_health")
    sources = {"bbc_health": make_source("bbc_health")}

    with pytest.raises(RenderError, match="no longer defines"):
        render(digest(("obsolete", "🗞 Obsolete", [item])), sources, spec)


def test_an_empty_digest_renders_only_its_header(spec):
    messages = render(Digest(issue=1, week_start=WEEK[0], week_end=WEEK[1]), {}, spec)

    assert len(messages) == 1 and "Issue 1" in messages[0]


def _summary_line(message: str) -> str:
    """The body text of the first item in a rendered message."""
    lines = [line for line in message.splitlines() if line and not line.startswith(("<b>", "<i>"))]
    return lines[0] if lines else ""
