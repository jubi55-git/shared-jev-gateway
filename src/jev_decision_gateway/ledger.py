"""同一input hashの再送を防ぐ台帳。

started を送信前に書き、応答の有無が分からない呼び出しは二度と送らない。
既定はJSONLファイル。DBに置きたいプロジェクトは同じ3メソッドを持つ物を渡す。
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path


class LedgerError(RuntimeError):
    """台帳を信用できない。判定はHOLDにする。"""


class JsonlLedger:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def locked(self):
        """予約から結果保存まで排他。プロセスが落ちてもOSがロックを解放する。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix + ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def last(self, key: str) -> dict | None:
        if not self.path.is_file():
            return None
        found = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                raise LedgerError("ledger_corrupt") from None
            if not isinstance(event, dict) or not event.get("event") or not event.get("input_hash"):
                raise LedgerError("ledger_corrupt")
            if event["input_hash"] == key:
                found = event
        return found

    def append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
