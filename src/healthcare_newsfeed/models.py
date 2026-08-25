"""Core domain types shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Licence(str, Enum):
    """How much of an item we may republish.

    Drives rendering: PUBLIC_DOMAIN and CC_REPUBLISHABLE items may carry a
    long extract, LINK_ONLY items get a headline, a short own-words summary
    and a link.
    """

    PUBLIC_DOMAIN = "public_domain"      # WHO
    CC_REPUBLISHABLE = "cc"              # The Conversation (CC-BY-ND), KFF
    LINK_ONLY = "link_only"              # everything else


@dataclass(frozen=True)
class Source:
    """A configured upstream source (one row of config/sources.yaml)."""

    key: str
    name: str
    url: str
    adapter: str                  # "rss" | "who_odata"
    licence: Licence
    weight: float                 # base score multiplier
    sections: tuple[str, ...]     # digest sections this source may fill
    paywalled: bool = False
    poll_hours: int = 24
    enabled: bool = True
    tolerate_failure: bool = False   # a flaky host must not fail the whole poll


@dataclass
class RawItem:
    """An item exactly as an adapter found it, before normalisation."""

    source_key: str
    title: str
    url: str
    published: datetime | None
    summary: str = ""
    authors: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    guid: str | None = None


@dataclass
class Item:
    """A normalised, stored item."""

    id: str                       # stable hash of canonical_url
    source_key: str
    title: str
    canonical_url: str
    published: datetime | None
    first_seen: datetime
    summary: str = ""
    categories: tuple[str, ...] = ()
    cluster_id: str | None = None       # set by dedupe
    score: float | None = None          # set by score
    section: str | None = None          # set by select
    published_in_issue: int | None = None


@dataclass
class Section:
    """One rendered block of the weekly digest."""

    key: str
    heading: str
    items: list[Item] = field(default_factory=list)


@dataclass
class Digest:
    """A full weekly issue, ready to render."""

    issue: int
    week_start: datetime
    week_end: datetime
    sections: list[Section] = field(default_factory=list)
