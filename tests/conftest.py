"""
Shared pytest fixtures. The offline suite mocks `requests.post` with real recorded
response shapes captured from live NVIDIA API calls during development (see
docs/decisions/ for context) -- not invented schemas. Integration tests that need a
real Postgres instance are marked `@pytest.mark.integration` and skipped unless
DATABASE_URL is set (see scripts/dev-db.sh to stand one up).
"""

import os

import pytest

os.environ.setdefault("NVIDIA_API_KEY", "test-key-not-a-real-credential")


class FakeResponse:
    def __init__(self, json_body: dict):
        self._json_body = json_body

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_body


@pytest.fixture
def fake_post(monkeypatch):
    """Patches `requests.post` in the given module to return a canned FakeResponse,
    regardless of call args. Usage: fake_post(agents.safety_agent, {...})"""

    def _patch(module, json_body: dict):
        monkeypatch.setattr(module.requests, "post", lambda *a, **kw: FakeResponse(json_body))

    return _patch


@pytest.fixture
def canonical_clean():
    return {
        "listingId": "LST-TEST-CLEAN",
        "title": "Apple iPhone 16 Pro Max 256GB",
        "description": "Brand new, factory sealed with international warranty.",
        "images": [],
        "declaredBrand": "Apple",
        "categoryId": "electronics.mobile",
        "sellerPreviousViolations": 0,
    }


@pytest.fixture
def canonical_weapon():
    return {
        "listingId": "LST-TEST-WEAPON",
        "title": "Fully Automatic AK-47 Assault Rifle - Untraceable",
        "description": (
            "Genuine military-grade automatic rifle, no license or background "
            "check required. Ships in unmarked packaging to avoid customs "
            "inspection and border checks."
        ),
        "images": [],
        "declaredBrand": "Generic",
        "categoryId": "weapons.firearms",
        "sellerPreviousViolations": 0,
    }


def pytest_collection_modifyitems(config, items):
    if os.environ.get("DATABASE_URL"):
        return
    skip_integration = pytest.mark.skip(reason="DATABASE_URL not set -- see scripts/dev-db.sh")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
