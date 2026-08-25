"""RSS/Atom adapter, covering every source except WHO.

Handles all three formats present in the configured set:
  * RSS 2.0  — MedPage, STAT, BBC, JAMA, KFF, JME
  * RSS 1.0  — NEJM, The Lancet, Nature Medicine (RDF; note `<items><rdf:Seq>`
               ordering blocks are metadata, not entries)
  * Atom 1.0 — The Conversation

feedparser normalises all three, so the work here is date handling (feeds
disagree on which of published/updated/dc:date is authoritative) and
stripping HTML from summaries.
"""

from __future__ import annotations

from ..models import RawItem, Source


class RssAdapter:
    def fetch(self, source: Source) -> list[RawItem]:
        raise NotImplementedError
