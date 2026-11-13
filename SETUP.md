# business-graph-mcp 安裝與設定

本機 stdio 模式由 MCP client 在需要時啟動；遠端 Streamable HTTP 模式為常駐服務，第 9 節說明部署與還原。

### 在 Windows 共用一個常駐 MCP 行程

使用 Node.js 22 以上與含工作區路由功能的版本。在此專案目錄執行以下 PowerShell 指令，並將路徑換成實際位置。憑證檔使用第 9 節的格式，`BUSINESS_GRAPH_MCP_TOKEN` 必須對應其中一筆憑證的 SHA256。

```powershell
$launcher = (Resolve-Path .\scripts\start-local-mcp.mjs).Path
$python = (Resolve-Path .\.venv\Scripts\pythonw.exe).Path
node $launcher 18763 'C:\mcp' $python -m business_graph_mcp serve `
  --host 127.0.0.1 --port 18763 --public-url http://127.0.0.1:18763 `
  --local-project-root 'C:\repos' `
  --credentials-file 'C:\mcp\credentials.toml' --audit-file 'C:\mcp\audit.jsonl'
```

啟動器等服務通過 MCP 驗證後退出，Python 在背景繼續執行，紀錄寫到狀態目錄的 `server.log`。同時執行多次時，各啟動器會共用同一個連接埠上的服務。

讓各 MCP client 連到 `http://127.0.0.1:18763/mcp` 並帶上 Bearer token。Codex 使用同一個 `[mcp_servers.business-graph]` 區段，將原本的 `command` 換成 HTTP URL 設定，並以環境變數提供 token。Windows 重開機後可先執行上方的啟動命令，再開啟 Codex。

```toml
[mcp_servers.business-graph]
url = "http://127.0.0.1:18763/mcp"
bearer_token_env_var = "BUSINESS_GRAPH_MCP_TOKEN"
```

Claude Code 可將使用者層級的 `business-graph` 設為 `type: http`，`url` 指向相同端點，並將 `headersHelper` 設為上方啟動命令，在腳本路徑後、連接埠前加上 `--headers`。Helper 會在 Claude Code 連線時啟動或重用服務，再將授權標頭交給 Claude Code。命令內使用絕對路徑，含空白的路徑需加雙引號。

各 agent 傳入目前專案的絕對 `project_path`（Git 專案目錄或其中的既有檔案）。服務從該路徑找最近的 Git 根目錄，並使用該專案的 `.bgraph`。缺少 `.bgraph` 時，agent 先詢問使用者。取得同意後，具寫入權限的 agent 可呼叫 `business_init`，後續使用同一份 `.bgraph`。可重複指定 `--local-project-root` 以涵蓋多個存放 Git 專案的目錄。此模式使用 loopback，適用於信任該專案目錄與本機 MCP client 的情境。

`--workspace` 可選用來設定省略 `project_path` 時使用的全域預設。若未設定，工具會要求提供 `project_path`，因為 HTTP 服務無法從連線得知 agent 的工作目錄。若使用者指定某專案要共用另一份知識庫，可用 `--project-workspace` 登記該 `.bgraph`，再用 `--project-binding` 保存專案到知識庫的對應。各知識庫各有自己的索引和鎖。`--codegraph-db` 套用到 `--workspace` 指定的知識庫。

## 1. Claude Code 一條指令安裝

先確認已安裝 Claude Code 與 [uv](https://docs.astral.sh/uv/)（含 `uvx`），且 Git 可以連線到 GitHub。執行：

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
| `uvx` 無法取得套件 | 確認 Git 可以連線到 GitHub，且 Claude Code 行程的網路設定允許下載。 |
| `business_explore` 回覆尚未建立知識圖 | 正常初始狀態；第一次 `business_write` 會建立。 |
| `business_write` 回覆與現況不符 | 節點在你讀取後已改變；重新 `business_explore`，以最新全文與 `base_hash` 重做更新。 |
| code 錨點顯示「未解析」 | `.codegraph/codegraph.db` 不存在、不相容或查無符號。這是選用功能的降級，不影響節點、路徑與影響半徑。 |
| `business_lint` 沒有未提交變更區段 | 該檢查依賴系統 `git`，且 `.bgraph/` 必須位於 Git repo；條件不符時會略過。 |
| 手動改節點後仍讀到舊內容 | 通常是 `project_path` 指到另一個 `.bgraph/`；明確指定正確子專案路徑。 |

完整資料模型與工具回應見 [README.md](README.md)。

## 9. 遠端共用知識庫

遠端服務一個實例綁定一份知識庫；工具不接受 `project_path`，也不往祖先目錄尋找資料。`--workspace` 指向 `.bgraph` 本身，裡面直接是 `nodes/`，不是專案根目錄。`bgraph.toml` 可省略，沿用 README 的預設值。遠端預設不使用 codegraph；僅管理者可用 `--codegraph-db` 指定服務端既有唯讀索引，不會查使用者電腦或服務程式的祖先工作區。

`read` 可呼叫 explore、lint；`write` 另可呼叫 write。read 仍可能更新衍生索引與查詢統計，資料卷必須可寫。只支援本機持久化磁碟上的單一服務實例；不要多主機共用 SQLite、使用 NFS，或讓 Obsidian／同步程式並行改檔。寫入以單節點為一致性單位，多頁更新須逐頁檢查結果。

本節命令供 Linux Docker 主機，在專案根目錄以 POSIX shell 執行。當前 Windows 開發主機沒有 Docker／Podman，因此未執行映像建置、Linux 並行測試、TLS smoke、容器重建與新卷還原；Python 還原測試與設定靜態檢查不能取代這些部署驗收。主機與 Codespaces 免費方案限制見[主機評估](docs/hosting-options.md)，本次沒有建立雲端資源。

### 個別憑證

每個人或 agent 產生獨立的至少 256-bit 高熵 token；不要使用密碼、共用 token 或範例值。部署主機先以受控方式建立專案外的 `credentials.toml`，格式參考 [credentials.example.toml](deploy/credentials.example.toml)。範例佔位值刻意無效，直接使用會拒絕啟動。

在可信任的本機終端執行以下命令；輸出含秘密，應立即放入呼叫端的秘密儲存，不要貼到聊天、issue、Git 或日誌：

```sh
python - <<'PY'
import hashlib
import secrets
value = secrets.token_urlsafe(32)
print('client token:', value)
print('server token_sha256:', hashlib.sha256(value.encode('utf-8')).hexdigest())
PY
```

憑證檔只填 `subject`、`role`（read 或 write）、`token_sha256`（64 位十六進位 SHA256）；不要把明文 token 放入檔案。每個 subject 與摘要都須唯一。憑證、audit 及其衍生 `.lock`、知識節點、設定、索引與 codegraph 不得佔用相同路徑或硬連結。

每次 HTTP 請求重新讀取檔案。移除該筆可撤銷，換摘要可輪替，改 role 可調整權限；下一請求生效，已在執行的操作不追溯取消。格式／讀檔失敗時拒絕授權。bind mount 檔案若以 rename 原子替換，容器可能仍指向舊 inode；更新後用 `docker compose -f deploy/compose.yaml up -d --force-recreate app` 重新掛載，再驗證舊 token 回 401。一般原地編輯可直接被下一請求讀取，但編輯期間可能暫時拒絕授權。

### Docker 與 HTTPS

部署者提供已指向主機的真實 DNS 名稱，開放代理的 80／443 埠；Caddy 申請並保存 TLS 憑證。`BGRAPH_DOMAIN` 只能填主機名稱，不能包含 scheme、路徑、萬用字元、查詢或 fragment。應用使用明確的 `https://DOMAIN/mcp`、Host=`DOMAIN` 與 Origin=`https://DOMAIN`。缺環境變數、缺憑證檔、無效憑證或網路設定都會明確失敗；DNS／ACME 可達性另由代理日誌驗證。

```sh
export BGRAPH_DOMAIN=knowledge.your-domain.example
export BGRAPH_CREDENTIALS_FILE=/absolute/private/credentials.toml
export BGRAPH_DATA_VOLUME=business_graph_data
# 替換網域及路徑；憑證須可由容器 UID 10001 讀取。
sudo chown 10001:10001 "$BGRAPH_CREDENTIALS_FILE"
sudo chmod 0400 "$BGRAPH_CREDENTIALS_FILE"
docker compose -f deploy/compose.yaml config
docker compose -f deploy/compose.yaml build app
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml ps
curl --fail "https://$BGRAPH_DOMAIN/healthz"
```

應用以 UID/GID `10001:10001` 執行，只在容器網路監聽 `0.0.0.0:8000`，沒有發佈主機 8000 埠。只有 Caddy 對外；它保留原 Host／Authorization／Origin，不另開 access log。應用不信任任意 forwarded header、不記請求 URL。`/healthz` 不需 token，但同樣驗證 Host／Origin，只回 `{"status":"ok"}`，是存活檢查。容器內健康檢查雖連 loopback，仍帶公開 Host。若瀏覽器 client 的 Origin 與服務不同，須由管理者另外加明確的 `--allowed-origin https://client.example`；不要加 `*`。代理不代替應用授權，也不承諾任意瀏覽器 CORS client 可用。

命名資料卷掛在 `/data`，存放 `/data/.bgraph/nodes`、選用設定、衍生索引與 `/data/audit.jsonl`。audit 鎖是 `/data/audit.jsonl.lock`，與知識庫的 `workspace.lock` 分開。映像中的 `/data` 已給 UID 10001，Docker 首次掛上空命名卷時沿用該目錄權限。啟動的 `--init` 只建立不存在／空白的 `.bgraph`；已有 `nodes` 時保留內容與設定；未知非空 `.bgraph` 直接拒絕，不清空或猜測搬遷。

若改用 bind mount，先在主機建立專用資料目錄，將 compose 的 `graph_data:/data` 改為該絕對路徑。**既有專案應掛載 `.bgraph` 的父目錄**，使容器內成為 `/data/.bgraph/nodes`；不要把含節點的 `.bgraph` 直接掛成 `/data`，以免另外初始化新圖。先停掉所有寫入者、備份，再檢查既有 owner／ACL，以部署者核可的方式授予 UID/GID 10001 對資料目錄與節點／設定／audit 的讀寫及目錄搜尋權限。新空目錄可用 `sudo install -d -o 10001 -g 10001 -m 0700 /absolute/new-bgraph-data`；不要對未知既有目錄直接做遞迴 chown。既有 root-owned 命名卷亦須先確認內容並修正權限，不能假設映像會覆寫舊卷的 ownership。憑證始終獨立唯讀掛在 `/run/secrets/credentials.toml`。

### 客戶端與錯誤判讀

僅適用支援 Streamable HTTP 與自訂 Authorization header 的客戶端。以下是通用設定形狀，欄位以各 client 文件為準，`${BGRAPH_TOKEN}` 須由 client 的安全環境變數功能展開，不能假設所有 client 都會代換：

```json
{
  "mcpServers": {
    "business-graph-remote": {
      "type": "http",
      "url": "https://knowledge.your-domain.example/mcp",
      "headers": {"Authorization": "Bearer ${BGRAPH_TOKEN}"}
    }
  }
}
```

這是靜態 Bearer 驗證，沒有 OAuth 授權伺服器、discovery 或互動登入；要求 OAuth 的 client 需要另做整合。遠端的三個工具參數與 README 相同，但完全移除 `project_path`，傳入會拒絕。工具清單不是授權邊界，read 即使直接呼叫 write 仍被拒絕。

- 新增省略 `base_hash`；更新先 explore，將最新 hash 與整合後的全文交回。衝突時重讀整合，不盲目覆寫。
- `indexed: false` 與新 `base_hash` 代表 Markdown 已保存、衍生索引同步／驗證失敗；下次呼叫會重試索引，保留新 hash，不拿舊 hash 重送。
- `audit_warning` 代表業務操作已完成，audit 未能確認保存；檢查磁碟／權限與可能的部分 JSONL 紀錄，不重送業務寫入。
- 網路斷線／逾時可能發生在保存後，先 explore 查現況。單次寫入完成不表示 Git 已提交或推送；本版沒有自動 Git 歷史。

代理可用後，以專案既有 Python 環境（`uv sync`）執行以下真實 HTTPS SDK smoke。先在私密終端安全設定 `BGRAPH_TOKEN` 為 read token；不把 token 放在命令列參數。此 smoke 不改知識節點，但可能更新索引／統計並追加 audit：

```sh
uv run python - <<'PY'
import asyncio
import os
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    url = 'https://' + os.environ['BGRAPH_DOMAIN']
    async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + os.environ['BGRAPH_TOKEN']}) as http:
        assert (await http.get(url + '/healthz')).json() == {'status': 'ok'}
        async with streamable_http_client(url + '/mcp', http_client=http) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                assert {t.name for t in (await session.list_tools()).tools} == {'business_explore', 'business_write', 'business_lint'}
                result = await session.call_tool('business_lint', {})
                assert not result.is_error
    async with httpx2.AsyncClient() as unauthenticated:
        assert (await unauthenticated.post(url + '/mcp', json={})).status_code == 401
    print('HTTPS MCP smoke passed')

asyncio.run(main())
PY
```

另外在獨立驗收資料卷以 write token 新增頁、explore 取 hash、更新頁，測 read 拒寫、撤銷後 401、錯 Host／Origin 拒絕。可用以下 Linux 命令重跑已存在的真實 HTTP、跨行程鎖與還原驗收，環境置於容器 `/tmp`，避免使用主機 Windows `.venv`：

```sh
docker run --rm --mount "type=bind,source=$PWD,target=/app" --workdir /app --env UV_PROJECT_ENVIRONMENT=/tmp/bgraph-venv ghcr.io/astral-sh/uv:python3.11-bookworm-slim uv run --group dev pytest tests/test_remote_http.py tests/test_remote_concurrency.py tests/test_audit.py tests/test_locking.py tests/test_remote_backup.py -q --basetemp=/tmp/bgraph-validation
```

### 停機備份與新卷還原

停止唯一 app，並確認沒有其他 server、手動編輯或同步寫入者。不要在執行中直接複製 SQLite，也不要以 `/healthz` 當備份成功證據。備份白名單為 `.bgraph/nodes/`、存在時的 `.bgraph/bgraph.toml` 與 `audit.jsonl`；不複製 index.db／sidecars、workspace.lock、audit.jsonl.lock。還原會失去衍生查詢統計。憑證及 Caddy TLS 資料用各自安全備份流程，不放進知識備份。

下面先在主機既有備份父目錄建立全新、絕對路徑的備份目錄，已有同名目錄時 `mkdir` 會失敗。請改用實際路徑。所有命令在同一 shell 執行；`set -eu` 確保失敗就停下。服務只有在備份成功後才重新啟動：

```sh
set -eu
export BGRAPH_BACKUP=/absolute/backups/bgraph-20261022-001
mkdir "$BGRAPH_BACKUP"
sudo chown 10001:10001 "$BGRAPH_BACKUP"
sudo chmod 0700 "$BGRAPH_BACKUP"
docker compose -f deploy/compose.yaml stop app
docker compose -f deploy/compose.yaml run --rm --no-deps -T --entrypoint python --volume "$BGRAPH_BACKUP:/backup" app - <<'PY'
from pathlib import Path
import shutil
source, destination = Path('/data'), Path('/backup/snapshot')
destination.mkdir(exist_ok=True)
if any(destination.iterdir()):
    raise FileExistsError('備份或還原目的地必須為空。')
(destination / '.bgraph').mkdir()
shutil.copytree(source / '.bgraph' / 'nodes', destination / '.bgraph' / 'nodes')
if (source / '.bgraph' / 'bgraph.toml').exists():
    shutil.copy2(source / '.bgraph' / 'bgraph.toml', destination / '.bgraph' / 'bgraph.toml')
if (source / 'audit.jsonl').exists():
    shutil.copy2(source / 'audit.jsonl', destination / 'audit.jsonl')
print('停止寫入後的白名單備份完成。')
PY
docker compose -f deploy/compose.yaml start app
```

將備份另存於不同磁碟／主機，保護 audit 的主體與節點識別。驗證實際節點數、全文與 SHA256，確認原本自訂設定及 audit 也存在；能讀到一個頁面不代表全部備份完整。原始資料卷與備份都保留。還原時再次停掉 app，明確改用**從未存在的新卷名稱**；不要使用 `down -v` 或覆寫唯一舊卷：

```sh
set -eu
docker compose -f deploy/compose.yaml stop app
export BGRAPH_OLD_DATA_VOLUME="$BGRAPH_DATA_VOLUME"
export BGRAPH_DATA_VOLUME=business_graph_recovery_20261022_001
if docker volume inspect "$BGRAPH_DATA_VOLUME" >/dev/null 2>&1; then
    echo '還原卷已存在，請換一個全新名稱。' >&2
    exit 1
fi
docker compose -f deploy/compose.yaml run --rm --no-deps -T --entrypoint python --volume "$BGRAPH_BACKUP:/backup:ro" app - <<'PY'
from pathlib import Path
import shutil
source, destination = Path('/backup/snapshot'), Path('/data')
destination.mkdir(exist_ok=True)
if any(destination.iterdir()):
    raise FileExistsError('備份或還原目的地必須為空。')
(destination / '.bgraph').mkdir()
shutil.copytree(source / '.bgraph' / 'nodes', destination / '.bgraph' / 'nodes')
if (source / '.bgraph' / 'bgraph.toml').exists():
    shutil.copy2(source / '.bgraph' / 'bgraph.toml', destination / '.bgraph' / 'bgraph.toml')
if (source / 'audit.jsonl').exists():
    shutil.copy2(source / 'audit.jsonl', destination / 'audit.jsonl')
assert not (destination / '.bgraph' / 'index.db').exists()
from business_graph_mcp.engine import open_workspace_session
from business_graph_mcp.remote_config import validate_remote_workspace
ws = validate_remote_workspace(destination / '.bgraph')
with open_workspace_session(ws) as session:
    assert not session.sync.failed, session.sync.failed
    print('nodes:', session.conn.execute('SELECT count(*) FROM nodes').fetchone()[0])
    print('edges:', [tuple(row) for row in session.conn.execute('SELECT source_slug, edge_type, target_raw, target_slug FROM edges ORDER BY source_slug, edge_type, target_raw')])
print('新卷還原與索引重建完成；仍須核對備份的數量、全文、hash 與關係。')
PY
docker compose -f deploy/compose.yaml up -d --force-recreate app
```

還原先在新卷由 Markdown 重建 index.db，再核對節點數、每個檔案全文／hash、設定與 audit、關係及 explore/lint。`tests/test_remote_backup.py` 用非空真實知識庫驗證同一複製白名單與原資料完全不變，也驗證自訂設定不能遺漏。若失敗，保持兩份資料並分析；需要回舊卷時將 `BGRAPH_DATA_VOLUME` 改回記錄的舊名稱，再重建 app，避免同時執行兩個寫入實例。

最後在獨立驗收卷記錄既有頁全文與 hash，執行 `docker compose -f deploy/compose.yaml up -d --force-recreate app`，再次透過 HTTPS explore 核對；再走一次上述停機備份、新卷還原。這些容器／TLS 驗收在本次主機上仍未執行，不能因 Python 測試通過而標記完成。
