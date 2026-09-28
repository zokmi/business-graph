# business-graph-mcp 安裝與設定

`business-graph-mcp` 是 stdio MCP server，由 MCP client 在需要時啟動，不需另設常駐服務。

## 1. 安裝

### 方式 A：`uv tool install`（推薦）

```powershell
uv tool install --from "git+https://github.com/zokmi/business-graph.git" business-graph-mcp
```

完成後 `business-graph-mcp` 會出現在 PATH；Windows 通常位於 `%USERPROFILE%\.local\bin\business-graph-mcp.exe`。更新使用：

```powershell
uv tool upgrade business-graph-mcp
```

建立對應的 Git tag 後，若要鎖定發行版本：

```powershell
uv tool install --from "git+https://github.com/zokmi/business-graph.git@business-graph-mcp-v0.1.0" business-graph-mcp
```

> 請勿把 `uvx --from "git+https://..."` 設為 MCP server 啟動命令。那會讓每個 session 都連 GitHub 解析與可能重建，容易超過 MCP client 的啟動 timeout。明示安裝與升級可讓啟動快且版本可控。

### 方式 B：`pipx`

```powershell
pipx install "git+https://github.com/zokmi/business-graph.git"
```

更新用 `pipx upgrade business-graph-mcp`；若所用 pipx 版本無法直接升級 Git 來源，使用 `pipx reinstall business-graph-mcp`。

## 2. 在 Claude Code 註冊

```powershell
claude mcp add business-graph --scope user -- business-graph-mcp
```

- Server key 是 `business-graph`，執行檔與套件名都是 `business-graph-mcp`。
- 若執行檔不在 Claude Code 的 PATH，請改用絕對路徑。
- `--scope user` 讓所有專案都能使用；若團隊確實要共享專案設定，再選 project scope。

重開 Claude Code 後以 `/mcp` 確認 `business-graph` 顯示為 connected。

可先直接做啟動 smoke test：

```powershell
"" | business-graph-mcp
```

stderr 出現 `business-graph MCP server 啟動` 表示環境檢查通過。這是 stdio server，沒有輸入時會等待；空輸入讓它完成啟動後正常結束。

## 3. 其他 MCP client

任何支援 stdio transport 的 client 都可使用同一組名稱：

```json
{
  "mcpServers": {
    "business-graph": {
      "command": "business-graph-mcp",
      "args": []
    }
  }
}
```

Windows 絕對路徑範例：

```json
{
  "mcpServers": {
    "business-graph": {
      "command": "C:\\Users\\<你的帳號>\\.local\\bin\\business-graph-mcp.exe",
      "args": []
    }
  }
}
```

## 4. 已有 llm-wiki-mcp 的使用者

先停止使用舊 server 並移除舊註冊，再安裝與註冊新名稱。Claude Code user scope 的典型流程是：

```powershell
claude mcp remove llm-wiki --scope user
uv tool install --from "git+https://github.com/zokmi/business-graph.git" business-graph-mcp
business-graph-mcp migrate <repo>
claude mcp add business-graph --scope user -- business-graph-mcp
```

在已啟用 `business-graph-mcp` 的 Python 環境或本專案 checkout 中，遷移也可用計畫指定的 module 形式執行：

```powershell
python -m business_graph_mcp migrate <repo>
# 本專案 checkout：uv run python -m business_graph_mcp migrate <repo>
```

遷移是一次性的保守複製：

- `.wiki/pages/*.md` 複製到 `.bgraph/nodes/`，所有可解析節點暫定 `type: rule`。
- `wiki.toml` 只在目標不存在時複製成 `bgraph.toml`。
- 原 `.wiki/` 保留，已存在的 `.bgraph` 檔案不覆寫；失敗或衝突項目列為跳過。
- 只處理 `pages/` 第一層 Markdown；舊 `index.db` 不遷移，第一次使用時會建立新索引。
- 遷移不會更新 MCP client 設定或 `.gitignore`，也不會判斷正確的節點型別、關係或 code 錨點。完成後必須逐頁複查。
- 目前遷移實作需以硬連結原子建立每個新檔；不支援硬連結的檔案系統會明確失敗，原 `.wiki/` 與已完成項目仍保留。

移除舊 `.gitignore` 項目並加入：

```gitignore
.bgraph/index.db
.bgraph/index.db-wal
.bgraph/index.db-shm
```

確認遷移結果並提交 `.bgraph/nodes/` 與需要的 `.bgraph/bgraph.toml` 後，才刪除舊 `.wiki/`；遷移程式本身不會刪除它。

## 5. `project_path` 與 monorepo

三個工具都接受選用的 `project_path`。省略時以 server 行程工作目錄為起點；指定時，工具由該路徑向上尋找最近的 `.bgraph/`。`project_path` 可以是目錄或其中任一檔案。

```text
business_explore(
  query="多站台設定怎麼運作",
  project_path="/path/to/your-project"
)
```

找不到 `.bgraph/` 時，`business_explore` 與 `business_lint` 回傳「尚未建立」訊息；第一次 `business_write` 會在該 repo 建立 `.bgraph/nodes/`。在 monorepo 中應明確指定子專案路徑，以免查到另一個子專案的知識圖。

## 6. codegraph（選用）

若 repo 根目錄有 `.codegraph/codegraph.db`，`business_explore` 會唯讀解析節點 `code` 欄位中的符號，顯示實際檔案與行號。沒有索引、不相容或損壞時會降級成「未解析」，知識查詢仍正常；不需要為了使用 business-graph 額外安裝 codegraph。

## 7. 更新

```powershell
uv tool upgrade business-graph-mcp
```

鎖定特定版本時重新執行帶 `@business-graph-mcp-v<版本>` 的安裝命令。更新磁碟上的套件後，須在 MCP client 重新連線或重開 session，既有 server 行程不會熱重載。

## 8. 排錯

| 症狀 | 處理 |
|---|---|
| 啟動顯示 SQLite 未啟用 FTS5 | 改用 `uv` 管理的 Python 或 python.org 官方版本；自行編譯 SQLite 時啟用 FTS5。 |
| `/mcp` 看不到 `business-graph` | 重開 client，並以 `claude mcp list` 確認 key 與 scope。若 PATH 不同，改用執行檔絕對路徑。 |
| `business_explore` 回覆尚未建立知識圖 | 正常初始狀態；第一次 `business_write` 會建立。 |
| `business_write` 回覆與現況不符 | 節點在你讀取後已改變；重新 `business_explore`，以最新全文與 `base_hash` 重做更新。 |
| code 錨點顯示「未解析」 | `.codegraph/codegraph.db` 不存在、不相容或查無符號。這是選用功能的降級，不影響節點、路徑與影響半徑。 |
| `business_lint` 沒有未提交變更區段 | 該檢查依賴系統 `git`，且 `.bgraph/` 必須位於 Git repo；條件不符時會略過。 |
| 手動改節點後仍讀到舊內容 | 通常是 `project_path` 指到另一個 `.bgraph/`；明確指定正確子專案路徑。 |

完整資料模型與工具回應見 [README.md](README.md)。
