-- business-graph 索引 schema。
-- 本檔描述的整個資料庫都是衍生物：內容真相在 .bgraph/nodes/*.md，
-- 因此 schema 改版時直接刪檔重建，不提供 migration。

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 節點主表。body 存原文供回應顯示；檢索用的分詞文字另存於 nodes_fts。
CREATE TABLE IF NOT EXISTS nodes (
    id           INTEGER PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    -- front matter 解析失敗時，node_type/status/updated_at 為 NULL，
    -- 但 body 仍會入庫：一個節點壞掉不得讓它的內容從檢索中消失。
    node_type    TEXT,
    aliases_text TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',
    path         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status       TEXT,
    updated_at   TEXT,
    indexed_at   TEXT NOT NULL,
    parse_error  TEXT
);

CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes (status);
CREATE INDEX IF NOT EXISTS idx_nodes_updated ON nodes (updated_at);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes (node_type);

-- 別名查詢表。nodes.aliases_text 是給 FTS 用的合併字串，
-- 這張表則供精確比對（explore 要能用中文術語直接命中英文標題的節點）。
CREATE TABLE IF NOT EXISTS aliases (
    slug  TEXT NOT NULL,
    alias TEXT NOT NULL,
    PRIMARY KEY (slug, alias)
);

CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases (alias);

-- 邊。target_slug 為 NULL 代表懸空邊（目標節點不存在）。
-- 主鍵含 edge_type：同兩個節點之間可以同時存在多種關係（例如既覆寫又依賴），
-- 那是兩條不同的邊，不可互相覆蓋。
-- 每次同步後重算，不做增量維護——節點數小，全量重算比維護一致性便宜。
CREATE TABLE IF NOT EXISTS edges (
    source_slug TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    target_raw  TEXT NOT NULL,
    target_slug TEXT,
    PRIMARY KEY (source_slug, edge_type, target_raw)
);

CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target_slug);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges (edge_type);

-- 程式碼錨點。只存 front matter 宣告的符號名；解析成檔案與行號是
-- 查詢時即時做的事，不在此快取（見 codegraph adapter 的設計）。
CREATE TABLE IF NOT EXISTS code_anchors (
    slug   TEXT NOT NULL,
    symbol TEXT NOT NULL,
    PRIMARY KEY (slug, symbol)
);

CREATE INDEX IF NOT EXISTS idx_code_anchors_slug ON code_anchors (slug);

-- 查無結果的查詢統計。單次未命中只是提醒，反覆未命中才是高價值知識缺口。
CREATE TABLE IF NOT EXISTS misses (
    query_norm TEXT PRIMARY KEY,
    count      INTEGER NOT NULL,
    last_seen  TEXT NOT NULL
);

-- 全文檢索表。存的是分詞後的文字（見 tokenizer.py），與 nodes.body 內容不同，
-- 因此採獨立表而非 external content——後者要求兩邊欄位一致，會逼出額外的
-- 分詞欄位與同步 trigger。rowid 對齊 nodes.id，查詢時直接 JOIN。
-- node_type 刻意不進 FTS：以型別名當關鍵字檢索是噪音，該由參數過濾。
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    title,
    aliases_text,
    body,
    tokenize = 'unicode61'
);
