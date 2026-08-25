"""SQLite persistence.

Load-bearing, not an optimisation: five upstream feeds retain less than a
week of history (MedPage and JAMA Online First turn over in ~4 days), so
items must be captured on a daily poll and held until the weekly publish.
See README "Why polling is daily".

Tables
------
items     one row per canonical_url; append-only apart from pipeline columns
issues    one row per published weekly digest
polls     one row per source: when it was last reached, and how it went

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

-- What `newsfeed poll` did last time, per source. last_success is what makes
-- poll_hours mean anything: without it every run would re-fetch every source,
-- and a failure would be invisible the moment the run's output scrolled away.
CREATE TABLE IF NOT EXISTS polls (
    source_key   TEXT PRIMARY KEY,
    last_attempt TEXT NOT NULL,
    last_success TEXT,
    status       TEXT NOT NULL,
    fetched      INTEGER NOT NULL DEFAULT 0,
    added        INTEGER NOT NULL DEFAULT 0,
    detail       TEXT
);
"""

POLL_STATUSES = ("ok", "blocked", "failed")
"""blocked is the host refusing us — see sources/base.py BLOCKED_CODES."""


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

    def record_poll(self, source_key: str, *, status: str, fetched: int = 0,
                    added: int = 0, detail: str | None = None) -> None:
        """Note how one source's poll went.

        A failed attempt updates last_attempt but leaves last_success alone,
        so a source that is failing stays due and is retried on the next run
        rather than waiting out its poll_hours.
        """
        if status not in POLL_STATUSES:
            raise ValueError(f"unknown poll status {status!r}; expected one of {POLL_STATUSES}")
        now = _iso(dt.datetime.now(dt.UTC))
        with self.conn:
            self.conn.execute(
                "INSERT INTO polls "
                "(source_key, last_attempt, last_success, status, fetched, added, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_key) DO UPDATE SET "
                "  last_attempt = excluded.last_attempt, "
                "  last_success = COALESCE(excluded.last_success, polls.last_success), "
                "  status = excluded.status, "
                "  fetched = excluded.fetched, "
                "  added = excluded.added, "
                "  detail = excluded.detail",
                (source_key, now, now if status == "ok" else None, status,
                 fetched, added, detail),
            )

    def last_successful_poll(self, source_key: str) -> dt.datetime | None:
        """When this source last came back with items, or None if never."""
        row = self.conn.execute(
            "SELECT last_success FROM polls WHERE source_key = ?", (source_key,)
        ).fetchone()
        return _from_iso(row["last_success"]) if row else None

    def next_issue(self) -> int:
        """The number the next published issue would carry."""
        row = self.conn.execute("SELECT max(issue) AS latest FROM issues").fetchone()
        return (row["latest"] or 0) + 1

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
