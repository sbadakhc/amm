"""
scripts/preflight_check.py has no real-call dependency of its own to test against --
requests.get/requests.post are mocked here the same way tests/test_embeddings.py mocks
requests.post for embed_text. Focused on the GONE-vs-UNREACHABLE distinction
(docs/decisions/0026) that's this script's entire reason to exist -- AGENTS.md
mandates running it "ALWAYS ... first", so its classification logic is worth pinning
down with regression tests even though the network calls themselves aren't real here.
"""

import requests

from scripts import preflight_check as pf


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, elapsed_seconds=0.5):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self._json_body = json_body or {}
        self.elapsed = _FakeElapsed(elapsed_seconds)

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if not self.ok:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


class _FakeElapsed:
    def __init__(self, seconds):
        self._seconds = seconds

    def total_seconds(self):
        return self._seconds


def test_fetch_catalog_returns_model_ids_on_success(monkeypatch):
    monkeypatch.setattr(
        pf.requests,
        "get",
        lambda *a, **kw: FakeResponse(json_body={"data": [{"id": "vendor/model-a"}, {"id": "vendor/model-b"}]}),
    )
    assert pf._fetch_catalog() == {"vendor/model-a", "vendor/model-b"}


def test_fetch_catalog_returns_none_on_request_exception(monkeypatch):
    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(pf.requests, "get", _raise)
    assert pf._fetch_catalog() is None


def test_check_chat_model_ok_when_in_catalog_and_reachable(monkeypatch):
    monkeypatch.setattr(pf.requests, "post", lambda *a, **kw: FakeResponse(status_code=200))
    result = pf._check_chat_model("vendor/model-a", "used by X", catalog={"vendor/model-a"})
    assert result["status"] == "OK"


def test_check_chat_model_gone_when_not_in_catalog(monkeypatch):
    """The whole point of docs/decisions/0026 -- a model missing from /v1/models is
    reported as GONE (needs a code fix) without even attempting a live call."""

    def _fail_if_called(*a, **kw):
        raise AssertionError("should not attempt a live call for a model missing from the catalog")

    monkeypatch.setattr(pf.requests, "post", _fail_if_called)
    result = pf._check_chat_model("vendor/removed-model", "used by X", catalog={"vendor/model-a"})
    assert result["status"] == "GONE"
    assert result["detail"] == "not in /v1/models catalog"


def test_check_chat_model_gone_on_410(monkeypatch):
    monkeypatch.setattr(
        pf.requests, "post", lambda *a, **kw: FakeResponse(status_code=410, json_body={"detail": "model retired"})
    )
    result = pf._check_chat_model("vendor/model-a", "used by X", catalog={"vendor/model-a"})
    assert result["status"] == "GONE"
    assert result["detail"] == "model retired"


def test_check_chat_model_unreachable_on_request_exception(monkeypatch):
    """Transient network failure -- distinct from GONE, may just need a retry."""

    def _raise(*a, **kw):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(pf.requests, "post", _raise)
    result = pf._check_chat_model("vendor/model-a", "used by X", catalog={"vendor/model-a"})
    assert result["status"] == "UNREACHABLE"
    assert result["detail"] == "Timeout"


def test_check_chat_model_unreachable_on_non_ok_status(monkeypatch):
    monkeypatch.setattr(pf.requests, "post", lambda *a, **kw: FakeResponse(status_code=500))
    result = pf._check_chat_model("vendor/model-a", "used by X", catalog={"vendor/model-a"})
    assert result["status"] == "UNREACHABLE"
    assert result["detail"] == "HTTP 500"


def test_check_chat_model_still_attempts_live_call_when_catalog_unknown(monkeypatch):
    """catalog=None means _fetch_catalog itself failed -- can't confirm GONE, so it
    falls through to a live call rather than assuming either way."""
    monkeypatch.setattr(pf.requests, "post", lambda *a, **kw: FakeResponse(status_code=200))
    result = pf._check_chat_model("vendor/model-a", "used by X", catalog=None)
    assert result["status"] == "OK"


def test_check_embedding_model_gone_when_not_in_catalog(monkeypatch):
    def _fail_if_called(*a, **kw):
        raise AssertionError("should not attempt a live call for a model missing from the catalog")

    monkeypatch.setattr(pf.requests, "post", _fail_if_called)
    result = pf._check_embedding_model("vendor/removed-embed-model", "used by Y", catalog={"vendor/model-a"})
    assert result["status"] == "GONE"


def test_check_embedding_model_ok_when_reachable(monkeypatch):
    monkeypatch.setattr(pf.requests, "post", lambda *a, **kw: FakeResponse(status_code=200))
    result = pf._check_embedding_model("vendor/embed-model", "used by Y", catalog={"vendor/embed-model"})
    assert result["status"] == "OK"


def test_suggest_candidates_matches_same_owner_and_keyword(monkeypatch):
    catalog = {
        "vendor/removed-vision-model",
        "vendor/other-safety-model",
        "othervendor/vision-model",
        "vendor/unrelated-text-model",
    }
    candidates = pf._suggest_candidates("vendor/removed-vision-model", catalog)
    assert "vendor/other-safety-model" in candidates  # same owner
    assert "othervendor/vision-model" in candidates  # "vision" keyword match
    assert "vendor/removed-vision-model" not in candidates  # excludes itself


def test_suggest_candidates_empty_when_no_match():
    catalog = {"othervendor/unrelated-model"}
    assert pf._suggest_candidates("vendor/removed-model", catalog) == []


def test_check_all_aggregates_chat_and_embedding_results(monkeypatch):
    monkeypatch.setattr(pf, "_fetch_catalog", lambda: {"fake"})
    monkeypatch.setattr(
        pf, "_check_chat_model", lambda model_id, used_by, catalog: {"model": model_id, "status": "OK"}
    )
    monkeypatch.setattr(
        pf, "_check_embedding_model", lambda model_id, used_by, catalog: {"model": model_id, "status": "OK"}
    )

    results = pf.check_all()

    assert {r["model"] for r in results} == set(pf.CHAT_MODELS) | set(pf.EMBEDDING_MODELS)
    assert all(r["status"] == "OK" for r in results)
