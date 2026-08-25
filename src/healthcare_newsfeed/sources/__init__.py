"""Source adapters.

Each adapter turns one upstream source into a list of `RawItem`.
Most sources are RSS/Atom (see `rss.py`); WHO requires a bespoke
OData JSON adapter (see `who.py`) because it no longer publishes RSS.
"""

from .base import ADAPTERS, Adapter, FeedError, clean_text
from .rss import RssAdapter
from .who import WhoODataAdapter

ADAPTERS["rss"] = RssAdapter
ADAPTERS["who_odata"] = WhoODataAdapter

__all__ = ["ADAPTERS", "Adapter", "FeedError", "RssAdapter", "WhoODataAdapter", "clean_text"]
