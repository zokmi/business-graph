"""跨行程工作區互斥與寫入競態的行為測試。"""
from __future__ import annotations

import subprocess
import sys
from datetime import date

import pytest

from business_graph_mcp.engine import open_session
from business_graph_mcp.writer import write_node

_HOLD_LOCK = """
import sys
from pathlib import Path
from business_graph_mcp.locking import workspace_lock
with workspace_lock(Path(sys.argv[1])):
    print('READY', flush=True)
    sys.stdin.read()
"""

_CONCURRENT_WRITE = """
import sys
import time
from datetime import date
from pathlib import Path
import business_graph_mcp.writer as writer
from business_graph_mcp.engine import open_session
from business_graph_mcp.errors import GraphConflictError, GraphError
root, start, mode, suffix, base = sys.argv[1:]
while not Path(start).exists():
    time.sleep(0.01)
real_write = writer._atomic_write
def slow_write(path, text):
    time.sleep(0.35)
    real_write(path, text)
writer._atomic_write = slow_write
try:
    with open_session(root, create=True) as session:
        writer.write_node(
            session, title='Shared', node_type='rule', content=suffix,
            status='inferred', aliases=(), code=(),
            base_hash=base if mode == 'update' else None,
            today=date(2026, 9, 28),
        )
except GraphConflictError:
    print('CONFLICT')
except GraphError:
    print('EXISTS')
else:
    print('OK')
"""


def test_workspace_lock_跨行程逾時後可重試且不同工作區獨立(tmp_path):
    from business_graph_mcp.errors import GraphLockTimeoutError
    from business_graph_mcp.locking import workspace_lock

    root = tmp_path / '.bgraph'
    root.mkdir()
    child = subprocess.Popen(
        [sys.executable, '-c', _HOLD_LOCK, str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == 'READY'
        with pytest.raises(GraphLockTimeoutError, match='重試'):
            with workspace_lock(root, timeout=0.15):
                pytest.fail('不應同時取得同一把鎖')
        with workspace_lock(tmp_path / 'other' / '.bgraph', timeout=0.15):
            pass
    finally:
        child.terminate()
        child.wait(timeout=5)
    with workspace_lock(root, timeout=0.5):
        pass


def test_open_session_例外後釋放鎖(tmp_path, monkeypatch):
    import business_graph_mcp.engine as engine
    from business_graph_mcp.locking import workspace_lock

    root = tmp_path / '.bgraph'
    def fail_open(_path):
        raise RuntimeError('open failed')
    monkeypatch.setattr(engine, 'open_index', fail_open)
    with pytest.raises(RuntimeError, match='open failed'):
        with open_session(str(tmp_path), create=True):
            pass
    with workspace_lock(root, timeout=0.2):
        pass


@pytest.mark.parametrize('mode', ['update', 'create'])
def test_兩個行程同時寫同頁只有一個成功(tmp_path, mode):
    root = tmp_path / 'repo'
    root.mkdir()
    base = ''
    if mode == 'update':
        with open_session(str(root), create=True) as session:
            first = write_node(
                session, title='Shared', node_type='rule', content='initial',
                status='inferred', aliases=(), code=(), base_hash=None,
                today=date(2026, 9, 28),
            )
        base = first.base_hash
    start = tmp_path / 'go'
    children = [
        subprocess.Popen(
            [sys.executable, '-c', _CONCURRENT_WRITE, str(root), str(start), mode, suffix, base],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for suffix in ('winner one', 'winner two')
    ]
    try:
        start.write_text('go', encoding='ascii')
        outputs = [child.communicate(timeout=10) for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert all(child.returncode == 0 for child in children), outputs
    statuses = sorted(stdout.strip() for stdout, _ in outputs)
    assert statuses == (['CONFLICT', 'OK'] if mode == 'update' else ['EXISTS', 'OK'])
    saved = (root / '.bgraph' / 'nodes' / 'Shared.md').read_text(encoding='utf-8')
    assert ('winner one' in saved) != ('winner two' in saved)
