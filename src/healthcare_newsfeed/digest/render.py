"""Render a Digest into Telegram-ready messages.

Constraints:
  * 4096 characters per message, so a full issue is split across an ordered
    burst — split on section boundaries, never mid-item
  * licence-aware bodies: PUBLIC_DOMAIN and CC_REPUBLISHABLE items may carry
    a long extract; LINK_ONLY items get headline + own-words summary + link
  * paywalled sources are labelled inline, so readers know before they click
  * NEJM supplies no usable summary (its feed carries an 87-character
    citation string), so those items render title + link only
"""

from __future__ import annotations

from ..models import Digest


def render(digest: Digest) -> list[str]:
    """Return the ordered message bodies for one issue."""
    raise NotImplementedError
