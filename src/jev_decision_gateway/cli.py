"""入口A: CLI。判断はしない。読んでCoreへ渡し、結果JSONを標準出力へ出すだけ。

呼び出し側の約束: 終了コードが0以外、またはJSONを読めないときはHOLDとして扱う。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .gateway import _base, evaluate
from .ledger import JsonlLedger
from .policy import PolicyError, load_policy
from .telemetry import JsonlTelemetry, TelemetryError


def _read(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print(value) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _evaluate(args) -> int:
    if args.expect_version and args.expect_version != __version__:
        _print({**_base(None, args.escalation_target), "reason_code": "core_version_mismatch"})
        return 0
    try:
        policy, state, facts = _read(args.policy), _read(args.state), _read(args.facts)
    except (OSError, ValueError):
        _print({**_base(None, args.escalation_target), "reason_code": "input_unreadable"})
        return 0
    _print(evaluate(policy, state, facts,
                    ledger=JsonlLedger(args.ledger),
                    telemetry=JsonlTelemetry(args.telemetry),
                    escalation_target=args.escalation_target))
    return 0


def _lint(args) -> int:
    try:
        policy = load_policy(_read(args.policy))
    except (OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, PolicyError) else "input_unreadable"
        _print({"ok": False, "reason_code": code})
        return 1
    _print({"ok": True, "policy": policy.name, "version": policy.version, "policy_hash": policy.hash})
    return 0


def _parent(args) -> int:
    try:
        JsonlTelemetry(args.telemetry).record_parent_call(args.input_hash, args.target)
    except TelemetryError as exc:
        _print({"ok": False, "reason_code": str(exc)})
        return 1
    _print({"ok": True})
    return 0


def _summary(args) -> int:
    _print(JsonlTelemetry(args.telemetry).summary())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="jev-gateway")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    ev = sub.add_parser("evaluate")
    ev.add_argument("--policy", required=True)
    ev.add_argument("--state", required=True)
    ev.add_argument("--facts", required=True)
    ev.add_argument("--ledger", required=True)
    ev.add_argument("--telemetry", required=True)
    ev.add_argument("--escalation-target")
    ev.add_argument("--expect-version")
    ev.set_defaults(func=_evaluate)

    lint = sub.add_parser("lint-policy")
    lint.add_argument("policy")
    lint.set_defaults(func=_lint)

    parent = sub.add_parser("record-parent-call")
    parent.add_argument("--telemetry", required=True)
    parent.add_argument("--input-hash", required=True)
    parent.add_argument("--target", required=True)
    parent.set_defaults(func=_parent)

    summary = sub.add_parser("telemetry")
    summary.add_argument("--telemetry", required=True)
    summary.set_defaults(func=_summary)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
