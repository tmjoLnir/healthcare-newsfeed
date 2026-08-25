"""WHO OData JSON adapter.

WHO retired RSS: every documented feed path returns 404, and the one URL
that still resolves (`/rss-feeds/news-english.xml`) is abandoned — 25 items
spanning more than a year. Live data is served from a Sitecore OData API:

    https://www.who.int/api/news/newsitems
    https://www.who.int/api/news/diseaseoutbreaknews

Both default to an unsorted page, so the query must order explicitly:

    ?$orderby=PublicationDateAndTime desc&$top=20

Fields of interest: Title, PublicationDateAndTime, ItemDefaultUrl, Summary.
"""

from __future__ import annotations

from ..models import RawItem, Source


class WhoODataAdapter:
    def fetch(self, source: Source) -> list[RawItem]:
        raise NotImplementedError
