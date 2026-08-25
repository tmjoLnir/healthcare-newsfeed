"""Validate the shipped configuration, and the loader that reads it.

These run offline and guard the cross-references between sources.yaml and
digest.yaml, which are easy to break by hand and produce confusing runtime
failures rather than obvious ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from healthcare_newsfeed.config import ConfigError, load_digest_template, load_sources
from healthcare_newsfeed.models import Licence

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


# --- the loader -------------------------------------------------------------

def write(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(document))
    return path


def source_entry(**overrides) -> dict:
    return {"key": "bbc", "name": "BBC", "url": "https://bbc.test/feed",
            "weight": 0.7, "sections": ["also_reading"]} | overrides


def test_the_shipped_config_loads(tmp_path):
    """The loader and the files it reads have to agree, not just parse."""
    sources = load_sources(CONFIG / "sources.yaml")

    assert len(sources) == 17
    assert {s.adapter for s in sources} == {"rss", "who_odata"}
    assert next(s for s in sources if s.key == "nature_med").tolerate_failure
    assert next(s for s in sources if s.key == "who_news").licence is Licence.PUBLIC_DOMAIN
    assert next(s for s in sources if s.key == "statnews").paywalled

    # The regional trio is the reason the issue is not entirely UK/US/global,
    # so it is worth pinning rather than leaving to the count above.
    regional = {"annals_sg", "lancet_wpc", "lancet_sea"}
    assert regional <= {s.key for s in sources}
    assert all("global_health" in s.sections for s in sources if s.key in regional)
    assert next(s for s in sources if s.key == "annals_sg").licence is Licence.CC_REPUBLISHABLE
    assert "kesehatan" in next(s for s in sources if s.key == "conversation_id").url

    template = load_digest_template(CONFIG / "digest.yaml")
    assert next(s["key"] for s in template["sections"]) == "story_of_week"


def test_defaults_fill_in_what_a_source_leaves_out(tmp_path):
    path = write(tmp_path, {"defaults": {"adapter": "rss", "licence": "cc", "poll_hours": 12},
                            "sources": [source_entry()]})

    (source,) = load_sources(path)

    assert (source.adapter, source.licence, source.poll_hours) == ("rss", Licence.CC_REPUBLISHABLE, 12)
    assert source.enabled and not source.paywalled and not source.tolerate_failure


def test_a_source_overrides_the_defaults(tmp_path):
    path = write(tmp_path, {"defaults": {"licence": "link_only"},
                            "sources": [source_entry(licence="public_domain")]})

    assert load_sources(path)[0].licence is Licence.PUBLIC_DOMAIN


def test_disabled_sources_are_skipped(tmp_path):
    path = write(tmp_path, {"sources": [source_entry(),
                                        source_entry(key="off", enabled=False)]})

    assert [s.key for s in load_sources(path)] == ["bbc"]


def test_documentation_only_fields_are_allowed(tmp_path):
    """retention_days and notes explain why a source is configured as it is."""
    path = write(tmp_path, {"sources": [source_entry(retention_days=4, notes="flaky host")]})

    assert load_sources(path)[0].key == "bbc"


@pytest.mark.parametrize("entry,message", [
    ({"weigth": 1.0}, "unknown field"),
    ({"weight": None}, "missing"),
    ({"licence": "made_up"}, "unknown licence"),
    ({"adapter": "carrier_pigeon"}, "not registered"),
    ({"weight": "heavy"}, "non-numeric"),
    ({"poll_hours": 0}, "invalid poll_hours"),
    ({"sections": []}, "at least one section"),
])
def test_a_bad_source_says_what_is_wrong(tmp_path, entry, message):
    path = write(tmp_path, {"sources": [source_entry(**entry)]})

    with pytest.raises(ConfigError, match=message):
        load_sources(path)


def test_duplicate_keys_are_rejected(tmp_path):
    path = write(tmp_path, {"sources": [source_entry(), source_entry()]})

    with pytest.raises(ConfigError, match="duplicate source key"):
        load_sources(path)


def test_an_empty_or_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="no such file"):
        load_sources(tmp_path / "absent.yaml")

    with pytest.raises(ConfigError, match="no sources configured"):
        load_sources(write(tmp_path, {"defaults": {}}))

    with pytest.raises(ConfigError, match="every source is disabled"):
        load_sources(write(tmp_path, {"sources": [source_entry(enabled=False)]}))


def test_a_digest_section_with_an_impossible_quota_is_rejected(tmp_path):
    path = tmp_path / "digest.yaml"
    path.write_text(yaml.safe_dump({"issue": {"title": "x"}, "sections": [
        {"key": "journals", "heading": "J", "min": 4, "max": 2}]}))

    with pytest.raises(ConfigError, match="impossible quota"):
        load_digest_template(path)
