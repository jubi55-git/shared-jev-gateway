from __future__ import annotations

import copy

import pytest

MODEL = "jev-1.13.0"

POLICY = {
    "policy": "sample_review",
    "version": "sample-v1",
    "facts": [
        {"name": "checks_green", "must_be": True, "else": "REJECT"},
        {"name": "within_limit", "must_be": True, "else": "ESCALATE"},
    ],
    "state_fields": {
        "requirements": {"max_chars": 500},
        "change": {"max_chars": 2000},
    },
    "questions": [
        {"name": "scope_deviation_present", "failure_mode": "scope", "polarity": "risk",
         "severity": "critical", "required": True,
         "instructions": "変更に、要求一覧に無い挙動の変更が含まれている"},
        {"name": "requirement_missing", "failure_mode": "completeness", "polarity": "risk",
         "severity": "critical", "required": True,
         "instructions": "要求一覧のうち、変更で扱われていない項目がある"},
        {"name": "edge_case_ignored", "failure_mode": "boundary", "polarity": "risk",
         "severity": "normal", "required": True,
         "instructions": "変更が、入力の境界や例外の扱いを考慮していない"},
        {"name": "parent_review_needed", "failure_mode": "uncertainty", "polarity": "risk",
         "severity": "uncertainty", "required": True,
         "instructions": "提示された情報だけでは判断しきれず、より高性能なモデルによる確認が必要な不確実性が残っている"},
    ],
}

STATE = {"requirements": ["一覧の並び順を日付順にする"], "change": "sort(key=date)"}
FACTS = {"checks_green": True, "within_limit": True}


@pytest.fixture
def policy():
    return copy.deepcopy(POLICY)


@pytest.fixture
def state():
    return copy.deepcopy(STATE)


@pytest.fixture
def facts():
    return dict(FACTS)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    monkeypatch.delenv("TYPESAFE_JEV_MODEL", raising=False)
    monkeypatch.delenv("TYPESAFE_AUTH", raising=False)


class FakeJev:
    """TypeSafeの代わり。呼ばれた回数と受け取ったrequestを残す。"""

    def __init__(self, answers: dict[str, float] | None = None, *, model=MODEL, error=None):
        self.answers = answers or {}
        self.model = model
        self.error = error
        self.requests: list[dict] = []

    def __call__(self, request: dict) -> dict:
        self.requests.append(request)
        if self.error:
            raise self.error
        return {
            "model": self.model,
            "answers": {n: {"type": "noul", "noul": v} for n, v in self.answers.items()},
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }


def answers(policy: dict, **overrides) -> dict[str, float]:
    """既定は全設問「失敗なし」を強く答える（risk設問なので p_yes=0.02）。"""
    out = {q["name"]: 0.02 for q in policy["questions"]}
    out.update(overrides)
    return out
