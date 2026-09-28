"""索引資料庫的 schema 套用與頁面存取。

所有 SQL 集中於本模組；上層邏輯一律透過這裡的函式操作資料庫，
以維持「驗證邏輯是不碰 sqlite 的純函式」這條分界。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from business_graph_mcp.db.index import SCHEMA_VERSION as SCHEMA_VERSION
from business_graph_mcp.db.index import open_index as open_index
from business_graph_mcp.edges import IMPACT_EDGE_TYPES, Edge
from business_graph_mcp.node import NODE_RULE
from business_graph_mcp.tokenizer import segment_cjk

#: 別名在 nodes.aliases_text 中的分隔符號；不會出現在正常術語裡。
_ALIAS_SEP = "\x1f"

@dataclass(frozen=True)
class NodeRow:
    """索引中一頁的完整資料。

    欄位:
        id: 內部主鍵，同時是 nodes_fts 的 rowid。
        slug: 頁面識別字，同時是檔名（不含副檔名）。
        title: 節點標題。
        node_type: 節點型別；front matter 解析失敗時為 None。
        aliases: 別名；供中文術語命中英文標題的頁。
        body: 頁面內文原文（非分詞後的檢索文字）。
        path: 相對於 .bgraph/ 的檔案路徑。
        content_hash: 檔案內容雜湊，用於增量同步與寫入的樂觀鎖。
        status: confirmed 或 inferred；解析失敗時為 None。
        updated_at: ISO 格式日期字串；解析失敗時為 None。
        parse_error: front matter 解析失敗的原因，成功時為 None。
    """

    id: int
    slug: str
    title: str
    node_type: str | None
    aliases: tuple[str, ...]
    body: str
    path: str
    content_hash: str
    status: str | None
    updated_at: str | None
    parse_error: str | None


def _row_to_node(row: sqlite3.Row) -> NodeRow:
    """把查詢結果列轉成 NodeRow。

    參數:
        row: nodes 表的一列。
    """
    raw_aliases = row["aliases_text"]
    aliases = tuple(a for a in raw_aliases.split(_ALIAS_SEP) if a) if raw_aliases else ()
    return NodeRow(
        id=row["id"],
        slug=row["slug"],
        title=row["title"],
        node_type=row["node_type"],
        aliases=aliases,
        body=row["body"],
        path=row["path"],
        content_hash=row["content_hash"],
        status=row["status"],
        updated_at=row["updated_at"],
        parse_error=row["parse_error"],
    )


def upsert_node(
    conn: sqlite3.Connection,
    *,
    slug: str,
    title: str,
    node_type: str | None,
    aliases: tuple[str, ...],
    body: str,
    path: str,
    content_hash: str,
    status: str | None,
    updated_at: str | None,
    parse_error: str | None,
    edges: tuple[Edge, ...],
    code: tuple[str, ...],
    indexed_at: datetime,
) -> None:
    """寫入或更新一頁，並同步重建其別名、連結與全文索引。

    別名與連結採「先刪後寫」而非逐筆比對：頁面層級的資料量極小，
    全量重寫比維護差異簡單，也不會留下更新遺漏的幽靈記錄。

    參數:
        conn: 索引連線。
        slug: 頁面識別字。
        title: 節點標題。
        node_type: 節點型別；front matter 解析失敗時為 None。
        aliases: 別名。
        body: 頁面內文原文。
        path: 相對於 .bgraph/ 的檔案路徑。
        content_hash: 檔案內容雜湊。
        status: confirmed／inferred，解析失敗時為 None。
        updated_at: ISO 日期字串，解析失敗時為 None。
        parse_error: 解析失敗原因，成功時為 None。
        edges: 內文中的型別化邊；解析錯誤的邊不入庫。
        code: front matter 宣告的程式碼符號。
        indexed_at: 本次索引時間。
    """
    aliases_text = _ALIAS_SEP.join(aliases)
    conn.execute(
        """
        INSERT INTO nodes (
            slug, title, node_type, aliases_text, body, path, content_hash,
            status, updated_at, indexed_at, parse_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (slug) DO UPDATE SET
            title = excluded.title,
            node_type = excluded.node_type,
            aliases_text = excluded.aliases_text,
            body = excluded.body,
            path = excluded.path,
            content_hash = excluded.content_hash,
            status = excluded.status,
            updated_at = excluded.updated_at,
            indexed_at = excluded.indexed_at,
            parse_error = excluded.parse_error
        """,
        (
            slug,
            title,
            node_type,
            aliases_text,
            body,
            path,
            content_hash,
            status,
            updated_at,
            indexed_at.isoformat(timespec="seconds"),
            parse_error,
        ),
    )
    node_id = conn.execute("SELECT id FROM nodes WHERE slug = ?", (slug,)).fetchone()["id"]

    conn.execute("DELETE FROM aliases WHERE slug = ?", (slug,))
    conn.executemany(
        "INSERT OR IGNORE INTO aliases (slug, alias) VALUES (?, ?)",
        [(slug, alias) for alias in aliases],
    )

    conn.execute("DELETE FROM edges WHERE source_slug = ?", (slug,))
    conn.executemany(
        """
        INSERT OR IGNORE INTO edges (source_slug, edge_type, target_raw, target_slug)
        VALUES (?, ?, ?, COALESCE(
            -- 先比對 slug：slug 是頁面的正式身分，命中優先權最高。
            (SELECT slug FROM nodes   WHERE slug  = ? COLLATE NOCASE),
            -- 找不到再比對 alias：別名是輔助識別，中文術語常寫成別名而非標題。
            -- 重複 alias 沿用可解析行為，但以 slug 排序，避免依賴資料庫掃描順序。
            (SELECT slug FROM aliases WHERE alias = ? COLLATE NOCASE
             ORDER BY slug COLLATE NOCASE, slug LIMIT 1),
            -- 最後比對原始 title，讓含冒號等檔名不安全字元的標題仍可連結。
            -- 重複 title 是歧義，不猜其中一個；保留 NULL 交由 lint 報懸空。
            (SELECT CASE WHEN COUNT(*) = 1 THEN MIN(slug) END
             FROM nodes WHERE title = ? COLLATE NOCASE)
        ))
        """,
        [
            (
                slug,
                edge.edge_type,
                edge.target,
                edge.target,
                edge.target,
                edge.target,
            )
            for edge in edges
            if edge.error is None
        ],
    )
    # 注意：這裡只解析「本頁自己的」連結。新頁出現會讓「別頁」原本懸空的連結
    # 變成有效，那需要全表重解，但每寫一頁就做一次是 O(頁數 × 連結數)——
    # 初次索引上千頁時要數秒。全表重解改由 sync_index 在一輪結束時呼叫
    # resolve_edges() 做一次。

    conn.execute("DELETE FROM code_anchors WHERE slug = ?", (slug,))
    conn.executemany(
        "INSERT OR IGNORE INTO code_anchors (slug, symbol) VALUES (?, ?)",
        [(slug, symbol) for symbol in code],
    )

    # FTS 存分詞後的文字；先刪後插，避免同一 rowid 留下舊內容。
    conn.execute("DELETE FROM nodes_fts WHERE rowid = ?", (node_id,))
    conn.execute(
        "INSERT INTO nodes_fts (rowid, title, aliases_text, body) VALUES (?, ?, ?, ?)",
        (
            node_id,
            segment_cjk(title),
            segment_cjk(" ".join(aliases)),
            segment_cjk(body),
        ),
    )


def resolve_edges(conn: sqlite3.Connection) -> None:
    """重新解析全部連結的指向。

    頁面新增或刪除會改變連結的有效性：新頁讓原本懸空的連結變成有效，
    刪頁讓原本有效的連結變成懸空。兩者都需要全表重解。

    刻意不放在 upsert_node／delete_node 內部：那會讓每寫一頁就掃一次全表，
    初次索引上千頁時是 O(頁數 × 連結數)。改由 sync_index 在一輪同步結束、
    確實有增刪改時呼叫一次。

    參數:
        conn: 索引連線。
    """
    conn.execute(
        """
        UPDATE edges SET target_slug = COALESCE(
            -- 先比對 slug：slug 是頁面的正式身分，命中優先權最高。
            (SELECT slug FROM nodes   WHERE slug  = edges.target_raw COLLATE NOCASE),
            -- 找不到再比對 alias：別名是輔助識別，中文術語常寫成別名而非標題。
            -- 注意：COLLATE NOCASE 在 SQLite 只對 ASCII 生效；中文本無大小寫之分，
            -- 不受影響。
            (SELECT slug FROM aliases WHERE alias = edges.target_raw COLLATE NOCASE
             ORDER BY slug COLLATE NOCASE, slug LIMIT 1),
            -- title 與 slug 不同時（例如冒號被 slugify 換成連字號）仍可連結。
            -- 只接受唯一 title；重複 title 保持懸空，避免靜默連到錯誤節點。
            (SELECT CASE WHEN COUNT(*) = 1 THEN MIN(slug) END
             FROM nodes WHERE title = edges.target_raw COLLATE NOCASE)
        )
        """
    )


def delete_node(conn: sqlite3.Connection, slug: str) -> None:
    """刪除一頁及其別名、連結與全文索引。

    指向本頁的連結會就此變成懸空，但那由 sync_index 在結尾呼叫
    resolve_edges() 統一處理，本函式不自行全表重解。

    參數:
        conn: 索引連線。
        slug: 要刪除的頁面識別字。
    """
    row = conn.execute("SELECT id FROM nodes WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        return

    conn.execute("DELETE FROM nodes_fts WHERE rowid = ?", (row["id"],))
    conn.execute("DELETE FROM nodes WHERE slug = ?", (slug,))
    conn.execute("DELETE FROM aliases WHERE slug = ?", (slug,))
    conn.execute("DELETE FROM edges WHERE source_slug = ?", (slug,))
    conn.execute("DELETE FROM code_anchors WHERE slug = ?", (slug,))


def get_node(conn: sqlite3.Connection, slug: str) -> NodeRow | None:
    """依 slug 取回一頁；不存在時回傳 None。

    參數:
        conn: 索引連線。
        slug: 頁面識別字。
    """
    row = conn.execute("SELECT * FROM nodes WHERE slug = ?", (slug,)).fetchone()
    return None if row is None else _row_to_node(row)


def all_content_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """取回全部頁面的內容雜湊，供增量同步比對。

    參數:
        conn: 索引連線。
    """
    return {
        row["slug"]: row["content_hash"]
        for row in conn.execute("SELECT slug, content_hash FROM nodes")
    }


def record_miss(conn: sqlite3.Connection, query_norm: str, now: datetime) -> int:
    """記錄一次查無結果並回傳累計次數。

    參數:
        conn: 索引連線。
        query_norm: 正規化後的查詢字串。
        now: 本次查詢時間。

    回傳:
        含本次在內的累計未命中次數。
    """
    conn.execute(
        """
        INSERT INTO misses (query_norm, count, last_seen) VALUES (?, 1, ?)
        ON CONFLICT (query_norm) DO UPDATE SET
            count = count + 1,
            last_seen = excluded.last_seen
        """,
        (query_norm, now.isoformat(timespec="seconds")),
    )
    conn.commit()
    row = conn.execute(
        "SELECT count FROM misses WHERE query_norm = ?", (query_norm,)
    ).fetchone()
    return int(row["count"])


def search_nodes(
    conn: sqlite3.Connection, match_query: str, limit: int
) -> list[tuple[NodeRow, float]]:
    """以 FTS5 檢索頁面，回傳 (頁面, 分數) 並依分數由高到低排序。

    bm25 回傳的是「越小越相關」的負值，取負號轉成越大越相關，
    讓門檻比較（low_confidence_score）可用直覺的大小關係表達。

    欄位權重 title=10、aliases=5、body=1：業務術語命中標題或別名，
    幾乎等同命中答案；命中內文則可能只是順帶提及。

    參數:
        conn: 索引連線。
        match_query: 已由 tokenizer.build_match_query 組好的 MATCH 運算式。
        limit: 最多回傳幾頁。
    """
    if not match_query:
        return []
    rows = conn.execute(
        """
        SELECT n.*, -bm25(nodes_fts, 10.0, 5.0, 1.0) AS score
        FROM nodes_fts
        JOIN nodes n ON n.id = nodes_fts.rowid
        WHERE nodes_fts MATCH ?
        ORDER BY score DESC
        LIMIT ?
        """,
        (match_query, limit),
    ).fetchall()
    return [(_row_to_node(row), float(row["score"])) for row in rows]


def neighbors(
    conn: sqlite3.Connection, slugs: Sequence[str]
) -> list[tuple[NodeRow, str]]:
    """取出與指定頁面相鄰一跳的頁面（雙向）。

    relation 為 references（被指定頁引用）或 referenced_by（引用了指定頁）。
    同一頁同時符合兩種關係時只保留 references——「這頁被引用了」比
    「這頁引用了別人」更能說明它為何相關。

    參數:
        conn: 索引連線。
        slugs: 命中頁的 slug 清單。
    """
    if not slugs:
        return []
    placeholders = ",".join("?" for _ in slugs)
    rows = conn.execute(
        f"""
        SELECT n.*, 'references' AS relation
        FROM edges e JOIN nodes n ON n.slug = e.target_slug
        WHERE e.source_slug IN ({placeholders}) AND e.target_slug IS NOT NULL
        UNION
        SELECT n.*, 'referenced_by' AS relation
        FROM edges e JOIN nodes n ON n.slug = e.source_slug
        WHERE e.target_slug IN ({placeholders})
        """,
        (*slugs, *slugs),
    ).fetchall()

    seen: dict[str, tuple[NodeRow, str]] = {}
    for row in rows:
        page = _row_to_node(row)
        if page.slug in slugs:
            continue
        existing = seen.get(page.slug)
        if existing is None or row["relation"] == "references":
            seen[page.slug] = (page, row["relation"])
    return [seen[slug] for slug in sorted(seen)]


def dangling_targets(
    conn: sqlite3.Connection, slugs: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    """取出指定頁面中指向不存在頁面的連結。

    參數:
        conn: 索引連線。
        slugs: 要檢查的頁面 slug 清單。

    回傳:
        來源頁 slug → 懸空的連結目標；同目標的多種關係只回傳一次。
        沒有懸空連結的頁不會出現在結果中。
    """
    if not slugs:
        return {}
    placeholders = ",".join("?" for _ in slugs)
    rows = conn.execute(
        f"""
        SELECT DISTINCT source_slug, target_raw FROM edges
        WHERE target_slug IS NULL AND source_slug IN ({placeholders})
        ORDER BY source_slug, target_raw
        """,
        tuple(slugs),
    ).fetchall()

    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row["source_slug"], []).append(row["target_raw"])
    return {slug: tuple(targets) for slug, targets in result.items()}


def orphan_nodes(conn: sqlite3.Connection) -> list[NodeRow]:
    """列出沒有任何頁面引用的頁。

    參數:
        conn: 索引連線。
    """
    rows = conn.execute(
        """
        SELECT n.* FROM nodes n
        WHERE NOT EXISTS (SELECT 1 FROM edges WHERE target_slug = n.slug)
        ORDER BY n.slug
        """
    ).fetchall()
    return [_row_to_node(row) for row in rows]


def all_dangling(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """列出全部懸空連結。

    參數:
        conn: 索引連線。

    回傳:
        (來源頁 slug, 懸空的目標文字) 的清單；同來源與目標只回傳一次。
    """
    rows = conn.execute(
        "SELECT DISTINCT source_slug, target_raw FROM edges WHERE target_slug IS NULL "
        "ORDER BY source_slug, target_raw"
    ).fetchall()
    return [(row["source_slug"], row["target_raw"]) for row in rows]


def ambiguous_dangling_titles(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    """列出因多個 title 命中而無法解析的懸空目標。

    只檢查目前仍懸空的目標；已由優先級較高的 slug 或 alias 解析的連結
    不屬於 title 歧義。Title 比對沿用解析器的 ASCII 不分大小寫規則。

    參數:
        conn: 索引連線。

    回傳:
        目標原文 → 依 slug 穩定排序的候選頁面；只有至少兩個候選才會出現。
    """
    rows = conn.execute(
        """
        SELECT dangling.target_raw, nodes.slug
        FROM (
            SELECT DISTINCT target_raw
            FROM edges
            WHERE target_slug IS NULL
        ) AS dangling
        JOIN nodes ON nodes.title = dangling.target_raw COLLATE NOCASE
        ORDER BY dangling.target_raw COLLATE NOCASE, dangling.target_raw,
                 nodes.slug COLLATE NOCASE, nodes.slug
        """
    ).fetchall()
    candidates: dict[str, list[str]] = {}
    for row in rows:
        candidates.setdefault(row["target_raw"], []).append(row["slug"])
    return {
        target: tuple(slugs)
        for target, slugs in candidates.items()
        if len(slugs) > 1
    }


def stale_nodes(conn: sqlite3.Connection, cutoff: str) -> list[NodeRow]:
    """列出更新日期早於 cutoff 的頁。

    參數:
        conn: 索引連線。
        cutoff: ISO 格式日期字串；早於此日期者視為過期。
    """
    rows = conn.execute(
        "SELECT * FROM nodes WHERE updated_at IS NOT NULL AND updated_at < ? "
        "ORDER BY updated_at",
        (cutoff,),
    ).fetchall()
    return [_row_to_node(row) for row in rows]


def inferred_nodes(conn: sqlite3.Connection) -> list[NodeRow]:
    """列出尚未經業務確認的頁。

    參數:
        conn: 索引連線。
    """
    rows = conn.execute(
        "SELECT * FROM nodes WHERE status = 'inferred' ORDER BY slug"
    ).fetchall()
    return [_row_to_node(row) for row in rows]


def frequent_misses(conn: sqlite3.Connection, threshold: int) -> list[tuple[str, int]]:
    """列出累計未命中次數達門檻的查詢。

    參數:
        conn: 索引連線。
        threshold: 次數門檻。
    """
    rows = conn.execute(
        "SELECT query_norm, count FROM misses WHERE count >= ? ORDER BY count DESC, query_norm",
        (threshold,),
    ).fetchall()
    return [(row["query_norm"], int(row["count"])) for row in rows]


def node_count(conn: sqlite3.Connection) -> int:
    """回傳目前索引中的頁數。

    供 lint 判斷共現線索的全表自連接掃描是否已超出可負擔的規模；
    超過門檻時應整個跳過該檢查，而非硬跑一次代價過高的查詢。

    參數:
        conn: 索引連線。
    """
    row = conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()
    return int(row["n"])


def unlinked_mentions(
    conn: sqlite3.Connection, min_term_length: int, limit: int
) -> list[tuple[str, str]]:
    """找出「被提及但未被連結」的頁面組合。

    某頁的標題或別名出現在另一頁的內文中，兩頁之間卻沒有連結。這可能表示
    該補一條連結，也可能表示兩頁在講同一件事而互相矛盾。判讀是語意問題，
    交給 agent；server 只負責把組合找出來。

    比對對象包含標題與別名——別名存在的理由正是「同一概念的不同說法」，
    共現線索要抓的正是換個說法講同一件事，漏掉別名等於漏掉這個功能
    最該抓的情況。

    參數:
        conn: 索引連線。
        min_term_length: 詞的最短長度；短於此長度的標題／別名不參與比對，
            避免短詞（如 "API"）在任意內文中命中大量不相干位置。
        limit: SQL LIMIT 值。呼叫端若要判斷結果是否被截斷，應傳入
            「顯示上限 + 1」，並在拿到結果後自行比對筆數。

    回傳:
        (被提及的頁 slug, 提及它的頁 slug) 清單，最多 limit 筆。
    """
    rows = conn.execute(
        """
        WITH terms AS (
            SELECT slug, title AS term FROM nodes
            UNION
            SELECT slug, alias AS term FROM aliases
        )
        SELECT DISTINCT t.slug AS mentioned, source.slug AS mentioner
        FROM terms t
        JOIN nodes source ON source.slug <> t.slug
        WHERE length(t.term) >= ?
          AND instr(source.body, t.term) > 0
          AND NOT EXISTS (
              SELECT 1 FROM edges
              WHERE source_slug = source.slug AND target_slug = t.slug
          )
        ORDER BY t.slug, source.slug
        LIMIT ?
        """,
        (min_term_length, limit),
    ).fetchall()
    return [(row["mentioned"], row["mentioner"]) for row in rows]


def resolved_edges(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """取出全部已解析（非懸空）的邊，供關係路徑走訪。

    參數:
        conn: 索引連線。

    回傳:
        (source_slug, edge_type, target_slug) 的清單。
    """
    rows = conn.execute(
        "SELECT source_slug, edge_type, target_slug FROM edges "
        "WHERE target_slug IS NOT NULL"
    ).fetchall()
    return [(row["source_slug"], row["edge_type"], row["target_slug"]) for row in rows]


def impact_edges(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """取出全部計入影響半徑的邊（已解析、非懸空）。

    回傳整張圖而非只取命中節點的鄰邊：影響半徑要沿著邊反向傳遞多跳，
    在 Python 端以純函式走訪比逐跳發 SQL 簡單，且節點數在數百量級，
    一次全取的成本可忽略。

    參數:
        conn: 索引連線。

    回傳:
        (source_slug, edge_type, target_slug) 的清單。
    """
    placeholders = ",".join("?" for _ in IMPACT_EDGE_TYPES)
    rows = conn.execute(
        f"""
        SELECT source_slug, edge_type, target_slug
        FROM edges
        WHERE target_slug IS NOT NULL AND edge_type IN ({placeholders})
        """,
        tuple(IMPACT_EDGE_TYPES),
    ).fetchall()
    return [
        (row["source_slug"], row["edge_type"], row["target_slug"]) for row in rows
    ]


def code_anchors_for(
    conn: sqlite3.Connection, slugs: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    """取出指定節點的程式碼錨點符號名。

    參數:
        conn: 索引連線。
        slugs: 節點識別字清單。

    回傳:
        slug 對應到符號名 tuple；沒有錨點的節點不會出現在結果中。
    """
    if not slugs:
        return {}
    placeholders = ",".join("?" for _ in slugs)
    rows = conn.execute(
        f"SELECT slug, symbol FROM code_anchors WHERE slug IN ({placeholders}) "
        "ORDER BY slug, symbol",
        tuple(slugs),
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row["slug"], []).append(row["symbol"])
    return {slug: tuple(symbols) for slug, symbols in grouped.items()}


def all_code_anchors(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """取出全部程式碼錨點，供稽核檢查解析狀況。

    參數:
        conn: 索引連線。

    回傳:
        (slug, symbol) 的清單，依 slug、symbol 排序。
    """
    return [
        (row["slug"], row["symbol"])
        for row in conn.execute(
            "SELECT slug, symbol FROM code_anchors ORDER BY slug, symbol"
        )
    ]


def rule_nodes_without_code(conn: sqlite3.Connection) -> list[NodeRow]:
    """列出型別為 rule 但沒有任何程式碼錨點的節點。

    只查 rule：其他型別本來就常常沒有對應程式碼，不應列為缺漏。

    參數:
        conn: 索引連線。

    回傳:
        節點清單，依 slug 排序。
    """
    rows = conn.execute(
        """
        SELECT n.* FROM nodes n
        WHERE n.node_type = ?
          AND NOT EXISTS (SELECT 1 FROM code_anchors c WHERE c.slug = n.slug)
        ORDER BY n.slug
        """,
        (NODE_RULE,),
    ).fetchall()
    return [_row_to_node(row) for row in rows]
