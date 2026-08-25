"""RSS/Atom adapter, covering every source except WHO.

Handles all three formats present in the configured set:
  * RSS 2.0  — MedPage, STAT, BBC, JAMA, KFF, JME
  * RSS 1.0  — NEJM, The Lancet, Nature Medicine (RDF; note `<items><rdf:Seq>`
               ordering blocks are metadata, not entries)
  * Atom 1.0 — The Conversation

feedparser normalises all three, so the work here is date handling (feeds
disagree on which of published/updated/dc:date is authoritative) and
stripping HTML from summaries.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import urljoin

import feedparser

from ..models import RawItem, Source
from .base import FeedError, HttpSource, clean_text


def entry_date(entry) -> dt.datetime | None:
    """The item's timestamp, in UTC.

    Feeds disagree on which element is authoritative: Atom carries both
    published and updated, RSS 2.0 carries pubDate, and the RSS 1.0 journals
    carry only `dc:date` — which feedparser reports as `updated_parsed`, so
    published alone would date every NEJM, Lancet and Nature Medicine item
    as unknown. feedparser has already normalised each of them to UTC.
    """
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return dt.datetime(*value[:6], tzinfo=dt.UTC)
    return None


def _body(entry) -> str:
    """The fullest text the feed offers, before any licence limit applies.

    The Conversation and KFF ship whole articles (in `content` and
    `content:encoded` respectively) and licence that text for republication,
    alongside a teaser of a few hundred characters in the summary element;
    everyone else ships the teaser alone. Preferring the full form keeps the
    choice open — `digest/render.py` is where licence decides how much of it
    may actually go out.
    """
    if entry.get("content"):
        return entry["content"][0].get("value", "")
    return entry.get("summary", "") or entry.get("description", "")


def _link(entry, feed_url: str) -> str:
    link = entry.get("link") or ""
    if not link:
        link = next((ref.get("href", "") for ref in entry.get("links") or []
                     if ref.get("rel") == "alternate"), "")
    return urljoin(feed_url, link.strip()) if link else ""


def _authors(entry) -> tuple[str, ...]:
    names = [clean_text(a.get("name", "")) for a in entry.get("authors") or []]
    names = [name for name in names if name]
    if not names and entry.get("author"):
        names = [clean_text(entry["author"])]
    return tuple(names)


class RssAdapter(HttpSource):
    """Fetch one RSS/Atom source and return its current items."""

    def fetch(self, source: Source) -> list[RawItem]:
        return self.parse(self.get(source.url, source.key).content, source)

    def parse(self, body: bytes, source: Source) -> list[RawItem]:
        """Turn a feed response into RawItems.

        Separate from `fetch` so the suite can run against captured
        responses. Entries without a title or a link are dropped — there is
        nothing to publish and nothing to deduplicate on — but no other
        filtering happens here; ranking and licence limits come later.
        """
        parsed = feedparser.parse(body)
        if not parsed.entries and (not parsed.get("version") or parsed.get("bozo")):
            # A publisher that gates on IP or User-Agent may answer 200 with a
            # challenge page. feedparser reports no version for it, and an
            # unnoticed empty result is indistinguishable from a quiet week —
            # which is precisely what the daily poll exists to prevent. Only a
            # well-formed feed that genuinely holds nothing returns [].
            raise FeedError(
                f"{source.key}: response is not a feed "
                f"({parsed.get('bozo_exception') or 'no recognised feed format'})"
            )

        items = []
        for entry in parsed.entries:
            title = clean_text(entry.get("title", ""))
            link = _link(entry, source.url)
            if not title or not link:
                continue
            items.append(RawItem(
                source_key=source.key,
                title=title,
                url=link,
                published=entry_date(entry),
                summary=clean_text(_body(entry)),
                authors=_authors(entry),
                categories=tuple(
                    clean_text(tag.get("term", "")) for tag in entry.get("tags") or []
                    if tag.get("term")
                ),
                guid=entry.get("id") or entry.get("guid") or None,
            ))
        return items
