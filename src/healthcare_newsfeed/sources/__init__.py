"""Source adapters.

Each adapter turns one upstream source into a list of `RawItem`.
Most sources are RSS/Atom (see `rss.py`). Some publish no feed at all and
need bespoke adapters: WHO serves OData JSON (`who.py`), and the Singapore
agencies on Isomer Next embed their listing index in the page (`isomer.py`).
"""

from .base import ADAPTERS, Adapter, FeedError, clean_text
from .isomer import IsomerNewsroomAdapter
from .rss import RssAdapter
from .who import WhoODataAdapter

ADAPTERS["rss"] = RssAdapter
ADAPTERS["who_odata"] = WhoODataAdapter
ADAPTERS["isomer_newsroom"] = IsomerNewsroomAdapter

__all__ = ["ADAPTERS", "Adapter", "FeedError", "IsomerNewsroomAdapter", "RssAdapter",
           "WhoODataAdapter", "clean_text"]
