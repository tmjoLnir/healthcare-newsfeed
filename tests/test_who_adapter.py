"""WHO OData adapter, against captured responses.

WHO is the one source with no RSS at all, and its two collections do not
share a shape: news items carry no summary and outbreak news does, and the
slug in ItemDefaultUrl hangs off a different base path in each. See
tests/fixtures/README.md.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from healthcare_newsfeed.models import Licence, Source
from healthcare_newsfeed.sources.base import UA, FeedError
from healthcare_newsfeed.sources.who import TOP, WhoODataAdapter


def make_source(key: str, url: str) -> Source:
    return Source(key=key, name=key, url=url, adapter="who_odata",
                  licence=Licence.PUBLIC_DOMAIN, weight=1.0, sections=("global_health",))


NEWS = make_source("who_news", "https://www.who.int/api/news/newsitems")
DONS = make_source("who_dons", "https://www.who.int/api/news/diseaseoutbreaknews")


@pytest.fixture
def parse(fixture_bytes):
    def _parse(name: str, source: Source):
        return WhoODataAdapter().parse(fixture_bytes(name), source)
    return _parse


def parse_records(records: list[dict], source: Source = NEWS):
    body = json.dumps({"@odata.context": "…", "value": records}).encode()
    return WhoODataAdapter().parse(body, source)


def record(**overrides) -> dict:
    base = {"Title": "WHO statement", "ItemDefaultUrl": "/24-08-2026-who-statement",
            "PublicationDateAndTime": "2026-08-24T14:22:25Z", "NewsType": "Statement",
            "Id": "99d65c3f"}
    return base | overrides


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- both collections ------------------------------------------------------

@pytest.mark.parametrize("name,source,expected", [
    ("who_news_odata.json", NEWS, 3),
    ("who_dons_odata.json", DONS, 2),
])
def test_both_collections_parse(parse, name, source, expected):
    items = parse(name, source)

    assert len(items) == expected
    for item in items:
        assert item.source_key == source.key
        assert item.title and "<" not in item.title
        assert item.url.startswith("https://www.who.int/")
        assert item.published is not None
        assert item.published.tzinfo is not None
        assert item.published.utcoffset() == dt.timedelta(0)


def test_news_items_get_the_news_base_path(parse):
    """ItemDefaultUrl is a bare slug, and www.who.int alone gives a 404.

    Verified against the live site on 2026-08-25 for every NewsType the
    collection returns; this is the prefix that answers 200.
    """
    assert parse("who_news_odata.json", NEWS)[0].url == (
        "https://www.who.int/news/item/24-08-2026-second-meeting-of-the-ihr-emergency-"
        "committee-on-the-epidemic-of-ebola-bundibugyo-virus-disease-in-the-democratic-"
        "republic-of-the-congo-temporary-recommendations"
    )


def test_outbreak_news_gets_its_own_base_path(parse):
    """The same field on the other collection hangs off a different path."""
    assert parse("who_dons_odata.json", DONS)[0].url == (
        "https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON615"
    )


def test_news_items_carry_no_summary(parse):
    """The news collection has no Summary field, so those render title + link."""
    assert all(item.summary == "" for item in parse("who_news_odata.json", NEWS))


def test_outbreak_news_carries_a_plain_text_summary(parse):
    summary = parse("who_dons_odata.json", DONS)[0].summary

    assert len(summary) > 1000
    assert "<" not in summary and "&#" not in summary


def test_news_type_becomes_a_category(parse):
    """WHO's own taxonomy, and the only structured category on offer."""
    assert [item.categories for item in parse("who_news_odata.json", NEWS)] == [
        ("Statement",), ("Departmental update",), ("Departmental update",)]
    assert parse("who_dons_odata.json", DONS)[0].categories == ()


def test_the_override_title_wins_where_who_sets_one(parse):
    """Outbreak news carries an override that WHO's own page prefers."""
    items = parse_records([record(Title="Raw title", OverrideTitle="Published title",
                                 UseOverrideTitle=True)])
    assert items[0].title == "Published title"

    ignored = parse_records([record(Title="Raw title", OverrideTitle="Unused",
                                    UseOverrideTitle=False)])
    assert ignored[0].title == "Raw title"


def test_dates_without_a_zone_are_read_as_utc():
    item = parse_records([record(PublicationDateAndTime="2026-08-24T14:22:25")])[0]
    assert item.published == dt.datetime(2026, 8, 24, 14, 22, 25, tzinfo=dt.UTC)


def test_the_date_only_field_is_the_fallback():
    """Outbreak news carries PublicationDate as well, and it can be the only one."""
    item = parse_records([record(PublicationDateAndTime=None,
                                 PublicationDate="2026-08-14T15:54:20Z")])[0]
    assert item.published == dt.datetime(2026, 8, 14, 15, 54, 20, tzinfo=dt.UTC)


def test_records_without_a_title_or_slug_are_dropped():
    items = parse_records([
        record(Title="Keep me"),
        record(Title="", ItemDefaultUrl="/no-title"),
        record(Title="No slug", ItemDefaultUrl=""),
    ])
    assert [item.title for item in items] == ["Keep me"]


# --- the failures that would otherwise be silent -----------------------------

def test_an_unsorted_collection_is_an_error():
    """Without $orderby the API answers 200 with an arbitrary page.

    Live, the default page's first three records were dated 2017, 2020 and
    2016. Accepting that would put a decade-old backlog into this week's
    candidates — window() selects on when an item was stored, not when it
    was published, so nothing downstream would catch it.
    """
    with pytest.raises(FeedError, match="unsorted"):
        parse_records([
            record(ItemDefaultUrl="/a", PublicationDateAndTime="2017-09-28T00:00:00Z"),
            record(ItemDefaultUrl="/b", PublicationDateAndTime="2020-02-04T23:01:00Z"),
            record(ItemDefaultUrl="/c", PublicationDateAndTime="2016-05-27T06:00:00Z"),
        ])


def test_a_correctly_ordered_collection_is_not():
    """Equal timestamps are ordered too — the check must not trip on a tie."""
    items = parse_records([
        record(ItemDefaultUrl="/a", PublicationDateAndTime="2026-08-24T14:22:25Z"),
        record(ItemDefaultUrl="/b", PublicationDateAndTime="2026-08-24T14:22:25Z"),
        record(ItemDefaultUrl="/c", PublicationDateAndTime="2026-08-21T12:22:00Z"),
    ])
    assert len(items) == 3


def test_an_empty_collection_is_an_error():
    """These collections are archives back to 2016; $top alone would fill a page."""
    with pytest.raises(FeedError, match="empty"):
        parse_records([])


@pytest.mark.parametrize("body", [
    b"<!DOCTYPE html><html><body>Access denied</body></html>",
    b"not json at all",
    b'{"error": {"code": "429"}}',
    b'[{"Title": "a bare array"}]',
    b"",
])
def test_a_response_that_is_not_a_collection_raises(body):
    with pytest.raises(FeedError):
        WhoODataAdapter().parse(body, NEWS)


def test_an_unknown_collection_raises():
    """The item URL base is per-collection, so a new one cannot be guessed."""
    with pytest.raises(FeedError, match="no item URL base"):
        parse_records([record()], make_source("who_other",
                                              "https://www.who.int/api/news/somethingelse"))


# --- fetching ---------------------------------------------------------------

def test_fetch_asks_for_an_explicit_ordering(fixture_bytes):
    """The whole reason WHO needs a bespoke adapter: the default page is unsorted."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, content=fixture_bytes("who_news_odata.json"))

    with WhoODataAdapter(client=mock_client(handler)) as adapter:
        items = adapter.fetch(NEWS)

    assert len(items) == 3
    assert seen["params"] == {"$orderby": "PublicationDateAndTime desc", "$top": str(TOP)}
    assert seen["ua"] == UA


@pytest.mark.parametrize("status,blocked", [(403, True), (429, True), (404, False)])
def test_fetch_separates_a_blocked_host_from_a_broken_collection(status, blocked):
    adapter = WhoODataAdapter(client=mock_client(lambda request: httpx.Response(status)))

    with pytest.raises(FeedError) as raised:
        adapter.fetch(NEWS)

    assert raised.value.status == status
    assert raised.value.blocked is blocked
