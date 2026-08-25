"""Load and validate config/sources.yaml and config/digest.yaml."""

from __future__ import annotations

from pathlib import Path

from .models import Source


def load_sources(path: Path) -> list[Source]:
    """Parse sources.yaml into Source objects, skipping disabled entries."""
    raise NotImplementedError


def load_digest_template(path: Path) -> dict:
    """Parse digest.yaml: section order, headings, and per-section quotas."""
    raise NotImplementedError
