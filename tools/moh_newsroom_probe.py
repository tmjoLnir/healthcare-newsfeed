#!/usr/bin/env python3
"""Report what a MOH poll would see.

MOH publishes no feed, so `verify_feeds.py`'s reachability and retention
numbers do not apply to it. This prints the same kind of summary from the
newsroom index instead: how many items land in each window, split by MOH's
own categories, and the newest few by name.

It drives `sources/moh.py`, so what it reports is what the poller would
store — including the byte-range request and the recasing of MOH's
all-capitals headlines.

Usage:
    python tools/moh_newsroom_probe.py [--days N] [--json out.json]
    python tools/moh_newsroom_probe.py --whole-page   # ignore the byte range
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from healthcare_newsfeed.models import Licence, Source
from healthcare_newsfeed.sources.base import FeedError
from healthcare_newsfeed.sources.moh import (
    MAX_ITEMS,
    WINDOW_BYTES,
    MohNewsroomAdapter,
)

SOURCE = Source(
    key="moh_sg", name="MOH Singapore", url="https://www.moh.gov.sg/newsroom/",
    adapter="moh_newsroom", licence=Licence.LINK_ONLY, weight=1.0,
    sections=("policy", "also_reading"),
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="extra reporting window")
    ap.add_argument("--whole-page", action="store_true",
                    help="fetch all 7.5 MB instead of the byte range the poller uses")
    ap.add_argument("--json", type=Path, help="also write the parsed items here")
    args = ap.parse_args()

    adapter = MohNewsroomAdapter()
    headers = None if args.whole_page else {"Range": f"bytes=0-{WINDOW_BYTES}"}
    try:
        with adapter:
            response = adapter.get(SOURCE.url, SOURCE.key, headers=headers)
            items = adapter.parse(response.content, SOURCE)
    except FeedError as exc:
        print(exc, file=sys.stderr)
        return 1

    # Whether the range was granted is worth seeing: CloudFront answers a cache
    # hit with the whole page, so a poll's transfer varies between runs.
    granted = "granted" if response.status_code == 206 else "not granted"
    asked = "not asked for" if args.whole_page else f"asked for {WINDOW_BYTES:,}, {granted}"
    print(f"HTTP {response.status_code} — {len(response.content):,} bytes ({asked})")
    print(f"{len(items)} items, capped at {MAX_ITEMS}")

    now = dt.datetime.now(dt.UTC)
    dated = [item for item in items if item.published]
    if not dated:
        print("no dated items — the index shape has changed", file=sys.stderr)
        return 1

    # A byte range holds a fixed number of items, not a fixed span, so any
    # window longer than the oldest item recovered is a floor, not a count.
    covered = (now - min(item.published for item in dated)).days

    print(f"\n{'WINDOW':<10}{'ITEMS':>6}   BY CATEGORY")
    print("-" * 62)
    for days in sorted({7, 30, args.days, 365}):
        recent = [i for i in dated if (now - i.published).days < days]
        mix = ", ".join(f"{name} {count}" for name, count
                        in Counter(c for i in recent for c in i.categories).most_common())
        partial = "  (partial — the fetched window ends here)" if days > covered else ""
        print(f"{str(days) + 'd':<10}{len(recent):>6}   {mix or '—'}{partial}")

    print(f"\nthe fetched window covers {covered} days of history")

    print(f"\nNewest {min(12, len(items))} items, as the poller would store them:")
    for item in items[:12]:
        print(f"  {item.published:%Y-%m-%d}  [{'/'.join(item.categories) or '—':<16}] "
              f"{item.title[:58]}")

    if args.json:
        args.json.write_text(json.dumps([{
            "title": i.title, "url": i.url, "categories": list(i.categories),
            "published": i.published.isoformat() if i.published else None,
        } for i in items], indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
