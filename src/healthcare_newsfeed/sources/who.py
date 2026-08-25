"""WHO OData JSON adapter.

WHO retired RSS: every documented feed path returns 404, and the one URL
that still resolves (`/rss-feeds/news-english.xml`) is abandoned — 25 items
spanning more than a year. Live data is served from a Sitecore OData API:

    https://www.who.int/api/news/newsitems
    https://www.who.int/api/news/diseaseoutbreaknews

Both default to an unsorted page, so the query must order explicitly:

    ?$orderby=PublicationDateAndTime desc&$top=20

Fields of interest: Title, PublicationDateAndTime, ItemDefaultUrl, Summary.

Two collections, two shapes. News items carry no Summary field at all, so
they reach the digest as title and link; outbreak news carries a plain-text
Summary alongside the HTML body sections we do not use. ItemDefaultUrl is a
bare slug in both, and the base path it hangs off differs per collection —
see ITEM_BASES.
"""

from __future__ import annotations

import datetime as dt
import json
from itertools import pairwise
from urllib.parse import urlsplit

from ..models import RawItem, Source
from .base import FeedError, HttpSource, clean_text

# ItemDefaultUrl is relative — `/2026-DON615`, `/24-08-2026-who-and-…` — and
# each collection hangs off its own base path. Verified 2026-08-25 across
# every NewsType the news collection returns (Statement, News release, Joint
# News Release, Departmental update, Medical product alert): these two
# prefixes give 200, and prefixing with www.who.int alone gives 404.
ITEM_BASES = {
    "newsitems": "https://www.who.int/news/item",
    "diseaseoutbreaknews": "https://www.who.int/emergencies/disease-outbreak-news/item",
}

TOP = 20

# The default page is not merely unordered but effectively arbitrary: without
# $orderby the first three records came back dated 2017, 2020 and 2016. A poll
# that dropped this parameter would store a decade-old backlog and miss the
# week's news, so the ordering is both requested here and checked on arrival.
QUERY = {"$orderby": "PublicationDateAndTime desc", "$top": TOP}


def _collection(source: Source) -> str:
    return urlsplit(source.url).path.rstrip("/").rsplit("/", 1)[-1].lower()


def _title(record: dict) -> str:
    """Outbreak news carries an override WHO's own page prefers."""
    if record.get("UseOverrideTitle") and record.get("OverrideTitle"):
        return record["OverrideTitle"]
    return record.get("Title") or ""


def _published(record: dict) -> dt.datetime | None:
    """PublicationDateAndTime, falling back to the date-only field DONs add."""
    for key in ("PublicationDateAndTime", "PublicationDate"):
        raw = record.get(key)
        if not raw:
            continue
        try:
            stamp = dt.datetime.fromisoformat(raw)
        except ValueError:
            continue
        return stamp.replace(tzinfo=dt.UTC) if stamp.tzinfo is None else stamp.astimezone(dt.UTC)
    return None


class WhoODataAdapter(HttpSource):
    """Fetch one WHO OData collection and return its current items."""

    def fetch(self, source: Source) -> list[RawItem]:
        return self.parse(self.get(source.url, source.key, params=QUERY).content, source)

    def parse(self, body: bytes, source: Source) -> list[RawItem]:
        """Turn an OData collection into RawItems.

        Separate from `fetch` so the suite can run against captured
        responses. Records without a title or a URL slug are dropped; no
        other filtering happens here.
        """
        collection = _collection(source)
        if collection not in ITEM_BASES:
            raise FeedError(
                f"{source.key}: no item URL base known for the '{collection}' "
                f"collection (expected one of {sorted(ITEM_BASES)})"
            )
        base = ITEM_BASES[collection].rstrip("/")

        try:
            records = json.loads(body)["value"]
        except (ValueError, KeyError, TypeError) as exc:
            raise FeedError(f"{source.key}: response is not an OData collection ({exc})") from exc

        if not records:
            # Unlike a quiet RSS feed, these collections are archives running
            # back to 2016 and $top alone would fill a page from them. Nothing
            # at all means the request did not do what it asked, not that WHO
            # had a quiet week.
            raise FeedError(f"{source.key}: collection came back empty")

        items = []
        for record in records:
            title = clean_text(_title(record))
            slug = (record.get("ItemDefaultUrl") or "").strip()
            if not title or not slug:
                continue
            items.append(RawItem(
                source_key=source.key,
                title=title,
                url=f"{base}/{slug.lstrip('/')}",
                published=_published(record),
                # News items have no Summary field; outbreak news has one in
                # plain text. The HTML body sections DONs also carry (Overview,
                # Epidemiology, Assessment, Advice) are deliberately left out.
                summary=clean_text(record.get("Summary") or ""),
                # WHO's own taxonomy — "Statement", "News release" and so on —
                # and the only structured category either collection offers.
                categories=(clean_text(record["NewsType"]),) if record.get("NewsType") else (),
                guid=record.get("Id") or record.get("DonId") or None,
            ))

        _check_ordering(items, source)
        return items


def _check_ordering(items: list[RawItem], source: Source) -> None:
    """Fail loudly if $orderby did not take effect.

    The API ignores an ordering it cannot parse and answers 200 with an
    arbitrary page instead. Silently accepting that would put items a decade
    old into this week's candidates, and window() selects on when an item was
    stored rather than when it was published, so nothing downstream would
    catch it.
    """
    dated = [item.published for item in items if item.published]
    if any(following > preceding for preceding, following in pairwise(dated)):
        raise FeedError(
            f"{source.key}: collection came back unsorted — the $orderby "
            f"parameter did not take effect, so these are not the newest items"
        )
