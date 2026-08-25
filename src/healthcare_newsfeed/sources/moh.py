"""MOH Singapore newsroom adapter.

MOH publishes no feed. `/rss`, `/feed.xml` and `/newsroom/rss.xml` all 404,
and the site runs on Isomer Next — a Next.js platform with no syndication.
The README recorded it as unreachable, and covering it as a page-scraping
job. Neither is quite right: the newsroom listing page ships its whole index
inside the React Server Component flight payload, and the host honours byte
ranges, so one partial request reads it.

    GET https://www.moh.gov.sg/newsroom/   Range: bytes=0-500000

The page is 7.5 MB and the index starts about 4.3% in, ordered newest first,
so the first 0.5 MB carries roughly four months of it. CloudFront grants that
range only sometimes — see WINDOW_BYTES — so the adapter reads at most
MAX_ITEMS records whichever size arrives, and parses a partial and a whole page
the same way. That cap, not the byte range, is what bounds a poll. Records look
like this, embedded in a chunk of escaped JSON:

    {"id":"/newsroom/<slug>","date":"$D2026-08-24T00:00:00.000Z",
     "plaintextTags":[{"category":"Category","selected":["Speeches"]}],
     "title":"SPEECH BY MR TAN KIAT HOW, ...","description":" "}

Three consequences shape what is below:

*   **A granted range cuts the payload mid-string**, so both the chunk split
    and the record scan end on the last complete unit rather than expecting a
    closed array. That is a normal case here, not an error case.
*   **`description` is always blank**, and MOH's Terms of Use forbid
    reproducing site contents without written permission, so items carry no
    summary and reach the digest as title and link — the same shape as WHO
    news items. Item pages do carry the full body, deliberately not used.
*   **Titles are published in capitals**, which would shout among the
    sentence-case headlines of every other source, so `title_case` restores
    them. It is a best-effort transformation; see its docstring.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from ..models import RawItem, Source
from .base import FeedError, HttpSource, clean_text

ITEM_BASE = "https://www.moh.gov.sg"

# The page is 7.5 MB and the index starts 4.3% in, newest first, so asking for
# the first 0.5 MB is enough — when it is granted. MOH sits behind CloudFront,
# which answers a cache hit with the whole page and no Accept-Ranges: eight
# consecutive requests measured on 2026-08-25 all came back 200 and 7.5 MB,
# where the same request had returned 206 earlier the same day. So the range is
# an optimisation that often does not apply, not a contract.
WINDOW_BYTES = 500_000

# The contract is this instead. Whether 0.5 MB or 7.5 MB arrives, the adapter
# reads the newest MAX_ITEMS and stops, which keeps a poll from handing the
# store all 8,367 archived records on the days the range is ignored.
#
# 40 is chosen against how MOH actually publishes rather than against a span:
# Parliamentary QAs land in same-day bursts — 22, 21, 19 and 17 items on the
# four busiest days in one window — so a cap counted in items covers fewer days
# than the ~11/week average suggests. Measured 2026-08-25, 40 reaches back 21
# days, against 34 for 60 and 170 for 200.
#
# Three weeks is the balance. It is ample margin for a daily poll, and it keeps
# the first poll's backlog inside recency()'s decay: the oldest item it can
# introduce scores about 0.74 rather than the 0.0 that a four-month-old one
# does. That matters because window() selects on when an item was *stored*, so
# every record the first poll returns becomes a candidate for that week's
# issue — and select() has no score floor, so a stale item still fills an
# optional section when nothing else competes for it.
MAX_ITEMS = 40

# Next.js streams the payload as a sequence of JS string literals.
PUSH_PREFIX = 'self.__next_f.push([1,"'
PUSH_SUFFIX = '"])'

# The listing's own array, as opposed to the navigation and breadcrumb
# structures that also carry /newsroom/ links earlier in the payload. Matched
# tolerantly: the payload is compact today, but its whitespace is a property
# of Next.js's serializer rather than anything MOH controls.
ITEMS_RE = re.compile(r'"items"\s*:\s*\[')
RECORD_RE = re.compile(r'\{\s*"id"\s*:\s*"/newsroom/')

# React marks a Date value with this prefix inside the JSON string.
DATE_MARKER = "$D"


class MohNewsroomAdapter(HttpSource):
    """Fetch MOH's newsroom index and return its current items."""

    def fetch(self, source: Source) -> list[RawItem]:
        response = self.get(source.url, source.key,
                            headers={"Range": f"bytes=0-{WINDOW_BYTES}"})
        return self.parse(response.content, source)

    def parse(self, body: bytes, source: Source) -> list[RawItem]:
        """Turn a newsroom page — whole or partial — into RawItems.

        Separate from `fetch` so the suite can run against a captured
        response. Records without a title or a slug are dropped; no other
        filtering happens here.
        """
        document = body.decode("utf-8", "replace")
        payload = _flight_payload(document)
        if not payload:
            raise FeedError(
                f"{source.key}: no Next.js flight payload in the response — "
                f"the newsroom is no longer an Isomer Next page, or the "
                f"request did not reach it"
            )

        records = _index_records(payload, source)
        items = []
        for record in records:
            title = clean_text(record.get("title") or "")
            slug = (record.get("id") or "").strip()
            if not title or not slug:
                continue
            items.append(RawItem(
                source_key=source.key,
                title=title_case(title),
                url=ITEM_BASE + slug,
                published=_published(record),
                # The index carries a `description` key, but it is blank for
                # every record, and MOH's Terms of Use put the body text on
                # the item pages out of reach. Title and link it is.
                summary="",
                categories=_categories(record),
                # The slug is MOH's own stable identifier for the item and
                # the path the canonical URL is built from.
                guid=slug,
            ))
        return items


# --- the flight payload -----------------------------------------------------

def _flight_payload(document: str) -> str:
    """Concatenate and decode the page's RSC chunks.

    Each chunk is a JS string literal, so it is decoded as a JSON string
    rather than with `unicode_escape`: the latter round-trips through
    latin-1 and turns every multi-byte character in the payload into
    mojibake — 2,210 corrupted bytes on the live page when measured.
    """
    chunks = []
    for part in document.split(PUSH_PREFIX)[1:]:
        end = _literal_end(part)
        chunks.append(_decode(part if end < 0 else part[:end]))
    return "".join(chunks)


def _literal_end(part: str) -> int:
    """Index of the `"])` that closes this chunk, or -1 if the window cut it.

    An escaped quote inside the chunk can be followed by `])`, which would
    end the literal early and silently drop the records after it, so the
    candidate is rejected unless the quote is a real delimiter.
    """
    found = part.find(PUSH_SUFFIX)
    while found > 0 and _is_escaped(part, found):
        found = part.find(PUSH_SUFFIX, found + 1)
    return found


def _is_escaped(text: str, index: int) -> bool:
    """True when the character at `index` is escaped by an odd run of backslashes."""
    backslashes = 0
    while index - backslashes > 0 and text[index - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _decode(raw: str) -> str:
    """Decode one chunk, trimming a tail the byte range cut mid-escape.

    A `\\uXXXX` is the longest escape, so a valid prefix is at most six
    characters back; eight is margin.
    """
    for end in range(len(raw), max(len(raw) - 8, -1), -1):
        try:
            return json.loads(f'"{raw[:end]}"')
        except ValueError:
            continue
    return ""


def _index_records(payload: str, source: Source) -> list[dict]:
    """Read the listing's item array, which the byte range leaves unclosed.

    Records are decoded one at a time from the first `"items":[` onward, so a
    truncated final record ends the scan instead of invalidating the array —
    parsing the array as a whole would fail on every partial fetch, which is
    every fetch.
    """
    array = ITEMS_RE.search(payload)
    if array is None:
        raise FeedError(
            f"{source.key}: newsroom page carries no \"items\" array — "
            f"the listing's shape has changed"
        )

    decoder = json.JSONDecoder()
    records: list[dict] = []
    position = array.end()
    while len(records) < MAX_ITEMS:
        found = RECORD_RE.search(payload, position)
        if found is None:
            break
        try:
            record, position = decoder.raw_decode(payload, found.start())
        except ValueError:
            break  # the range cut this record; everything before it is whole
        if isinstance(record, dict) and record.get("date"):
            records.append(record)

    if not records:
        # A quiet week still leaves an archive of 8,000 items in the index.
        # Nothing at all means the fetch or the parse failed, not that MOH
        # published nothing.
        raise FeedError(
            f"{source.key}: found the item array but read no records from it"
        )
    return records


def _published(record: dict) -> dt.datetime | None:
    raw = (record.get("date") or "").removeprefix(DATE_MARKER)
    try:
        stamp = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return stamp.replace(tzinfo=dt.UTC) if stamp.tzinfo is None else stamp.astimezone(dt.UTC)


def _categories(record: dict) -> tuple[str, ...]:
    """MOH's own taxonomy: Press Releases, Parliamentary QA, Speeches, Forum Replies."""
    for tag in record.get("plaintextTags") or []:
        if tag.get("category") == "Category" and tag.get("selected"):
            return tuple(clean_text(value) for value in tag["selected"] if value)
    return ()


# --- titles -----------------------------------------------------------------

# Words left lowercase inside a headline. Deliberately short: the risk of
# lowercasing a word that mattered is worse than the odd capitalised "Over".
MINOR_WORDS = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "onto", "or", "over", "the", "to", "under", "with",
})

# Capitals that are not shouting. Drawn from the 600 most recent headlines
# plus the agencies and schemes that recur in MOH's beat; extend as needed —
# a missed one degrades to ordinary title case rather than breaking anything.
ACRONYMS = frozenset({
    "A&E", "ACP", "AIC", "AED", "CDMP", "CHAS", "COVID", "COVID-19", "CPF",
    "GP", "GPS", "GST",
    "HDB", "HIV", "HPB", "HSA", "ICU", "ILTC", "IMH", "IT", "KKH", "MDDI",
    "MOE", "MOF", "MOH", "MOM", "MSF", "MRT", "NCID", "NCCS", "NHCS", "NHG",
    "NTFGH", "NUH", "NUHS", "PHPC", "SARS", "SGH", "SNEC", "TB", "TCM",
    "TTSH", "VWO", "VWOS", "WHO",
})

# Brand names MOH sets in camel case everywhere except these headlines.
MIXED_CASE = {
    "MEDISAVE": "MediSave", "MEDISHIELD": "MediShield", "MEDIFUND": "MediFund",
    "CARESHIELD": "CareShield", "ELDERSHIELD": "ElderShield",
    "HEALTHIERSG": "HealthierSG", "SINGHEALTH": "SingHealth",
    "HEALTHHUB": "HealthHub", "SINGAPORE": "Singapore",
}

# One word, keeping the joiners that hold an acronym together: without the
# ampersand "A&E" splits into "A" and "E" and comes back as "a&E", and
# without the hyphen "COVID-19" never matches the acronym list.
_WORD = re.compile(r"[^\W_]+(?:[&\-'’][^\W_]+)*|\S", re.UNICODE)


def title_case(headline: str) -> str:
    """Restore a headline MOH publishes in capitals.

    Best-effort, and unavoidably so: capitalising the source destroys the
    distinction between an acronym and an ordinary word, so anything not in
    ACRONYMS or MIXED_CASE comes back as Ordinary Title Case. A headline that
    is not all capitals is left exactly as it is, so this is a no-op if MOH
    ever changes house style.
    """
    if headline != headline.upper():
        return headline

    words = list(_WORD.finditer(headline))
    out = headline
    # Rebuild right to left so earlier spans keep their offsets.
    for position, match in reversed(list(enumerate(words))):
        replacement = _recase(
            match.group(),
            first=position == 0,
            last=position == len(words) - 1,
        )
        out = out[:match.start()] + replacement + out[match.end():]
    return out


def _recase(word: str, *, first: bool, last: bool) -> str:
    if word in ACRONYMS:
        return word
    if word in MIXED_CASE:
        return MIXED_CASE[word]
    lowered = word.lower()
    if lowered in MINOR_WORDS and not first and not last:
        return lowered
    return lowered[:1].upper() + lowered[1:]
