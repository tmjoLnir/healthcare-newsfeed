"""Source adapters.

Each adapter turns one upstream source into a list of `RawItem`.
Most sources are RSS/Atom (see `rss.py`). Two publish no feed at all and
need bespoke adapters: WHO serves OData JSON (`who.py`), and MOH Singapore
embeds its newsroom index in a Next.js page (`moh.py`).
"""

from .base import ADAPTERS, Adapter, FeedError, clean_text
from .moh import MohNewsroomAdapter
from .rss import RssAdapter
from .who import WhoODataAdapter

ADAPTERS["rss"] = RssAdapter
ADAPTERS["who_odata"] = WhoODataAdapter
ADAPTERS["moh_newsroom"] = MohNewsroomAdapter

__all__ = ["ADAPTERS", "Adapter", "FeedError", "MohNewsroomAdapter", "RssAdapter",
           "WhoODataAdapter", "clean_text"]
