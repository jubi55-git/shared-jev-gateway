"""TypeSafe / Jev 共通ガード。

Jevを使う全工程で、接続方法とfail-closed条件を1か所に固定する。
自由文生成・自動retry・latest alias・第三者gateway/routerは使わない。
"""
from __future__ import annotations

import math
import os

import httpx

DEFAULT_MODEL = "jev-1.13.0"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
TIMEOUT_SECONDS = 10.0


class JevStopped(RuntimeError):
    """Jevの結果を安全に確定できないとき止める。"""


# **承認済みの版の一覧**(2026-10-01)。形式上正しい `jev-<版>` でも、ここに無ければ通信の前に止める。
# 版を足すのは、その版で比較(blind comparison 等)をしてから。ここを直して新しい版を固定し、
# 利用側の依存の固定(SHA)を上げる。
APPROVED_MODELS = frozenset({DEFAULT_MODEL})


def check_model(model: object) -> str:
    """固定版かつ承認済みの版だけを返す。latest alias・未承認の版は `JevStopped`。"""
    if (not isinstance(model, str) or not model.startswith("jev-")
            or "latest" in model.lower() or model not in APPROVED_MODELS):
        raise JevStopped(
            f"Jev のモデルは承認済みの固定版（{', '.join(sorted(APPROVED_MODELS))}）だけを受け付けます。"
        )
    return model


def resolve_model() -> str:
    """検証済み固定版だけを許可する。latest alias・未承認の版は拒否。"""
    return check_model(os.environ.get("TYPESAFE_JEV_MODEL", "").strip() or DEFAULT_MODEL)


def api_key() -> str:
    """通信前に鍵を検証する。"""
    value = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not value or not value.isascii() or any(ch.isspace() for ch in value):
        raise JevStopped("TYPESAFE_API_KEY が未設定または不正です。")
    return value


def auth_headers() -> dict[str, str]:
    """認証ヘッダー。**`TYPESAFE_API_KEY` があれば従来どおり Bearer**(Cloud Run 等)。

    鍵が無く、`TYPESAFE_AUTH=proxy` が**明示されている**ときだけ、ヘッダーを付けずに送る
    (Claude Cloud の API Credential。Bearer はプロキシが付ける。Credential は環境変数に出ない)。
    自動判定はしない ── Credential を登録していない環境で素通しにしないため。
    """
    if os.environ.get("TYPESAFE_API_KEY", "").strip():
        return {"Authorization": "Bearer " + api_key()}
    if os.environ.get("TYPESAFE_AUTH", "").strip().lower() == "proxy":
        return {}
    return {"Authorization": "Bearer " + api_key()}      # 鍵が無い → ここで止まる


def auth_available() -> bool:
    """通信せずに、認証の手段があるか。"""
    try:
        auth_headers()
    except JevStopped:
        return False
    return True


def post_system_one(request: dict) -> dict:
    """TypeSafe公式APIへ1回だけ送る。自動retryしない。"""
    auth = auth_headers()
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = client.post(
                ENDPOINT,
                headers={
                    **auth,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=request,
            )
    except httpx.HTTPError:
        raise JevStopped("TypeSafe通信が中断しました。自動再送しません。") from None
    if response.status_code != 200:
        raise JevStopped(
            f"TypeSafe HTTP {response.status_code}。自動再送しません。"
        )
    try:
        data = response.json()
    except ValueError:
        raise JevStopped("TypeSafe応答がJSONではありません。") from None
    if not isinstance(data, dict):
        raise JevStopped("TypeSafe応答がJSON objectではありません。")
    return data


def validate_noul_response(
    data: dict,
    question_names,
    *,
    expected_model: str,
) -> tuple[dict[str, float], dict]:
    """指定したNoulだけが返ったことと固定版一致を検証する。"""
    model = data.get("model")
    if model != expected_model:
        raise JevStopped("要求したJev固定版と応答モデルが一致しません。")

    answers = data.get("answers")
    if not isinstance(answers, dict):
        raise JevStopped("TypeSafe応答にanswersがありません。")
    expected_names = set(question_names)
    if set(answers) != expected_names:
        raise JevStopped("TypeSafe応答の質問項目が要求schemaと一致しません。")

    probs: dict[str, float] = {}
    for name in question_names:
        answer = answers.get(name)
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise JevStopped(f"TypeSafe応答の{name}がNoulではありません。")
        value = answer.get("noul")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise JevStopped(f"TypeSafe応答の{name}確率が不正です。")
        value = float(value)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise JevStopped(f"TypeSafe応答の{name}確率が範囲外です。")
        probs[name] = value

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return probs, usage
