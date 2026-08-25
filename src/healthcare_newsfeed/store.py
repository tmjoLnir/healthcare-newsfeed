"""SQLite persistence.

Load-bearing, not an optimisation: five upstream feeds retain less than a
week of history (MedPage and JAMA Online First turn over in ~4 days), so
items must be captured on a daily poll and held until the weekly publish.
See README "Why polling is daily".

Tables
------
items     one row per canonical_url; append-only apart from pipeline columns
issues    one row per published weekly digest

Timestamps are stored as UTC ISO-8601 strings to the second, so SQLite's
lexicographic ordering is chronological and a window query is a plain BETWEEN.
A naive datetime handed to any method is read as UTC.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Self

from .models import Item, RawItem
from .pipeline.dedupe import canonical_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    issue        INTEGER PRIMARY KEY,
    published_at TEXT NOT NULL,
    item_count   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id                 TEXT PRIMARY KEY,
    source_key         TEXT NOT NULL,
    title              TEXT NOT NULL,
    canonical_url      TEXT NOT NULL UNIQUE,
    published          TEXT,
    first_seen         TEXT NOT NULL,
    summary            TEXT NOT NULL DEFAULT '',
    categories         TEXT NOT NULL DEFAULT '[]',
    cluster_id         TEXT,
    score              REAL,
    section            TEXT,
    published_in_issue INTEGER REFERENCES issues(issue)
);

-- window() scans by first_seen; select() excludes what earlier issues carried.
CREATE INDEX IF NOT EXISTS items_first_seen ON items (first_seen);
CREATE INDEX IF NOT EXISTS items_issue ON items (published_in_issue);
"""


def item_id(canonical: str) -> str:
    """The stable id for a canonical URL: same URL, same row, every poll."""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _iso(value: dt.datetime) -> str:
    aware = value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value
    return aware.astimezone(dt.UTC).isoformat(timespec="seconds")


def _from_iso(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def _to_item(row: sqlite3.Row) -> Item:
    return Item(
        id=row["id"],
        source_key=row["source_key"],
        title=row["title"],
        canonical_url=row["canonical_url"],
        published=_from_iso(row["published"]),
        first_seen=_from_iso(row["first_seen"]),
        summary=row["summary"],
        categories=tuple(json.loads(row["categories"])),
        cluster_id=row["cluster_id"],
        score=row["score"],
        section=row["section"],
        published_in_issue=row["published_in_issue"],
    )


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if self.path.name != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def migrate(self) -> None:
        """Create tables if absent. Safe to call on every run."""
        with self.conn:
            self.conn.executescript(SCHEMA)

    def upsert(self, items: list[RawItem]) -> int:
        """Insert unseen items. Returns the count actually new.

        Insert-only by design. A feed that re-runs a story with an edited
        headline must not overwrite what we captured on the day, and
        first_seen has to keep meaning "when this reached the store" — it is
        what window() selects on, and rewriting it would let an item that
        arrived early in the week slide into the next one.
        """
        now = _iso(dt.datetime.now(dt.UTC))
        rows = []
        for raw in items:
            url = canonical_url(raw.url)
            if not url:
                continue                      # nothing to key the row on
            rows.append((
                item_id(url), raw.source_key, raw.title, url,
                _iso(raw.published) if raw.published else None,
                now, raw.summary, json.dumps(list(raw.categories)),
            ))

        before = self.conn.total_changes
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO items "
                "(id, source_key, title, canonical_url, published, first_seen, "
                " summary, categories) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return self.conn.total_changes - before

    def window(self, start: dt.datetime, end: dt.datetime) -> list[Item]:
        """Every stored item first seen in [start, end) — the digest candidates.

        Items already carried by an earlier issue are included, with
        published_in_issue set; excluding them is select()'s call, not the
        store's, and scoring uses the whole week either way.
        """
        cursor = self.conn.execute(
            "SELECT * FROM items WHERE first_seen >= ? AND first_seen < ? "
            "ORDER BY COALESCE(published, first_seen) DESC, id",
            (_iso(start), _iso(end)),
        )
        return [_to_item(row) for row in cursor]

    def mark_published(self, issue: int, items: list[Item]) -> None:
        """Record which items went out, so later issues never repeat them.

        Also persists the pipeline columns as they stood at publication, and
        sets published_in_issue on the passed items so the in-memory digest
        matches the database. Re-running it for the same issue is harmless.
        """
        now = _iso(dt.datetime.now(dt.UTC))
        with self.conn:
            self.conn.execute(
                "INSERT INTO issues (issue, published_at, item_count) VALUES (?, ?, ?) "
                "ON CONFLICT(issue) DO UPDATE SET published_at = excluded.published_at, "
                "item_count = excluded.item_count",
                (issue, now, len(items)),
            )
            self.conn.executemany(
                "UPDATE items SET cluster_id = ?, score = ?, section = ?, "
                "published_in_issue = ? WHERE id = ?",
                [(i.cluster_id, i.score, i.section, issue, i.id) for i in items],
            )
        for item in items:
            item.published_in_issue = issue

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
