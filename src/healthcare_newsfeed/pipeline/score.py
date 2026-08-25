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
"""

from __future__ import annotations

from ..models import Item


def score(items: list[Item]) -> list[Item]:
    """Set `score` on each item and return sorted, highest first."""
    raise NotImplementedError
