"""判定1件ごとの記録と、親モデル呼出し削減の集計。

削減率の基準は「Gatewayが無ければ全件を親モデルへ回していた」とみなす推定値。
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

from .policy import ESCALATE, HOLD, PASS, REJECT, ROUTES


class TelemetryError(RuntimeError):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class JsonlTelemetry:
    def __init__(self, path):
        self.path = Path(path)

    def _append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def events(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    raise TelemetryError("telemetry_corrupt") from None
        return out

    def record(self, result: dict) -> None:
        self._append({
            "event": "decision",
            "ts": _now(),
            "input_hash": result.get("input_hash"),
            "policy": result.get("policy"),
            "policy_version": result.get("policy_version"),
            "route": result.get("route"),
            "stage": result.get("stage"),
            "reason_code": result.get("reason_code"),
            "jev_calls": result.get("jev_calls", 0),
            "sub_questions": len(result.get("sub_checks") or []),
            "low_confidence": any(s.get("band") == "unknown" for s in result.get("sub_checks") or []),
            "replayed": bool(result.get("replayed")),
        })

    def record_parent_call(self, input_hash: str, target: str) -> None:
        """Callerが親モデルを呼んだら記録する。ESCALATE以外の判定には記録させない。"""
        if not input_hash or not target:
            raise TelemetryError("parent_call_shape")
        escalated = any(
            e.get("event") == "decision" and e.get("input_hash") == input_hash
            and e.get("route") == ESCALATE
            for e in self.events()
        )
        if not escalated:
            raise TelemetryError("parent_call_without_escalate")
        self._append({"event": "parent_call", "ts": _now(), "input_hash": input_hash, "target": target})

    def summary(self) -> dict:
        return summarize(self.events())


def summarize(events: list[dict]) -> dict:
    decisions = [e for e in events if e.get("event") == "decision" and not e.get("replayed")]
    routes = {r: sum(1 for e in decisions if e.get("route") == r) for r in ROUTES}
    total = len(decisions)
    jev = [e for e in decisions if e.get("stage") == "jev"]
    parent_hashes = {e.get("input_hash") for e in events if e.get("event") == "parent_call"}
    parent_calls = len(parent_hashes)
    jev_resolved = sum(1 for e in jev if e.get("route") in (PASS, REJECT))
    return {
        "decisions_total": total,
        "deterministic_only": sum(1 for e in decisions if e.get("stage") == "deterministic"),
        "jev_reached": len(jev),
        "jev_sub_questions": sum(int(e.get("sub_questions") or 0) for e in jev),
        "routes": routes,
        "resolved_without_parent": routes[PASS] + routes[REJECT],
        "hold": routes[HOLD],
        "parent_calls": parent_calls,
        "parent_calls_avoided": total - parent_calls,
        "api_failures": sum(1 for e in decisions if e.get("reason_code") == "jev_error"),
        "low_confidence": sum(1 for e in decisions if e.get("low_confidence")),
        "replayed": sum(1 for e in events if e.get("event") == "decision" and e.get("replayed")),
        "parent_call_reduction_rate": (1 - parent_calls / total) if total else None,
        "escalation_avoidance_rate": (jev_resolved / len(jev)) if jev else None,
    }
