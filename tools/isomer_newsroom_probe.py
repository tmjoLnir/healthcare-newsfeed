#!/usr/bin/env python3
"""Report what a poll of an Isomer listing would see.

The Singapore agencies on Isomer Next publish no feed, so `verify_feeds.py`'s
reachability and retention numbers do not apply to them. This prints the same
kind of summary from the listing index instead: how many items land in each
window, split by the agency's own categories, and the newest few by name.

It drives `sources/isomer.py`, so what it reports is what the poller would
store — including the byte-range request, the per-source record cap, and the
recasing of MOH's all-capitals headlines.

Reads the source row from config/sources.yaml, so the cap and the url are the
configured ones rather than a second copy of them here.

Usage:
    python tools/isomer_newsroom_probe.py [KEY] [--days N] [--json out.json]
    python tools/isomer_newsroom_probe.py hsa_sg
    python tools/isomer_newsroom_probe.py --whole-page   # ignore the byte range

KEY is a source key using the isomer_newsroom adapter; it defaults to moh_sg.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from healthcare_newsfeed.config import load_sources
from healthcare_newsfeed.sources.base import FeedError
from healthcare_newsfeed.sources.isomer import (
    MAX_ITEMS,
    WINDOW_BYTES,
    IsomerNewsroomAdapter,
)

CONFIG = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"
ADAPTER_NAME = "isomer_newsroom"


def load_source(key: str):
    """The configured row for `key`, so the probe cannot drift from the poll."""
    rows = {source.key: source for source in load_sources(CONFIG)}
    source = rows.get(key)
    if source is None or source.adapter != ADAPTER_NAME:
        usable = sorted(k for k, row in rows.items() if row.adapter == ADAPTER_NAME)
        raise SystemExit(
            f"{key!r} is not a configured {ADAPTER_NAME} source — try {usable}"
        )
    return source


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key", nargs="?", default="moh_sg",
                    help="source key from config/sources.yaml (default: moh_sg)")
    ap.add_argument("--days", type=int, default=90, help="extra reporting window")
    ap.add_argument("--whole-page", action="store_true",
                    help="fetch all 7.5 MB instead of the byte range the poller uses")
    ap.add_argument("--json", type=Path, help="also write the parsed items here")
    args = ap.parse_args()

    source = load_source(args.key)
    adapter = IsomerNewsroomAdapter()
    headers = None if args.whole_page else {"Range": f"bytes=0-{WINDOW_BYTES}"}
    try:
        with adapter:
            response = adapter.get(source.url, source.key, headers=headers)
            items = adapter.parse(response.content, source)
    except FeedError as exc:
        print(exc, file=sys.stderr)
        return 1

    # Whether the range was granted is worth seeing: CloudFront answers a cache
    # hit with the whole page, so a poll's transfer varies between runs.
    granted = "granted" if response.status_code == 206 else "not granted"
    asked = "not asked for" if args.whole_page else f"asked for {WINDOW_BYTES:,}, {granted}"
    print(f"HTTP {response.status_code} — {len(response.content):,} bytes ({asked})")
    cap = source.max_items or MAX_ITEMS
    print(f"{source.name} ({source.key}) — {len(items)} items, capped at {cap}")

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
