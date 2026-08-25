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

from .config import ConfigError, load_sources
from .models import Source
from .sources import ADAPTERS, Adapter, FeedError
from .store import Store

DEFAULT_CONFIG = Path("config/sources.yaml")
DEFAULT_DB = Path("data/newsfeed.db")
VERIFY_SCRIPT = Path("tools/verify_feeds.py")

# poll_hours defaults to 24 and the workflow runs daily, so "due" is decided
# on an interval the size of the gap being measured. A scheduled run that
# lands slightly early — Actions cron drifts either way — would then find the
# source not-quite-due and skip it, losing a day from a feed that may only
# hold four. The grace period makes the check answer "has it been about a
# day?" rather than "has it been 24 hours to the second?".
DUE_GRACE = dt.timedelta(hours=1)


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
    build_parser.add_argument("--week", help="ISO date inside the week to build")
    build_parser.set_defaults(run=_unbuilt("build", "pipeline/score.py, pipeline/select.py"))

    publish_parser = commands.add_parser("publish", help="build and post the weekly issue")
    publish_parser.add_argument("--dry-run", action="store_true")
    publish_parser.set_defaults(run=_unbuilt("publish", "digest/render.py, telegram.py"))

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


def _unbuilt(command: str, waiting_on: str):
    def run(args: argparse.Namespace) -> int:
        print(f"`newsfeed {command}` is not built yet — it needs {waiting_on}. "
              f"See the roadmap in README.md.", file=sys.stderr)
        return 2
    return run


if __name__ == "__main__":
    raise SystemExit(main())
