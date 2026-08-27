#!/usr/bin/env python3
"""Discover whether a host publishes a feed at all.

`verify_feeds.py` answers "is this known feed healthy?". This answers the
question that comes before it: **given a bare hostname, is there anything here
to poll?** It is the tool the Singapore institutional sweep needs, because
those hosts arrive as domains rather than as feed URLs.

Four verdicts, and the distinctions are the point:

  BLOCKED    the egress path refused CONNECT, or the host answered 401/403/429.
             A property of where this ran, not of the host — same split
             verify_feeds.py draws for NEJM and BMJ.
  CHALLENGE  a 2xx carrying a bot-check stub rather than a page. Recorded
             separately because reading one as a real response is exactly how
             the survey once concluded Duke-NUS had "no feed"; see
             docs/asia-sources.md.
  NO FEED    the host served real pages, and neither autodiscovery nor any
             candidate path produced a parseable feed.
  <URL>      a feed, with its item count, newest date and robots verdict.

Autodiscovery is tried first, since a `<link rel="alternate">` is the
publisher telling you where the feed is; the path list is only the fallback
for sites that have one but do not advertise it.

Usage:
    python tools/probe_feeds.py HOST [HOST ...]
    python tools/probe_feeds.py --file hosts.txt [--json out.json]

Exit status is 0 whatever is found — this is a survey, not a gate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

import feedparser

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 25
DELAY = 1.0  # between requests to one host; several of these publish a Crawl-delay

# Set REQUESTS_CA_BUNDLE / SSL_CERT_FILE if behind a TLS-terminating proxy.
_CA = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")

BLOCKED_CODES = {401, 403, 429}

# Ordered by how likely each is to be the real feed rather than a coincidence.
CANDIDATE_PATHS = (
    "/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml",
    "/index.xml", "/blog/feed", "/news/feed", "/news/rss", "/news/rss.xml",
    "/newsroom/rss.xml", "/media/rss", "/?feed=rss2",
)

# Bot-check pages answer 2xx with a body that is not the site. Each of these
# appears in the stub and never in real editorial markup.
CHALLENGE_MARKERS = (
    "_incapsula_resource", "incapsula incident", "cf-browser-verification",
    "challenge-platform", "/cdn-cgi/challenge", "just a moment...",
    "enable javascript and cookies to continue", "attention required!",
)

_LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_REL_ALT = re.compile(r'rel\s*=\s*["\']?[^"\'>]*\balternate\b', re.IGNORECASE)
_FEED_TYPE = re.compile(r'type\s*=\s*["\']?(application/(?:rss|atom)\+xml)', re.IGNORECASE)
_HREF = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


class Blocked(Exception):
    """CONNECT refused, or the host gated on our address."""


def fetch(url: str) -> tuple[int, bytes, str]:
    """Return (status, body, final_url). Raises Blocked for a gated response."""
    ctx = ssl.create_default_context(cafile=_CA) if _CA else ssl.create_default_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml,application/atom+xml,application/xml,"
                  "text/xml,text/html,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.status, r.read(), r.url
    except urllib.error.HTTPError as e:
        if e.code in BLOCKED_CODES:
            raise Blocked(f"HTTP {e.code}") from e
        return e.code, e.read()[:4096], url
    except urllib.error.URLError as e:
        # A proxy that refuses CONNECT surfaces here, not as an HTTPError.
        raise Blocked(str(getattr(e, "reason", e))[:120]) from e
    except Exception as e:  # a survey reports, it does not crash
        raise Blocked(str(e)[:120]) from e


def is_challenge(body: bytes) -> bool:
    head = body[:4096].decode("utf-8", "replace").lower()
    if any(marker in head for marker in CHALLENGE_MARKERS):
        return True
    # The Incapsula stub is a few hundred bytes of noindex plus one script.
    return len(body) < 1024 and "noindex,nofollow" in head and "<script" in head


def as_feed(body: bytes) -> dict | None:
    """Parse if this is genuinely a feed with entries, else None."""
    parsed = feedparser.parse(body)
    if not parsed.entries or not parsed.get("version"):
        return None
    dates = []
    for entry in parsed.entries:
        for key in ("published_parsed", "updated_parsed", "created_parsed"):
            if entry.get(key):
                dates.append(dt.datetime(*entry[key][:6], tzinfo=dt.UTC))
                break
    return {
        "format": parsed.version,
        "items": len(parsed.entries),
        "newest": max(dates).strftime("%Y-%m-%d") if dates else None,
        "title": (parsed.feed.get("title") or "")[:60],
    }


def autodiscovered(html: bytes, base: str) -> list[str]:
    """Feed URLs the page advertises in <link rel="alternate" type="…+xml">."""
    text = html.decode("utf-8", "replace")
    found = []
    for tag in _LINK_TAG.findall(text):
        if _REL_ALT.search(tag) and _FEED_TYPE.search(tag):
            href = _HREF.search(tag)
            if href:
                url = urllib.parse.urljoin(base, href.group(1).strip())
                if url not in found:
                    found.append(url)
    return found


def robots_for(host: str) -> urllib.robotparser.RobotFileParser | None:
    """The host's robots.txt, or None if it has none we could read.

    A poller is a crawler — the reading this project settled on for CNA's
    disallowed feed endpoints — so a discovered feed that robots forbids is
    reported as such rather than quietly recommended.
    """
    parser = urllib.robotparser.RobotFileParser()
    try:
        status, body, _ = fetch(f"https://{host}/robots.txt")
    except Blocked:
        return None
    if status != 200 or is_challenge(body):
        return None
    parser.parse(body.decode("utf-8", "replace").splitlines())
    return parser


def probe(host: str) -> dict:
    """Everything worth knowing about one host, in one pass."""
    result = {"host": host, "verdict": None, "detail": "", "feeds": []}

    try:
        status, body, final = fetch(f"https://{host}/")
    except Blocked as e:
        result.update(verdict="BLOCKED", detail=str(e))
        return result

    # A bot-check stub is not a page, so there is nothing to autodiscover from
    # — but the paths are still worth trying, and sometimes still answer:
    # medicine.nus.edu.sg fronts an Incapsula stub and yet serves a genuine
    # `wp_die` 500 at /feed/. Bailing here would report that host as blocked
    # and never learn why. So record the challenge and carry on.
    challenged = is_challenge(body)
    if challenged:
        result["detail"] = f"HTTP {status}, {len(body)} B bot-check stub"

    result["landed_on"] = final
    if not challenged and final.rstrip("/") != f"https://{host}".rstrip("/"):
        result["detail"] = f"redirected to {final}"

    robots = robots_for(host)
    time.sleep(DELAY)

    asked: set[str] = set()      # candidate URLs already requested
    landed: set[str] = set()     # feed URLs already reported, after redirects
    candidates = [] if challenged else autodiscovered(body, final)
    result["autodiscovered"] = list(candidates)
    candidates += [urllib.parse.urljoin(final, path) for path in CANDIDATE_PATHS]

    for url in candidates:
        if url in asked:
            continue
        asked.add(url)
        try:
            fstatus, fbody, furl = fetch(url)
        except Blocked:
            continue
        finally:
            time.sleep(DELAY)
        if fstatus != 200 or is_challenge(fbody):
            continue
        # Several candidate paths redirect onto the same feed — /feed, /feed/
        # and /?feed=rss2 are one WordPress feed between them — so dedupe on
        # where the request landed as well as on what was asked for.
        if furl in landed:
            continue
        landed.add(furl)
        info = as_feed(fbody)
        if not info:
            continue
        info["url"] = furl
        info["robots_ok"] = robots.can_fetch(UA, furl) if robots else None
        info["advertised"] = url in result["autodiscovered"]
        result["feeds"].append(info)

    if result["feeds"]:
        result["verdict"] = "FEED"
    elif challenged:
        result["verdict"] = "CHALLENGE"
    else:
        result["verdict"] = "NO FEED"
    return result


def header() -> None:
    print(f"{'HOST':<32} {'VERDICT':<10} DETAIL")
    print("-" * 96, flush=True)


def render_one(r: dict) -> None:
    """One host's line, flushed immediately.

    A full institutional sweep takes a quarter of an hour — the per-host
    delay is deliberate, since several of these publish a Crawl-delay — so
    holding every line until the end makes the run look hung.
    """
    print(f"{r['host']:<32} {r['verdict']:<10} {r['detail']}")
    for feed in r["feeds"]:
        robots = {True: "robots ok", False: "ROBOTS DISALLOW",
                  None: "robots unknown"}[feed["robots_ok"]]
        how = "advertised" if feed["advertised"] else "guessed path"
        print(f"{'':<32} {'':<10}   → {feed['url']}")
        print(f"{'':<32} {'':<10}     {feed['format']}, {feed['items']} items, "
              f"newest {feed['newest']}, {how}, {robots}")
    sys.stdout.flush()


def render_tally(results: list[dict]) -> None:
    tally: dict[str, int] = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("\n" + ", ".join(f"{count} {verdict.lower()}"
                           for verdict, count in sorted(tally.items())), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hosts", nargs="*", help="bare hostnames, e.g. www.hsa.gov.sg")
    ap.add_argument("--file", help="file of hostnames, one per line; # comments ignored")
    ap.add_argument("--json", help="also write the full result to this path")
    args = ap.parse_args()

    hosts = list(args.hosts)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            hosts += [line.split("#")[0].strip() for line in fh]
    hosts = [h for h in hosts if h]
    if not hosts:
        ap.error("give at least one host, or --file")

    header()
    results = []
    for host in hosts:
        result = probe(host)
        results.append(result)
        render_one(result)
    render_tally(results)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
