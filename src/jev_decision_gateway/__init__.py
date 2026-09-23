"""Jev Decision Gateway Core.

プロジェクト非依存の判定ゲート。Jevには小さな設問のNoul確率だけを聞き、
PASS / REJECT / ESCALATE / HOLD はコードが決める。親モデルは呼ばない。
"""
from __future__ import annotations

__version__ = "0.1.0"

from .gateway import evaluate  # noqa: E402
from .policy import Policy, PolicyError, load_policy  # noqa: E402

__all__ = ["__version__", "evaluate", "Policy", "PolicyError", "load_policy"]
