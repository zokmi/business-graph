# Remote Knowledge Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供多人與 agent 共用單一知識庫的 HTTP MCP，保留本機 stdio 行為。

**Architecture:** MCPServer 的 Streamable HTTP 入口使用獨立憑證驗證與逐次工具授權。工具核心抽出後共用固定 Workspace 的 Session；Markdown 與 SQLite 留在單機持久化資料卷。

**Tech Stack:** Python >=3.11、現有 mcp>=2.0,<3、SQLite FTS5、ASGI、Docker Compose、Caddy。HTTP 啟動所用 uvicorn 與整合測試所用 httpx 列為直接相依，不依賴 SDK 的間接安裝保證。

**Spec:** `docs/superpowers/specs/2026-10-22-remote-knowledge-design.md`

## Global Constraints

- 第一版一個服務綁定一份知識庫；單一服務實例、本機持久化磁碟。
- 本機 stdio 工具介面與目錄搜尋規則保持相容。
- 遠端工具不接受 project_path；不能自動落回其他目錄。
- read 可使用 explore、lint；write 另可使用 write；讀取可更新衍生索引與統計。
- 更新須比對 base_hash；單節點一致性，不宣稱多頁原子交易。
- 不清除現存資料，不存放憑證或知識全文於操作日誌。
- 不新增 Obsidian 同步、OAuth 授權伺服器或放寬 issue 收錄政策。
- 程式註解、docstring、錯誤訊息使用繁體中文；知識內容仍以英文為預設。

## Review Focus

1. 讀取者直接呼叫 write 或重用其他主體的 MCP session，仍依本次憑證拒絕寫入（Task 2、3）。
2. 未知 project_path、符號連結與錯誤工作目錄不可轉移服務的知識庫（Task 1、3）。
3. 網路中斷後寫入可能已完成，重送不可默默覆寫，索引失敗須保留 indexed=false（Task 4）。
4. 超大分塊請求與不可信 Origin／Host 在進入引擎前被拒絕（Task 3）。
5. 磁碟寫入成功但操作紀錄失敗，回應須揭露已保存結果，不能讓 agent 誤判為未寫入（Task 4）。

## Task 1: 固定 Workspace 與可共用工具核心

**Files:** Modify `engine.py`、`tools/explore.py`、`tools/write.py`、`tools/lint.py`、`lint.py`（均位於 `src/business_graph_mcp/`）；Create `src/business_graph_mcp/service.py`；Test `tests/test_bound_service.py`。

**Interfaces:**
- `open_workspace_session(ws: Workspace) -> Iterator[Session]`：contextmanager，持鎖、同步、關閉連線；`open_session` 定位後委派此函式。
- `GraphService(workspace: Workspace, codegraph_db: Path | None)`：`explore(query: str) -> dict[str, str]`、`write(*, title: str, node_type: str, content: str, status: str, aliases: tuple[str, ...] = (), code: tuple[str, ...] = (), base_hash: str | None = None) -> dict[str, object]`、`lint() -> dict[str, str]`；同步方法，HTTP 包裝以 asyncio.to_thread 呼叫。
- codegraph 查詢核心接受明確的 `Path | None`；本機包裝沿用 find_codegraph_db，遠端預設 None，禁止再次向上搜尋。

- [ ] 寫測試：兩個圖 A/B 與不同 cwd 下，綁定 A 的 service 只讀寫 A；codegraph=None 即使祖先有索引也不解析；原 stdio 工具結果保持相容。
- [ ] 執行 `uv run pytest tests/test_bound_service.py -q`，確認新介面尚不存在而失敗。
- [ ] 抽出工具核心與 session，不複製原有查詢或寫入算法；本機維持既有例外與回應語意。
- [ ] 執行 `uv run pytest tests/test_bound_service.py tests/test_explore_tool.py tests/test_write_tool.py tests/test_lint.py -q`，全部通過。
- [ ] 提交本任務檔案，訊息 `refactor: share graph operations with a bound workspace`。

## Task 2: 憑證、權限與遠端設定

**Files:** Create `src/business_graph_mcp/remote_config.py`、`remote_auth.py`；Test `tests/test_remote_config.py`、`tests/test_remote_auth.py`。

**Interfaces:**
- `RemoteSettings` frozen dataclass：workspace: Path、credentials_file: Path、audit_file: Path、public_url: str、host: str、port: int、allowed_hosts: tuple[str, ...]、allowed_origins: tuple[str, ...]、max_request_bytes: int、codegraph_db: Path | None。
- `Principal(subject: str, role: Literal['read', 'write'])`。
- `FileTokenVerifier.verify_token(token: str) -> AccessToken | None`，實作已安裝 SDK 的 TokenVerifier，主體對應 client_id，權限對應 scopes。
- `require_tool_access(principal: Principal, tool_name: str) -> None`；未知工具拒絕。每次驗證重新讀取憑證檔，使刪除後下一請求失效。

- [ ] 寫測試：缺設定、相對 workspace、無效角色、重複憑證、無效 public_url 啟動失敗；read 可讀不可寫；write 可讀寫；缺／錯／移除 token 驗證失敗。
- [ ] 執行 `uv run pytest tests/test_remote_config.py tests/test_remote_auth.py -q`，確認缺少介面而失敗。
- [ ] 實作嚴格 TOML 憑證格式 `[[principals]] subject, role, token_sha256`，只存 SHA256 摘要，使用高熵 token 與常數時間比對；不記錄輸入 token。解析失敗 fail closed，不沿用舊權限。
- [ ] workspace 是 `.bgraph` 目錄本身；啟動時須已有 nodes 與可讀設定，不能自動尋找祖先。明確 `--init` 才能初始化空目錄；非空未知目錄拒絕，不改動現存檔案。管理者提供的 symlink 路徑啟動時解析成固定目標。
- [ ] 執行上述兩個測試檔，全部通過。
- [ ] 提交，訊息 `feat: configure remote workspace and individual access credentials`。

## Task 3: HTTP MCP、CLI 與逐次授權

**Files:** Create `src/business_graph_mcp/remote.py`；Modify `server.py`、`__main__.py`、`pyproject.toml`、`uv.lock`；Test `tests/test_remote_http.py`、`tests/test_cli.py`。

**Interfaces:** `create_remote_app(settings: RemoteSettings) -> ASGIApp`；`run_remote(settings: RemoteSettings) -> None`。保留 `create_server()` 本機入口。CLI 新增 `serve --workspace ... --credentials-file ... --audit-file ... --public-url ... [--host 127.0.0.1] [--port 8000] [--init] [--codegraph-db ...]`；原 migrate 與無參數啟動相容。

- [ ] 寫真實 HTTP 測試：SDK client 初始化、工具列表與讀寫；遠端三工具 schema 無 project_path，傳入額外路徑也必須拒絕；缺憑證 401；read 直接呼叫 write 被拒，且 nodes hash 不變；session 不成為繞過當次驗證的授權憑證。
- [ ] 寫請求邊界測試：不可信 Origin／Host 拒絕；缺 Origin 的 CLI 客戶端可用；Content-Length 與 chunked 超限都回 413；`GET /healthz` 僅含存活狀態。
- [ ] 執行 `uv run pytest tests/test_remote_http.py tests/test_cli.py -q`，確認新入口不存在而失敗。
- [ ] 使用 MCPServer.streamable_http_app 與 SDK TokenVerifier；採 stateless HTTP 避免跨主體 session 狀態混用。在工具包裝從 SDK request context 取得當次 AccessToken 並逐次授權，之後才進入執行緒與引擎。不要依賴只隱藏工具列表達成權限控制。
- [ ] 新增 ASGI body limit（預設 1048576 bytes，完整計數後才交給 MCP）、Host／Origin allowlist；只信任明確部署設定，不依任意 forwarded header 推導身分或公開 URL。
- [ ] 遠端 instructions 說明固定知識庫與唯讀主體無寫入權；不再要求傳入本機路徑。SDK 的 HTTP 安全設定與中介層按本機已安裝版本實際簽名實作。
- [ ] 執行上述測試與原工具測試，全部通過。
- [ ] 提交，訊息 `feat: expose authenticated streamable HTTP MCP`。

## Task 4: 並行寫入與操作追溯

**Files:** Create `src/business_graph_mcp/audit.py`；Modify `remote.py`；Test `tests/test_remote_concurrency.py`、`tests/test_audit.py`、`tests/test_locking.py`。

**Interfaces:** `AuditWriter(path: Path).append(event: Mapping[str, object]) -> None`；JSONL 僅記 timestamp、subject、tool、slug、before_hash、after_hash、outcome。寫入回應可附 `audit_warning`；索引失敗維持既有 `warning` 與 `indexed`。

- [ ] 寫測試：兩個獨立 HTTP client 以相同 hash 並行更新恰一成功；同名並行新增恰一成功；寫不同頁皆保存；跨執行緒與跨行程鎖競爭不丟資料；鎖逾時有明確結果。
- [ ] 寫故障測試：索引同步失敗仍能查驗 Markdown 已寫入；完成後 client 斷線再查可辨識現況；舊 hash 重送拒絕；audit 磁碟失敗時回傳已保存的新 hash 與 audit_warning。
- [ ] 寫 audit 測試：並行紀錄仍逐行可解析；不含 token、content 或 query；拒絕與衝突有結果，未完成寫入不虛構 after_hash。
- [ ] 執行 `uv run pytest tests/test_remote_concurrency.py tests/test_audit.py tests/test_locking.py -q`，確認新增行為先失敗。
- [ ] 實作 audit 與必要鎖修正，保留 hash 檢查和寫入在同一 workspace lock 內；操作日誌用獨立同步鎖，記錄失敗不可重送已執行的寫入。SDK 錯誤回應不得暴露其他節點或憑證。
- [ ] 執行上述測試；在 Linux 容器重跑並行測試，全部通過。
- [ ] 提交，訊息 `feat: audit remote operations and verify concurrent updates`。

## Task 5: Docker、備份還原與交付文件

**Files:** Create `Dockerfile`、`.dockerignore`、`deploy/compose.yaml`、`deploy/Caddyfile`、`deploy/credentials.example.toml`、`tests/test_remote_backup.py`；Modify `README.md`、`SETUP.md`。

**Interfaces:** 應用非 root 執行，資料卷掛載 `/data`，憑證唯讀掛載 `/run/secrets/credentials.toml`；應用內部 8000 埠，只透過代理公開。備份採 `docker compose stop app` 後複製 nodes/config/audit，還原到新卷並重建 index.db。

- [ ] 寫還原測試：備份既有非空知識庫至新目錄，忽略衍生索引，重建後節點全文／hash／關係一致，原資料不變；空資料庫初始化與既有資料重新啟動均保留內容。
- [ ] 執行 `uv run pytest tests/test_remote_backup.py -q`，先確認還原流程的驗收測試會辨識遺漏 nodes／設定的錯誤備份。
- [ ] 實作 Docker 與部署範例，無效網域或未提供憑證須明確失敗；.dockerignore 排除 .git、.venv、.bgraph、憑證與使用者資料。
- [ ] 文件說明產生個別高熵 token 與摘要、撤銷、read/write、HTTPS、允許的 Host/Origin、支援 Authorization header 的客戶端配置、單實例限制、停止後備份與新卷還原。區分知識保存成功、索引失敗與 audit 失敗。說明 git 歷史與 OAuth 互動登入未納入。
- [ ] README 連結既有 Karpathy 對照分析；不變更知識範本、型別或收錄政策。若需範本行為變更，另依使用者指示排程。
- [ ] 執行 `uv run pytest -q`、`uv run ruff check .`、`uv run mypy`、`uv build`；全部成功才宣稱通過。
- [ ] 執行 `docker compose -f deploy/compose.yaml config`、建置映像、HTTPS HTTP MCP smoke、重建容器保留資料及新卷還原；如主機無 Docker，明列未實測項目，不以設定靜態檢查取代。
- [ ] 提交，訊息 `docs: ship remote deployment and recovery workflow`。

## 執行與審閱

建議 Native（本對話直接實作），因工具核心、授權與 HTTP 包裝的介面相依緊密。實作前讀取 using-git-worktrees 與 executing-plans；按實際工作區權限隔離變更。任何 git 寫入受限時保留檔案成果並明列未提交，不能將既有未追蹤 CSV 納入提交。

本計畫已按 spec 自行核對；SDK 協定細節以已安裝套件原始碼與 HTTP 整合測試為依據，不猜測其他版本介面。完成計畫審閱並選擇執行方式後開始產品程式修改。
