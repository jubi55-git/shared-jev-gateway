"""evaluate(): 事実 → state検査 → hash → Jev 1リクエスト → コード側集計。

Jevは各設問のp(YES)を返すだけ。routeはここで決める。
親モデルは呼ばない（ESCALATEを返すまで）。どの例外もPASSへ落とさない。
"""
from __future__ import annotations

import json
import os
import re
import time

from . import __version__ as CORE_VERSION
from . import typesafe
from .ledger import LedgerError
from .policy import (
    ESCALATE, HOLD, PASS, REJECT, REJECT_THRESHOLD, ROUTES, UNKNOWN_HIGH,
    Policy, PolicyError, digest, load_policy,
)

_SECRET_PATTERNS = [re.compile(p) for p in (
    r"sk-[A-Za-z0-9_-]{16,}",
    r"AIza[0-9A-Za-z_-]{30,}",
    r"AQ\.[A-Za-z0-9_-]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"(?i)bearer\s+[A-Za-z0-9._~+/-]{16,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{8,}",
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
)]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _base(policy: Policy | None, escalation_target) -> dict:
    return {
        "route": HOLD,
        "stage": "error",
        "reason_code": "unset",
        "policy": policy.name if policy else None,
        "policy_version": policy.version if policy else None,
        "policy_hash": policy.hash if policy else None,
        "core_version": CORE_VERSION,
        "model": None,
        "input_hash": None,
        "probabilities": None,
        "min_confidence": None,
        "sub_checks": [],
        "jev_calls": 0,
        "escalation_target": escalation_target,
        "replayed": False,
    }


def _resolve_model(model: str | None) -> str:
    if model is None:
        return typesafe.resolve_model()
    if not isinstance(model, str) or not model.startswith("jev-") or "latest" in model.lower():
        raise typesafe.JevStopped("固定版のJevモデルだけを受け付けます。")
    return model


def check_facts(policy: Policy, facts) -> tuple[str, str] | None:
    """事実の条件表。決まればJevを呼ばない。欠け・余分はHOLD。"""
    if not isinstance(facts, dict):
        return HOLD, "facts_invalid"
    declared = {f.name for f in policy.facts}
    if set(facts) - declared:
        return HOLD, "fact_unexpected"
    decided: dict[str, str] = {}
    for fact in policy.facts:
        value = facts.get(fact.name)
        if not isinstance(value, bool):
            return HOLD, f"fact_missing:{fact.name}"
        if value != fact.must_be:
            decided.setdefault(fact.otherwise, fact.name)
    for route in (HOLD, REJECT, ESCALATE):
        if route in decided:
            return route, f"fact:{decided[route]}"
    return None


def check_state(policy: Policy, state) -> str | None:
    """最小stateの検査。問題があれば reason_code を返す。"""
    if not isinstance(state, dict):
        return "state_invalid"
    if set(state) - set(policy.state_fields):
        return "state_unexpected_field"
    for field in policy.state_fields.values():
        if field.name not in state:
            if field.required:
                return f"state_missing:{field.name}"
            continue
        value = state[field.name]
        if value is None or value == "" or value == [] or value == {}:
            if field.required:
                return f"state_missing:{field.name}"
        try:
            text = _canonical(value)
        except (TypeError, ValueError):
            return "state_not_json"
        if len(text) > field.max_chars:
            return f"state_too_large:{field.name}"
    text = _canonical(state)
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key and key in text:
        return "state_secret"
    if any(p.search(text) for p in _SECRET_PATTERNS):
        return "state_secret"
    return None


def aggregate(policy: Policy, probs: dict[str, float]) -> tuple[str, str, list[dict], float]:
    """Noul確率を route へ。順番: 矛盾HOLD → critical失敗REJECT → 全必須明瞭PASS → ESCALATE。"""
    if set(probs) != {q.name for q in policy.questions}:
        raise ValueError("probabilities_mismatch")
    p_ok = {}
    for q in policy.questions:
        p = float(probs[q.name])
        p_ok[q.name] = 1.0 - p if q.polarity == "risk" else p

    def band(v: float) -> str:
        if v <= REJECT_THRESHOLD:
            return "fail"
        if v < UNKNOWN_HIGH:
            return "unknown"
        if v < policy.pass_threshold:
            return "weak"
        return "ok"

    subs = [{
        "name": q.name,
        "failure_mode": q.failure_mode,
        "polarity": q.polarity,
        "severity": q.severity,
        "required": q.required,
        "p_yes": float(probs[q.name]),
        "p_ok": p_ok[q.name],
        "band": band(p_ok[q.name]),
        "effect": None,
    } for q in policy.questions]
    by_name = {s["name"]: s for s in subs}
    required = [q.name for q in policy.questions if q.required]
    min_conf = min(p_ok[n] for n in required)

    for q in policy.questions:
        if q.counter_of is None:
            continue
        a, b = by_name[q.name]["band"], by_name[q.counter_of]["band"]
        if {a, b} == {"ok", "fail"}:
            by_name[q.name]["effect"] = by_name[q.counter_of]["effect"] = "contradiction"
            return HOLD, f"contradiction:{q.counter_of}", subs, min_conf

    failed = [q.name for q in policy.questions
              if q.severity == "critical" and p_ok[q.name] <= REJECT_THRESHOLD]
    if failed:
        for n in failed:
            by_name[n]["effect"] = "reject"
        return REJECT, f"failure:{failed[0]}", subs, min_conf

    blocking = [n for n in required if p_ok[n] < policy.pass_threshold]
    if not blocking:
        for n in required:
            by_name[n]["effect"] = "pass"
        return PASS, "all_required_clear", subs, min_conf

    for n in blocking:
        by_name[n]["effect"] = "escalate"
    return ESCALATE, f"unclear:{blocking[0]}", subs, min_conf


def evaluate(policy, state, facts, *, ledger, telemetry=None, escalation_target=None,
             transport=None, model=None) -> dict:
    """1件の判定。必ず DecisionResult(dict) を返し、例外を外へ出さない。"""
    result = _evaluate(policy, state, facts, ledger=ledger, escalation_target=escalation_target,
                       transport=transport, model=model)
    if result.get("route") not in ROUTES:
        result = {**result, "route": HOLD, "reason_code": "unexpected_route"}
    if telemetry is not None:
        telemetry.record(result)
    return result


def _evaluate(policy, state, facts, *, ledger, escalation_target, transport, model) -> dict:
    try:
        pol = policy if isinstance(policy, Policy) else load_policy(policy)
    except PolicyError as exc:
        return {**_base(None, escalation_target), "reason_code": f"policy_invalid:{exc.code}"}
    result = _base(pol, escalation_target)

    decided = check_facts(pol, facts)
    if decided is not None:
        route, reason = decided
        stage = "error" if reason.startswith(("facts_", "fact_missing", "fact_unexpected")) else "deterministic"
        return {**result, "route": route, "reason_code": reason, "stage": stage}

    bad_state = check_state(pol, state)
    if bad_state:
        return {**result, "reason_code": bad_state}

    try:
        resolved = _resolve_model(model)
    except typesafe.JevStopped:
        return {**result, "reason_code": "model_invalid"}
    key = digest([CORE_VERSION, pol.hash, resolved, state])
    result.update(model=resolved, input_hash=key)

    if ledger is None:
        return {**result, "reason_code": "ledger_required"}
    if transport is None:
        try:
            typesafe.api_key()
        except typesafe.JevStopped:
            return {**result, "reason_code": "api_key_invalid"}

    request = {"state": state, "model": resolved, "questions": pol.question_payload()}
    names = [q.name for q in pol.questions]
    try:
        with ledger.locked():
            prior = ledger.last(key)
            if prior is not None:
                if prior.get("event") == "decided" and isinstance(prior.get("result"), dict):
                    return {**prior["result"], "replayed": True}
                held = {**result, "reason_code": "previous_unresolved", "stage": "jev"}
                ledger.append({"event": "decided", "input_hash": key, "ts": _now(), "result": held})
                return held

            ledger.append({"event": "started", "input_hash": key, "ts": _now(),
                           "policy": pol.name, "policy_version": pol.version, "model": resolved,
                           "question_names": names})
            try:
                data = (transport or typesafe.post_system_one)(request)
                probs, _usage = typesafe.validate_noul_response(data, names, expected_model=resolved)
                route, reason, subs, min_conf = aggregate(pol, probs)
            except Exception:
                ledger.append({"event": "unknown", "input_hash": key, "ts": _now()})
                held = {**result, "reason_code": "jev_error", "stage": "jev", "jev_calls": 1}
                ledger.append({"event": "decided", "input_hash": key, "ts": _now(), "result": held})
                return held

            final = {**result, "route": route, "reason_code": reason, "stage": "jev",
                     "probabilities": probs, "min_confidence": min_conf,
                     "sub_checks": subs, "jev_calls": 1}
            ledger.append({"event": "decided", "input_hash": key, "ts": _now(), "result": final})
            return final
    except LedgerError:
        return {**result, "reason_code": "ledger_corrupt"}
    except Exception:
        return {**result, "reason_code": "internal_error"}
