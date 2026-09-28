"""同一知識庫在不同 server 行程間共用的檔案鎖。"""
from __future__ import annotations

import errno
import importlib
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from business_graph_mcp.errors import GraphLockTimeoutError

_RETRY_INTERVAL = 0.05
_LOCK_NAME = "workspace.lock"


def _try_lock(stream: BinaryIO) -> bool:
    """嘗試鎖住檔案第一個位元組；競爭時回傳 False。"""
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN) or exc.winerror in (33, 36):
                return False
            raise
    else:
        fcntl = importlib.import_module("fcntl")

        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                return False
            raise
    return True


def _unlock(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl = importlib.import_module("fcntl")

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def workspace_lock(root: Path, timeout: float = 10.0) -> Iterator[None]:
    """取得 workspace 專屬的跨行程互斥鎖，逾時請呼叫端稍後重試。"""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / _LOCK_NAME
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while not _try_lock(stream):
            if time.monotonic() >= deadline:
                raise GraphLockTimeoutError(
                    f"知識庫 {root} 正被另一個作業使用；等待逾時，請稍後重試。"
                )
            time.sleep(min(_RETRY_INTERVAL, max(0.0, deadline - time.monotonic())))
        try:
            yield
        finally:
            _unlock(stream)
