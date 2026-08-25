"""Adapter protocol, and the HTTP plumbing both adapters share."""

from __future__ import annotations

import os
import re
from html import unescape
from typing import Protocol, Self

import httpx

from ..models import RawItem, Source


class Adapter(Protocol):
    """Fetch one source and return its current items.

    Adapters do no deduplication, scoring or filtering — they only translate
    an upstream format into RawItem. Network and parse errors are raised;
    the caller decides whether one dead source fails the run.
    """

    def fetch(self, source: Source) -> list[RawItem]:
        ...


ADAPTERS: dict[str, type[Adapter]] = {}
"""Registry keyed by the `adapter:` field in sources.yaml."""


# --- HTTP -------------------------------------------------------------------

# Several publishers gate on User-Agent as well as on IP reputation, and
# answer a default client string with a challenge page rather than the feed.
# Same string as tools/verify_feeds.py, so a source that verifies also polls.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ACCEPT = ("application/rss+xml,application/atom+xml,application/xml,"
          "text/xml,application/json,*/*")

HEADERS = {"User-Agent": UA, "Accept": ACCEPT}

TIMEOUT = 30.0

# A block on IP reputation, not a broken source: NEJM answers GitHub Actions
# runners this way while serving normally from elsewhere, and BMJ answers
# every datacenter address that way. The poll caller needs to tell the two
# apart to decide whether one dead source should fail the run.
BLOCKED_CODES = frozenset({401, 403, 429})


class FeedError(RuntimeError):
    """A source could not be fetched, or did not come back in the shape it should."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def blocked(self) -> bool:
        """True when the host refused us rather than the source being broken."""
        return self.status in BLOCKED_CODES


def default_client() -> httpx.Client:
    # Honour the CA bundle vars .env.example documents, for deployments behind
    # a TLS-terminating proxy; httpx would otherwise use its own bundle.
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    return httpx.Client(verify=ca or True)


class HttpSource:
    """One client, one set of headers and one error type for every adapter.

    Pass a client to poll several sources over one connection pool, or to
    drive an adapter from captured responses in tests.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.Client:
        """Built on first use, so parsing a captured response opens nothing."""
        if self._client is None:
            self._client = default_client()
        return self._client

    def get(self, url: str, key: str, params: dict | None = None) -> httpx.Response:
        """GET one source, reporting any failure as FeedError."""
        try:
            # Headers, timeout and redirects go on the request, not the
            # client: a caller sharing one connection pool across the poll
            # would otherwise fetch as python-httpx, which is enough on its
            # own to get a challenge page from several of these publishers.
            response = self.client.get(url, params=params, headers=HEADERS,
                                       timeout=TIMEOUT, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FeedError(f"{key}: HTTP {exc.response.status_code}",
                            status=exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise FeedError(f"{key}: {exc}") from exc
        return response

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# --- text -------------------------------------------------------------------

_SCRIPT = re.compile(r"(?is)<(script|style)\b.*?</\1>")
# Tags that separate words. Dropped without a space they run sentences
# together — the journals' RSS 1.0 summaries open with a `<p>` citation line
# closed straight against the abstract.
_BLOCK = re.compile(r"(?i)</?(?:p|br|div|li|tr|td|h[1-6]|blockquote|section|figure)\b[^>]*>")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(markup: str) -> str:
    """Reduce source markup to the plain text a Telegram message can carry."""
    text = _SCRIPT.sub(" ", markup or "")
    text = _TAG.sub("", _BLOCK.sub(" ", text))
    return _WS.sub(" ", unescape(text)).strip()
