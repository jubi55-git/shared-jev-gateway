"""登録時検査: 包括的一問・同義多数決・反証/不確実性の欠落・閾値の緩和を弾く（4〜8）。"""
from __future__ import annotations

import pytest

from jev_decision_gateway.policy import PolicyError, digest, load_policy


def code(policy) -> str:
    with pytest.raises(PolicyError) as exc:
        load_policy(policy)
    return exc.value.code


def test_sample_policy_loads(policy):
    p = load_policy(policy)
    assert p.name == "sample_review" and len(p.questions) == 4


@pytest.mark.parametrize("text", [
    "このPRはマージしてよい", "この実装は安全か", "特に問題はないかを判定する", "Is it OK to ship", "大丈夫かどうか",
])
def test_comprehensive_question_rejected(policy, text):
    policy["questions"][0]["instructions"] = text
    assert code(policy) == "question_comprehensive"


def test_duplicate_failure_mode_rejected(policy):
    # 同じfailure modeの言い換え（同義質問多数決）を禁止
    policy["questions"][1]["failure_mode"] = "scope"
    assert code(policy) == "failure_mode_duplicate"


def test_duplicate_text_rejected(policy):
    policy["questions"][1]["instructions"] = policy["questions"][0]["instructions"]
    assert code(policy) == "question_duplicate_text"


def test_uncertainty_check_required(policy):
    policy["questions"] = [q for q in policy["questions"] if q["severity"] != "uncertainty"]
    assert code(policy) == "uncertainty_check_required"


def test_refutation_check_required(policy):
    for q in policy["questions"]:
        if q["severity"] != "uncertainty":
            q["polarity"] = "assurance"
    assert code(policy) == "refutation_check_required"


def test_min_required_checks(policy):
    for q in policy["questions"][:2]:
        q["required"] = False
    assert code(policy) == "too_few_required_checks"


def test_counter_must_be_opposite_same_mode(policy):
    policy["questions"].append({
        "name": "scope_again", "failure_mode": "scope", "polarity": "risk", "severity": "normal",
        "required": False, "counter_of": "scope_deviation_present",
        "instructions": "変更に、要求に無い挙動が混ざっている"})
    assert code(policy) == "counter_invalid"  # 同じ極性の言い換えは反証にならない


def test_threshold_cannot_be_loosened(policy):
    policy["thresholds"] = {"pass": 0.8}
    assert code(policy) == "threshold_loosened"
    policy["thresholds"] = {"reject": 0.3}
    assert code(policy) == "thresholds_shape"


def test_fact_cannot_route_to_pass(policy):
    policy["facts"][0]["else"] = "PASS"
    assert code(policy) == "fact_shape"


def test_unknown_keys_rejected(policy):
    policy["questions"][0]["weight"] = 2
    assert code(policy) == "question_shape"


def test_digest_matches_dearm_ai_call_guard():
    # dearm-app scripts/ai_call_guard.digest({'a':[1,'夢']}) の出力。hash互換を固定する。
    assert digest({"a": [1, "夢"]}) == "de19123c536da252113e4c92dcf3a28252ff69cb1d0533c08c92352cadc2dfec"
