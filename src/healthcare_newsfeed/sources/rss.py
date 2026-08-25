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
import os
import re
from html import unescape
from typing import Self
from urllib.parse import urljoin

import feedparser
import httpx

from ..models import RawItem, Source

# Several publishers gate on User-Agent as well as on IP reputation, and
# answer a default client string with a challenge page rather than the feed.
# Same string as tools/verify_feeds.py, so a source that verifies also polls.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ACCEPT = ("application/rss+xml,application/atom+xml,application/xml,"
          "text/xml,application/json,*/*")

HEADERS = {"User-Agent": UA, "Accept": ACCEPT}

TIMEOUT = 30.0

# A block on IP reputation, not a broken feed: NEJM answers GitHub Actions
# runners this way while serving normally from elsewhere, and BMJ answers
# every datacenter address that way. The poll caller needs to tell the two
# apart to decide whether one dead source should fail the run.
BLOCKED_CODES = frozenset({401, 403, 429})

_SCRIPT = re.compile(r"(?is)<(script|style)\b.*?</\1>")
# Tags that separate words. Dropped without a space they run sentences
# together — the journals' RSS 1.0 summaries open with a `<p>` citation line
# closed straight against the abstract.
_BLOCK = re.compile(r"(?i)</?(?:p|br|div|li|tr|td|h[1-6]|blockquote|section|figure)\b[^>]*>")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class FeedError(RuntimeError):
    """A source could not be fetched, or did not come back as a feed."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def blocked(self) -> bool:
        """True when the host refused us rather than the feed being broken."""
        return self.status in BLOCKED_CODES


def clean_text(markup: str) -> str:
    """Reduce feed markup to the plain text a Telegram message can carry."""
    text = _SCRIPT.sub(" ", markup or "")
    text = _TAG.sub("", _BLOCK.sub(" ", text))
    return _WS.sub(" ", unescape(text)).strip()


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


def _default_client() -> httpx.Client:
    # Honour the CA bundle vars .env.example documents, for deployments behind
    # a TLS-terminating proxy; httpx would otherwise use its own bundle.
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    return httpx.Client(verify=ca or True)


class RssAdapter:
    """Fetch one RSS/Atom source and return its current items.

    Pass a client to poll several sources over one connection pool, or to
    drive the adapter from captured responses in tests.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.Client:
        """Built on first use, so parsing a captured response opens nothing."""
        if self._client is None:
            self._client = _default_client()
        return self._client

    def fetch(self, source: Source) -> list[RawItem]:
        try:
            # Headers, timeout and redirects go on the request, not the
            # client: a caller sharing one connection pool across the poll
            # would otherwise fetch as python-httpx, which is enough on its
            # own to get a challenge page from several of these publishers.
            response = self.client.get(source.url, headers=HEADERS, timeout=TIMEOUT,
                                       follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FeedError(f"{source.key}: HTTP {exc.response.status_code}",
                            status=exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise FeedError(f"{source.key}: {exc}") from exc
        return self.parse(response.content, source)

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

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
