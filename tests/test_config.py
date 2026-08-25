"""Validate the shipped configuration.

These run offline and guard the cross-references between sources.yaml and
digest.yaml, which are easy to break by hand and produce confusing runtime
failures rather than obvious ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"
VALID_LICENCES = {"public_domain", "cc", "link_only"}
VALID_ADAPTERS = {"rss", "who_odata"}


@pytest.fixture(scope="module")
def sources() -> dict:
    return yaml.safe_load((CONFIG / "sources.yaml").read_text())


@pytest.fixture(scope="module")
def digest() -> dict:
    return yaml.safe_load((CONFIG / "digest.yaml").read_text())


def test_every_source_has_required_fields(sources):
    for source in sources["sources"]:
        for field in ("key", "name", "url", "weight", "sections"):
            assert field in source, f"{source.get('key', '?')} is missing {field}"


def test_source_keys_are_unique(sources):
    keys = [s["key"] for s in sources["sources"]]
    assert len(keys) == len(set(keys))


def test_licences_and_adapters_are_known(sources):
    default_licence = sources["defaults"]["licence"]
    default_adapter = sources["defaults"]["adapter"]
    for source in sources["sources"]:
        assert source.get("licence", default_licence) in VALID_LICENCES
        assert source.get("adapter", default_adapter) in VALID_ADAPTERS


def test_sources_only_reference_declared_sections(sources, digest):
    declared = {s["key"] for s in digest["sections"]}
    for source in sources["sources"]:
        unknown = set(source["sections"]) - declared
        assert not unknown, f"{source['key']} references unknown section(s) {unknown}"


def test_every_section_can_be_filled(sources, digest):
    """A section no source feeds would render empty every week."""
    supplied = {s for source in sources["sources"] for s in source["sections"]}
    for section in digest["sections"]:
        assert section["key"] in supplied, f"no source fills '{section['key']}'"


def test_section_quotas_are_coherent(digest):
    for section in digest["sections"]:
        assert 0 <= section["min"] <= section["max"], section["key"]


def test_required_sections_have_more_than_one_supplier(sources, digest):
    """A min>0 section fed by a single source fails whenever that source is quiet."""
    suppliers: dict[str, list[str]] = {}
    for source in sources["sources"]:
        for section in source["sections"]:
            suppliers.setdefault(section, []).append(source["key"])
    for section in digest["sections"]:
        if section["min"] > 0:
            assert len(suppliers[section["key"]]) > 1, (
                f"section '{section['key']}' requires {section['min']} item(s) "
                f"but only {suppliers[section['key']]} can supply it"
            )
