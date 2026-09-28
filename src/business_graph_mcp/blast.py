"""沿影響邊反向計算影響半徑，不存取資料庫或檔案系統。

邊 (source, type, target) 表示 source 依賴、覆寫或例外於 target，
因此改動 target 會波及 source。
"""
from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from business_graph_mcp.edges import IMPACT_EDGE_TYPES

__all__ = ["IMPACT_EDGE_TYPES", "BlastEntry", "blast_radius"]


@dataclass(frozen=True)
class BlastEntry:
    """受影響的節點、連向上一層的邊型別，以及由種子近至遠的中繼節點。

    via 不含種子與受影響節點本身，直接相連時為空 tuple。
    """

    slug: str
    edge_type: str
    via: tuple[str, ...]


def blast_radius(
    impact: Sequence[tuple[str, str, str]],
    seeds: Sequence[str],
    depth: int,
) -> tuple[BlastEntry, ...]:
    """回傳改動 seeds 後，在 depth 跳內受影響的節點，依 slug 排序。

    impact 須已篩選為 IMPACT_EDGE_TYPES，且目標須已解析；三種邊型別可
    接續傳遞。廣度優先走訪只保留首次抵達的最短路徑，種子不列入結果。
    排序種子與反向鄰居，讓同長路徑的選擇不受輸入順序影響。
    depth 為零或負數時不展開任何節點。
    """
    反向: dict[str, list[tuple[str, str]]] = {}
    for source, edge_type, target in impact:
        反向.setdefault(target, []).append((source, edge_type))

    起點 = set(seeds)
    已見 = set(起點)
    結果: dict[str, BlastEntry] = {}
    佇列: deque[tuple[str, tuple[str, ...], int]] = deque(
        (slug, (), 0) for slug in sorted(起點)
    )
    while 佇列:
        目前, 路徑, 距離 = 佇列.popleft()
        if 距離 >= depth:
            continue
        # 下一跳只把目前節點加入中繼路徑；種子是起點，不是中繼。
        子路徑 = () if 目前 in 起點 else (*路徑, 目前)
        for 來源, 邊型別 in sorted(反向.get(目前, [])):
            if 來源 in 已見:
                continue
            已見.add(來源)
            結果[來源] = BlastEntry(slug=來源, edge_type=邊型別, via=子路徑)
            佇列.append((來源, 子路徑, 距離 + 1))

    return tuple(結果[slug] for slug in sorted(結果))
