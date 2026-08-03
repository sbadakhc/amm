"""
Shared pytest fixtures. The offline suite mocks `requests.post` with real recorded
response shapes captured from live NVIDIA API calls during development (see
docs/decisions/ for context) -- not invented schemas. Integration tests that need a
real Postgres instance are marked `@pytest.mark.integration` and skipped unless
DATABASE_URL is set (see scripts/dev-db.sh to stand one up). Tests marked
`@pytest.mark.fraud_eval` hit the live NVIDIA API for real and are skipped unless
AMM_RUN_FRAUD_EVAL is set (see docs/decisions/0021 and tests/test_fraud_eval.py).
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
    skip_integration = pytest.mark.skip(reason="DATABASE_URL not set -- see scripts/dev-db.sh")
    skip_fraud_eval = pytest.mark.skip(reason="opt-in only -- set AMM_RUN_FRAUD_EVAL=1 (see docs/decisions/0021)")
    for item in items:
        if "integration" in item.keywords and not os.environ.get("DATABASE_URL"):
            item.add_marker(skip_integration)
        if "fraud_eval" in item.keywords and not os.environ.get("AMM_RUN_FRAUD_EVAL"):
            item.add_marker(skip_fraud_eval)
