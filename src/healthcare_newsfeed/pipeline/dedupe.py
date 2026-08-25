"""Cluster items that report the same story.

A weekly cycle makes this both easier and more valuable than it would be
daily: the whole week is in hand, so clusters can be formed once and the
best-written member promoted rather than whichever source published first.

Two passes:
  1. exact — canonical URL match after stripping tracking parameters
  2. near  — title similarity within a time window, to catch the same
             WHO announcement arriving via BBC, STAT and The Conversation

Pass 1 lives in `canonical_url`, which the store calls on every insert: the
canonical URL is the items table's identity, so exact duplicates never reach
pass 2. Pass 2 is `cluster`, still to be written.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import Item

# Query parameters that identify the referrer rather than the article. The
# same story arrives from several sources — and from one source over several
# days — with these appended, so they have to go before URLs can be compared.
# Prefixes cover the analytics families (utm_ everywhere, at_ on BBC, dgcid on
# Elsevier titles including The Lancet); the exact set covers the one-offs,
# among them the `?af=R` the journals append and Elsevier's `?rss=yes`.
_TRACKING_PREFIXES = ("utm_", "at_", "ns_", "pk_", "mc_", "_hs", "dgcid", "wt.mc")
_TRACKING_PARAMS = frozenset({
    "af", "rss", "cmp", "ito", "fbclid", "gclid", "gbraid", "wbraid", "msclkid",
    "igshid", "guccounter", "ref_src", "spm", "s_cid", "sh_kit", "xtor",
    "__twitter_impression", "feature",
})


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PREFIXES)


def canonical_url(url: str) -> str:
    """Strip utm_*, at_medium, ?af=R and other tracking noise.

    Also lowercases the scheme and host, drops a redundant default port and
    drops the fragment, none of which name a different resource. The host is
    otherwise left alone: this string is both the store's primary key and the
    link the digest publishes, so `www.` stays where a publisher put it.
    """
    parts = urlsplit(url.strip())
    if not parts.scheme and not parts.netloc:
        return url.strip()          # a bare path or junk; nothing to normalise

    netloc = parts.netloc.lower()
    for scheme, port in (("https", ":443"), ("http", ":80")):
        if parts.scheme.lower() == scheme and netloc.endswith(port):
            netloc = netloc[: -len(port)]

    query = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(name)
    ]
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, urlencode(query), ""))


# Words that carry no subject. Dropped before comparing titles, so "Ebola
# outbreak in the DRC" and "Outbreak of Ebola in DRC" are not held apart by
# the scaffolding between the nouns.
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "to", "for", "and", "on", "with", "is", "are",
    "as", "at", "by", "from", "that", "this", "it", "its", "be", "has", "have",
    "new", "study", "says", "after",
})

_WORD = re.compile(r"[a-z0-9]+")

# Two titles are the same story if half their subject words agree, or — for
# titles long enough that the test means something — if one is almost wholly
# contained in the other, which is how a wire headline relates to a journal's
# fuller one. Measured on the configured sources' real output: these thresholds
# cluster a WHO statement with the outbreak report behind it, and NEJM's phase 3
# write-up with The Lancet's companion paper, without a single false pairing.
SIMILARITY = 0.50
CONTAINMENT = 0.75
MIN_TOKENS = 4

# How far apart two reports of one story can be. Three weeks looks generous
# for a weekly digest, and the candidates argue for it: WHO files a fresh
# outbreak report on the same epidemic every week or two, and the journals
# ran one HIV trial in NEJM a fortnight before The Lancet's companion paper.
# At a week those all stay separate and the issue carries the same outbreak
# twice; at three they collapse and the freshest member is the one published.
#
# The cost, measured on real output: two WHO announcements about different
# university partnerships merged, their headlines being near-identical
# boilerplate. Nine of ten clusters were genuine, and the item lost to the
# tenth was one this audience had no use for either way.
WINDOW = dt.timedelta(days=21)


def _tokens(title: str) -> frozenset[str]:
    return frozenset(word for word in _WORD.findall(title.lower())
                     if word not in _STOPWORDS and len(word) > 2)


def _same_story(left: frozenset[str], right: frozenset[str]) -> bool:
    if not left or not right:
        return False
    shared = len(left & right)
    if shared / len(left | right) >= SIMILARITY:
        return True
    smaller = min(len(left), len(right))
    return smaller >= MIN_TOKENS and shared / smaller >= CONTAINMENT


def _when(item: Item) -> dt.datetime:
    """Publication date where there is one; WHO news items carry none."""
    return item.published or item.first_seen


def cluster(items: list[Item]) -> list[Item]:
    """Assign cluster_id in place and return the same list.

    Pass 2 of dedupe: near-duplicate titles within a time window, catching
    the same announcement arriving through several sources. Pass 1 — exact
    canonical URLs — has already happened, in the store.

    Every item ends up in a cluster, singletons included, so downstream code
    can treat cluster_id as always present. The id is the lowest member id,
    which makes it stable whatever order the items arrive in.
    """
    parent = {item.id: item.id for item in items}

    def root(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def merge(left: str, right: str) -> None:
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            # Lowest id wins, so the cluster is named the same way every run.
            high, low = sorted((left_root, right_root), reverse=True)
            parent[high] = low

    prepared = [(item, _tokens(item.title), _when(item)) for item in items]
    for index, (item, tokens, when) in enumerate(prepared):
        for other, other_tokens, other_when in prepared[index + 1:]:
            if abs(when - other_when) > WINDOW:
                continue
            if _same_story(tokens, other_tokens):
                merge(item.id, other.id)

    for item in items:
        item.cluster_id = root(item.id)
    return items
