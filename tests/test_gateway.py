"""Gatewayの判定経路。番号は設計書のテスト方針（1〜23）。"""
from __future__ import annotations

import httpx

from conftest import MODEL, FakeJev, answers
from jev_decision_gateway import evaluate, typesafe
from jev_decision_gateway.ledger import JsonlLedger
from jev_decision_gateway.telemetry import JsonlTelemetry


def run(tmp_path, policy, state, facts, jev, **kw):
    return evaluate(policy, state, facts, ledger=JsonlLedger(tmp_path / "ledger.jsonl"),
                    telemetry=JsonlTelemetry(tmp_path / "tel.jsonl"), transport=jev,
                    model=MODEL, **kw)


# 1 / 22: コードで決まればJevを呼ばない（親モデルも呼ばずに終わる経路）
def test_fact_reject_skips_jev(tmp_path, policy, state, facts):
    jev = FakeJev(answers(policy))
    facts["checks_green"] = False
    r = run(tmp_path, policy, state, facts, jev)
    assert (r["route"], r["stage"], r["reason_code"], r["jev_calls"]) == ("REJECT", "deterministic", "fact:checks_green", 0)
    assert jev.requests == []


def test_fact_escalate_skips_jev(tmp_path, policy, state, facts):
    jev = FakeJev(answers(policy))
    facts["within_limit"] = False
    r = run(tmp_path, policy, state, facts, jev)
    assert r["route"] == "ESCALATE" and jev.requests == []


def test_fact_missing_or_extra_is_hold(tmp_path, policy, state, facts):
    jev = FakeJev(answers(policy))
    del facts["within_limit"]
    assert run(tmp_path, policy, state, facts, jev)["reason_code"] == "fact_missing:within_limit"
    facts.update(within_limit=True, ci_count=3)
    assert run(tmp_path, policy, state, facts, jev)["route"] == "HOLD"
    assert jev.requests == []


# 2 / 3: 送るのは意味判断の設問だけ。包括的一問ではない
def test_only_semantic_questions_sent(tmp_path, policy, state, facts):
    jev = FakeJev(answers(policy))
    run(tmp_path, policy, state, facts, jev)
    (req,) = jev.requests
    assert set(req) == {"state", "model", "questions"}
    assert req["state"] == state
    assert set(req["questions"]) == {q["name"] for q in policy["questions"]}
    assert "checks_green" not in str(req)
    assert all(v["type"] == "noul" for v in req["questions"].values())


# 8 / 22: 全必須checkが明瞭なときだけPASS（Jevだけで安全に終わる経路）
def test_all_clear_pass(tmp_path, policy, state, facts):
    r = run(tmp_path, policy, state, facts, FakeJev(answers(policy)))
    assert (r["route"], r["reason_code"], r["jev_calls"]) == ("PASS", "all_required_clear", 1)
    assert all(s["effect"] == "pass" for s in r["sub_checks"])


# 7: 1つのYES（明瞭）だけではPASSにならない
def test_single_clear_answer_is_not_pass(tmp_path, policy, state, facts):
    a = {q["name"]: 0.5 for q in policy["questions"]}
    a["scope_deviation_present"] = 0.01
    r = run(tmp_path, policy, state, facts, FakeJev(a))
    assert r["route"] == "ESCALATE"


# 9: 明確な重大failureはREJECT
def test_critical_failure_rejects(tmp_path, policy, state, facts):
    r = run(tmp_path, policy, state, facts, FakeJev(answers(policy, requirement_missing=0.95)))
    assert (r["route"], r["reason_code"]) == ("REJECT", "failure:requirement_missing")


def test_normal_failure_escalates_not_rejects(tmp_path, policy, state, facts):
    r = run(tmp_path, policy, state, facts, FakeJev(answers(policy, edge_case_ignored=0.95)))
    assert r["route"] == "ESCALATE"


# 10 / 12 / 13: グレー・UNKNOWN帯・確信度不足はESCALATE（PASSにしない）
def test_unknown_band_escalates(tmp_path, policy, state, facts):
    r = run(tmp_path, policy, state, facts, FakeJev(answers(policy, scope_deviation_present=0.5)))
    assert (r["route"], r["reason_code"]) == ("ESCALATE", "unclear:scope_deviation_present")
    sub = {s["name"]: s for s in r["sub_checks"]}
    assert sub["scope_deviation_present"]["band"] == "unknown"


def test_weak_confidence_is_not_pass(tmp_path, policy, state, facts):
    # p_ok=0.88: UNKNOWN帯の外でも、PASS閾値0.90に届かなければPASSにしない
    r = run(tmp_path, policy, state, facts, FakeJev(answers(policy, edge_case_ignored=0.12)))
    assert r["route"] == "ESCALATE"


def test_uncertainty_check_blocks_pass(tmp_path, policy, state, facts):
    r = run(tmp_path, policy, state, facts, FakeJev(answers(policy, parent_review_needed=0.6)))
    assert (r["route"], r["reason_code"]) == ("ESCALATE", "unclear:parent_review_needed")


def test_policy_stricter_pass_threshold(tmp_path, policy, state, facts):
    policy["thresholds"] = {"pass": 0.97}
    r = run(tmp_path, policy, state, facts, FakeJev(answers(policy, edge_case_ignored=0.05)))
    assert r["route"] == "ESCALATE"


# 14: 相反する回答（counter_of の対が両方強い）はHOLD
def test_contradiction_holds(tmp_path, policy, state, facts):
    policy["questions"].append({
        "name": "requirements_all_addressed", "failure_mode": "completeness", "polarity": "assurance",
        "severity": "normal", "required": False, "counter_of": "requirement_missing",
        "instructions": "要求一覧のすべての項目について、対応する変更箇所を指し示せる"})
    a = answers(policy, requirements_all_addressed=0.02)  # 「欠けていない」と「対応箇所が無い」
    r = run(tmp_path, policy, state, facts, FakeJev(a))
    assert (r["route"], r["reason_code"]) == ("HOLD", "contradiction:requirement_missing")


# 11 / 23(fail-closed): API障害・応答異常はHOLD
def test_api_failure_holds(tmp_path, policy, state, facts):
    r = run(tmp_path, policy, state, facts, FakeJev(error=typesafe.JevStopped("down")))
    assert (r["route"], r["reason_code"], r["stage"]) == ("HOLD", "jev_error", "jev")


def test_http_error_via_real_transport_holds(tmp_path, policy, state, facts, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectTimeout("timeout")
    monkeypatch.setattr(httpx.Client, "post", boom)
    r = evaluate(policy, state, facts, ledger=JsonlLedger(tmp_path / "l.jsonl"), model=MODEL)
    assert (r["route"], r["reason_code"]) == ("HOLD", "jev_error")


def test_schema_mismatch_holds(tmp_path, policy, state, facts):
    a = answers(policy)
    a.pop("edge_case_ignored")
    assert run(tmp_path, policy, state, facts, FakeJev(a))["reason_code"] == "jev_error"
    a = answers(policy, unknown_question=0.1)
    assert run(tmp_path, policy, state, facts | {}, FakeJev(a), escalation_target="x")["route"] == "HOLD"


def test_bad_probability_holds(tmp_path, policy, state, facts):
    for bad in (1.5, -0.1, float("nan"), True, "0.1", None):
        state["change"] = f"case {bad!r}"
        r = run(tmp_path, policy, state, facts, FakeJev(answers(policy, edge_case_ignored=bad)))
        assert r["route"] == "HOLD", bad


# 17: 固定モデル以外は拒否
def test_model_mismatch_holds(tmp_path, policy, state, facts):
    r = run(tmp_path, policy, state, facts, FakeJev(answers(policy), model="jev-1.14.0"))
    assert (r["route"], r["reason_code"]) == ("HOLD", "jev_error")


def test_latest_model_rejected_before_call(tmp_path, policy, state, facts):
    jev = FakeJev(answers(policy))
    r = evaluate(policy, state, facts, ledger=JsonlLedger(tmp_path / "l.jsonl"),
                 transport=jev, model="jev-latest")
    assert r["reason_code"] == "model_invalid" and jev.requests == []


def test_api_key_missing_holds_without_ledger_write(tmp_path, policy, state, facts, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    r = evaluate(policy, state, facts, ledger=JsonlLedger(tmp_path / "l.jsonl"), model=MODEL)
    assert r["reason_code"] == "api_key_invalid"
    assert not (tmp_path / "l.jsonl").exists()


# 15: 同じinput hashは再送しない
def test_same_hash_not_resent(tmp_path, policy, state, facts):
    jev = FakeJev(answers(policy))
    first = run(tmp_path, policy, state, facts, jev)
    second = run(tmp_path, policy, state, facts, jev)
    assert len(jev.requests) == 1
    assert second["replayed"] and second["route"] == first["route"] == "PASS"


def test_failed_hash_stays_hold(tmp_path, policy, state, facts):
    run(tmp_path, policy, state, facts, FakeJev(error=RuntimeError("x")))
    jev = FakeJev(answers(policy))
    r = run(tmp_path, policy, state, facts, jev)
    assert r["route"] == "HOLD" and jev.requests == []


def test_unresolved_started_is_not_resent(tmp_path, policy, state, facts):
    ledger = JsonlLedger(tmp_path / "ledger.jsonl")

    class Crash(BaseException):
        pass

    def crash(_req):
        raise Crash()  # プロセスが落ちた相当: started だけ残る

    try:
        evaluate(policy, state, facts, ledger=ledger, transport=crash, model=MODEL)
    except Crash:
        pass
    jev = FakeJev(answers(policy))
    r = evaluate(policy, state, facts, ledger=ledger, transport=jev, model=MODEL)
    assert (r["route"], r["reason_code"]) == ("HOLD", "previous_unresolved")
    assert jev.requests == []


def test_hash_changes_with_policy_version(tmp_path, policy, state, facts):
    a = run(tmp_path, policy, state, facts, FakeJev(answers(policy)))
    policy["version"] = "sample-v2"
    jev = FakeJev(answers(policy))
    b = run(tmp_path, policy, state, facts, jev)
    assert a["input_hash"] != b["input_hash"] and len(jev.requests) == 1


def test_corrupt_ledger_holds(tmp_path, policy, state, facts):
    (tmp_path / "ledger.jsonl").write_text("{broken\n")
    jev = FakeJev(answers(policy))
    assert run(tmp_path, policy, state, facts, jev)["reason_code"] == "ledger_corrupt"
    assert jev.requests == []


def test_ledger_required(tmp_path, policy, state, facts):
    jev = FakeJev(answers(policy))
    r = evaluate(policy, state, facts, ledger=None, transport=jev, model=MODEL)
    assert r["reason_code"] == "ledger_required" and jev.requests == []


# 16: 秘密情報・不要な項目はpayloadへ入らない
def test_secrets_blocked(tmp_path, policy, state, facts, monkeypatch):
    for leak in ("sk-abcdefghijklmnopqrstu", "AQ.abcdefghijklmnopqrstuvwx",
                 "ghp_abcdefghijklmnopqrstuvwx", "Authorization: Bearer abcdefghijklmnopqr",
                 "-----BEGIN RSA PRIVATE KEY-----", "api_key = 12345678abc",
                 "someone@example.com", "test-key-not-real"):
        jev = FakeJev(answers(policy))
        state["change"] = f"x {leak} y"
        r = run(tmp_path, policy, state, facts, jev)
        assert (r["route"], r["reason_code"]) == ("HOLD", "state_secret"), leak
        assert jev.requests == []


def test_state_shape_enforced(tmp_path, policy, state, facts):
    jev = FakeJev(answers(policy))
    assert run(tmp_path, policy, state | {"full_text": "x"}, facts, jev)["reason_code"] == "state_unexpected_field"
    assert run(tmp_path, policy, {"change": "x"}, facts, jev)["reason_code"] == "state_missing:requirements"
    assert run(tmp_path, policy, state | {"change": "x" * 3000}, facts, jev)["reason_code"] == "state_too_large:change"
    assert jev.requests == []


# 18: Gatewayは親モデルを呼ばず、escalation_target は書き写すだけ
def test_escalation_target_is_echo_only(tmp_path, policy, state, facts):
    jev = FakeJev(answers(policy, scope_deviation_present=0.5))
    r = run(tmp_path, policy, state, facts, jev, escalation_target="opus-5.5-medium")
    assert r["route"] == "ESCALATE" and r["escalation_target"] == "opus-5.5-medium"
    assert len(jev.requests) == 1
    assert "opus" not in str(jev.requests)


def test_invalid_policy_holds_without_call(tmp_path, policy, state, facts):
    policy["questions"] = policy["questions"][:1]
    jev = FakeJev({})
    r = run(tmp_path, policy, state, facts, jev)
    assert r["route"] == "HOLD" and r["reason_code"].startswith("policy_invalid:")
    assert jev.requests == []


def test_result_fields(tmp_path, policy, state, facts):
    r = run(tmp_path, policy, state, facts, FakeJev(answers(policy)))
    for key in ("route", "policy", "policy_version", "policy_hash", "core_version", "model",
                "input_hash", "probabilities", "min_confidence", "sub_checks", "reason_code"):
        assert r[key] is not None, key
