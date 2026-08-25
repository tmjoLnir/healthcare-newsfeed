#!/usr/bin/env python3
"""Read MOH Singapore's newsroom index without an RSS feed.

MOH publishes no feed — every documented path 404s — but the newsroom page is
an Isomer Next (Next.js) app whose RSC flight payload embeds the complete
8,367-item index, newest first, each record carrying a real publication date,
a category and a title.

The full page is 7.5 MB. The index starts 4.3% in and is sorted newest-first,
and the host honours byte ranges, so a single partial request covers months of
history: the default 500 KB window returns the ~175 most recent items.

This is a measurement tool, not an adapter — it prints what a poll would see so
the volume and the shape can be checked before anything is built on it. See
docs/asia-sources.md.

Usage:
    python tools/moh_newsroom_probe.py [--bytes N] [--days N] [--json out.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import urllib.request
from collections import Counter
from pathlib import Path

URL = "https://www.moh.gov.sg/newsroom/"
ITEM_BASE = "https://www.moh.gov.sg"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 30

# Set REQUESTS_CA_BUNDLE / SSL_CERT_FILE if behind a TLS-terminating proxy.
_CA = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")

# Each index record, in payload order. `description` is present but always
# blank — the index carries no summary, so a summary costs one extra fetch of
# the item page, which is server-rendered and carries the full body.
RECORD = re.compile(
    r'"id":"(?P<slug>/newsroom/[^"]+)",'
    r'"date":"\$D(?P<date>[^"]+)".*?'
    r'"selected":\["(?P<category>[^"]+)"\][^{]*?'
    r'"title":"(?P<title>(?:[^"\\]|\\.)*)"'
)


def fetch(limit: int) -> tuple[int, str]:
    ctx = ssl.create_default_context(cafile=_CA) if _CA else ssl.create_default_context()
    headers = {"User-Agent": UA}
    if limit:
        headers["Range"] = f"bytes=0-{limit}"
    req = urllib.request.Request(URL, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as response:
        return response.status, response.read().decode("utf-8", "replace")


def flight_payload(raw: str) -> str:
    """Concatenate the page's RSC chunks and unescape them.

    A ranged response cuts the final `self.__next_f.push([1,"…"])` mid-string,
    so the tail is taken as-is rather than anchored on its closing `"])`.
    """
    chunks = []
    for part in raw.split('self.__next_f.push([1,"')[1:]:
        end = part.find('"])')
        chunks.append(part if end < 0 else part[:end])
    return "".join(chunks).encode().decode("unicode_escape", "replace")


def parse(raw: str) -> list[dict]:
    items = []
    for match in RECORD.finditer(flight_payload(raw)):
        items.append({
            "url": ITEM_BASE + match["slug"],
            "published": match["date"],
            "category": match["category"],
            # Index titles are ALL CAPS; a renderer would need to title-case them.
            "title": match["title"].encode().decode("unicode_escape", "replace"),
        })
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bytes", type=int, default=500_000,
                    help="size of the Range window; 0 fetches the whole 7.5 MB page")
    ap.add_argument("--days", type=int, default=90, help="reporting window")
    ap.add_argument("--json", type=Path, help="also write the parsed items here")
    args = ap.parse_args()

    status, raw = fetch(args.bytes)
    items = parse(raw)
    if not items:
        print(f"HTTP {status}: read {len(raw):,} bytes but recovered no items — "
              f"the page structure has changed", file=sys.stderr)
        return 1

    print(f"HTTP {status} — read {len(raw):,} bytes, recovered {len(items)} items")

    now = dt.datetime.now(dt.UTC)
    def age(item):
        return (now - dt.datetime.fromisoformat(item["published"])).days

    # A Range window holds a fixed number of items, not a fixed span, so any
    # window longer than the oldest item recovered is a floor, not a count.
    covered = age(items[-1])

    print(f"\n{'WINDOW':<10}{'ITEMS':>6}   BY CATEGORY")
    print("-" * 62)
    for window in sorted({7, 30, args.days, 365}):
        recent = [i for i in items if age(i) < window]
        mix = ", ".join(f"{k} {v}" for k, v in Counter(i["category"] for i in recent).most_common())
        truncated = " (partial — widen --bytes)" if window > covered else ""
        print(f"{str(window) + 'd':<10}{len(recent):>6}   {mix or '—'}{truncated}")

    print(f"\nread window covers {covered} days of history")

    print(f"\nNewest {min(12, len(items))} items:")
    for item in items[:12]:
        print(f"  {item['published'][:10]}  [{item['category']:<16}] {item['title'][:58]}")

    if args.json:
        args.json.write_text(json.dumps(items, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
