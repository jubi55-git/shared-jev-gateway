"""CLI（入口A）と計測（20・22・23）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import MODEL, FakeJev, answers
from jev_decision_gateway import __version__, cli, evaluate
from jev_decision_gateway.ledger import JsonlLedger
from jev_decision_gateway.telemetry import JsonlTelemetry, TelemetryError

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "learning_question_quality.json"


def _write(tmp_path, name, value) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(value, ensure_ascii=False))
    return str(path)


def _cli(capsys, *args) -> tuple[int, dict]:
    code = cli.main(list(args))
    return code, json.loads(capsys.readouterr().out)


# 20: 外部プロジェクトのpolicy（見本）がそのまま使える
def test_external_example_policy(tmp_path):
    policy = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    state = {"source_excerpt": "光合成は光のエネルギーで糖を作る。", "question": "光合成で作られるものは?",
             "answer": "糖", "explanation": "原文のとおり、光合成は糖を作る。"}
    facts = {"schema_valid": True, "within_length": True, "source_attached": True}
    r = evaluate(policy, state, facts, ledger=JsonlLedger(tmp_path / "l.jsonl"),
                 transport=FakeJev(answers(policy)), model=MODEL)
    assert (r["route"], r["policy"]) == ("PASS", "question_quality")
    r = evaluate(policy, state, facts | {"schema_valid": False},
                 ledger=JsonlLedger(tmp_path / "l.jsonl"), transport=FakeJev({}), model=MODEL)
    assert (r["route"], r["stage"]) == ("REJECT", "deterministic")


def test_cli_evaluate_deterministic(tmp_path, capsys, policy, state, facts):
    facts["checks_green"] = False
    code, out = _cli(capsys, "evaluate", "--policy", _write(tmp_path, "p.json", policy),
                     "--state", _write(tmp_path, "s.json", state),
                     "--facts", _write(tmp_path, "f.json", facts),
                     "--ledger", str(tmp_path / "l.jsonl"), "--telemetry", str(tmp_path / "t.jsonl"),
                     "--escalation-target", "atlas-high")
    assert code == 0 and out["route"] == "REJECT" and out["escalation_target"] == "atlas-high"


def test_cli_version_mismatch_holds(tmp_path, capsys, policy, state, facts):
    code, out = _cli(capsys, "evaluate", "--policy", _write(tmp_path, "p.json", policy),
                     "--state", _write(tmp_path, "s.json", state),
                     "--facts", _write(tmp_path, "f.json", facts),
                     "--ledger", str(tmp_path / "l.jsonl"), "--telemetry", str(tmp_path / "t.jsonl"),
                     "--expect-version", "9.9.9")
    assert out["route"] == "HOLD" and out["reason_code"] == "core_version_mismatch"


def test_cli_unreadable_input_holds(tmp_path, capsys):
    code, out = _cli(capsys, "evaluate", "--policy", str(tmp_path / "none.json"),
                     "--state", str(tmp_path / "none.json"), "--facts", str(tmp_path / "none.json"),
                     "--ledger", str(tmp_path / "l.jsonl"), "--telemetry", str(tmp_path / "t.jsonl"))
    assert out["route"] == "HOLD" and out["reason_code"] == "input_unreadable"


def test_cli_lint(tmp_path, capsys, policy):
    assert _cli(capsys, "lint-policy", str(EXAMPLE))[0] == 0
    policy["thresholds"] = {"pass": 0.5}
    code, out = _cli(capsys, "lint-policy", _write(tmp_path, "p.json", policy))
    assert code == 1 and out["reason_code"] == "threshold_loosened"


def test_cli_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == __version__


# 23: 親モデル削減を計測できる
def test_telemetry_measures_parent_reduction(tmp_path, policy, state, facts):
    tel = JsonlTelemetry(tmp_path / "t.jsonl")
    ledger = JsonlLedger(tmp_path / "l.jsonl")

    def go(change, jev, f=facts):
        return evaluate(policy, state | {"change": change}, f, ledger=ledger, telemetry=tel,
                        transport=jev, model=MODEL)

    go("a", FakeJev({}), facts | {"checks_green": False})               # deterministic REJECT
    go("b", FakeJev(answers(policy)))                                    # Jev PASS
    go("c", FakeJev(answers(policy, requirement_missing=0.97)))          # Jev REJECT
    esc = go("d", FakeJev(answers(policy, scope_deviation_present=0.5)))  # ESCALATE
    go("e", FakeJev(error=RuntimeError("down")))                         # HOLD
    go("b", FakeJev(answers(policy)))                                    # replay（数えない）

    tel.record_parent_call(esc["input_hash"], "opus-5.5-medium")
    s = tel.summary()
    assert s["decisions_total"] == 5
    assert s["deterministic_only"] == 1 and s["jev_reached"] == 4
    assert s["routes"] == {"PASS": 1, "REJECT": 2, "ESCALATE": 1, "HOLD": 1}
    assert s["resolved_without_parent"] == 3
    assert s["parent_calls"] == 1 and s["parent_calls_avoided"] == 4
    assert s["api_failures"] == 1 and s["low_confidence"] == 1 and s["replayed"] == 1
    assert s["jev_sub_questions"] == 3 * len(policy["questions"])  # HOLD(jev_error)は設問結果なし
    assert s["parent_call_reduction_rate"] == pytest.approx(0.8)
    assert s["escalation_avoidance_rate"] == pytest.approx(2 / 4)


def test_parent_call_only_after_escalate(tmp_path, policy, state, facts):
    tel = JsonlTelemetry(tmp_path / "t.jsonl")
    r = evaluate(policy, state, facts, ledger=JsonlLedger(tmp_path / "l.jsonl"), telemetry=tel,
                 transport=FakeJev(answers(policy)), model=MODEL)
    assert r["route"] == "PASS"
    with pytest.raises(TelemetryError):
        tel.record_parent_call(r["input_hash"], "opus-5.5-medium")
