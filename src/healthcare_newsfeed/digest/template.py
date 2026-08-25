"""Section definitions for the weekly issue.

The canonical order and headings live in config/digest.yaml; this module
resolves that file into the objects the renderer walks.

`config.load_digest_template` already checks the file's shape — every
section has a key, a heading and a possible quota. What it cannot check is
`style`, because what a style means belongs to the renderer: `extract` asks
for a long body and `headline` for none. Resolving here gives that check a
home, and it fires when the config loads rather than at 19:00 on a Sunday.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import ConfigError
from ..telegram import MESSAGE_LIMIT

# How much body text each style asks for, in characters. This is the *ask*;
# the item's licence sets a ceiling over it, so a `long` section fed from a
# link-only source still renders short. See digest/render.py.
STYLE_LENGTHS = {
    "headline": 0,        # title + link only
    "short": 180,
    "long": 400,
    "extract": 900,       # CC-licensed or public domain: may carry real text
}

DEFAULT_STYLE = "short"

# Below this a message cannot carry a section heading, one headline and its
# link, so the renderer would have nothing it could legally emit. A limit
# that small is a typo rather than a preference.
MIN_MESSAGE_CHARS = 280

# A zone that any working tz database has. If this one resolves and the
# configured zone does not, the configured zone is a typo and worth an
# error; if neither resolves there is no tz database at all, and falling
# back to UTC is better than refusing to publish. The two cases raise the
# same exception, so telling them apart takes asking.
_TZ_CANARY = "Etc/UTC"


@dataclass(frozen=True)
class SectionSpec:
    """One section of the template: what it is called and how much it takes."""

    key: str
    heading: str
    min: int
    max: int
    style: str = DEFAULT_STYLE
    diversify_by: str | None = None

    @property
    def body_chars(self) -> int:
        """How much summary text this section asks each item to carry."""
        return STYLE_LENGTHS[self.style]


@dataclass(frozen=True)
class IssueSpec:
    """The `issue:` block, resolved: how a rendered issue is titled and split."""

    title: str
    timezone: str
    publish: str
    max_message_chars: int
    sections: tuple[SectionSpec, ...]

    def section(self, key: str) -> SectionSpec | None:
        """The spec for a section key, or None if the template dropped it."""
        return next((spec for spec in self.sections if spec.key == key), None)

    @property
    def tz(self) -> dt.tzinfo:
        """The zone the issue's dates read in.

        Dates are the one thing a reader checks against their own calendar,
        so an issue published at 19:00 SGT should not be headed with the
        previous day. The store stays UTC either way.
        """
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return dt.UTC          # no tz database; resolve() already warned


def resolve(template: dict) -> IssueSpec:
    """Turn a loaded digest.yaml into the objects the renderer walks."""
    issue = template.get("issue") or {}
    if not isinstance(issue, dict):
        raise ConfigError("digest template: 'issue' should be a mapping")

    sections = tuple(_section(entry) for entry in template["sections"])
    timezone = str(issue.get("timezone") or "UTC")
    _check_timezone(timezone)

    return IssueSpec(
        title=str(issue.get("title") or "Weekly digest"),
        timezone=timezone,
        publish=str(issue.get("publish") or ""),
        max_message_chars=_message_limit(issue.get("max_message_chars")),
        sections=sections,
    )


def _section(entry: dict) -> SectionSpec:
    style = entry.get("style") or DEFAULT_STYLE
    if style not in STYLE_LENGTHS:
        raise ConfigError(
            f"digest template: section '{entry['key']}' has unknown style "
            f"'{style}' — expected one of {sorted(STYLE_LENGTHS)}"
        )
    return SectionSpec(
        key=str(entry["key"]),
        heading=str(entry["heading"]),
        min=int(entry["min"]),
        max=int(entry["max"]),
        style=str(style),
        diversify_by=entry.get("diversify_by"),
    )


def _message_limit(value: object) -> int:
    """Telegram's cap, or a smaller one the template asked for.

    A larger one is refused rather than clamped: the API rejects the message
    outright, and a config that reads as though 8,000 characters will go out
    is worse than one that will not load. A far smaller one is refused too —
    see MIN_MESSAGE_CHARS.
    """
    if value is None:
        return MESSAGE_LIMIT
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"digest template: max_message_chars {value!r} is not a positive integer")
    if value > MESSAGE_LIMIT:
        raise ConfigError(
            f"digest template: max_message_chars {value} exceeds Telegram's "
            f"limit of {MESSAGE_LIMIT}"
        )
    if value < MIN_MESSAGE_CHARS:
        raise ConfigError(
            f"digest template: max_message_chars {value} leaves no room for a "
            f"heading and one item; the minimum is {MIN_MESSAGE_CHARS}"
        )
    return value


def _check_timezone(name: str) -> None:
    """Reject a misspelled zone, but tolerate a container with no tz data."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        try:
            ZoneInfo(_TZ_CANARY)
        except (ZoneInfoNotFoundError, ValueError):
            return              # no tz database anywhere; IssueSpec.tz falls back to UTC
        raise ConfigError(f"digest template: unknown timezone '{name}'") from exc
