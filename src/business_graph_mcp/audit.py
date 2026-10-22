"""以獨立檔案鎖追加固定欄位的操作紀錄，不保存工具本文。"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from business_graph_mcp.locking import file_lock

_FIELDS = ("subject", "tool", "slug", "before_hash", "after_hash", "outcome")


def audit_lock_path(path: Path) -> Path:
    """回傳紀錄檔專用的鎖側檔，供設定驗證與追加共用。"""
    return path.with_name(path.name + ".lock")


class AuditWriter:
    """逐筆追加 JSONL；錯誤交由呼叫端處理，絕不重送業務操作。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: Mapping[str, object]) -> None:
        """只選取核准的純量欄位，flush 與 fsync 成功後才視為完成。"""
        record = {key: event.get(key) for key in _FIELDS}
        if any(value is not None and not isinstance(value, str) for value in record.values()):
            raise ValueError("操作紀錄欄位只接受字串或空值。")
        record["timestamp"] = datetime.now(UTC).isoformat()
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        with file_lock(audit_lock_path(self.path)):
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
