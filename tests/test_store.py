"""SQLite persistence.

The store is what makes a weekly digest possible on feeds that turn over in
four days, so the properties worth guarding are the archival ones: an item
seen once stays put, keeps the timestamp it arrived with, and survives the
process that wrote it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from healthcare_newsfeed.models import RawItem
from healthcare_newsfeed.store import Store, item_id

MONDAY = dt.datetime(2026, 8, 24, tzinfo=dt.UTC)
WEEK = (MONDAY, MONDAY + dt.timedelta(days=7))


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "newsfeed.db") as store:
        store.migrate()
        yield store


def raw(url: str, *, key: str = "bbc_health", title: str = "A study",
        published: dt.datetime | None = MONDAY, **kwargs) -> RawItem:
    return RawItem(source_key=key, title=title, url=url, published=published, **kwargs)


def test_migrate_is_safe_to_repeat(store):
    store.migrate()
    store.migrate()

    assert store.upsert([raw("https://x.test/a")]) == 1


def test_upsert_counts_only_what_was_new(store):
    assert store.upsert([raw("https://x.test/a"), raw("https://x.test/b")]) == 2
    assert store.upsert([raw("https://x.test/b"), raw("https://x.test/c")]) == 1
    assert store.upsert([raw("https://x.test/c")]) == 0


def test_upsert_keys_on_the_canonical_url(store):
    """BBC appends at_medium=RSS to every feed link; STAT appends utm_*.

    The same article therefore arrives with different query strings from one
    poll to the next, and each one would otherwise become a separate row and
    a separate candidate for the same slot in the issue.
    """
    added = store.upsert([
        raw("https://www.bbc.co.uk/news/articles/c9w4?at_medium=RSS&at_campaign=rss"),
        raw("https://www.bbc.co.uk/news/articles/c9w4"),
        raw("https://www.bbc.co.uk/news/articles/c9w4?utm_source=twitter"),
    ])

    assert added == 1
    assert store.window(*WEEK)[0].canonical_url == "https://www.bbc.co.uk/news/articles/c9w4"


def test_upsert_skips_items_with_no_usable_url(store):
    assert store.upsert([raw(""), raw("   ")]) == 0


def test_a_second_sighting_does_not_rewrite_the_first(store):
    """first_seen is what window() selects on, so re-polling must not move it.

    MedPage and JAMA hold four days of history; an item captured on Monday
    has to stay in Monday's week even though the poll sees it again all week,
    and the headline we captured is the one the issue was built from.
    """
    store.upsert([raw("https://x.test/a", title="Original headline")])
    first = store.window(*WEEK)[0]

    store.upsert([raw("https://x.test/a", title="Rewritten headline")])
    again = store.window(*WEEK)[0]

    assert again.first_seen == first.first_seen
    assert again.title == "Original headline"


def test_window_is_half_open(store):
    store.upsert([raw("https://x.test/a")])
    seen = store.window(*WEEK)[0].first_seen

    assert store.window(seen, seen + dt.timedelta(seconds=1)) != []
    assert store.window(seen - dt.timedelta(seconds=1), seen) == []
    assert store.window(seen + dt.timedelta(seconds=1), seen + dt.timedelta(days=1)) == []


def test_window_excludes_other_weeks(store):
    store.upsert([raw("https://x.test/a")])

    assert store.window(MONDAY - dt.timedelta(days=14), MONDAY - dt.timedelta(days=7)) == []


def test_window_round_trips_the_stored_fields(store):
    store.upsert([raw("https://x.test/a?utm_source=rss", key="kff", title="Ebola",
                      summary="A summary", categories=("Global Health", "Policy"))])

    item = store.window(*WEEK)[0]

    assert item.id == item_id("https://x.test/a")
    assert item.canonical_url == "https://x.test/a"
    assert item.source_key == "kff"
    assert item.title == "Ebola"
    assert item.summary == "A summary"
    assert item.categories == ("Global Health", "Policy")
    assert item.published == MONDAY
    assert item.first_seen.tzinfo is not None
    assert (item.cluster_id, item.score, item.section, item.published_in_issue) == \
        (None, None, None, None)


def test_window_keeps_undated_items(store):
    """WHO news items carry no date at all; they are still candidates."""
    store.upsert([raw("https://x.test/undated", published=None)])

    (item,) = store.window(*WEEK)
    assert item.published is None


def test_window_orders_newest_first(store):
    store.upsert([
        raw("https://x.test/old", published=MONDAY),
        raw("https://x.test/new", published=MONDAY + dt.timedelta(days=2)),
    ])

    assert [i.canonical_url for i in store.window(*WEEK)] == \
        ["https://x.test/new", "https://x.test/old"]


def test_mark_published_records_the_issue_and_its_items(store):
    store.upsert([raw("https://x.test/a"), raw("https://x.test/b")])
    carried, held_back = store.window(*WEEK)
    carried.section, carried.score, carried.cluster_id = "journals", 8.5, "c1"

    store.mark_published(12, [carried])

    assert carried.published_in_issue == 12          # the in-memory digest too
    stored = {i.canonical_url: i for i in store.window(*WEEK)}
    assert stored[carried.canonical_url].published_in_issue == 12
    assert stored[carried.canonical_url].section == "journals"
    assert stored[carried.canonical_url].score == 8.5
    assert stored[carried.canonical_url].cluster_id == "c1"
    assert stored[held_back.canonical_url].published_in_issue is None
    issues = store.conn.execute("SELECT issue, item_count FROM issues").fetchall()
    assert [tuple(row) for row in issues] == [(12, 1)]


def test_mark_published_can_be_repeated(store):
    store.upsert([raw("https://x.test/a")])
    items = store.window(*WEEK)

    store.mark_published(12, items)
    store.mark_published(12, items)

    assert store.conn.execute("SELECT count(*) FROM issues").fetchone()[0] == 1


def test_the_store_outlives_the_process_that_wrote_it(tmp_path):
    """The whole point of polling daily: a fresh runner must find last week.

    A store that did not survive would make the daily poll pointless — see
    the note on persistence in .github/workflows/poll.yml.
    """
    path = tmp_path / "nested" / "newsfeed.db"
    with Store(path) as store:
        store.migrate()
        store.upsert([raw("https://x.test/a")])

    with Store(path) as reopened:
        reopened.migrate()
        assert reopened.upsert([raw("https://x.test/a")]) == 0
        assert len(reopened.window(*WEEK)) == 1


def test_item_id_is_derived_from_the_canonical_url():
    assert item_id("https://x.test/a") == item_id("https://x.test/a")
    assert item_id("https://x.test/a") != item_id("https://x.test/b")


# --- the poll log ------------------------------------------------------------

def test_a_successful_poll_is_recorded(store):
    store.record_poll("bbc_health", status="ok", fetched=52, added=50)

    row = store.conn.execute("SELECT * FROM polls").fetchone()
    assert (row["source_key"], row["status"], row["fetched"], row["added"]) == \
        ("bbc_health", "ok", 52, 50)
    assert store.last_successful_poll("bbc_health") is not None


def test_a_source_never_polled_has_no_last_success(store):
    assert store.last_successful_poll("bbc_health") is None


def test_a_failure_does_not_clear_the_last_success(store):
    """poll_hours is measured from the last success, so a failing source
    stays due and is retried on the next run instead of waiting it out."""
    store.record_poll("bbc_health", status="ok", fetched=52, added=50)
    succeeded_at = store.last_successful_poll("bbc_health")

    store.record_poll("bbc_health", status="failed", detail="HTTP 500")

    assert store.last_successful_poll("bbc_health") == succeeded_at
    row = store.conn.execute("SELECT * FROM polls").fetchone()
    assert row["status"] == "failed"
    assert row["detail"] == "HTTP 500"


def test_a_source_that_has_never_succeeded_stays_without_one(store):
    store.record_poll("nejm", status="blocked", detail="HTTP 403")

    assert store.last_successful_poll("nejm") is None


def test_each_source_keeps_one_row(store):
    for _ in range(3):
        store.record_poll("bbc_health", status="ok", fetched=1, added=1)

    assert store.conn.execute("SELECT count(*) FROM polls").fetchone()[0] == 1


def test_an_unknown_status_is_rejected(store):
    with pytest.raises(ValueError, match="unknown poll status"):
        store.record_poll("bbc_health", status="probably-fine")
