# business-graph-mcp

本機端、隨 repo 版控的業務邏輯知識圖，供 LLM agent（如 Claude Code）查詢、連結並累積規則、實體、決策與術語。

專案的業務知識常散落在對話、人的記憶與程式碼註解中。`business-graph-mcp` 讓 agent 在處理業務問題前先查；查無或內容不足時，把本次釐清的答案寫回。節點之間有明確關係，因此查詢也能回答「這條規則依賴什麼」與「改動它會波及誰」。

Server 本身不呼叫 LLM，也不替內容背書。它負責儲存、檢索、關係與一致性檢查；內容品質與確認狀態由呼叫端 agent 負責。

## 快速開始

需要 [uv](https://docs.astral.sh/uv/)（提供 `uvx`）與 Claude Code。只要執行一條指令：

```powershell
claude mcp add business-graph --scope user -- uvx --from git+https://github.com/zokmi/business-graph.git business-graph-mcp
```

Claude Code 會在啟動 server 時透過 `uvx` 取得套件並快取於本機。首次連線需要下載與建置，可能較久；重開 Claude Code 後以 `/mcp` 確認連線。若要固定安裝於本機或處理啟動逾時，見 [SETUP.md](SETUP.md)。

第一次 `business_write` 會在目前專案建立 `.bgraph/nodes/`；之後可用 `business_explore` 查詢，用 `business_lint` 檢查知識圖。每個工具都能傳入 `project_path` 指定專案位置。

## 三個工具

| 工具 | 用途 |
|---|---|
| `business_explore(query, project_path?)` | 唯一讀取入口。回傳命中節點全文與 `base_hash`、一跳鄰居、關係路徑、反向影響半徑、程式碼錨點位置，以及待辦。中英文皆可查。 |
| `business_write(title, type, content, status, aliases?, code?, base_hash?, project_path?)` | 建立或整頁更新節點。更新既有節點須帶 `business_explore` 回傳的 `base_hash`；雜湊不符時拒絕覆寫。 |
| `business_lint(project_path?)` | 稽核過期、未確認、懸空、孤兒、反覆未命中、共現未連結、未解析程式碼錨點、無 code 的 rule 與未提交變更，並給出修正指引。 |

若 `business_write` 已保存 Markdown、但索引同步或驗證失敗，回應會包含 `indexed: false`、新的 `base_hash`、`dangling: null` 與警告。內容已寫入；下次工具呼叫會重試同步，請勿拿舊 `base_hash` 重送。

三個工具共用 server 指示詞：業務問題先查，查無或不足而本次已弄清楚時寫回，發現既有節點過時時主動更正。詳見 `src/business_graph_mcp/server.py` 的 `INSTRUCTIONS`。

## `.bgraph/` 結構

知識圖位於使用端 repo 根目錄；工具會由 `project_path` 向上尋找最近的 `.bgraph/`，並受 Git repo 與使用者家目錄邊界限制。

```text
<repo>/
└─ .bgraph/
   ├─ nodes/           # Markdown 節點，真相來源，應進 git
   │  ├─ Customer Tier.md
   │  └─ ...
   ├─ bgraph.toml      # 選用設定檔
   ├─ index.db         # 衍生的 SQLite FTS5 索引，不進 git
   └─ workspace.lock   # 跨行程鎖檔，不進 git
```

將索引、SQLite sidecar 與鎖檔加到使用端 repo 的 `.gitignore`：

```gitignore
.bgraph/index.db
.bgraph/index.db-wal
.bgraph/index.db-shm
.bgraph/workspace.lock
```

`nodes/` 必須進版控。索引只是衍生物，schema 版本改變時會重建。每次工具呼叫前會依內容雜湊做 lazy sync，手動修改 Markdown 後不需啟動 watcher。

同一個知識庫的工具呼叫會持有 `workspace.lock`，最多等待 10 秒；若其他 server 行程持鎖過久，可稍後重試。索引同步以整批交易提交，失敗後下次呼叫會重新比對 Markdown 並重試。暫時性的 SQLite 鎖定或 I/O 錯誤會保留索引原檔。

## 節點格式

```markdown
---
title: VIP Discount
type: rule
aliases: [VIP 折扣]
status: confirmed
code: [VipDiscount.apply]
updated: 2026-09-18
---

VIP discount depends on [[depends_on:Customer Tier]].
VIP pricing follows [[overrides:General Discount]].
```

| 欄位 | 必填 | 意義 |
|---|---:|---|
| `title` | 是 | 節點標題，也是連結目標與檔名。 |
| `type` | 是 | `rule`、`entity`、`decision`、`term` 四選一。 |
| `aliases` | 否 | 別名；中文術語應放在此處，讓中英文都能命中。 |
| `status` | 是 | `confirmed` 表示業務方確認過；`inferred` 表示由程式碼或推理得出。 |
| `code` | 否 | 程式碼符號名陣列，例如 `OrderService.CalculateDiscount`；不要填路徑或行號。 |
| `updated` | 自動 | 由 `business_write` 寫入當日日期。 |

四種節點型別：

| 型別 | 適合內容 |
|---|---|
| `rule` | 業務規則，例如折扣算法、受理條件與額度判定。 |
| `entity` | 實體或欄位的業務意義與合法值。 |
| `decision` | 設計決策、理由、替代方案與取捨。 |
| `term` | 業務術語及其在系統中的對應。 |

內容預設使用英文；中文術語應同時列入 `aliases`。`status` 沒有預設值：只有經業務方確認的內容才能標為 `confirmed`。

## 關係語法

節點關係寫在說明關係的句子旁：`[[關係:目標]]`。無前綴的 `[[目標]]` 等同 `[[relates_to:目標]]`。

| 關係 | 語意 | 計入影響半徑 |
|---|---|---:|
| `depends_on` | 本節點依賴目標。 | 是 |
| `overrides` | 本節點覆寫目標。 | 是 |
| `exception_to` | 本節點是目標的例外。 | 是 |
| `relates_to` | 相關但沒有方向語意。 | 否 |

`implemented_by` 不可寫在內文；它由 front matter 的 `code` 欄位產生，目標是程式碼符號而非另一個業務節點。

標題可以含冒號，例如 `[[ADR-3: Pricing Model]]`。只有「冒號前全為小寫字母或底線，且冒號後緊接非空白字元」才會被判定為關係前綴；未知前綴會被 `business_write` 拒絕。

關係目標依序比對 slug、alias、完整 title；因此 title 內因檔名限制被換成連字號的冒號等字元仍可照原標題連結。Slug 命中優先於 alias，alias 又優先於 title。重複 alias 以 slug 排序後取第一個，確保結果穩定；重複 title 則視為歧義並保持懸空，不猜測其中一個。`business_lint` 會列出候選頁，要求將重複 title 改名，或把引用改成明確的 slug／alias。

## 關係路徑與影響半徑

`business_explore` 會沿關係展開路徑，最大深度由 `explore_depth` 控制。一跳鄰居依 token 預算展開全文或首段；第二跳以後只列在路徑中。

影響半徑反向追蹤 `depends_on`、`overrides`、`exception_to`。例如 `General Discount [[depends_on:Customer Tier]]` 且 `VIP Discount [[overrides:General Discount]]`，查 `Customer Tier` 時會列出直接受影響的 `General Discount`，也會列出經 `General Discount` 受影響的 `VIP Discount`。全文搜尋仍用 OR 找出多個閱讀候選；若查詢與某頁的完整標題或別名完全相同，影響半徑只以完全同名的頁為起點，避免引用該名稱的頁也成為起點而從影響結果消失。沒有完全同名頁時，以全部搜尋命中為起點。`relates_to` 不參與，避免沒有方向的關係把整張圖都算成受影響。

## 選用的 codegraph 整合

若同一 repo 有 `.codegraph/codegraph.db`，`business_explore` 會以 `code` 裡的符號名唯讀查詢 codegraph 索引，顯示 `檔案:起行-迄行`。它不回傳原始碼；需要程式碼內容時由呼叫端另用 codegraph 工具查詢。

codegraph 是選用相依。沒有索引、檔案不是 SQLite、schema 不相容或查無符號時，錨點仍保留符號名並顯示「未解析」，其餘知識圖輸出照常產生。`business_lint` 只有在索引查詢完整成功時才判定哪些錨點查無；索引缺少或損壞時會略過該段，避免把可選功能的失敗誤報成所有錨點失效。無 code 的檢查只針對 `rule`，不會警告 `entity`、`decision` 或 `term`。

## `bgraph.toml` 設定

`.bgraph/bgraph.toml` 不存在時使用以下預設值：

```toml
stale_days = 90
recall_budget = 8000
miss_threshold = 3
low_confidence_score = 2.0
explore_depth = 2
```

| 欄位 | 預設值 | 意義 |
|---|---:|---|
| `stale_days` | 90 | `updated` 超過幾天視為過期。 |
| `recall_budget` | 8000 | 一跳鄰居內容的 token 預算；命中節點仍一律全文回傳。 |
| `miss_threshold` | 3 | 同一正規化查詢連續查無達此次數後，標記為高價值知識缺口。 |
| `low_confidence_score` | 2.0 | FTS 最高分低於此值時提示命中信心不足。 |
| `explore_depth` | 2 | 關係路徑與影響半徑的最大跳數，必須為正整數。 |

未知欄位會報錯，避免設定拼錯卻靜默採用預設值。

### 門檻校準依據

`low_confidence_score` 的預設值 2.0 來自 16 頁中英混合知識圖的初始校準。以 12 筆應命中查詢和 12 筆不該命中查詢量測全文搜尋分數：

- 應命中組分數落在 `[3.8234, 12.4731]`
- 不該命中組分數落在 `[0.0, 1.8849]`（排除一筆因英數詞前綴運算子造成詞根巧合的查詢，`stand*` 誤中 `standard`，那是 tokenizer 刻意的召回優先取捨，非門檻雜訊）

兩組不重疊，選定 **2.0**：貼近不該命中組上限，寧可漏掉部分邊緣情況的警告，也不讓門檻逼近應命中組下緣而誤標真正的好命中——警報氾濫，agent 就會開始無視所有提醒。

## 從 llm-wiki-mcp 遷移

先安裝新套件，再在舊 `.wiki/` 所在 repo 執行：

```powershell
python -m business_graph_mcp migrate <repo>
```

遷移會把 `.wiki/pages/*.md` 複製到 `.bgraph/nodes/`、把每個可解析頁暫定為 `type: rule`，並在目標不存在時把 `wiki.toml` 複製為 `bgraph.toml`。既有 `[[X]]` 在新模型中等同 `relates_to`。

這是保守的一次性複製：原 `.wiki/` 不會刪除，既有 `.bgraph` 檔案不會覆寫，解析或讀取失敗的頁會列為跳過，只有 `pages/` 第一層的 `*.md` 會處理，舊 `index.db` 不會搬移。所有遷移節點都暫定為 `rule`，必須逐頁改成正確型別並補上可用的關係與 `code`；若檔案系統不支援建立硬連結，命令會停止並保留已完成項目與原始 `.wiki/`。它也不會修改 MCP client 的舊 server 註冊或 `.gitignore`，請依 [SETUP.md](SETUP.md) 手動處理。

## 安裝與設定

見 [SETUP.md](SETUP.md)。

## 開發

```powershell
git clone https://github.com/zokmi/business-graph.git
cd business-graph
uv sync
uv run pytest -q
uv run ruff check .
uv run mypy
uv build
```

## 授權

MIT，見 [LICENSE](LICENSE)。
