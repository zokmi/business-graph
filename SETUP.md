# business-graph-mcp 安裝與設定

`business-graph-mcp` 是 stdio MCP server，由 MCP client 在需要時啟動，不需另設常駐服務。

## 1. Claude Code 一條指令安裝

先確認已安裝 Claude Code 與 [uv](https://docs.astral.sh/uv/)（含 `uvx`），且 Git 可讀取此私有 GitHub 倉庫。執行：

```powershell
claude mcp add business-graph --scope user -- uvx --from git+https://github.com/zokmi/business-graph.git business-graph-mcp
```

- `claude mcp add` 註冊 server；Claude Code 第一次啟動它時，`uvx` 會下載並建立套件環境，之後使用本機快取。
- `--scope user` 讓所有專案都能使用。若團隊確實要共享設定，再選 project scope。
- 未指定版本時，`uvx` 會確認 Git 倉庫的 HEAD；快取可減少後續啟動時間，但啟動仍可能受網路或 GitHub 權限影響。

重開 Claude Code 後以 `/mcp` 或 `claude mcp get business-graph` 確認連線。實測首次下載約 23 秒、快取後約 4 秒；首次啟動若逾時，可再試一次，或改用下列固定安裝方式。

## 2. 固定安裝於本機（選用）

若要避免每次啟動時檢查 Git 倉庫，可明示安裝套件，再讓 Claude Code 執行固定的本機檔案：

```powershell
uv tool install --from "git+https://github.com/zokmi/business-graph.git" business-graph-mcp
claude mcp add business-graph --scope user -- business-graph-mcp
```

若已註冊同名 server，先用 `claude mcp remove business-graph --scope user` 移除舊設定，再新增。本機執行檔若不在 Claude Code 的 PATH，請在 `claude mcp add` 中改用絕對路徑；Windows 通常位於 `%USERPROFILE%\.local\bin\business-graph-mcp.exe`。固定安裝的版本可用 `uv tool upgrade business-graph-mcp` 更新。

## 3. 其他 MCP client

任何支援 stdio transport 的 client 都可使用同一組名稱：

```json
{
  "mcpServers": {
    "business-graph": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/zokmi/business-graph.git", "business-graph-mcp"]
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

先停止使用舊 server 並移除舊註冊，再註冊新名稱。Claude Code user scope 的典型流程是：

```powershell
claude mcp remove llm-wiki --scope user
claude mcp add business-graph --scope user -- uvx --from git+https://github.com/zokmi/business-graph.git business-graph-mcp
uvx --from git+https://github.com/zokmi/business-graph.git business-graph-mcp migrate <repo>
```

在已啟用 `business-graph-mcp` 的 Python 環境或本專案 checkout 中，遷移也可直接以 Python module 執行：

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
.bgraph/workspace.lock
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

預設的 `uvx` 指令指向 Git 倉庫 HEAD；重新連線時會檢查版本。若使用第 2 節的固定安裝方式，執行 `uv tool upgrade business-graph-mcp`。無論哪種方式，既有 server 行程都不會熱重載，更新後須重開 Claude Code 或重新連線。

## 8. 排錯

| 症狀 | 處理 |
|---|---|
| 啟動顯示 SQLite 未啟用 FTS5 | 改用 `uv` 管理的 Python 或 python.org 官方版本；自行編譯 SQLite 時啟用 FTS5。 |
| `/mcp` 看不到 `business-graph` | 重開 client，並以 `claude mcp list` 確認 key 與 scope；另確認 Claude Code 能從 PATH 找到 `uvx`。 |
| 首次 `uvx` 連線逾時 | 首次需要從 GitHub 下載並建立環境，重試連線；若持續逾時，使用第 2 節的固定安裝方式。 |
| `uvx` 無法取得套件 | 確認 Git 可以讀取此私有倉庫，且 Claude Code 行程能使用相同的 GitHub 憑證。 |
| `business_explore` 回覆尚未建立知識圖 | 正常初始狀態；第一次 `business_write` 會建立。 |
| `business_write` 回覆與現況不符 | 節點在你讀取後已改變；重新 `business_explore`，以最新全文與 `base_hash` 重做更新。 |
| code 錨點顯示「未解析」 | `.codegraph/codegraph.db` 不存在、不相容或查無符號。這是選用功能的降級，不影響節點、路徑與影響半徑。 |
| `business_lint` 沒有未提交變更區段 | 該檢查依賴系統 `git`，且 `.bgraph/` 必須位於 Git repo；條件不符時會略過。 |
| 手動改節點後仍讀到舊內容 | 通常是 `project_path` 指到另一個 `.bgraph/`；明確指定正確子專案路徑。 |

完整資料模型與工具回應見 [README.md](README.md)。
