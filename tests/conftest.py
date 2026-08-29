"""
Shared pytest fixtures. The offline suite mocks `requests.post` with real recorded
response shapes captured from live NVIDIA API calls during development (see
docs/decisions/ for context) -- not invented schemas. Integration tests that need a
real Postgres instance are marked `@pytest.mark.integration` and skipped unless
DATABASE_URL is set (see scripts/dev-db.sh to stand one up). Tests marked
`@pytest.mark.fraud_eval` hit the live NVIDIA API for real and are skipped unless
AMM_RUN_FRAUD_EVAL is set (see docs/decisions/0021 and tests/test_fraud_eval.py). Tests
marked `@pytest.mark.ebay_fp_eval` are the same real-call shape but for false-positive
testing against real eBay listing titles; skipped unless AMM_RUN_EBAY_FP_EVAL is set
AND the local fixture file exists -- that file is never committed (CC BY-NC 4.0
source data, see docs/decisions/0023), so a fresh clone has neither by default.

Before either real-call suite actually runs, a one-time preflight
(`scripts/preflight_check.py`, docs/decisions/0026) confirms the models each of them
needs are actually callable -- added after two real incidents the same day (2026-08-29)
where a dead/flaky model silently hung or degraded a test run instead of failing
clearly. A model that's gone or unreachable skips the test with a clear reason instead
of letting it hang or fail confusingly mid-run.
"""

import os

import pytest

from scripts.preflight_check import check_all

# Hard dependencies only -- mistral-nemotron is deliberately excluded even though both
# suites can trigger a call to it (Safety Agent's prize-scam check), because that call
# fails open (docs/decisions/0022): the test still produces a valid result without it,
# so gating on it here would skip runnable tests over an optional signal being down.
REQUIRED_MODELS_BY_MARKER = {
    "fraud_eval": ["nvidia/llama-3.1-nemotron-safety-guard-8b-v3"],
    "ebay_fp_eval": ["nvidia/llama-3.1-nemotron-safety-guard-8b-v3"],
}

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


EBAY_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "ebay_titles.local.tsv")


_preflight_cache = None


def _preflight_status_by_model() -> dict:
    """Runs the real-call preflight at most once per pytest session (only if a
    real-call test actually needs it -- collection always runs this function, but the
    result is only fetched when a marker's opt-in condition is otherwise satisfied)."""
    global _preflight_cache
    if _preflight_cache is None:
        _preflight_cache = {r["model"]: r for r in check_all()}
    return _preflight_cache


def pytest_collection_modifyitems(config, items):
    skip_integration = pytest.mark.skip(reason="DATABASE_URL not set -- see scripts/dev-db.sh")
    skip_fraud_eval = pytest.mark.skip(reason="opt-in only -- set AMM_RUN_FRAUD_EVAL=1 (see docs/decisions/0021)")
    skip_ebay_fp_eval_optin = pytest.mark.skip(reason="opt-in only -- set AMM_RUN_EBAY_FP_EVAL=1 (see docs/decisions/0023)")
    skip_ebay_fp_eval_nofixture = pytest.mark.skip(
        reason=f"local fixture not found at {EBAY_FIXTURE_PATH} -- run scripts/fetch_ebay_titles_fixture.py (see docs/decisions/0023)"
    )
    for item in items:
        if "integration" in item.keywords and not os.environ.get("DATABASE_URL"):
            item.add_marker(skip_integration)

        if "fraud_eval" in item.keywords and not os.environ.get("AMM_RUN_FRAUD_EVAL"):
            item.add_marker(skip_fraud_eval)
        if "ebay_fp_eval" in item.keywords:
            if not os.environ.get("AMM_RUN_EBAY_FP_EVAL"):
                item.add_marker(skip_ebay_fp_eval_optin)
            elif not os.path.exists(EBAY_FIXTURE_PATH):
                item.add_marker(skip_ebay_fp_eval_nofixture)

        for marker_name, required_models in REQUIRED_MODELS_BY_MARKER.items():
            if marker_name not in item.keywords or any(m.name.startswith("skip") for m in item.own_markers):
                continue
            statuses = _preflight_status_by_model()
            for model_id in required_models:
                status = statuses.get(model_id)
                if status and status["status"] != "OK":
                    item.add_marker(
                        pytest.mark.skip(
                            reason=f"preflight: {model_id} is {status['status']} ({status['detail']}) -- "
                            "run scripts/preflight_check.py for details (docs/decisions/0026)"
                        )
                    )
