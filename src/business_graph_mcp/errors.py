"""business-graph 的例外型別。"""
from __future__ import annotations


class GraphError(RuntimeError):
    """所有知識圖相關錯誤的基底型別。"""


class GraphNotFoundError(GraphError):
    """指定路徑向上找不到 .bgraph/ 目錄。"""


class GraphConflictError(GraphError):
    """base_hash 與現況不符；訊息須帶回現況全文供呼叫端重做。"""


class GraphIndexError(GraphError):
    """索引無法讀寫，或執行環境缺少必要的 SQLite 功能。"""


class GraphLockTimeoutError(GraphError):
    """工作區正被其他行程使用，逾時後可重試。"""


class GraphMigrationError(GraphError):
    """環境無法安全發布遷移檔案，需修正後重跑。"""
