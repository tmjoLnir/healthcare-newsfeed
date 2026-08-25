"""Canonical URLs — pass 1 of dedupe.

The store keys every row on the canonical URL, so these cases decide what
counts as the same article. Pass 2, `cluster`, lands with the dedupe work
item; its tests belong here too.
"""

from __future__ import annotations

import pytest

from healthcare_newsfeed.pipeline.dedupe import canonical_url

# Real shapes from the configured sources, so a publisher changing its
# tracking scheme shows up here rather than as duplicate items in an issue.
TRACKED = [
    # BBC appends these to every link in the feed.
    ("https://www.bbc.co.uk/news/articles/c9w4jgg4y55o?at_medium=RSS&at_campaign=rss",
     "https://www.bbc.co.uk/news/articles/c9w4jgg4y55o"),
    # The journals' "available from" marker.
    ("https://www.nejm.org/doi/full/10.1056/NEJMoa2401?af=R",
     "https://www.nejm.org/doi/full/10.1056/NEJMoa2401"),
    # Elsevier, which serves The Lancet.
    ("https://www.thelancet.com/journals/lancet/article/PIIS01/fulltext?rss=yes&dgcid=raven",
     "https://www.thelancet.com/journals/lancet/article/PIIS01/fulltext"),
    # Anything shared onward picks up utm_*.
    ("https://kffhealthnews.org/article/x/?utm_source=rss&utm_medium=feed&utm_campaign=x",
     "https://kffhealthnews.org/article/x/"),
    ("https://www.statnews.com/2026/08/24/a/?utm_campaign=rss&fbclid=IwAR1&igshid=9",
     "https://www.statnews.com/2026/08/24/a/"),
]


@pytest.mark.parametrize("tracked,clean", TRACKED)
def test_tracking_parameters_are_stripped(tracked, clean):
    assert canonical_url(tracked) == clean


def test_meaningful_parameters_are_kept():
    """NEJM's own feed URL carries its journal code in the query string."""
    url = "https://www.nejm.org/action/showFeed?type=etoc&feed=rss&jc=nejm&utm_source=x"
    assert canonical_url(url) == "https://www.nejm.org/action/showFeed?type=etoc&feed=rss&jc=nejm"


def test_scheme_host_and_fragment_are_normalised():
    assert canonical_url("HTTPS://WWW.Example.TEST:443/Path/A?id=7#section-2") == \
        "https://www.example.test/Path/A?id=7"


def test_the_host_is_otherwise_left_alone():
    """This string is also the link the digest publishes, so www. stays."""
    assert canonical_url("https://www.who.int/news/item/x") == "https://www.who.int/news/item/x"


def test_the_path_is_left_alone():
    """A trailing slash is the publisher's choice; both forms are consistent
    within a source, and dropping one can turn a link into a redirect."""
    assert canonical_url("https://kffhealthnews.org/a/") == "https://kffhealthnews.org/a/"
    assert canonical_url("https://www.nature.com/articles/s41591") == \
        "https://www.nature.com/articles/s41591"


@pytest.mark.parametrize("value", ["", "   ", "not a url"])
def test_junk_is_returned_unchanged(value):
    assert canonical_url(value) == value.strip()
