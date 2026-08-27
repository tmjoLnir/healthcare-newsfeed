"""Load and validate config/sources.yaml and config/digest.yaml.

Validation is deliberately strict — an unknown key is an error rather than
something quietly ignored. These files are edited by hand, and the failures
they cause otherwise show up far from the typo: a misspelled `weigth` would
leave a source silently ranked at its default, and a section key that no
longer matches digest.yaml would empty a block of the issue without saying
why.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import Licence, Source
from .sources import ADAPTERS

# Fields a source entry may carry. `retention_days` and `notes` document why
# a source is configured the way it is — measured feed history, a publisher
# that blocks datacenter IPs — and are read by people, not by the pipeline.
SOURCE_FIELDS = frozenset({
    "key", "name", "url", "adapter", "licence", "weight", "sections",
    "paywalled", "poll_hours", "enabled", "tolerate_failure",
    "max_items", "retention_days", "notes",
})

REQUIRED_SOURCE_FIELDS = ("key", "name", "url", "weight", "sections")

SECTION_FIELDS = frozenset({"key", "heading", "min", "max", "style", "diversify_by"})

REQUIRED_SECTION_FIELDS = ("key", "heading", "min", "max")


class ConfigError(ValueError):
    """A configuration file is missing something, or says something impossible."""


def _document(path: Path) -> dict:
    try:
        document = yaml.safe_load(Path(path).read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: no such file") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML ({exc})") from exc
    if not isinstance(document, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return document


def load_sources(path: Path) -> list[Source]:
    """Parse sources.yaml into Source objects, skipping disabled entries."""
    document = _document(path)
    defaults = document.get("defaults") or {}
    entries = document.get("sources")
    if not entries:
        raise ConfigError(f"{path}: no sources configured")

    sources: list[Source] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: source #{index + 1} is not a mapping")

        key = entry.get("key", f"#{index + 1}")
        unknown = set(entry) - SOURCE_FIELDS
        if unknown:
            raise ConfigError(
                f"{path}: source '{key}' has unknown field(s) {sorted(unknown)} — "
                f"known fields are {sorted(SOURCE_FIELDS)}"
            )
        missing = [field for field in REQUIRED_SOURCE_FIELDS if entry.get(field) is None]
        if missing:
            raise ConfigError(f"{path}: source '{key}' is missing {missing}")

        merged = {**defaults, **entry}
        if not merged.get("enabled", True):
            continue                    # disabled sources never reach the poll
        if key in seen:
            raise ConfigError(f"{path}: duplicate source key '{key}'")
        seen.add(key)

        sources.append(Source(
            key=key,
            name=str(merged["name"]),
            url=str(merged["url"]),
            adapter=_adapter(merged.get("adapter", "rss"), key, path),
            licence=_licence(merged.get("licence", "link_only"), key, path),
            weight=_number(merged["weight"], "weight", key, path),
            sections=_sections(merged["sections"], key, path),
            paywalled=bool(merged.get("paywalled", False)),
            poll_hours=_poll_hours(merged.get("poll_hours", 24), key, path),
            enabled=True,
            tolerate_failure=bool(merged.get("tolerate_failure", False)),
            max_items=_max_items(merged.get("max_items"), key, path),
        ))

    if not sources:
        raise ConfigError(f"{path}: every source is disabled")
    return sources


def _adapter(value: object, key: str, path: Path) -> str:
    """Check against the live registry, not a list — an adapter named here
    with nothing registered for it would fail at poll time instead."""
    if value not in ADAPTERS:
        raise ConfigError(
            f"{path}: source '{key}' wants adapter '{value}', "
            f"which is not registered (have {sorted(ADAPTERS)})"
        )
    return str(value)


def _licence(value: object, key: str, path: Path) -> Licence:
    try:
        return Licence(value)
    except ValueError as exc:
        raise ConfigError(
            f"{path}: source '{key}' has unknown licence '{value}' — "
            f"expected one of {[licence.value for licence in Licence]}"
        ) from exc


def _number(value: object, field: str, key: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{path}: source '{key}' has non-numeric {field} {value!r}")
    return float(value)


def _max_items(value: object, key: str, path: Path) -> int | None:
    """A per-poll record cap for feedless listings, or None for the default."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(
            f"{path}: source '{key}' has max_items {value!r} — "
            f"expected a positive whole number of records"
        )
    return value


def _poll_hours(value: object, key: str, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{path}: source '{key}' has invalid poll_hours {value!r}")
    return value


def _sections(value: object, key: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{path}: source '{key}' must list at least one section")
    return tuple(str(section) for section in value)


def load_digest_template(path: Path) -> dict:
    """Parse digest.yaml: section order, headings, and per-section quotas."""
    document = _document(path)
    if "issue" not in document:
        raise ConfigError(f"{path}: no 'issue' block")
    sections = document.get("sections")
    if not sections:
        raise ConfigError(f"{path}: no sections configured")

    seen: set[str] = set()
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            raise ConfigError(f"{path}: section #{index + 1} is not a mapping")

        key = section.get("key", f"#{index + 1}")
        unknown = set(section) - SECTION_FIELDS
        if unknown:
            raise ConfigError(
                f"{path}: section '{key}' has unknown field(s) {sorted(unknown)} — "
                f"known fields are {sorted(SECTION_FIELDS)}"
            )
        missing = [field for field in REQUIRED_SECTION_FIELDS if section.get(field) is None]
        if missing:
            raise ConfigError(f"{path}: section '{key}' is missing {missing}")
        if key in seen:
            raise ConfigError(f"{path}: duplicate section key '{key}'")
        seen.add(key)

        low, high = section["min"], section["max"]
        if not isinstance(low, int) or not isinstance(high, int) or not 0 <= low <= high:
            raise ConfigError(
                f"{path}: section '{key}' has an impossible quota "
                f"(min {low!r}, max {high!r}); expected 0 <= min <= max"
            )

    return document
