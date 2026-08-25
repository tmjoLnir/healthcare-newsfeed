"""Fill the digest's fixed sections from scored candidates.

Section quotas come from config/digest.yaml. Selection is per-section
rather than global so a strong news week cannot crowd out the ethics slot,
which is the section this audience benefits from most and the one with the
thinnest supply (the JME blog averages 1-2 posts a week and can fall
silent for a fortnight).

Rules:
  * one item per cluster
  * at most SUBJECT_CAP items on any one subject
  * never republish an item carried by an earlier issue
  * a section short on supply shrinks; it does not borrow from another

Which sections an item may fill is a property of its source, so this takes
the configured sources alongside the template — `Item` carries a source key
and nothing else that could answer the question.

Allocation runs twice over the sections in template order: once giving each
its `min`, then again topping up to `max`. One pass would let an early
greedy section take an item a later thin one was relying on, which is the
crowding-out the quotas exist to prevent.
"""

from __future__ import annotations

import collections
import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..models import Digest, Item, Section, Source
from .dedupe import subjects

SUBJECT_CAP = 2
"""How many items in one issue may be about the same thing.

Clusters catch a story filed twice; this catches a story that runs all week.
A major outbreak legitimately produces a situation report, a case report, a
vaccine trial and an explainer — all worth reading, but not all worth an
issue that carries twelve other things. Two lets the digest cover a big week
properly and still be about more than one subject.

The cap is counted against the items already chosen, never across the whole
candidate pool, so it cannot chain the way a looser cluster rule would.
"""


@dataclass
class _Ledger:
    """What must not repeat, tracked across every section as they fill."""

    subject_words: Mapping[str, frozenset[str]]
    taken: set[str] = field(default_factory=set)
    clusters: set[str] = field(default_factory=set)
    subjects: collections.Counter = field(default_factory=collections.Counter)

    def blocks(self, item: Item) -> bool:
        if item.id in self.taken or (item.cluster_id or item.id) in self.clusters:
            return True
        return any(self.subjects[word] >= SUBJECT_CAP
                   for word in self.subject_words.get(item.id, ()))

    def record(self, item: Item) -> None:
        self.taken.add(item.id)
        self.clusters.add(item.cluster_id or item.id)
        self.subjects.update(self.subject_words.get(item.id, ()))


def select(items: list[Item], template: dict, issue: int,
           sources: Mapping[str, Source],
           *, week: tuple[dt.datetime, dt.datetime] | None = None) -> Digest:
    """Assemble one issue, setting `section` on the items that made it."""
    candidates = [item for item in items if item.published_in_issue is None]
    candidates.sort(key=lambda item: (-(item.score or 0.0), item.id))

    specs = template["sections"]
    chosen: dict[str, list[Item]] = {spec["key"]: [] for spec in specs}
    ledger = _Ledger(subject_words=subjects(items))

    for quota in ("min", "max"):
        for spec in specs:
            _fill(spec, spec[quota], chosen, candidates, ledger, sources)

    sections = [
        Section(key=spec["key"], heading=spec["heading"], items=chosen[spec["key"]])
        for spec in specs
        if chosen[spec["key"]]           # an absent block beats a padded one
    ]
    start, end = week or _span(items)
    return Digest(issue=issue, week_start=start, week_end=end, sections=sections)


def _fill(spec: dict, limit: int, chosen: dict[str, list[Item]], candidates: list[Item],
          ledger: _Ledger, sources: Mapping[str, Source]) -> None:
    key = spec["key"]
    picked = chosen[key]
    if len(picked) >= limit:
        return

    # `diversify_by: source` earns a first sweep that skips a source already
    # in the block, so four journal slots cannot all go to NEJM while the
    # others are represented. The second sweep allows a repeat rather than
    # leave the section short.
    diversify = spec.get("diversify_by") == "source"
    represented = {item.source_key for item in picked}

    for first_sweep in (True, False) if diversify else (False,):
        for item in candidates:
            if len(picked) >= limit:
                return
            if ledger.blocks(item):
                continue
            source = sources.get(item.source_key)
            if source is None or key not in source.sections:
                continue
            if first_sweep and item.source_key in represented:
                continue

            item.section = key
            picked.append(item)
            ledger.record(item)
            represented.add(item.source_key)


def _span(items: list[Item]) -> tuple[dt.datetime, dt.datetime]:
    """The week the candidates came from, when the caller did not say."""
    if not items:
        now = dt.datetime.now(dt.UTC)
        return now - dt.timedelta(days=7), now
    seen = [item.first_seen for item in items]
    return min(seen), max(seen)
