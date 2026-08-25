"""Rank candidates for a weekly issue.

Weekly selection is roughly 15:1 (~175 candidates -> ~12 published), which
is a ranking problem rather than a filtering one; keyword rules alone
cannot carry it.

Signals:
  source weight    editorial trust, from sources.yaml
  topic fit        relevance to medical-school interview themes — ethics,
                   health policy, global health, new treatments, AI in
                   medicine
  durability       explainers and reviews keep their value across a week;
                   breaking news does not, so time-sensitive items are
                   penalised rather than promoted
  cluster size     independent corroboration is mild evidence of importance
  readability      favour items an applicant can actually follow

Every signal is a small named function returning a documented range, and
the weights below are the whole tuning surface. That matters more than
sophistication here: when an issue carries the wrong story, the question is
always which signal put it there.

`score` needs the source weights, so it takes the configured sources as
well as the items — the stub's one-argument signature could not reach the
first signal in its own list.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping

from ..models import Item, Source

# What each signal is worth, relative to a baseline of 1.0. Topic fit leads
# because the audience is specific: an applicant preparing for interview is
# served better by a policy debate they can argue about than by a better-
# reported story they cannot.
TOPIC = 1.0
DURABILITY = 0.6
CORROBORATION = 0.3
READABILITY = 0.4
RECENCY = 0.5

BASELINE = 1.0
FLOOR = 0.05          # keeps source weight a multiplier even when every signal is against

# The lede establishes the subject; the rest of a 6,000-character article
# mentions everything. Keyword matches beyond this point are weak evidence
# and are scored as such.
LEDE = 400

# Interview themes, and what a headline about each looks like. Weights are
# relative to each other: ethics is what this audience is least able to read
# up on elsewhere and most likely to be asked about.
#
# A trailing * matches the rest of the word, so "ethic*" covers ethics and
# ethical; everything else has to match whole. The distinction is not
# cosmetic — matching "who" as a prefix finds it in "whole", "ai" in "aid",
# and "live" in "liver", which in this corpus is a great many articles.
TOPICS: dict[str, tuple[float, tuple[str, ...]]] = {
    "ethics": (1.00, (
        "ethic*", "consent", "autonomy", "assisted dying", "euthanasia", "moral*",
        "dignity", "confidentiality", "conscientious", "rationing", "triage",
        "end of life", "safeguarding", "equity", "inequalit*", "disparit*",
    )),
    "policy": (0.85, (
        "policy", "policies", "nhs", "health system", "funding", "insurance",
        "regulat*", "reform*", "workforce", "waiting list", "medicaid", "medicare",
        "budget*", "legislation", "guideline*", "spending", "shortage*", "strike*",
    )),
    "global_health": (0.85, (
        "who", "outbreak*", "epidemic*", "pandemic*", "global health", "low-income",
        "malaria", "tuberculosis", "cholera", "measles", "ebola", "hiv",
        "immunis*", "immuniz*", "vaccination campaign", "refugee*",
    )),
    "treatment": (0.75, (
        "trial*", "phase 3", "phase 2", "treatment*", "therap*", "drug*",
        "vaccine*", "efficacy", "randomis*", "randomiz*", "approval", "breakthrough",
    )),
    "ai": (0.70, (
        "artificial intelligence", "machine learning", "algorithm*", "chatbot*",
        "large language model", "llm", "ai",
    )),
}

# A headline promising the week's news is worth less by Sunday than one
# promising an explanation, which is the whole reason this digest is weekly.
BREAKING = ("live", "breaking", "latest", "update*", "announce*", "dies", "died",
            "resign*", "warns", "urges", "calls for", "hits back")
EXPLAINER = ("what is", "what are", "what to know", "how", "why", "explain*",
             "guide*", "everything you need", "myth*", "q&a", "the science of",
             "what happens", "should you")

LONG_FORM = 1500      # characters of summary that mark analysis rather than a flash
JARGON = 12           # a word this long in a headline is nomenclature
CITATION = 18         # a "headline" this long is a citation line

FRESH_DAYS = 7
STALE_DAYS = 60


def _phrases(words: tuple[str, ...]) -> re.Pattern[str]:
    """Whole-word matching, unless a trailing * asks for the rest of the word."""
    parts = [rf"\b{re.escape(word[:-1])}\w*" if word.endswith("*") else rf"\b{re.escape(word)}\b"
             for word in words]
    return re.compile("|".join(parts), re.IGNORECASE)


_TOPIC_PATTERNS = {name: (weight, _phrases(words)) for name, (weight, words) in TOPICS.items()}
_BREAKING = _phrases(BREAKING)
_EXPLAINER = _phrases(EXPLAINER)


def topic_fit(item: Item) -> float:
    """0.0-1.0. The strongest theme the item hits, plus a little for breadth.

    A match in the title or the source's own categories counts fully; one
    buried in the lede counts half. Taking the best theme rather than the
    sum keeps a single squarely-on-topic item ahead of one that name-checks
    four subjects without being about any of them.
    """
    headline = f"{item.title} {' '.join(item.categories)}"
    lede = item.summary[:LEDE]

    hits = []
    for weight, pattern in _TOPIC_PATTERNS.values():
        if pattern.search(headline):
            hits.append(weight)
        elif pattern.search(lede):
            hits.append(weight * 0.5)

    if not hits:
        return 0.0
    return min(max(hits) + 0.1 * (len(hits) - 1), 1.0)


def durability(item: Item) -> float:
    """-0.5-1.0. Whether the item will still be worth reading on Sunday."""
    value = 0.0
    if _EXPLAINER.search(item.title):
        value += 1.0
    if _BREAKING.search(item.title):
        value -= 0.5
    if len(item.summary) >= LONG_FORM:
        value += 0.3
    return max(min(value, 1.0), -0.5)


def corroboration(sources_in_cluster: int) -> float:
    """0.0-1.0. Mild credit for a story more than one source thought mattered.

    Counted in distinct sources, not cluster members: a publisher running
    seven episodes of one column under an identical title is not seven
    outlets agreeing, and counting rows would rank that boilerplate top.
    """
    return min((sources_in_cluster - 1) * 0.4, 1.0)


def readability(item: Item) -> float:
    """0.0-1.0. Whether an applicant can actually follow it."""
    words = item.title.split()
    if not words:
        return 0.0
    jargon = sum(1 for word in words if len(word) > JARGON) / len(words)
    value = 1.0 - min(jargon * 3, 1.0)
    if len(words) > CITATION:
        value *= 0.7          # a citation line rather than a headline
    if not item.summary:
        value *= 0.5          # NEJM ships none; nothing explains the title
    return value


def recency(item: Item, now: dt.datetime) -> float:
    """1.0-0.0, decaying after a week.

    Not in the module's list of signals, and needed anyway: window() selects
    on when an item was stored, not when it was written, so the first poll of
    an archive-backed source lands months of WHO outbreak reports in one day
    looking every bit as new as this morning's.
    """
    age = (now - (item.published or item.first_seen)).days
    if age <= FRESH_DAYS:
        return 1.0
    if age >= STALE_DAYS:
        return 0.0
    return 1.0 - (age - FRESH_DAYS) / (STALE_DAYS - FRESH_DAYS)


def score(items: list[Item], sources: Mapping[str, Source],
          *, now: dt.datetime | None = None) -> list[Item]:
    """Set `score` on each item and return sorted, highest first."""
    now = now or dt.datetime.now(dt.UTC)

    breadth: dict[str, set[str]] = {}
    for item in items:
        breadth.setdefault(item.cluster_id or item.id, set()).add(item.source_key)

    for item in items:
        source = sources.get(item.source_key)
        signals = (
            TOPIC * topic_fit(item)
            + DURABILITY * durability(item)
            + CORROBORATION * corroboration(len(breadth[item.cluster_id or item.id]))
            + READABILITY * readability(item)
            + RECENCY * recency(item, now)
        )
        weight = source.weight if source else 1.0
        item.score = round(weight * max(BASELINE + signals, FLOOR), 4)

    # id breaks ties, so a rerun on unchanged data produces the same issue.
    items.sort(key=lambda item: (-(item.score or 0.0), item.id))
    return items
