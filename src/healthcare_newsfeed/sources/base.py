"""Adapter protocol."""

from __future__ import annotations

from typing import Protocol

from ..models import RawItem, Source


class Adapter(Protocol):
    """Fetch one source and return its current items.

    Adapters do no deduplication, scoring or filtering — they only translate
    an upstream format into RawItem. Network and parse errors are raised;
    the caller decides whether one dead source fails the run.
    """

    def fetch(self, source: Source) -> list[RawItem]:
        ...


ADAPTERS: dict[str, type[Adapter]] = {}
"""Registry keyed by the `adapter:` field in sources.yaml."""
