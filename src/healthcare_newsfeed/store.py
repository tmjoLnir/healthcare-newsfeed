"""SQLite persistence.

Load-bearing, not an optimisation: five upstream feeds retain less than a
week of history (MedPage and JAMA Online First turn over in ~4 days), so
items must be captured on a daily poll and held until the weekly publish.
See README "Why polling is daily".

Tables
------
items     one row per canonical_url; append-only apart from pipeline columns
issues    one row per published weekly digest
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import Item, RawItem


class Store:
    def __init__(self, path: Path) -> None:
        raise NotImplementedError

    def migrate(self) -> None:
        """Create tables if absent. Safe to call on every run."""
        raise NotImplementedError

    def upsert(self, items: list[RawItem]) -> int:
        """Insert unseen items. Returns the count actually new."""
        raise NotImplementedError

    def window(self, start: datetime, end: datetime) -> list[Item]:
        """Every stored item first seen in [start, end) — the digest candidates."""
        raise NotImplementedError

    def mark_published(self, issue: int, items: list[Item]) -> None:
        """Record which items went out, so later issues never repeat them."""
        raise NotImplementedError
