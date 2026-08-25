"""Shared fixtures.

Feed samples in tests/fixtures/ are captured responses, so the suite runs
without network access and stays stable when a publisher changes its
output. Refresh them with tools/verify_feeds.py when a source changes shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    def _load(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()
    return _load
