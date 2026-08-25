#!/usr/bin/env python3
"""Check feed health for every source in config/sources.yaml.

Reports reachability, parseability, freshness, weekly volume and — the
number that matters most here — retention window, i.e. how much history the
feed actually holds. A window near seven days means a weekly poll would
silently drop items, which is why this project polls daily.

Usage:
    python tools/verify_feeds.py [config/sources.yaml] [--json out.json]

Exit status is non-zero if any enabled source fails, so it can gate CI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import statistics
import sys
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path

import feedparser
import yaml

# Publishers that gate on IP reputation answer with these rather than serving
# the feed. NEJM does it to GitHub Actions runners; BMJ does it to every
# datacenter address we tried. That is a property of where the check runs, not
# of the feed, so it is reported as BLOCKED and does not fail the sweep unless
# --strict is passed.
BLOCKED_CODES = {401, 403, 429}

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NOW = dt.datetime.now(dt.UTC)
TIMEOUT = 30

# Set REQUESTS_CA_BUNDLE / SSL_CERT_FILE if behind a TLS-terminating proxy.
_CA = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")


def fetch(url: str, headers: dict | None = None) -> tuple[int, bytes]:
    ctx = ssl.create_default_context(cafile=_CA) if _CA else ssl.create_default_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml,application/atom+xml,application/xml,"
                  "text/xml,application/json,*/*",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500]
    except Exception as e:  # noqa: BLE001 - report, don't crash the sweep
        return 0, str(e).encode()[:200]


def entry_date(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return dt.datetime(*value[:6], tzinfo=dt.UTC)
    return None


def text_len(entry) -> int:
    body = ""
    if entry.get("content"):
        body = entry["content"][0].get("value", "")
    body = body or entry.get("summary", "") or entry.get("description", "")
    return len(unescape(re.sub("<[^>]+>", "", body)).strip())


def check_rss(url: str) -> dict:
    code, body = fetch(url)
    row: dict = {"http": code, "ok": False, "blocked": code in BLOCKED_CODES}
    if code != 200:
        row["error"] = body.decode("utf-8", "replace")[:100].strip()
        return row

    parsed = feedparser.parse(body)
    entries = parsed.entries
    row["format"] = parsed.version or "?"
    row["items"] = len(entries)
    if not entries:
        row["error"] = "parsed, but zero entries"
        return row

    dates = sorted((d for d in (entry_date(e) for e in entries) if d), reverse=True)
    if dates:
        row["newest"] = dates[0].strftime("%Y-%m-%d")
        row["oldest"] = dates[-1].strftime("%Y-%m-%d")
        row["age_hours"] = round((NOW - dates[0]).total_seconds() / 3600, 1)
        row["last_7d"] = sum(1 for d in dates if (NOW - d).days < 7)
        row["retention_days"] = (NOW - dates[-1]).days
    row["summary_chars"] = int(statistics.median(text_len(e) for e in entries[:10]))
    row["ok"] = True
    return row


def check_odata(url: str) -> dict:
    """WHO's OData API needs explicit ordering; its default page is unsorted."""
    query = "?$orderby=PublicationDateAndTime%20desc&$top=20"
    code, body = fetch(url + query)
    row: dict = {"http": code, "ok": False, "format": "odata",
                 "blocked": code in BLOCKED_CODES}
    if code != 200:
        row["error"] = body.decode("utf-8", "replace")[:100].strip()
        return row
    try:
        records = json.loads(body).get("value", [])
    except ValueError as e:
        row["error"] = f"invalid JSON: {e}"
        return row
    row["items"] = len(records)
    if not records:
        row["error"] = "no records"
        return row

    stamps = []
    for record in records:
        raw = record.get("PublicationDateAndTime") or record.get("PublicationDate")
        if raw:
            try:
                stamps.append(dt.datetime.fromisoformat(raw))
            except ValueError:
                pass
    if stamps:
        stamps.sort(reverse=True)
        row["newest"] = stamps[0].strftime("%Y-%m-%d")
        row["oldest"] = stamps[-1].strftime("%Y-%m-%d")
        row["age_hours"] = round((NOW - stamps[0]).total_seconds() / 3600, 1)
        row["last_7d"] = sum(1 for s in stamps if (NOW - s).days < 7)
        row["retention_days"] = (NOW - stamps[-1]).days
    row["summary_chars"] = int(statistics.median(
        len(str(r.get("Summary") or "")) for r in records))
    row["ok"] = True
    return row


def check_moh(url: str) -> dict:
    """MOH has no feed: the index is read out of a Next.js flight payload.

    Delegating to the adapter keeps one parser rather than two — this sweep
    is a gate, and a gate that checks something other than what the poller
    does is not one. Imported lazily so the rest of the sweep still runs
    from a checkout without the package importable.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from healthcare_newsfeed.models import Licence, Source
    from healthcare_newsfeed.sources.base import FeedError
    from healthcare_newsfeed.sources.moh import WINDOW_BYTES, MohNewsroomAdapter

    row: dict = {"http": 0, "ok": False, "format": "next-flight", "blocked": False}
    source = Source(key="moh_sg", name="MOH", url=url, adapter="moh_newsroom",
                    licence=Licence.LINK_ONLY, weight=1.0, sections=())
    code, body = fetch(url, headers={"Range": f"bytes=0-{WINDOW_BYTES}"})
    row["http"] = code
    row["blocked"] = code in BLOCKED_CODES
    if code not in (200, 206):
        row["error"] = body.decode("utf-8", "replace")[:100].strip()
        return row
    try:
        items = MohNewsroomAdapter().parse(body, source)
    except FeedError as exc:
        row["error"] = str(exc)[:100]
        return row

    dates = sorted((i.published for i in items if i.published), reverse=True)
    row["items"] = len(items)
    if dates:
        row["newest"] = dates[0].strftime("%Y-%m-%d")
        row["oldest"] = dates[-1].strftime("%Y-%m-%d")
        row["age_hours"] = round((NOW - dates[0]).total_seconds() / 3600, 1)
        row["last_7d"] = sum(1 for d in dates if (NOW - d).days < 7)
        row["retention_days"] = (NOW - dates[-1]).days
    # Items are title + link by design; see sources/moh.py.
    row["summary_chars"] = 0
    row["ok"] = True
    return row


def verdict(row: dict) -> str:
    if not row.get("ok"):
        label = "BLOCKED" if row.get("blocked") else "FAIL"
        return f"{label} http {row['http']} — {row.get('error', '')[:36]}"
    window = row.get("retention_days")
    if window is not None and window <= 8:
        return f"ok — {window}d window, daily poll required"
    if window is not None and window <= 14:
        return f"ok — {window}d window, marginal"
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default="config/sources.yaml", type=Path)
    ap.add_argument("--json", type=Path, help="also write raw results here")
    ap.add_argument("--strict", action="store_true",
                    help="treat host-level blocks as failures too")
    args = ap.parse_args()

    doc = yaml.safe_load(args.config.read_text())
    defaults = doc.get("defaults", {})

    rows = []
    header = (f"{'SOURCE':<18} {'HTTP':<5} {'ITEMS':>5} {'NEWEST':<11} "
              f"{'7D':>3} {'CHARS':>6}  VERDICT")
    print(header)
    print("-" * len(header))

    failed = blocked = 0
    for source in doc["sources"]:
        if not source.get("enabled", defaults.get("enabled", True)):
            continue
        adapter = source.get("adapter", defaults.get("adapter", "rss"))
        check = {"who_odata": check_odata, "moh_newsroom": check_moh}.get(adapter, check_rss)
        row = check(source["url"])
        row["key"] = source["key"]
        rows.append(row)
        if not row["ok"]:
            if row.get("blocked") and not args.strict:
                blocked += 1
            else:
                failed += 1
        print(f"{source['key']:<18} {row['http']:<5} {row.get('items', '-'):>5} "
              f"{row.get('newest', '-'):<11} {row.get('last_7d', '-'):>3} "
              f"{row.get('summary_chars', '-'):>6}  {verdict(row)}")

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))

    healthy = sum(1 for r in rows if r["ok"])
    print(f"\n{healthy}/{len(rows)} sources healthy")
    if blocked:
        print(f"{blocked} blocked by host (IP reputation, not a feed fault) "
              f"— rerun with --strict to treat these as failures")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
