"""Source adapters.

Each adapter turns one upstream source into a list of `RawItem`.
Most sources are RSS/Atom (see `rss.py`); WHO requires a bespoke
OData JSON adapter (see `who.py`) because it no longer publishes RSS.
"""

from .base import ADAPTERS, Adapter
from .rss import FeedError, RssAdapter

ADAPTERS["rss"] = RssAdapter
# "who_odata" registers here once sources/who.py is written.

__all__ = ["ADAPTERS", "Adapter", "FeedError", "RssAdapter"]
