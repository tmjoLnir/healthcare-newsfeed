"""Shared fixtures.

Feed samples in tests/fixtures/ are captured responses, so the suite runs
without network access and stays stable when a publisher changes its
output. Refresh them with tools/verify_feeds.py when a source changes shape.
"""

from __future__ import annotations

import datetime as dt
import itertools
from pathlib import Path

import pytest

from healthcare_newsfeed.models import Item, Licence, Source

FIXTURES = Path(__file__).parent / "fixtures"

MONDAY = dt.datetime(2026, 8, 24, tzinfo=dt.UTC)


@pytest.fixture
def fixture_bytes():
    def _load(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()
    return _load


@pytest.fixture
def make_item():
    """Build a stored Item directly, without going through a poll.

    ids are sequential and zero-padded so "lowest id" is predictable — the
    clusterer names a cluster after its lowest member.
    """
    counter = itertools.count(1)

    def _make(title: str = "A study", *, source: str = "bbc_health",
              published: dt.datetime | None = MONDAY, first_seen: dt.datetime = MONDAY,
              summary: str = "", categories: tuple[str, ...] = (),
              item_id: str | None = None, **overrides) -> Item:
        number = next(counter)
        return Item(
            id=item_id or f"item{number:04d}",
            source_key=source,
            title=title,
            canonical_url=f"https://{source}.test/{number}",
            published=published,
            first_seen=first_seen,
            summary=summary,
            categories=categories,
            **overrides,
        )
    return _make


@pytest.fixture
def make_source():
    def _make(key: str, *, sections: tuple[str, ...] = ("also_reading",),
              weight: float = 1.0, licence: Licence = Licence.LINK_ONLY) -> Source:
        return Source(key=key, name=key, url=f"https://{key}.test/feed", adapter="rss",
                      licence=licence, weight=weight, sections=sections)
    return _make
