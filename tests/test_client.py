"""AIClient tests with a fake httpx transport (no network)."""

from __future__ import annotations

import json

import pytest

from scanfiler.ai.client import OpenAICompatClient, make_client
from scanfiler.config import AIConfig


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._ok = status_ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("HTTP 500")

    def json(self):
        return self._payload


class _FakeClient:
    """Stands in for httpx.Client; scripted per-call responses."""

    script: list = []
    posted: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        _FakeClient.posted.append({"url": url, "json": json, "headers": headers})
        item = _FakeClient.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _decision_payload(**over):
    base = {"filename": "Doc", "subdir": "Misc", "confidence": 0.7}
    base.update(over)
    return {"choices": [{"message": {"content": json.dumps(base)}}]}


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch):
    import httpx

    _FakeClient.script = []
    _FakeClient.posted = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)


def _client(**over):
    cfg = AIConfig(max_retries=3, request_timeout_s=1, **over)
    return OpenAICompatClient(cfg)


def test_decide_success_parses_decision():
    _FakeClient.script = [_FakeResponse(_decision_payload(filename="Invoice"))]
    d = _client().decide("sys", [{"type": "text", "text": "x"}], ["Misc"], True)
    assert d.filename == "Invoice"
    assert d.confidence == 0.7


def test_decide_retries_then_succeeds():
    _FakeClient.script = [RuntimeError("boom"), _FakeResponse(_decision_payload())]
    d = _client().decide("sys", [], [], True)
    assert d.filename == "Doc"
    assert len(_FakeClient.posted) == 2  # one failed, one succeeded


def test_decide_exhausts_retries_and_raises():
    _FakeClient.script = [RuntimeError("a"), RuntimeError("b"), RuntimeError("c")]
    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        _client().decide("sys", [], [], True)


def test_constrained_output_adds_response_format():
    _FakeClient.script = [_FakeResponse(_decision_payload())]
    _client(constrained_output=True).decide("sys", [], ["A", "B"], False)
    assert "response_format" in _FakeClient.posted[0]["json"]


def test_api_key_sets_auth_header():
    _FakeClient.script = [_FakeResponse(_decision_payload())]
    _client(api_key="tok").decide("sys", [], [], True)
    assert _FakeClient.posted[0]["headers"]["Authorization"] == "Bearer tok"


def test_make_client_returns_openai_compat():
    assert isinstance(make_client(AIConfig()), OpenAICompatClient)
