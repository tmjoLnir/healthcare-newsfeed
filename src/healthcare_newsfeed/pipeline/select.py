"""Fill the digest's fixed sections from scored candidates.

Section quotas come from config/digest.yaml. Selection is per-section
rather than global so a strong news week cannot crowd out the ethics slot,
which is the section this audience benefits from most and the one with the
thinnest supply (the JME blog averages 1-2 posts a week and can fall
silent for a fortnight).

Rules:
  * one item per cluster
  * never republish an item carried by an earlier issue
  * a section short on supply shrinks; it does not borrow from another
"""

from __future__ import annotations

from ..models import Digest, Item


def select(items: list[Item], template: dict, issue: int) -> Digest:
    raise NotImplementedError
