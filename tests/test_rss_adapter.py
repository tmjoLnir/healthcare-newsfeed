"""RSS/Atom adapter, against captured responses.

The configured set spans three formats and the adapter has to flatten all
three into one shape, so every format has a fixture here. See
tests/fixtures/README.md for what each one is and what was trimmed.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from healthcare_newsfeed.models import Licence, Source
from healthcare_newsfeed.sources.base import UA, FeedError
from healthcare_newsfeed.sources.rss import RssAdapter


def make_source(key: str, url: str, licence: Licence = Licence.LINK_ONLY) -> Source:
    return Source(key=key, name=key, url=url, adapter="rss", licence=licence,
                  weight=1.0, sections=("also_reading",))


BBC = make_source("bbc_health", "https://feeds.bbci.co.uk/news/health/rss.xml")
NATURE = make_source("nature_med", "https://www.nature.com/nm.rss")
KFF = make_source("kff", "https://kffhealthnews.org/feed/", Licence.CC_REPUBLISHABLE)
CONVERSATION = make_source("conversation_uk",
                           "https://theconversation.com/uk/health/articles.atom",
                           Licence.CC_REPUBLISHABLE)

FIXTURES = {
    "rss20": ("bbc_health_rss2.xml", BBC, 4),
    "rss20-content": ("kff_rss2.xml", KFF, 2),
    "rss10-rdf": ("nature_med_rss1.xml", NATURE, 3),
    "atom10": ("conversation_uk_atom.xml", CONVERSATION, 3),
}


@pytest.fixture
def parse(fixture_bytes):
    """Parse a captured response — no client is built, so nothing is opened."""
    def _parse(name: str, source: Source):
        return RssAdapter().parse(fixture_bytes(name), source)
    return _parse


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- every configured format ---------------------------------------------

@pytest.mark.parametrize("fmt", list(FIXTURES))
def test_every_configured_format_parses(parse, fmt):
    name, source, expected = FIXTURES[fmt]
    items = parse(name, source)

    assert len(items) == expected
    for item in items:
        assert item.source_key == source.key
        assert item.title and "<" not in item.title
        assert item.url.startswith("https://")
        assert item.published is not None
        assert item.published.tzinfo is dt.UTC


@pytest.mark.parametrize("fmt", list(FIXTURES))
def test_summaries_carry_no_markup(parse, fmt):
    name, source, _ = FIXTURES[fmt]
    for item in parse(name, source):
        assert "<" not in item.summary
        assert "&#" not in item.summary and "&amp;" not in item.summary
        assert item.summary == item.summary.strip()


# --- format-specific hazards ----------------------------------------------

def test_rdf_seq_block_is_not_an_entry(parse):
    """RSS 1.0 lists item order in <items><rdf:Seq>; those are not entries.

    The fixture references eight resources in its Seq and carries three
    actual <item> elements, so a parser that counted both would show eleven.
    """
    assert len(parse("nature_med_rss1.xml", NATURE)) == 3


def test_rss10_dates_come_from_dc_date(parse):
    """The RSS 1.0 journals carry only dc:date, which arrives as `updated`.

    Reading `published` alone would leave every NEJM, Lancet and Nature
    Medicine item undated, and an undated item cannot be windowed.
    """
    published = [item.published for item in parse("nature_med_rss1.xml", NATURE)]
    assert all(p is not None for p in published)
    assert published[0] == dt.datetime(2026, 8, 24, tzinfo=dt.UTC)


def test_block_tags_do_not_run_words_together(parse):
    """Nature's summary opens with a <p> citation closed against the abstract."""
    summary = parse("nature_med_rss1.xml", NATURE)[0].summary
    assert "doi:10.1038/s41591-026-04663-5 In a case report" in summary


def test_full_text_is_preferred_over_the_teaser(parse):
    """CC sources publish whole articles; the licence limit is render's job.

    KFF ships the article in content:encoded and a 275-character teaser in
    <description>; The Conversation ships it in Atom <content> against a
    139-character <summary>. Taking the teaser would silently discard the
    text that makes the explainer and policy sections republishable.
    """
    assert len(parse("kff_rss2.xml", KFF)[0].summary) > 5000
    assert len(parse("conversation_uk_atom.xml", CONVERSATION)[0].summary) > 5000


def test_tracking_parameters_are_left_for_the_store(parse):
    """RawItem is the item as found; canonicalising is the store's insert step."""
    assert "at_medium=RSS" in parse("bbc_health_rss2.xml", BBC)[0].url


def test_atom_entry_metadata(parse):
    item = parse("conversation_uk_atom.xml", CONVERSATION)[0]

    assert item.url.startswith("https://theconversation.com/")
    assert len(item.authors) == 3
    assert item.guid == "tag:theconversation.com,2011:article/289335"


def test_categories_are_captured(parse):
    assert "Health Industry" in parse("kff_rss2.xml", KFF)[0].categories


# --- entries the adapter drops --------------------------------------------

MINIMAL = """<?xml version="1.0"?><rss version="2.0"><channel>
<title>Test</title><link>https://feeds.example.test/</link><description>d</description>
{items}
</channel></rss>"""


def parse_inline(items: str, source: Source = BBC):
    return RssAdapter().parse(MINIMAL.format(items=items).encode(), source)


def test_relative_links_resolve_against_the_feed_url():
    items = parse_inline("<item><title>T</title><link>/news/story</link></item>")
    assert items[0].url == "https://feeds.bbci.co.uk/news/story"


def test_entries_without_a_title_or_link_are_dropped():
    items = parse_inline(
        "<item><title>Keep me</title><link>https://x.test/a</link></item>"
        "<item><link>https://x.test/no-title</link></item>"
        "<item><title>No link</title></item>"
    )
    assert [item.title for item in items] == ["Keep me"]


def test_a_quiet_feed_is_not_an_error():
    assert parse_inline("") == []


@pytest.mark.parametrize("body", [
    (b"<!DOCTYPE html><html lang=en><head><meta charset=utf-8>"
     b"<title>Attention Required!</title></head><body>Access denied</body></html>"),
    b"<html><body>403 Forbidden</body></html>",
    b'{"value": []}',
    b"",
])
def test_a_response_that_is_not_a_feed_raises(body):
    """A challenge page served with HTTP 200 must not read as a quiet week.

    An empty result that nobody notices is the failure this project's daily
    poll exists to prevent, and one of these — well-formed HTML — parses
    without complaint, so bozo alone would not catch it.
    """
    with pytest.raises(FeedError):
        RssAdapter().parse(body, BBC)


# --- fetching --------------------------------------------------------------

def test_fetch_requests_the_feed_as_a_browser(fixture_bytes):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, content=fixture_bytes("bbc_health_rss2.xml"))

    with RssAdapter(client=mock_client(handler)) as adapter:
        items = adapter.fetch(BBC)

    assert len(items) == 4
    assert seen["url"] == BBC.url
    # Several publishers answer a default client string with a challenge page.
    assert seen["ua"] == UA


@pytest.mark.parametrize("status,blocked", [(403, True), (429, True), (404, False),
                                            (500, False)])
def test_fetch_separates_a_blocked_host_from_a_broken_feed(status, blocked):
    """NEJM answers Actions runners with 403 while serving normally elsewhere.

    That is a property of where the poll runs, not of the feed, and the
    caller decides whether it should fail the run — as verify_feeds.py does.
    """
    adapter = RssAdapter(client=mock_client(lambda request: httpx.Response(status)))

    with pytest.raises(FeedError) as raised:
        adapter.fetch(BBC)

    assert raised.value.status == status
    assert raised.value.blocked is blocked


def test_fetch_raises_on_a_network_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed", request=request)

    with pytest.raises(FeedError) as raised:
        RssAdapter(client=mock_client(handler)).fetch(BBC)

    assert raised.value.status is None
    assert raised.value.blocked is False
