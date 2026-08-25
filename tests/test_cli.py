"""The poll command.

Offline: a stub adapter is registered into ADAPTERS for the duration of each
test, so the whole path — config, due-ness, store, exit code — runs without
touching the network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from typing import ClassVar

import pytest
import yaml

from healthcare_newsfeed.cli import DUE_GRACE, database_path, is_due, main
from healthcare_newsfeed.models import RawItem
from healthcare_newsfeed.sources import ADAPTERS
from healthcare_newsfeed.sources.base import FeedError
from healthcare_newsfeed.store import Store
from healthcare_newsfeed.telegram import TelegramError

NOW = dt.datetime(2026, 8, 25, 2, 0, tzinfo=dt.UTC)


class FakeAdapter:
    """Returns whatever `responses` says for a source key, or raises it."""

    responses: ClassVar[dict[str, object]] = {}
    closed = 0

    def fetch(self, source):
        outcome = self.responses.get(source.key, [])
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        type(self).closed += 1


@pytest.fixture
def adapter():
    FakeAdapter.responses = {}
    FakeAdapter.closed = 0
    ADAPTERS["fake"] = FakeAdapter
    yield FakeAdapter
    del ADAPTERS["fake"]


@pytest.fixture
def config(tmp_path):
    """Write a sources.yaml using the stub adapter, and return its path."""
    def _config(*keys: str, **overrides) -> str:
        document = {
            "defaults": {"adapter": "fake", "licence": "link_only", "poll_hours": 24,
                         "enabled": True},
            "sources": [
                {"key": key, "name": key.title(), "url": f"https://{key}.test/feed",
                 "weight": 1.0, "sections": ["also_reading"], **overrides.get(key, {})}
                for key in keys
            ],
        }
        path = tmp_path / "sources.yaml"
        path.write_text(yaml.safe_dump(document))
        return str(path)
    return _config


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "newsfeed.db")


@pytest.fixture
def publishable(tmp_path):
    """A sources.yaml whose single source may fill the sections named.

    publish runs against the shipped config/digest.yaml, so the source has
    to be eligible for the sections that carry a `min` — otherwise the issue
    comes back empty for reasons that have nothing to do with the test.
    """
    def _publishable(*sections: str) -> str:
        document = {
            "sources": [{
                # publish never fetches — it reads the store — so the adapter
                # is irrelevant beyond having to be one the config recognises.
                "key": "bbc", "name": "BBC Health", "url": "https://bbc.test/feed",
                "adapter": "rss", "licence": "link_only", "weight": 1.0,
                "sections": list(sections) or ["story_of_week", "journals",
                                               "explainer", "also_reading"],
            }],
        }
        path = tmp_path / "publishable.yaml"
        path.write_text(yaml.safe_dump(document))
        return str(path)
    return _publishable


def item(key: str, slug: str) -> RawItem:
    return RawItem(source_key=key, title=f"{key} {slug}", url=f"https://{key}.test/{slug}",
                   published=NOW, summary="")


def backdate(db_path: str, key: str, hours: float) -> None:
    """Move a source's last successful poll into the past."""
    stamp = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)).isoformat(timespec="seconds")
    with Store(db_path) as store:
        store.conn.execute("UPDATE polls SET last_success = ? WHERE source_key = ?",
                           (stamp, key))
        store.conn.commit()


# --- polling ---------------------------------------------------------------

def test_poll_stores_what_the_adapter_returns(adapter, config, db, capsys):
    adapter.responses = {"bbc": [item("bbc", "a"), item("bbc", "b")]}

    assert main(["poll", "--config", config("bbc"), "--db", db]) == 0

    assert "1/1 sources polled, 2 new items" in capsys.readouterr().out
    with Store(db) as store:
        stored = store.window(NOW - dt.timedelta(days=1), NOW + dt.timedelta(days=1))
    assert sorted(i.title for i in stored) == ["bbc a", "bbc b"]


def test_a_second_run_adds_only_what_is_new(adapter, config, db):
    adapter.responses = {"bbc": [item("bbc", "a")]}
    main(["poll", "--config", config("bbc"), "--db", db])

    adapter.responses = {"bbc": [item("bbc", "a"), item("bbc", "b")]}
    main(["poll", "--config", config("bbc"), "--db", db, "--force"])

    with Store(db) as store:
        assert store.conn.execute("SELECT added FROM polls").fetchone()["added"] == 1


def test_one_dead_source_does_not_stop_the_others(adapter, config, db, capsys):
    adapter.responses = {"dead": FeedError("dead: HTTP 404", status=404),
                         "alive": [item("alive", "a")]}

    assert main(["poll", "--config", config("dead", "alive"), "--db", db]) == 1

    out = capsys.readouterr().out
    assert "1/2 sources polled, 1 new items" in out


def test_adapters_are_closed_even_when_a_source_fails(adapter, config, db):
    adapter.responses = {"dead": FeedError("dead: HTTP 404", status=404)}

    main(["poll", "--config", config("dead"), "--db", db])

    assert adapter.closed == 1


# --- due-ness --------------------------------------------------------------

def test_a_source_never_polled_is_due(db):
    with Store(db) as store:
        store.migrate()
        assert is_due(store, _source("bbc"), NOW)


def test_a_source_polled_just_now_is_not_due(adapter, config, db, capsys):
    adapter.responses = {"bbc": [item("bbc", "a")]}
    main(["poll", "--config", config("bbc"), "--db", db])

    assert main(["poll", "--config", config("bbc"), "--db", db]) == 0

    out = capsys.readouterr().out
    assert "skip" in out and "1 not yet due" in out


def test_force_polls_a_source_that_is_not_due(adapter, config, db, capsys):
    adapter.responses = {"bbc": [item("bbc", "a")]}
    main(["poll", "--config", config("bbc"), "--db", db])

    main(["poll", "--config", config("bbc"), "--db", db, "--force"])

    assert "1/1 sources polled" in capsys.readouterr().out


def test_a_run_arriving_slightly_early_still_polls(adapter, config, db, capsys):
    """The workflow's cadence equals the default poll_hours, so a scheduled
    run that drifts early would otherwise skip a day — from feeds that may
    only hold four."""
    adapter.responses = {"bbc": [item("bbc", "a")]}
    main(["poll", "--config", config("bbc"), "--db", db])
    backdate(db, "bbc", hours=23.5)

    main(["poll", "--config", config("bbc"), "--db", db])

    assert "1/1 sources polled" in capsys.readouterr().out


def test_the_grace_period_does_not_swallow_a_whole_cycle(db):
    """It absorbs scheduler drift, not a second poll on the same day."""
    with Store(db) as store:
        store.migrate()
        store.record_poll("bbc", status="ok", fetched=1, added=1)
        source = _source("bbc")
        polled_at = store.last_successful_poll("bbc")

        assert not is_due(store, source, polled_at)
        assert not is_due(store, source, polled_at + dt.timedelta(hours=22))
        assert is_due(store, source, polled_at + dt.timedelta(hours=24) - DUE_GRACE)
        assert is_due(store, source, polled_at + dt.timedelta(hours=24))


def test_a_failed_poll_stays_due(adapter, config, db):
    """A failure must not start the clock, or a broken source would wait out
    its poll_hours before anything tried again."""
    adapter.responses = {"bbc": FeedError("bbc: HTTP 500")}
    main(["poll", "--config", config("bbc"), "--db", db])

    with Store(db) as store:
        assert store.last_successful_poll("bbc") is None
        assert is_due(store, _source("bbc"), NOW)


# --- what counts as a failed run --------------------------------------------

def test_a_blocked_host_is_reported_but_does_not_fail_the_run(adapter, config, db, capsys):
    """NEJM answers Actions runners with 403 while serving normally elsewhere."""
    adapter.responses = {"nejm": FeedError("nejm: HTTP 403", status=403)}

    assert main(["poll", "--config", config("nejm"), "--db", db]) == 0
    assert "1 blocked by host" in capsys.readouterr().out


def test_strict_makes_a_block_fatal(adapter, config, db):
    adapter.responses = {"nejm": FeedError("nejm: HTTP 403", status=403)}

    assert main(["poll", "--config", config("nejm"), "--db", db, "--strict"]) == 1


def test_a_broken_source_fails_the_run(adapter, config, db):
    """A 404 or an unparseable response is the feed's fault, and the daily
    schedule must not rot quietly."""
    adapter.responses = {"bbc": FeedError("bbc: response is not a feed")}

    assert main(["poll", "--config", config("bbc"), "--db", db]) == 1


def test_tolerate_failure_keeps_a_flaky_host_out_of_the_exit_code(adapter, config, db, capsys):
    """Nature Medicine's config note, made behaviour."""
    adapter.responses = {"nature": FeedError("nature: HTTP 500")}
    path = config("nature", nature={"tolerate_failure": True})

    assert main(["poll", "--config", path, "--db", db]) == 0
    assert "(tolerated)" in capsys.readouterr().out


# --- flags and paths ---------------------------------------------------------

def test_dry_run_fetches_but_writes_nothing(adapter, config, db, capsys):
    adapter.responses = {"bbc": [item("bbc", "a")]}

    assert main(["poll", "--config", config("bbc"), "--db", db, "--dry-run"]) == 0

    assert "nothing was written" in capsys.readouterr().out
    with Store(db) as store:
        assert store.conn.execute("SELECT count(*) FROM items").fetchone()[0] == 0
        assert store.conn.execute("SELECT count(*) FROM polls").fetchone()[0] == 0


def test_only_selects_named_sources(adapter, config, db, capsys):
    adapter.responses = {"a": [item("a", "1")], "b": [item("b", "1")]}

    main(["poll", "--config", config("a", "b"), "--db", db, "--only", "a"])

    out = capsys.readouterr().out
    assert "1/1 sources polled" in out and "\nb " not in out


def test_an_unknown_source_key_is_an_error(adapter, config, db, capsys):
    assert main(["poll", "--config", config("a"), "--db", db, "--only", "nope"]) == 2
    assert "unknown source key" in capsys.readouterr().err


def test_a_broken_config_is_reported_not_raised(tmp_path, db, capsys):
    path = tmp_path / "sources.yaml"
    path.write_text("sources:\n  - key: x\n    name: X\n    url: u\n    weigth: 1.0\n"
                    "    sections: [a]\n")

    assert main(["poll", "--config", str(path), "--db", db]) == 2
    assert "unknown field" in capsys.readouterr().err


def test_database_path_prefers_the_flag_then_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWSFEED_DB", "/from/env.db")
    assert database_path(argparse.Namespace(db=tmp_path / "flag.db")) == tmp_path / "flag.db"
    assert str(database_path(argparse.Namespace(db=None))) == "/from/env.db"

    monkeypatch.delenv("NEWSFEED_DB")
    assert str(database_path(argparse.Namespace(db=None))) == "data/newsfeed.db"


# --- publishing --------------------------------------------------------------

# Distinct stories, not one story numbered six ways: dedupe clusters on
# title similarity and takes one item per cluster, so near-identical titles
# would leave a publishable issue holding a single item.
STORIES = [
    "Consent and autonomy at the end of life",
    "Why the NHS waiting list keeps growing",
    "Ebola outbreak update from the Democratic Republic of Congo",
    "A phase 3 trial of a new antiviral therapy",
    "Medicaid funding reform clears its first committee",
    "How rapid genome sequencing changed one paediatric unit",
    "Vaccination campaigns resume after a measles resurgence",
    "The ethics of rationing intensive care beds",
]


def stock_store(db_path: str, count: int = len(STORIES)) -> None:
    """Put a week's worth of candidates in the store, ready to publish."""
    with Store(db_path) as store:
        store.migrate()
        store.upsert([
            RawItem(source_key="bbc", title=STORIES[n % len(STORIES)],
                    url=f"https://bbc.test/{n}", published=NOW,
                    summary="A plainly worded account of what happened and why it matters "
                            "to a reader preparing for a medical school interview.")
            for n in range(count)
        ])


@pytest.fixture
def channel(monkeypatch):
    """Capture what publish would post, in place of the real client."""
    posted: list[str] = []

    class FakeClient:
        chat_id = "@test_channel"
        fail_on: ClassVar[int | None] = None

        def send(self, text, *, parse_mode="HTML", disable_preview=False):
            if self.fail_on is not None and len(posted) + 1 == self.fail_on:
                raise TelegramError("HTTP 429: Too Many Requests")
            posted.append(text)
            return {"message_id": len(posted)}

        def close(self):
            pass

    FakeClient.fail_on = None
    monkeypatch.setattr("healthcare_newsfeed.cli.TelegramClient",
                        type("Factory", (), {"from_env": staticmethod(FakeClient)}))
    FakeClient.posted = posted
    return FakeClient


def test_publish_posts_the_issue_and_records_it(publishable, db, channel, capsys):
    stock_store(db)

    assert main(["publish", "--config", publishable(), "--db", db]) == 0

    assert len(channel.posted) == 1
    assert "This Week in Medicine" in channel.posted[0]
    assert "issue 1 published to @test_channel" in capsys.readouterr().out
    with Store(db) as store:
        assert store.next_issue() == 2
        assert store.conn.execute(
            "SELECT count(*) FROM items WHERE published_in_issue = 1").fetchone()[0] > 0


def test_a_published_item_is_never_carried_again(publishable, db, channel):
    """select() excludes what an earlier issue carried; publish is what records it.

    One eligible section, so its `max` of 6 leaves candidates over for a
    second issue — with every section open the whole week fits in issue 1
    and there is nothing left to prove.
    """
    config_path = publishable("also_reading")
    stock_store(db)
    main(["publish", "--config", config_path, "--db", db])
    first = _linked(channel.posted)

    channel.posted.clear()
    main(["publish", "--config", config_path, "--db", db])
    second = _linked(channel.posted)

    assert first and second
    assert not first & second


def _linked(messages: list[str]) -> set[str]:
    """The item URLs a rendered burst carries."""
    return set(re.findall(r"https://bbc\.test/\d+", "\n".join(messages)))


def test_a_dry_run_prints_the_issue_without_posting_or_recording(publishable, db, channel,
                                                                 capsys):
    stock_store(db)

    assert main(["publish", "--config", publishable(), "--db", db, "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert not channel.posted
    assert "This Week in Medicine" in out and "nothing was posted" in out
    with Store(db) as store:
        assert store.next_issue() == 1                                     # not recorded


def test_a_partial_post_records_nothing(publishable, db, channel, capsys, monkeypatch):
    """Recording a half-posted issue would retire the items that never arrived.

    A duplicate is visible in the channel and a human can delete it; an item
    silently retired from every future issue is neither visible nor
    recoverable. So the store is written only once the whole burst lands.
    """
    stock_store(db)
    monkeypatch.setattr("healthcare_newsfeed.cli.render",
                        lambda *a, **k: ["message one", "message two", "message three"])
    channel.fail_on = 2

    assert main(["publish", "--config", publishable(), "--db", db]) == 1

    error = capsys.readouterr().err
    assert "failed on message 2 of 3" in error
    assert "was NOT recorded" in error and "re-run will repost" in error
    assert channel.posted == ["message one"]
    with Store(db) as store:
        assert store.next_issue() == 1
        assert store.conn.execute(
            "SELECT count(*) FROM items WHERE published_in_issue IS NOT NULL").fetchone()[0] == 0


def test_a_failure_on_the_first_message_does_not_warn_about_duplicates(publishable, db,
                                                                       channel, capsys):
    stock_store(db)
    channel.fail_on = 1

    assert main(["publish", "--config", publishable(), "--db", db]) == 1

    error = capsys.readouterr().err
    assert "failed on message 1" in error and "already went out" not in error


def test_publish_says_so_when_the_store_is_empty(publishable, db, channel, capsys):
    assert main(["publish", "--config", publishable(), "--db", db]) == 1

    assert "has `newsfeed poll` run?" in capsys.readouterr().err
    assert not channel.posted


def test_publish_reports_missing_credentials_rather_than_traceback(publishable, db,
                                                                   monkeypatch, capsys):
    stock_store(db)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert main(["publish", "--config", publishable(), "--db", db]) == 2

    assert "BotFather" in capsys.readouterr().err


def test_publish_takes_the_issue_number_it_is_given(publishable, db, channel):
    stock_store(db)

    assert main(["publish", "--config", publishable(), "--db", db, "--issue", "42"]) == 0

    with Store(db) as store:
        assert store.next_issue() == 43


def test_verify_shells_out_to_the_standalone_checker(monkeypatch, capsys):
    """It is deliberately outside the package — CI installs it without one."""
    seen = {}

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Result()

    monkeypatch.setattr("healthcare_newsfeed.cli.subprocess.run", fake_run)
    monkeypatch.setattr("healthcare_newsfeed.cli.VERIFY_SCRIPT", _existing_path())

    assert main(["verify"]) == 0
    assert "verify_feeds.py" in " ".join(seen["command"])


def _existing_path():
    from pathlib import Path
    return Path("tools/verify_feeds.py")


def _source(key: str):
    from healthcare_newsfeed.models import Licence, Source
    return Source(key=key, name=key, url=f"https://{key}.test/feed", adapter="fake",
                  licence=Licence.LINK_ONLY, weight=1.0, sections=("also_reading",))


# --- build -------------------------------------------------------------------

def stocked(db_path: str, adapter, config_path: str) -> None:
    adapter.responses = {"bbc": [
        RawItem("bbc", "Why the NHS waiting list keeps growing", "https://bbc.test/1", NOW,
                "A policy explainer about NHS funding and reform."),
        RawItem("bbc", "Consent and autonomy at the end of life", "https://bbc.test/2", NOW,
                "An ethics discussion of consent."),
        RawItem("bbc", "Ebola outbreak update from the DRC", "https://bbc.test/3", NOW, ""),
    ]}
    main(["poll", "--config", config_path, "--db", db_path])


def test_build_assembles_an_issue(adapter, config, db, tmp_path, capsys):
    path = config("bbc", bbc={"sections": ["story_of_week", "also_reading"]})
    stocked(db, adapter, path)

    code = main(["build", "--config", path, "--db", db])

    out = capsys.readouterr().out
    assert code == 0
    assert "This Week in Medicine — Issue 1" in out
    assert "Story of the week" in out
    assert "candidates published" in out


def test_build_writes_nothing(adapter, config, db):
    path = config("bbc", bbc={"sections": ["story_of_week", "also_reading"]})
    stocked(db, adapter, path)

    main(["build", "--config", path, "--db", db])

    with Store(db) as store:
        assert store.conn.execute("SELECT count(*) FROM issues").fetchone()[0] == 0
        assert store.next_issue() == 1


def test_build_says_so_when_the_store_is_empty(adapter, config, db, capsys):
    assert main(["build", "--config", config("bbc"), "--db", db]) == 1
    assert "has `newsfeed poll` run?" in capsys.readouterr().err


def test_build_takes_the_week_from_the_flag(adapter, config, db, capsys):
    path = config("bbc", bbc={"sections": ["story_of_week", "also_reading"]})
    stocked(db, adapter, path)

    assert main(["build", "--config", path, "--db", db, "--week", "1999-01-01"]) == 1
    assert "1998-12-26 to 1999-01-01" in capsys.readouterr().err


def test_build_rejects_a_week_that_is_not_a_date(adapter, config, db, capsys):
    assert main(["build", "--config", config("bbc"), "--db", db, "--week", "last"]) == 2
    assert "ISO date" in capsys.readouterr().err


def test_build_numbers_the_issue_after_the_last_published(adapter, config, db, capsys):
    path = config("bbc", bbc={"sections": ["story_of_week", "also_reading"]})
    stocked(db, adapter, path)
    with Store(db) as store:
        store.mark_published(7, [])

    main(["build", "--config", path, "--db", db])

    assert "Issue 8" in capsys.readouterr().out
