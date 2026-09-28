"""檢索、鄰居擴展與 token 預算配置。

本模組的配置邏輯（estimate_tokens、lead_paragraph、allocate_neighbors）
刻意寫成不碰資料庫的純函式，邊界情境才測得動。
"""
from __future__ import annotations

import re
import sqlite3
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from business_graph_mcp.blast import BlastEntry, blast_radius
from business_graph_mcp.config import GraphConfig
from business_graph_mcp.db.queries import (
    NodeRow,
    code_anchors_for,
    impact_edges,
    neighbors,
    resolved_edges,
    search_nodes,
)
from business_graph_mcp.tokenizer import build_match_query

#: 最多取幾頁命中。取太多會讓預算全花在勉強相關的頁上，
#: 排擠掉真正該展開的鄰居；八頁足以涵蓋一個業務問題的相關規則。
MAX_HITS = 8

_CJK_RANGE = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ]")
_PARAGRAPH_BREAK = re.compile(r"\r?\n\s*\r?\n")


@dataclass(frozen=True)
class Hit:
    """一筆命中結果。

    欄位:
        page: 命中的頁面。
        score: 檢索分數，越大越相關。
    """

    page: NodeRow
    score: float


@dataclass(frozen=True)
class Neighbor:
    """與命中頁相鄰一跳的頁面。

    欄位:
        page: 鄰居頁面。
        relation: references（被命中頁引用）或 referenced_by（引用了命中頁）。
    """

    page: NodeRow
    relation: str


@dataclass(frozen=True)
class PathStep:
    """關係路徑上的一步。

    欄位:
        target: 這一步抵達的節點識別字。
        edge_type: 抵達它所用的邊型別。
        depth: 距離起點幾跳，起點的直接鄰居為 1。
    """

    target: str
    edge_type: str
    depth: int
    source: str = ""


def relation_paths(
    all_edges: Sequence[tuple[str, str, str]],
    roots: Sequence[str],
    depth: int,
) -> dict[str, tuple[PathStep, ...]]:
    """從每個起點沿邊正向走訪，回答理解這條規則還得看哪些節點。

    廣度優先走訪只保留首次抵達的最短路徑，各節點的鄰邊依目標、
    型別排序，讓同長路徑不受輸入順序影響。已見節點不重複展開，
    循環自然終止，起點不列入自己的結果。

    參數:
        all_edges: 全部已解析的 (source_slug, edge_type, target_slug) 邊。
        roots: 起點節點，通常是本次命中的節點。
        depth: 最多展開幾跳，零或負數時不展開。

    回傳:
        起點 slug 對應到依廣度優先走訪順序排列的路徑步驟。
    """
    正向: dict[str, set[tuple[str, str]]] = {}
    for source, edge_type, target in all_edges:
        正向.setdefault(source, set()).add((target, edge_type))

    結果: dict[str, tuple[PathStep, ...]] = {}
    for root in roots:
        已見 = {root}
        步驟: list[PathStep] = []
        佇列: deque[tuple[str, int]] = deque([(root, 0)])
        while 佇列:
            目前, 距離 = 佇列.popleft()
            if 距離 >= depth:
                continue
            for 目標, 邊型別 in sorted(正向.get(目前, [])):
                if 目標 == root:
                    continue
                # 同一對節點可有數種關係；保留同層的平行邊，但只展開一次。
                if 目標 in 已見:
                    if any(s.target == 目標 and s.depth == 距離 + 1 for s in 步驟):
                        步驟.append(PathStep(目標, 邊型別, 距離 + 1, 目前))
                    continue
                已見.add(目標)
                步驟.append(PathStep(目標, 邊型別, 距離 + 1, 目前))
                佇列.append((目標, 距離 + 1))
        結果[root] = tuple(步驟)
    return 結果


@dataclass(frozen=True)
class ExploreResult:
    """一次 explore 的完整結果。

    欄位:
        hits: 命中頁，一律全文展開。
        neighbors_full: 預算容得下、全文展開的鄰居。
        neighbors_brief: 預算不足、只列標題與首段的鄰居。
        top_score: 最高命中分數；查無結果時為 0.0，供信心門檻判定。
        paths: 每個命中節點的關係路徑，鍵為命中節點的 slug。
        blast: 影響半徑——改動命中節點會波及哪些節點。
        anchors: 命中與完整鄰居節點宣告的程式碼符號名，鍵為節點的 slug。
    """

    hits: tuple[Hit, ...]
    neighbors_full: tuple[Neighbor, ...]
    neighbors_brief: tuple[Neighbor, ...]
    top_score: float
    paths: dict[str, tuple[PathStep, ...]]
    blast: tuple[BlastEntry, ...]
    anchors: dict[str, tuple[str, ...]]


def estimate_tokens(text: str) -> int:
    """估算文字的 token 數。

    中文一字約一個 token，英數約每四個字元一個 token。若不分語言一律
    用字元數除以四，中文內容會嚴重低估而衝爆實際的 context 上限。

    參數:
        text: 要估算的文字。
    """
    cjk = len(_CJK_RANGE.findall(text))
    return cjk + (len(text) - cjk + 3) // 4


def lead_paragraph(body: str) -> str:
    """取出內文的第一個段落，供預算不足的鄰居顯示。

    參數:
        body: 頁面內文。
    """
    return _PARAGRAPH_BREAK.split(body.strip(), maxsplit=1)[0].strip()


def allocate_neighbors(
    hits: list[Hit] | list[NodeRow],
    candidates: list[Neighbor],
    budget: int,
) -> tuple[list[Neighbor], list[Neighbor]]:
    """依 token 預算決定哪些鄰居全文展開、哪些只列首段。

    命中頁一律全文，即使已超出預算——Spec 第 4.1 節要求充分性優先，
    答案不完整會讓 agent 退回自行讀檔，前面省的全部白費。
    鄰居則逐一配置：放得下就展開，放不下的退為簡要，不做全有全無的判斷。

    參數:
        hits: 命中頁（NodeRow 或 Hit 皆可）。
        candidates: 候選鄰居，依相關性排序。
        budget: token 預算上限。
    """
    used = 0
    for hit in hits:
        page = hit.page if isinstance(hit, Hit) else hit
        used += estimate_tokens(page.body)

    full: list[Neighbor] = []
    brief: list[Neighbor] = []
    for candidate in candidates:
        cost = estimate_tokens(candidate.page.body)
        if used + cost <= budget:
            full.append(candidate)
            used += cost
        else:
            brief.append(candidate)
    return full, brief


def run_explore(conn: sqlite3.Connection, query: str, cfg: GraphConfig) -> ExploreResult:
    """執行 explore：檢索、鄰居預算配置，以及關係、影響半徑與錨點收集。

    參數:
        conn: 索引連線。
        query: 使用者或 agent 提供的查詢字串。
        cfg: 知識庫設定。
    """
    match_query = build_match_query(query)
    scored = search_nodes(conn, match_query, MAX_HITS)
    hits = [Hit(page=page, score=score) for page, score in scored]
    if not hits:
        return ExploreResult(
            hits=(), neighbors_full=(), neighbors_brief=(), top_score=0.0,
            paths={}, blast=(), anchors={},
        )

    candidates = [
        Neighbor(page=page, relation=relation)
        for page, relation in neighbors(conn, [h.page.slug for h in hits])
    ]
    full, brief = allocate_neighbors(hits, candidates, cfg.recall_budget)

    hit_slugs = [h.page.slug for h in hits]
    result_paths = relation_paths(resolved_edges(conn), hit_slugs, cfg.explore_depth)
    # FTS 用 OR 擴大閱讀候選；精確標題／別名查詢則只以同名頁為影響起點。
    # 否則引用該標題的頁面也成為種子，會從影響半徑中被排除。
    exact_roots = [
        h.page.slug for h in hits
        if h.page.title.casefold() == query.strip().casefold()
        or any(a.casefold() == query.strip().casefold() for a in h.page.aliases)
    ]
    result_blast = blast_radius(impact_edges(conn), exact_roots or hit_slugs, cfg.explore_depth)
    result_anchors = code_anchors_for(conn, [*hit_slugs, *(n.page.slug for n in full)])

    return ExploreResult(
        hits=tuple(hits),
        neighbors_full=tuple(full),
        neighbors_brief=tuple(brief),
        top_score=hits[0].score,
        paths=result_paths,
        blast=result_blast,
        anchors=result_anchors,
    )
