"""認証の選び方。鍵があれば従来どおり Bearer、無ければ明示した proxy だけ。"""
from __future__ import annotations

import pytest

from jev_decision_gateway import typesafe


def test_key_sends_bearer():
    assert typesafe.auth_headers() == {"Authorization": "Bearer test-key-not-real"}


def test_key_wins_over_proxy(monkeypatch):
    monkeypatch.setenv("TYPESAFE_AUTH", "proxy")
    assert typesafe.auth_headers() == {"Authorization": "Bearer test-key-not-real"}


def test_proxy_sends_no_authorization(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("TYPESAFE_AUTH", "proxy")
    assert typesafe.auth_headers() == {}
    assert typesafe.auth_available()


@pytest.mark.parametrize("value", [None, "", "on", "key"])
def test_no_key_without_explicit_proxy_stops(monkeypatch, value):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    if value is not None:
        monkeypatch.setenv("TYPESAFE_AUTH", value)
    with pytest.raises(typesafe.JevStopped):
        typesafe.auth_headers()
    assert not typesafe.auth_available()


def test_claude_cloud_markers_alone_do_not_enable_proxy(monkeypatch):
    """**自動判定はしない**(2026-09-23 オーナー 案2)。"""
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.setenv("CCR_AGENT_PROXY_ENABLED", "1")
    assert not typesafe.auth_available()


def test_post_uses_auth_headers(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("TYPESAFE_AUTH", "proxy")
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    class Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers, json):
            seen.update(headers)
            return Resp()

    monkeypatch.setattr(typesafe.httpx, "Client", Client)
    assert typesafe.post_system_one({}) == {"ok": True}
    assert "Authorization" not in seen
