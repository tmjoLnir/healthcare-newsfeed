"""MOH newsroom adapter, against a captured partial response.

MOH publishes no feed at all; the index is read out of a Next.js flight
payload fetched with a byte range, so every real response is cut mid-string.
The fixture is a trimmed capture that keeps that property — see
tests/fixtures/README.md.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from healthcare_newsfeed.models import Licence, Source
from healthcare_newsfeed.sources.base import UA, FeedError
from healthcare_newsfeed.sources.moh import (
    MAX_ITEMS,
    WINDOW_BYTES,
    MohNewsroomAdapter,
    title_case,
)

MOH = Source(key="moh_sg", name="MOH Singapore", url="https://www.moh.gov.sg/newsroom/",
             adapter="moh_newsroom", licence=Licence.LINK_ONLY, weight=1.0,
             sections=("policy",))


@pytest.fixture
def items(fixture_bytes):
    return MohNewsroomAdapter().parse(fixture_bytes("moh_newsroom_partial.html"), MOH)


def page(*records: dict, close: bool = True) -> bytes:
    """Wrap records in the page shape, escaped the way Next.js ships them."""
    compact = json.dumps({"items": list(records)}, separators=(",", ":"))
    chunk = json.dumps(f'{{"breadcrumb":{{}},{compact[1:-1]}}}')[1:-1]
    tail = '"])' if close else ""
    return f'<script>self.__next_f.push([1,"{chunk}{tail}</script>'.encode()


def record(**overrides) -> dict:
    base = {
        "id": "/newsroom/a-press-release",
        "date": "$D2026-08-24T00:00:00.000Z",
        "plaintextTags": [{"id": "x", "category": "Category",
                           "selected": ["Press Releases"]}],
        "title": "A PRESS RELEASE",
        "description": " ",
    }
    return base | overrides


# --- the captured page ------------------------------------------------------

def test_reads_every_whole_record_and_stops_at_the_one_the_window_cut(items):
    # The fixture holds six complete records and a seventh truncated mid-object,
    # which is what a byte-ranged fetch always looks like.
    assert len(items) == 6


def test_carries_date_category_and_absolute_url(items):
    newest = items[0]
    assert newest.published == dt.datetime(2026, 8, 24, tzinfo=dt.UTC)
    assert newest.categories == ("Speeches",)
    assert newest.url.startswith("https://www.moh.gov.sg/newsroom/")
    assert newest.guid == newest.url.removeprefix("https://www.moh.gov.sg")


def test_items_carry_no_summary(items):
    """MOH's Terms of Use forbid reproducing site contents, and the index
    carries no description anyway — these render as title and link."""
    assert all(item.summary == "" for item in items)


def test_titles_come_back_out_of_capitals(items):
    assert items[1].title == "Efforts Ongoing to Raise Awareness and Access to Palliative Care"
    assert not any(item.title.isupper() for item in items)


def test_newest_first_is_preserved(items):
    dates = [item.published for item in items]
    assert dates == sorted(dates, reverse=True)


# --- payload handling -------------------------------------------------------

def test_an_escaped_quote_before_the_terminator_does_not_end_the_chunk_early():
    """The fixture opens with a chunk containing a literal `"])` inside a
    string. Splitting on it naively would drop every record that follows."""
    escaped = r'{\"label\":\"])\"}'
    body = (f'<script>self.__next_f.push([1,"{escaped}'.encode()
            + page(record())[len('<script>self.__next_f.push([1,"'):])
    assert len(MohNewsroomAdapter().parse(body, MOH)) == 1


def test_a_chunk_cut_mid_escape_still_yields_its_whole_records():
    whole = page(record(id="/newsroom/one"), record(id="/newsroom/two"), close=False)
    # Chop inside a trailing unicode escape, as a byte boundary would.
    truncated = whole[: whole.rindex(b"}")] + rb'{\"id\":\"/newsroom/three\",\"date\":\"$D2026-08-01T00:00:00.000Z\",\u00'
    keys = [item.url for item in MohNewsroomAdapter().parse(truncated, MOH)]
    assert keys == ["https://www.moh.gov.sg/newsroom/one",
                    "https://www.moh.gov.sg/newsroom/two"]


def test_non_ascii_survives_decoding():
    """`unicode_escape` would round-trip this through latin-1 and mangle it."""
    (item,) = MohNewsroomAdapter().parse(page(record(title="SENIORS’ CARE")), MOH)
    assert item.title == "Seniors’ Care"


def test_records_without_a_title_or_slug_are_dropped():
    body = page(record(), record(title=""), record(id=""))
    assert len(MohNewsroomAdapter().parse(body, MOH)) == 1


def test_a_record_with_no_date_is_not_an_item():
    """Navigation and breadcrumb structures carry /newsroom/ links too."""
    body = page(record(), {"id": "/newsroom/", "title": "Newsroom"})
    assert len(MohNewsroomAdapter().parse(body, MOH)) == 1


def test_an_unparseable_date_leaves_the_item_undated():
    (item,) = MohNewsroomAdapter().parse(page(record(date="$Dnot-a-date")), MOH)
    assert item.published is None


# --- failing loudly ---------------------------------------------------------

def test_a_page_without_a_flight_payload_is_an_error():
    with pytest.raises(FeedError, match="no Next.js flight payload"):
        MohNewsroomAdapter().parse(b"<html><body>Newsroom</body></html>", MOH)


def test_a_payload_without_the_item_array_is_an_error():
    body = b'<script>self.__next_f.push([1,"{\\"breadcrumb\\":{}}"])</script>'
    with pytest.raises(FeedError, match="carries no .* array"):
        MohNewsroomAdapter().parse(body, MOH)


def test_an_item_array_that_yields_nothing_is_an_error():
    """The index is an 8,000-item archive: empty means the parse failed, not
    that MOH had a quiet week."""
    body = page()
    with pytest.raises(FeedError, match="read no records"):
        MohNewsroomAdapter().parse(body, MOH)


# --- the request ------------------------------------------------------------

def test_fetch_asks_for_a_byte_range_and_the_shared_user_agent(fixture_bytes):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(206, content=fixture_bytes("moh_newsroom_partial.html"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert len(MohNewsroomAdapter(client).fetch(MOH)) == 6
    assert seen["range"] == f"bytes=0-{WINDOW_BYTES}"
    assert seen["user-agent"] == UA


def test_the_newest_items_are_capped_however_much_of_the_page_arrives():
    """CloudFront grants the byte range only sometimes, so the cap rather than
    the range is what keeps a poll from handing the store the whole archive."""
    body = page(*(record(id=f"/newsroom/item-{n}") for n in range(MAX_ITEMS + 25)))
    items = MohNewsroomAdapter().parse(body, MOH)
    assert len(items) == MAX_ITEMS
    assert items[0].url.endswith("/item-0")     # newest first, so the cap drops the tail


def test_a_host_that_ignores_the_range_and_sends_the_whole_page_still_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=page(record(), record(id="/newsroom/two")))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert len(MohNewsroomAdapter(client).fetch(MOH)) == 2


def test_a_block_is_reported_as_blocked_not_broken():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"<html>denied</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FeedError) as caught:
        MohNewsroomAdapter(client).fetch(MOH)
    assert caught.value.blocked


# --- title_case -------------------------------------------------------------

@pytest.mark.parametrize("shouted, expected", [
    ("OVER 2,400 CAUGHT VAPING", "Over 2,400 Caught Vaping"),
    ("EXPANSION OF PAEDIATRIC A&E FACILITIES", "Expansion of Paediatric A&E Facilities"),
    ("UPDATE ON COVID-19 VACCINATION", "Update on COVID-19 Vaccination"),
    ("IMPACT OF IMH BRANDING ON STIGMA", "Impact of IMH Branding on Stigma"),
    ("CHAS AND MEDISAVE SUBSIDIES FOR GP VISITS",
     "CHAS and MediSave Subsidies for GP Visits"),
    ("SENIORS’ HEALTHCARE NEEDS", "Seniors’ Healthcare Needs"),
])
def test_title_case_restores_a_shouted_headline(shouted, expected):
    assert title_case(shouted) == expected


def test_a_minor_word_still_leads_and_closes_a_headline():
    assert title_case("THE COST OF CARE") == "The Cost of Care"
    assert title_case("WHAT SENIORS PAY FOR") == "What Seniors Pay For"


def test_a_headline_that_is_not_shouted_is_left_alone():
    """A no-op if MOH ever changes house style, rather than a re-casing."""
    original = "Public consultation on the Genetic Information Bill"
    assert title_case(original) == original
