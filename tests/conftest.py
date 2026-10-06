"""Shared test fixtures for the V4-only app. Offline by default; `live` tests may call a real provider."""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost:5432/unused")
os.environ["REQUIRE_LOGIN"] = "false"

LIVE_MARKER = "live"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: exercises a real AI provider; skipped when no key is configured")


@pytest.fixture(autouse=True)
def _offline_ai(request):
    """Offline by default; live where a test asks for it."""
    import dataclasses

    from backend import llm
    from backend.config import settings

    if request.node.get_closest_marker(LIVE_MARKER):
        yield
        return

    previous = os.environ.get("AI_PROVIDER")
    os.environ["AI_PROVIDER"] = "offline"
    original = llm.settings
    llm.settings = dataclasses.replace(settings, ai_provider="offline")
    llm.get_provider(refresh=True)
    yield
    llm.settings = original
    if previous is None:
        os.environ.pop("AI_PROVIDER", None)
    else:
        os.environ["AI_PROVIDER"] = previous
    llm.get_provider(refresh=True)
