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


def resolve_model() -> str:
    """検証済み固定版だけを許可する。latest aliasは拒否。"""
    model = os.environ.get("TYPESAFE_JEV_MODEL", "").strip() or DEFAULT_MODEL
    if not model.startswith("jev-") or "latest" in model.lower():
        raise JevStopped(
            "TYPESAFE_JEV_MODEL は検証済みの固定版（例 jev-1.13.0）を指定してください。"
        )
    return model


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
