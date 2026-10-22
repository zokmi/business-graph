"""操作紀錄只保存固定欄位，並行追加不得破壞 JSONL。"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


def test_audit_filters_payload_and_keeps_each_concurrent_event(tmp_path):
    from business_graph_mcp.audit import AuditWriter

    path = tmp_path / "logs" / "operations.jsonl"
    writers = [AuditWriter(path), AuditWriter(path)]

    def append(index):
        writers[index % 2].append({
            "subject": "writer", "tool": "business_write", "slug": f"Page {index}",
            "before_hash": None, "after_hash": "a" * 16, "outcome": "success",
            "token": "secret-token", "content": "secret-content", "query": "secret-query",
        })

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(80)))
    raw = path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    assert len(events) == 80
    assert {e["slug"] for e in events} == {f"Page {i}" for i in range(80)}
    assert all(set(e) == {"timestamp", "subject", "tool", "slug", "before_hash", "after_hash", "outcome"} for e in events)
    assert all(datetime.fromisoformat(e["timestamp"]).tzinfo is not None for e in events)
    assert "secret-" not in raw


def test_audit_disk_failure_is_not_silently_ignored(tmp_path):
    import pytest

    from business_graph_mcp.audit import AuditWriter

    path = tmp_path / "directory"
    path.mkdir()
    with pytest.raises(OSError):
        AuditWriter(path).append({"subject": "writer", "tool": "business_lint", "outcome": "success"})


def test_audit_crossprocess_append_keeps_all_lines(tmp_path):
    import subprocess
    import sys

    script = """
import sys
from pathlib import Path
from business_graph_mcp.audit import AuditWriter
writer = AuditWriter(Path(sys.argv[1]))
for i in range(25):
    writer.append({'subject': sys.argv[2], 'tool': 'business_lint', 'outcome': 'success'})
"""
    path = tmp_path / "audit.jsonl"
    children = [subprocess.Popen([sys.executable, "-c", script, str(path), subject]) for subject in ("alice", "bob")]
    try:
        assert [p.wait(timeout=15) for p in children] == [0, 0]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 50
    assert {s: sum(r["subject"] == s for r in rows) for s in ("alice", "bob")} == {"alice": 25, "bob": 25}
