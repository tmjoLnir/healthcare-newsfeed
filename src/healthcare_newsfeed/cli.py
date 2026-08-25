"""Command-line entrypoint.

    newsfeed poll                 fetch every due source into the store
    newsfeed build [--week ...]   assemble a digest without publishing
    newsfeed publish [--dry-run]  build and post the weekly issue
    newsfeed verify               check feed health (tools/verify_feeds.py)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

from .config import ConfigError, load_digest_template, load_sources
from .digest.render import render
from .digest.template import IssueSpec, resolve
from .models import Digest, Source
from .pipeline.dedupe import cluster
from .pipeline.score import score
from .pipeline.select import select
from .sources import ADAPTERS, Adapter, FeedError
from .store import Store
from .telegram import DryRunClient, TelegramClient, TelegramError

DEFAULT_CONFIG = Path("config/sources.yaml")
DEFAULT_TEMPLATE = Path("config/digest.yaml")
DEFAULT_DB = Path("data/newsfeed.db")
VERIFY_SCRIPT = Path("tools/verify_feeds.py")

# poll_hours defaults to 24 and the workflow runs daily, so "due" is decided
# on an interval the size of the gap being measured. A scheduled run that
# lands slightly early — Actions cron drifts either way — would then find the
# source not-quite-due and skip it, losing a day from a feed that may only
# hold four. The grace period makes the check answer "has it been about a
# day?" rather than "has it been 24 hours to the second?".
DUE_GRACE = dt.timedelta(hours=1)


class NothingToPublish(RuntimeError):
    """The week holds no candidates — an empty store, or a poll that never ran."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="newsfeed", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    poll_parser = commands.add_parser("poll", help="fetch every due source into the store")
    poll_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    poll_parser.add_argument("--db", type=Path, help=f"defaults to $NEWSFEED_DB or {DEFAULT_DB}")
    poll_parser.add_argument("--only", nargs="+", metavar="KEY",
                             help="poll just these source keys")
    poll_parser.add_argument("--force", action="store_true",
                             help="poll every source regardless of when it was last reached")
    poll_parser.add_argument("--strict", action="store_true",
                             help="treat a host-level block as a failure too")
    poll_parser.add_argument("--dry-run", action="store_true",
                             help="fetch and report, but write nothing to the store")
    poll_parser.set_defaults(run=poll)

    build_parser = commands.add_parser("build", help="assemble a digest without publishing")
    build_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    build_parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    build_parser.add_argument("--db", type=Path, help=f"defaults to $NEWSFEED_DB or {DEFAULT_DB}")
    build_parser.add_argument("--week", help="ISO date inside the week to build (default: today)")
    build_parser.add_argument("--issue", type=int, help="default: one past the last published")
    build_parser.set_defaults(run=build)

    publish_parser = commands.add_parser("publish", help="build and post the weekly issue")
    publish_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    publish_parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    publish_parser.add_argument("--db", type=Path, help=f"defaults to $NEWSFEED_DB or {DEFAULT_DB}")
    publish_parser.add_argument("--week", help="ISO date inside the week to publish (default: today)")
    publish_parser.add_argument("--issue", type=int, help="default: one past the last published")
    publish_parser.add_argument("--dry-run", action="store_true",
                                help="render and print the issue without posting, "
                                     "and without recording it as published")
    publish_parser.set_defaults(run=publish)

    verify_parser = commands.add_parser("verify", help="check feed health")
    verify_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    verify_parser.add_argument("--strict", action="store_true")
    verify_parser.set_defaults(run=verify)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2


def database_path(args: argparse.Namespace) -> Path:
    """--db, else $NEWSFEED_DB, else the path .env.example documents."""
    return args.db or Path(os.environ.get("NEWSFEED_DB") or DEFAULT_DB)


def is_due(store: Store, source: Source, now: dt.datetime) -> bool:
    last = store.last_successful_poll(source.key)
    if last is None:
        return True
    return (now - last) >= dt.timedelta(hours=source.poll_hours) - DUE_GRACE


def poll(args: argparse.Namespace) -> int:
    """Fetch every due source into the store.

    One dead source does not stop the others: adapters raise, and this is the
    caller that decides what a failure means. A host-level block is reported
    and tolerated — NEJM answers Actions runners with 403 while serving
    normally elsewhere — while a genuine failure fails the run so the daily
    schedule cannot rot quietly. Sources marked tolerate_failure in the
    config are reported either way but never fail the run.
    """
    sources = load_sources(args.config)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {source.key for source in sources}
        if unknown:
            print(f"unknown source key(s): {sorted(unknown)}", file=sys.stderr)
            return 2
        sources = [source for source in sources if source.key in wanted]

    now = dt.datetime.now(dt.UTC)
    adapters: dict[str, Adapter] = {}
    failed: list[str] = []
    blocked: list[str] = []
    polled = skipped = added_total = 0

    header = f"{'SOURCE':<18} {'STATUS':<8} {'FETCHED':>7} {'NEW':>4}  DETAIL"
    print(header)
    print("-" * len(header))

    with Store(database_path(args)) as store:
        store.migrate()
        try:
            for source in sources:
                if not args.force and not is_due(store, source, now):
                    skipped += 1
                    last = store.last_successful_poll(source.key)
                    hours = (now - last).total_seconds() / 3600
                    print(f"{source.key:<18} {'skip':<8} {'-':>7} {'-':>4}  "
                          f"polled {hours:.1f}h ago, every {source.poll_hours}h")
                    continue

                adapter = adapters.setdefault(source.adapter, ADAPTERS[source.adapter]())
                try:
                    items = adapter.fetch(source)
                except FeedError as exc:
                    status = "blocked" if exc.blocked else "failed"
                    detail = str(exc)
                    if source.tolerate_failure:
                        detail += "  (tolerated)"
                    elif exc.blocked:
                        blocked.append(source.key)
                    else:
                        failed.append(source.key)
                    if not args.dry_run:
                        store.record_poll(source.key, status=status, detail=detail)
                    print(f"{source.key:<18} {status:<8} {'-':>7} {'-':>4}  {detail[:60]}")
                    continue

                polled += 1
                added = 0 if args.dry_run else store.upsert(items)
                added_total += added
                if not args.dry_run:
                    store.record_poll(source.key, status="ok", fetched=len(items), added=added)
                print(f"{source.key:<18} {'ok':<8} {len(items):>7} "
                      f"{'-' if args.dry_run else added:>4}  ")
        finally:
            for adapter in adapters.values():
                adapter.close()

    print(f"\n{polled}/{len(sources)} sources polled, {added_total} new items"
          f"{f', {skipped} not yet due' if skipped else ''}")
    if args.dry_run:
        print("dry run — nothing was written to the store")
    if blocked:
        print(f"{len(blocked)} blocked by host (IP reputation, not a broken source): "
              f"{', '.join(blocked)}")

    fatal = failed + (blocked if args.strict else [])
    if fatal:
        print(f"{len(fatal)} source(s) failed: {', '.join(fatal)}", file=sys.stderr)
        return 1
    return 0


def week_bounds(args: argparse.Namespace) -> tuple[dt.datetime, dt.datetime]:
    """The seven days ending at the end of --week, or ending now.

    The end is exclusive, and lands on a whole second past the last moment
    meant to be included: window() is half-open and the store keeps
    timestamps to the second, so an end of exactly "now" would drop anything
    stored during the current second — which is every item, if someone runs
    poll and build back to back.

    UTC throughout, matching the store. digest.yaml's Asia/Singapore timezone
    governs when an issue is published and how its dates read, which is
    render's business rather than the window's.
    """
    if not args.week:
        last = dt.datetime.now(dt.UTC).replace(microsecond=0)
    else:
        try:
            day = dt.date.fromisoformat(args.week)
        except ValueError as exc:
            raise ConfigError(f"--week wants an ISO date like 2026-08-23, not {args.week!r}") from exc
        last = dt.datetime.combine(day, dt.time.max, tzinfo=dt.UTC).replace(microsecond=0)
    end = last + dt.timedelta(seconds=1)
    return end - dt.timedelta(days=7), end


def assemble(args: argparse.Namespace, store: Store) -> tuple[Digest, dict, IssueSpec, int]:
    """Run the pipeline for one week: the digest, and what shaped it.

    Shared by `build` and `publish` so the issue printed by one is the issue
    posted by the other. Raises NothingToPublish when the week holds no
    candidates at all, which is a different failure from a week that holds
    some and fills no section: the first means the poll is not running, the
    second means it is and the week was thin.
    """
    sources = {source.key: source for source in load_sources(args.config)}
    template = load_digest_template(args.template)
    spec = resolve(template)
    start, end = week_bounds(args)

    candidates = store.window(start, end)
    issue = args.issue if args.issue is not None else store.next_issue()
    if not candidates:
        raise NothingToPublish(
            f"no candidates stored for {start:%Y-%m-%d} to "
            f"{end - dt.timedelta(seconds=1):%Y-%m-%d} — has `newsfeed poll` run?"
        )

    cluster(candidates)
    score(candidates, sources)
    digest = select(candidates, template, issue, sources, week=(start, end))
    return digest, sources, spec, len(candidates)


def build(args: argparse.Namespace) -> int:
    """Assemble the issue for a week and print it, writing nothing.

    The listing below is a working view rather than the published one — it
    shows the scores that put each item where it is. For what actually goes
    out, formatted and licence-capped, use `newsfeed publish --dry-run`.
    """
    with Store(database_path(args)) as store:
        store.migrate()
        try:
            digest, _, spec, considered = assemble(args, store)
        except NothingToPublish as exc:
            print(exc, file=sys.stderr)
            return 1

    _print_digest(digest, spec, considered)
    if not digest.sections:
        print("nothing qualified for any section", file=sys.stderr)
        return 1
    return 0


def publish(args: argparse.Namespace) -> int:
    """Build the weekly issue and post it to the channel.

    The store is written only once the whole burst has landed. A digest goes
    out as several messages, and `mark_published` is what stops an item ever
    being carried again — so recording a half-posted issue would retire the
    items in the sections that never arrived, invisibly and permanently.
    Reposting a duplicate is visible and a human can delete it; losing the
    ethics section is neither. So a partial failure records nothing, says
    how far it got, and exits non-zero.
    """
    with Store(database_path(args)) as store:
        store.migrate()
        try:
            digest, sources, spec, considered = assemble(args, store)
        except NothingToPublish as exc:
            print(exc, file=sys.stderr)
            return 1

        if not digest.sections:
            print("nothing qualified for any section — not posting an empty issue",
                  file=sys.stderr)
            return 1

        messages = render(digest, sources, spec)
        items = [item for section in digest.sections for item in section.items]
        print(f"{spec.title} — Issue {digest.issue}: {len(items)} of {considered} "
              f"candidates, {len(messages)} message(s)")

        client = DryRunClient() if args.dry_run else TelegramClient.from_env()
        sent = 0
        try:
            for message in messages:
                client.send(message, disable_preview=True)
                sent += 1
        except TelegramError as exc:
            print(f"posting failed on message {sent + 1} of {len(messages)}: {exc}",
                  file=sys.stderr)
            if sent:
                print(f"{sent} message(s) already went out; issue {digest.issue} was NOT "
                      f"recorded, so a re-run will repost them — delete them from the "
                      f"channel first", file=sys.stderr)
            return 1
        finally:
            client.close()

        if args.dry_run:
            print(f"dry run — nothing was posted, and issue {digest.issue} was not recorded")
            return 0

        store.mark_published(digest.issue, items)

    print(f"issue {digest.issue} published to {client.chat_id}: "
          f"{sent} message(s), {len(items)} items recorded")
    return 0


def _print_digest(digest: Digest, spec: IssueSpec, considered: int) -> None:
    covers = digest.week_end - dt.timedelta(seconds=1)      # end is exclusive
    print(f"{spec.title} — Issue {digest.issue}")
    print(f"{digest.week_start:%Y-%m-%d} to {covers:%Y-%m-%d}")
    print("─" * 58)

    published = 0
    for section in digest.sections:
        print(f"\n{section.heading}")
        for item in section.items:
            published += 1
            print(f"  {item.score:6.3f}  {item.title}")
            print(f"          {item.source_key} · {item.canonical_url}")

    carried = {section.key for section in digest.sections}
    empty = [section.key for section in spec.sections if section.key not in carried]
    print(f"\n{published} of {considered} candidates published")
    if empty:
        # min: 0 sections are meant to disappear rather than carry filler.
        print(f"sections with nothing to carry: {', '.join(empty)}")


def verify(args: argparse.Namespace) -> int:
    """Run the feed health checker, which lives outside the package.

    tools/verify_feeds.py is deliberately standalone — CI installs it without
    the package — so this shells out rather than importing it.
    """
    if not VERIFY_SCRIPT.exists():
        print(f"{VERIFY_SCRIPT} not found — run this from the repository root, "
              f"or call the script directly", file=sys.stderr)
        return 2
    command = [sys.executable, str(VERIFY_SCRIPT), str(args.config)]
    if args.strict:
        command.append("--strict")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
