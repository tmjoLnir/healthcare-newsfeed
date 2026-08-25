"""Cluster items that report the same story.

A weekly cycle makes this both easier and more valuable than it would be
daily: the whole week is in hand, so clusters can be formed once and the
best-written member promoted rather than whichever source published first.

Two passes:
  1. exact — canonical URL match after stripping tracking parameters
  2. near  — title similarity within a time window, to catch the same
             WHO announcement arriving via BBC, STAT and The Conversation
"""

from __future__ import annotations

from ..models import Item


def canonical_url(url: str) -> str:
    """Strip utm_*, at_medium, ?af=R and other tracking noise."""
    raise NotImplementedError


def cluster(items: list[Item]) -> list[Item]:
    """Assign cluster_id in place and return the same list."""
    raise NotImplementedError
