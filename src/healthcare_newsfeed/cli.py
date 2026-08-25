"""Command-line entrypoint.

    newsfeed poll                 fetch every due source into the store
    newsfeed build [--week ...]   assemble a digest without publishing
    newsfeed publish [--dry-run]  build and post the weekly issue
    newsfeed verify               check feed health (tools/verify_feeds.py)
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
