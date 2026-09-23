"""policy（設問・failure mode・事実の条件表）の読み込みと登録時検査。

policyの中身は各プロジェクトが持つ。ここは「書式」と「守らせる構造」だけ。
閾値の既定値はCoreに固定し、policyからは厳しくする方向にしか変えられない。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

PASS = "PASS"
REJECT = "REJECT"
ESCALATE = "ESCALATE"
HOLD = "HOLD"
ROUTES = (PASS, REJECT, ESCALATE, HOLD)

PASS_THRESHOLD = 0.90      # 必須設問すべての p_ok がこれ以上で PASS
REJECT_THRESHOLD = 0.15    # critical 設問の p_ok がこれ以下で REJECT（policyから変更不可）
UNKNOWN_HIGH = 0.85        # REJECT_THRESHOLD < p_ok < UNKNOWN_HIGH は UNKNOWN 帯
MIN_REQUIRED = 3           # 1つのYESで PASS させないための必須設問の下限

POLARITIES = ("risk", "assurance")        # risk: YES=失敗がある / assurance: YES=保たれている
SEVERITIES = ("critical", "normal", "uncertainty")
UNCERTAINTY = "uncertainty"
FACT_ROUTES = (REJECT, HOLD, ESCALATE)    # 事実だけで PASS にはしない

# 包括的な一問・同義の「大丈夫か」を登録時に弾く（小文字で比較）
BANNED_PHRASES = (
    "マージしてよい", "マージして良い", "承認してよい", "公開してよい", "採用してよい",
    "問題ないか", "問題はないか", "問題なさそう", "大丈夫か", "安全か", "安全そうか",
    "safe to merge", "is it ok", "is this ok", "looks good",
)

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_POLICY_KEYS = {"policy", "version", "description", "facts", "state_fields", "questions", "thresholds"}
_QUESTION_KEYS = {"name", "failure_mode", "polarity", "severity", "required", "instructions", "counter_of"}


class PolicyError(ValueError):
    """policyが検査を通らない。reason_code を持つ。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def digest(value) -> str:
    """正準JSONのsha256。既存台帳のhashと互換（tests/test_policy.py で固定）。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Question:
    name: str
    failure_mode: str
    polarity: str
    severity: str
    required: bool
    instructions: str
    counter_of: str | None = None


@dataclass(frozen=True)
class Fact:
    name: str
    must_be: bool
    otherwise: str


@dataclass(frozen=True)
class StateField:
    name: str
    max_chars: int
    required: bool


@dataclass(frozen=True)
class Policy:
    name: str
    version: str
    hash: str
    facts: tuple[Fact, ...]
    state_fields: dict[str, StateField]
    questions: tuple[Question, ...]
    pass_threshold: float

    def question_payload(self) -> dict:
        return {q.name: {"type": "noul", "instructions": q.instructions} for q in self.questions}


def _str(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(code)
    return value.strip()


def _question(raw) -> Question:
    if not isinstance(raw, dict) or set(raw) - _QUESTION_KEYS:
        raise PolicyError("question_shape")
    name = _str(raw.get("name"), "question_name")
    if not _NAME.match(name):
        raise PolicyError("question_name")
    polarity = raw.get("polarity")
    severity = raw.get("severity")
    required = raw.get("required")
    if polarity not in POLARITIES:
        raise PolicyError("question_polarity")
    if severity not in SEVERITIES:
        raise PolicyError("question_severity")
    if not isinstance(required, bool):
        raise PolicyError("question_required")
    instructions = _str(raw.get("instructions"), "question_instructions")
    lowered = instructions.lower()
    if any(p in lowered for p in BANNED_PHRASES):
        raise PolicyError("question_comprehensive")
    counter_of = raw.get("counter_of")
    if counter_of is not None:
        counter_of = _str(counter_of, "question_counter_of")
    return Question(name, _str(raw.get("failure_mode"), "question_failure_mode"),
                    polarity, severity, required, instructions, counter_of)


def _check_questions(questions: list[Question]) -> None:
    names = [q.name for q in questions]
    if len(names) != len(set(names)):
        raise PolicyError("question_duplicate_name")
    texts = [q.instructions for q in questions]
    if len(texts) != len(set(texts)):
        raise PolicyError("question_duplicate_text")
    by_name = {q.name: q for q in questions}

    primary = [q for q in questions if q.counter_of is None]
    modes = [q.failure_mode for q in primary]
    if len(modes) != len(set(modes)):
        raise PolicyError("failure_mode_duplicate")
    for q in questions:
        if q.counter_of is None:
            continue
        target = by_name.get(q.counter_of)
        # 反証は「同じfailure modeを逆の極性から」だけ。言い換えの二重問いは不可。
        if (target is None or target.counter_of is not None
                or target.failure_mode != q.failure_mode or target.polarity == q.polarity):
            raise PolicyError("counter_invalid")

    unc = [q for q in questions if q.severity == UNCERTAINTY]
    if (len(unc) != 1 or unc[0].failure_mode != UNCERTAINTY
            or unc[0].polarity != "risk" or not unc[0].required):
        raise PolicyError("uncertainty_check_required")
    if any(q.failure_mode == UNCERTAINTY and q.severity != UNCERTAINTY for q in questions):
        raise PolicyError("uncertainty_check_required")
    if not any(q.polarity == "risk" and q.severity != UNCERTAINTY for q in questions):
        raise PolicyError("refutation_check_required")
    if sum(1 for q in questions if q.required) < MIN_REQUIRED:
        raise PolicyError("too_few_required_checks")


def load_policy(raw) -> Policy:
    """dict を検査して Policy にする。通らなければ PolicyError。"""
    if not isinstance(raw, dict) or set(raw) - _POLICY_KEYS:
        raise PolicyError("policy_shape")
    name = _str(raw.get("policy"), "policy_name")
    version = _str(raw.get("version"), "policy_version")

    facts = []
    for f in raw.get("facts") or []:
        if not isinstance(f, dict) or set(f) != {"name", "must_be", "else"}:
            raise PolicyError("fact_shape")
        fname = _str(f["name"], "fact_name")
        if not _NAME.match(fname) or not isinstance(f["must_be"], bool) or f["else"] not in FACT_ROUTES:
            raise PolicyError("fact_shape")
        facts.append(Fact(fname, f["must_be"], f["else"]))
    if len({f.name for f in facts}) != len(facts):
        raise PolicyError("fact_duplicate")

    fields_raw = raw.get("state_fields")
    if not isinstance(fields_raw, dict) or not fields_raw:
        raise PolicyError("state_fields_shape")
    fields = {}
    for fname, spec in fields_raw.items():
        if not isinstance(fname, str) or not _NAME.match(fname) or not isinstance(spec, dict):
            raise PolicyError("state_fields_shape")
        if set(spec) - {"max_chars", "required"}:
            raise PolicyError("state_fields_shape")
        max_chars = spec.get("max_chars")
        req = spec.get("required", True)
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0 \
                or not isinstance(req, bool):
            raise PolicyError("state_fields_shape")
        fields[fname] = StateField(fname, max_chars, req)

    qraw = raw.get("questions")
    if not isinstance(qraw, list) or not qraw:
        raise PolicyError("questions_shape")
    questions = [_question(q) for q in qraw]
    _check_questions(questions)

    thresholds = raw.get("thresholds") or {}
    if not isinstance(thresholds, dict) or set(thresholds) - {"pass"}:
        raise PolicyError("thresholds_shape")
    pass_t = thresholds.get("pass", PASS_THRESHOLD)
    if isinstance(pass_t, bool) or not isinstance(pass_t, (int, float)) \
            or not PASS_THRESHOLD <= float(pass_t) < 1.0:
        raise PolicyError("threshold_loosened")

    return Policy(name, version, digest(raw), tuple(facts), fields, tuple(questions), float(pass_t))
