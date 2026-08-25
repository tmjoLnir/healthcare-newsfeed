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
    # Interrogatives and light verbs. Headlines are built out of these — "How
    # a loyalist got the nomination", "Why editors should stop", "Can a vaccine
    # stop another" — and two titles sharing only "how" share nothing.
    "how", "why", "what", "when", "can", "could", "may", "might", "will",
    "would", "should", "not", "but", "you", "your", "their", "them", "they",
    "about", "into", "over", "out", "than", "more", "most", "show", "shows",
    "find", "finds", "stop", "make", "made", "take", "get", "use", "using",
})

_WORD = re.compile(r"[a-z0-9]+")

# The Lancet prefixes every item with its section — "[Comment]", "[Articles]",
# "[Correspondence]", "[Viewpoint]". That is structure, not subject, and left
# in it makes two unrelated Viewpoints look alike. Only a short leading
# bracket is stripped, so a bracketed phrase inside a title survives.
_SECTION_MARKER = re.compile(r"^\s*\[[^\]]{1,24}\]\s*")

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
    stripped = _SECTION_MARKER.sub("", title)
    return frozenset(word for word in _WORD.findall(stripped.lower())
                     if word not in _STOPWORDS and len(word) > 2)


def _same_story(left: frozenset[str], right: frozenset[str]) -> bool:
    if not left or not right:
        return False
    shared = len(left & right)
    if shared / len(left | right) >= SIMILARITY:
        return True
    smaller = min(len(left), len(right))
    return smaller >= MIN_TOKENS and shared / smaller >= CONTAINMENT


# --- subjects ---------------------------------------------------------------

# A running story does not arrive as near-duplicate headlines. The DRC
# Bundibugyo outbreak reached one week's candidates as 23 items: a WHO
# situation report, a Nature Medicine case report, a Lancet comment on civil
# society, a BBC vaccine trial and a Conversation explainer among them. They
# share a subject and almost no vocabulary — the closest pair of those five
# scores 0.08 on the similarity `cluster` uses, against a 0.50 threshold.
#
# Loosening that threshold is not the answer: `cluster` closes transitively,
# so a rule slack enough to link "Scientists deploy Merck's Ebola vaccine in
# DRC" to "[Comment] Communities and civil society..." chains the week's
# candidates into one 142-item component. Measured, not feared.
#
# So subjects are a separate, non-transitive idea: the distinctive words in a
# title, which `select` counts against the items it has already chosen rather
# than against the whole corpus. See select.SUBJECT_CAP.

# Words that name a field of medicine or a publisher's furniture rather than
# a story. They belong in a title's tokens — "vaccine trial" and "vaccine
# rollout" are more alike for sharing "vaccine" — but two items are not the
# same running story merely because both concern cancer, and "stat" appears
# only because STAT brands its metered items "STAT+:".
_GENERIC = frozenset({
    "health", "care", "patients", "patient", "disease", "diseases", "virus",
    "viral", "vaccine", "vaccines", "cancer", "drug", "drugs", "treatment",
    "treatments", "trial", "trials", "therapy", "risk", "doctors", "medical",
    "medicine", "research", "hospital", "hospitals", "guidelines", "stat",
})

SUBJECT_MAX_DF = 0.06
"""How common a word may be and still name a subject.

Measured over one week of real candidates (361 items): "ebola" appears in 20
titles (5.5%), "bundibugyo" and "congo" in 14, "cancer" and "vaccine" in 12.
"health" appears in 38 (10.5%) and names nothing in particular. The cut falls
between them, and errs generous — a word wrongly counted as a subject defers
one item to next week's issue, which is the cheap direction to be wrong in.
"""


def subjects(items: list[Item]) -> dict[str, frozenset[str]]:
    """Per item id, the words distinctive enough to say what it is about.

    Distinctiveness is relative to the week in hand: a word is a subject when
    it appears in few enough of this week's titles. That self-calibrates —
    "ebola" is a subject in a week with an outbreak and, in a week with two
    passing mentions, it is a subject then too, which is the point.

    Being a proportion, it needs a corpus to be a proportion of: below about
    34 items the ceiling rounds down to one, no word is shared, and this
    returns nothing anyone can be capped on. That is the right answer rather
    than a gap — a window that thin means the poll has stopped, and one
    subject repeating is the least of what is wrong with the issue.
    """
    frequency: dict[str, int] = {}
    tokenised = {}
    for item in items:
        tokenised[item.id] = tokens = _tokens(item.title)
        for token in tokens:
            frequency[token] = frequency.get(token, 0) + 1

    ceiling = max(1, int(len(items) * SUBJECT_MAX_DF))
    return {
        item_id: frozenset(t for t in tokens
                           if frequency[t] <= ceiling and t not in _GENERIC)
        for item_id, tokens in tokenised.items()
    }


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
