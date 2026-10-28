# Node.js Wiki Server：多知識庫、OAuth 與設備管理

日期：2026-10-28。狀態：獨立 builder check 已完成，未發現影響規劃的未決問題；待使用者審閱，本文件不代表已核准實作。

## 1. 意圖與已確認需求

讓管理者在遠端主機以 CLI 安裝 Wiki server，登錄為常駐服務並開機自啟，再自行新增知識庫與設定權限。多人與 agent 共用知識，server 不呼叫 LLM。

使用者已確認：

- 完整移植為 Node.js（方案 A），執行時不依賴 Python。
- Linux 與 Windows 都支援 CLI 安裝常駐服務及開機自啟。
- server 使用一個對外 port，可部署於公網或內網。
- 管理者在主機 CLI 新增知識庫與設定權限。
- agent 可跨有權限的知識庫搜尋；每次寫入必須明確指定目標知識庫。
- 客戶端為 Codex、Claude Code；每台機器可額外安裝輕量客戶端 CLI。
- 採 OAuth 授權及設備登錄，記錄 MAC 等設備資訊，管理者可核准或撤銷設備權限。

「oath」依後續對話確認按 OAuth 處理。MAC 是客戶端回報的管理資訊，不等於可信硬體身分。使用者沒有要求以 MAC 取代憑證。

## 2. Approaches 與技術提案

| 方案 | 取捨 | 結果 |
|---|---|---|
| A：完整 Node.js | 安裝只需 Node.js；須移植現有搜尋、圖與寫入安全語意 | 使用者選擇 |
| B：Node.js 服務加 Python 引擎 | 較多重用，但仍需 Python 與雙程序維運 | 未選擇 |

以下是設計者提案，尚非使用者逐項指定，須隨本文件審閱：

- TypeScript 開發、發行編譯後 JavaScript，Node.js 24 LTS 為第一版基線；使用維護中的安全修補版本。
- 一個 npm 發行套件提供 `bgraph` CLI，包含 server、管理命令、客戶端及本機 MCP bridge。
- Linux 以 systemd；Windows 以固定版本、驗證雜湊的 WinSW 服務宿主呼叫 Node.js。
- 輕量客戶端作為 stdio MCP bridge；Codex／Claude Code 啟動它，bridge 以 Streamable HTTP 連到 server。設備金鑰、OAuth、更新 proof 由 bridge 統一處理，不要求原生 agent 支援設備 proof。
- OAuth Authorization Code + PKCE S256，採標準 DPoP 將 token 綁定設備金鑰；不自訂加密握手。
- 第一版 OAuth 資源擁有者核准透過主機管理 CLI 完成，不依賴外部 SSO，也不建立遠端管理帳密系統。瀏覽器頁面僅呈現授權請求與結果。
- 原有節點關係先維持庫內解析；跨庫能力為授權後的聯合搜尋與答案整合，不新增跨庫依賴邊。

## 3. 目標、非目標與失敗條件

目標：一次安裝、開機自啟；多庫可動態建立；主體及設備皆有明確權限；設備可獨立撤銷；知識可攜且升級不丟資料；能以 Codex、Claude Code 的實際客戶端驗證。

非目標：外部 IdP／SSO 整合、公開自由註冊即有權限、硬體 attestation、TPM／Secure Enclave 強制要求、網頁管理後台、叢集／多主寫入、網路磁碟共用 SQLite、Obsidian 同步共寫、跨庫原子寫入、自動部署到雲端。

不可接受：MAC 相同就放行；撤銷設備仍可 refresh；搜尋或錯誤訊息洩漏未授權庫；寫入猜測預設庫；為移植清除舊資料；安裝後整個 server 長期使用 root／LocalSystem；token、私鑰或知識全文寫入日誌。

## 4. 組件與唯一對外入口

server 為單一 Node.js 常駐程序；資料目錄一次綁定。知識庫新增由管理 CLI 更新控制資料庫，程序於每次請求讀取有效狀態，不要求因建立庫或撤銷權限重啟。

外部預設 `https://host:8443`，port 可設定。HTTPS listener 承載 OAuth metadata、authorize/token、設備登錄與狀態、`/mcp` 及最小 health endpoint。無第二個對外管理或 metrics listener。

預設直接由 Node.js 終結 TLS，管理者提供與公開網址相符的憑證及私鑰。公網使用受信任公開 CA；內網可用公司 CA，客戶端須配置信任。無憑證不得以公網明文 HTTP 啟動；不自動開防火牆。第一版不自動申請 ACME 憑證，不因憑證失效關閉驗證。管理者可更新憑證後 restart 服務。

主機管理 CLI 直接使用受 OS ACL 保護的本機控制資料庫及管理鎖，不另開管理 HTTP API。對控制資料的寫入經相同驗證及交易函式，不能執行任意 SQL。控制檔只允許服務帳號與系統管理者讀寫。

客戶端的 OAuth callback 是該使用者機器上暫時的 loopback listener，只綁 `127.0.0.1` 動態 port；它不是 server 的第二個對外 port。第一版登入需本機瀏覽器或手動開啟網址，無人值守 agent 在事先完成登錄後使用 refresh；不承諾純 headless 初次登入。

## 5. 常駐服務與 CLI

以下命令是公開介面意圖；實作計畫可將複數名詞統一，但不得改變權限與資料保留語意。

| CLI | 行為 |
|---|---|
| `bgraph server install --data-dir <absolute> --public-url <https-url> --port <n> --tls-cert <file> --tls-key <file>` | 檢查安裝與資料路徑、建立服務身分及 ACL、註冊自啟服務；失敗 rollback 只撤銷本次新建服務資源 |
| `bgraph server start/stop/restart/status` | 操作 OS 服務；status 顯示版本、資料目錄、監聽狀態與憑證到期，無 secrets |
| `bgraph server uninstall` | 停止與移除服務註冊，保留全部資料及備份 |
| `bgraph kb create/list/disable` | 建立具永久 ID 的知識庫；停用不刪除內容 |
| `bgraph kb import --source <legacy-.bgraph> --name <name>` | 複製驗證舊知識內容到新庫，不改來源 |
| `bgraph principal create/list/disable` | 建立管理者可辨識的使用者／服務主體；不是公網帳密註冊 |
| `bgraph device pending/list/show` | 查看待核准、已核准與撤銷的設備及授權請求 |
| `bgraph device approve <request-id> --principal <id> --confirm-code <code>` | 核對使用者／機器的配對碼，綁定主體與設備金鑰並核准對應 OAuth 請求 |
| `bgraph device revoke <device-id>` | 撤銷該設備與其全部 grant、access/refresh token family |
| `bgraph access grant/revoke --principal <id> --kb <id> --role read|write` | 主體的逐庫權限；write 包含 read |
| `bgraph device access grant/revoke --device <id> --kb <id> --role read|write` | 設備逐庫權限，不能超過其主體權限 |
| `bgraph client login --server <https-url> --device-name <name>` | 產生或使用設備金鑰、登錄設備及 OAuth 授權；顯示等待核准與配對資訊 |
| `bgraph client status/logout` | 本機狀態／撤銷本機 grant 並清除憑證；保留設備身分供再次核准 |
| `bgraph client mcp --server <https-url>` | 啟動 stdio bridge；不在 stdout 輸出日誌，也不自動彈出互動登入 |
| `bgraph client config --target codex|claude-code` | 產生引用固定 CLI 絕對路徑的設定，無 token；預設只輸出，不改使用者設定 |

Linux 第一版支援具 systemd 的 x64／arm64 發行環境，以 Ubuntu 24.04 LTS 作實測基線；Windows 以 Windows 11／Server 2022+ x64 實測。其餘平台不宣稱已驗收。systemd unit 使用專屬低權限帳號、Restart=on-failure、明確工作目錄及環境；Windows SCM 設 Automatic、失敗重啟與專屬低權限服務身分，不以排程器假冒服務。

安裝需要管理權限，運行不需要。固定 Node、程式及 WinSW 絕對路徑，避免依賴互動 shell 的 PATH。Windows 可寫目錄與服務 binary 分離，避免非管理者替換 executable／服務設定。發行包帶驗證過的相容 WinSW 或安裝時取得固定版本並驗證事先隨套件發行的 digest，不能下載「latest」即執行；離線環境可指定同 digest 的本機檔。

## 6. OAuth 授權、設備登錄與握手

使用維護中的 OAuth／JOSE 程式庫完成標準驗證；不手寫 JWT 演算法。AS 與 resource server 同一程序與 issuer，metadata 公開，只有固定的第一方 public client `bgraph-client`；client ID 不當作設備 ID 或 secret。第一版不開放任意 DCR，也不宣稱未安裝 bridge 的原生 OAuth client 可直接繞過設備驗證。

1. 使用者先以 `client login` 信任 HTTPS 網址／CA；CLI 生成 ES256 設備金鑰。新安裝需產生新身分，不以 MAC 或 hostname 推導可預測 ID。
2. 客戶端傳送設備公鑰與自述 metadata，證明持有私鑰；server 建立短期 pending request、隨機設備 UUID、配對確認碼。設備無知識權限，也無有效 MCP access token。
3. CLI 開啟外部瀏覽器，使用 authorization code、PKCE S256、state、明確 resource／issuer、`dpop_jkt` 與綁定設備的登錄 request；callback host/path 嚴格匹配，僅 loopback port 依 native-app 規則可變。
4. 頁面顯示設備名稱、金鑰指紋、申請主體與配對碼；管理者透過可信管道核對 CLI 顯示的碼，於主機 CLI 核准並配置主體、設備 ACL。自述名稱不可作核准證據。已核准設備重新授權亦需管理 CLI 確認該新 grant，不自動批准 browser request。
5. 核准後瀏覽器返回一次性 authorization code；CLI 驗證 state／issuer，再以 verifier 與 DPoP proof 交換 token。配對碼、code 均一次性；拒絕、過期或已用 request 不能再授權。核准 OAuth request 不自動給所有知識庫權限。
6. server 發出短期、resource/audience 與設備公鑰綁定的 access token，以及同設備綁定的 rotating refresh token；MCP request 使用標準 `Authorization: DPoP` 加 proof，bridge 負責產生。
7. MCP initialize／capability negotiation 後才執行工具；server 每次請求仍驗證 token、DPoP、設備、主體、scope 與當前逐庫 ACL，session 不授權額外能力。

第一版預設：pending／OAuth 核准 request 10 分鐘；code 60 秒且一次性；access token 5 分鐘；refresh family 絕對 30 天、閒置 7 天。Refresh 每次輪替，重用已消耗 refresh 使整個 family 失效；CLI 必須協調同設備／server／grant 的多個 bridge，避免 refresh race。遇到不確定 refresh 結果，不反覆重送舊 token，要求重新登入。

DPoP 只接受 ES256 公鑰；檢查標準要求的 typ、alg、JWK、簽章、jkt、htm、htu、iat、jti、resource access 的 ath 及 server nonce。時鐘容許差 60 秒；proof replay 紀錄保留 5 分鐘，nonce 2 分鐘且區分 token/resource endpoint。庫內保存 consumed jti，重啟不能清空重放防護。htu 以配置的 canonical HTTPS origin 與實際 endpoint 驗證，不信任 forwarded headers。token response 使用 `token_type=DPoP`，不降級為可任意轉移的 Bearer。

狀態可用獨立 opaque token 紀錄保存其 digest、issuer/resource、subject、device、公鑰 thumbprint、scope、期限及撤銷狀態；網路協定遵守 OAuth/DPoP，不自製 token 密碼學。最終 library 選型由實作者以互通測試決定，以上規則不能因套件不支援而默默省略。

## 7. 設備與本機秘密

設備紀錄：server 指派 UUID、principal ID、狀態 pending/approved/revoked、設備公鑰 thumbprint、使用者設定名稱、OS、client 版本、回報的網卡／MAC 清單、登錄／核准／最後連線時間與觀察到的來源 IP。

MAC 限定為管理者 CLI 可查看的 metadata，來源標為 client-reported。多網卡、隨機化、VPN、容器、空清單與網卡變更皆可接受；不以 IP／MAC 同值合併設備，不因 MAC 變動自動給權或撤權。MAC 清單變更以同設備金鑰驗證後更新並記稽核；機器更換、重灌或金鑰遺失走新登錄。裝置管理不承諾證明物理硬體唯一性。

設備私鑰、refresh token 及本機 grant 協調資料保存在 OS 保護的使用者秘密儲存區。Windows 使用使用者範圍 DPAPI 配合受限檔案 ACL；Linux 使用 Secret Service，無 Secret Service 的 headless 主機則需明確輸入本機加密儲存 passphrase，不能默默明文儲存。bridge 非互動時無法解鎖則明確拒絕，管理者須先配置可用秘密儲存環境。自動化服務帳號須在其實際運行身分下登錄。

機器身分在第一版實際上是該 OS 使用者的 client 安裝及其私鑰，不假設不同使用者共用一把機器私鑰。若同一台機器的多個 OS 使用者登錄，產生多份可獨立管理的設備紀錄；MAC 可重複。複製私鑰仍可能複製身分，沒有硬體 attestation 保證。

設備撤銷使其所有 access／refresh grant 不再接受；主體停用同樣拒絕。重新核准不恢復舊 tokens。撤銷提交後開始的工具呼叫／refresh 必須拒絕，已進入原子寫入的操作可能完成；不承諾回滾已完成寫入。

## 8. 多知識庫與工具語意

授權有效權限為主體 ACL ∩ 設備 ACL ∩ OAuth grant scope ∩ 庫啟用狀態。預設無權；任一撤銷不能被其他 grant 擴大。

- `business_explore(query, knowledge_base_ids?)`：未指定則搜尋全部有 read 權限的庫；指定時仍須逐一驗證。明確指定不存在／無權 ID 統一拒絕，不揭露該 ID 是否存在；不悄悄忽略拼錯的 ID。
- `business_write(knowledge_base_id, title, type, content, status, aliases?, code?, base_hash?)`：目標必填、無預設；每次只寫一庫。hash 與節點識別皆在該庫範圍內驗證。
- `business_lint(knowledge_base_id)`：目標必填，檢查該庫並要求 read 權限；結果只引用庫內資訊。

搜尋先決定授權庫集合，再進入該庫索引，不先全庫搜尋才遮罩。命中、鄰居、路徑、miss、lint、錯誤及統計皆不可包含無權庫資訊。回應每頁附永久 knowledge_base_id、可讀名稱、slug、base_hash；同名頁分開呈現，不自動合併或跨庫連結。

第一版每庫最多取 8 個主命中，跨庫以 round-robin 合併並最多回 32 個，公開標示截斷及逐庫排序；不直接比較不同 FTS 資料庫的 BM25 分數。庫順序按永久 ID 穩定排序。這些上限可由管理者配置，但 agent 不能以參數取消 server 上限。全文與鄰居仍遵守受控回應預算。

跨庫不保證同一時間點的全域快照；每庫內持鎖讀取，回傳各庫讀取時間。啟用庫數預設最多 32，避免一個公開查詢觸發無界 fan-out。庫內任何失敗要標為部分結果及其安全錯誤；不可把「查詢失敗」當成「沒有知識」。

## 9. 儲存、移植與一致性

server data root 由管理者指定固定絕對路徑；registry 以 UUID 對應 `knowledge-bases/<uuid>`。名稱只作顯示，agent 永遠不傳實體路徑。拒絕 traversal、junction/symlink 逃出資料根目錄、Windows device names、大小寫碰撞與檔案別名覆蓋。

- `control.db` 是權威控制資料：schema version、installation、principal、設備、公鑰、ACL、OAuth requests/codes/grants/token digests、nonce/replay state、控制稽核。不得因「可重建索引」而刪除它。
- 每庫 `nodes/*.md` 是知識內容來源；`bgraph.toml` 沿用既有可選設定。
- 每庫 `index.db` 是衍生 SQLite FTS5，可在確認來源保存後重建；與 control.db 採不同管理流程。
- 知識寫入稽核獨立保存主體、設備、庫、工具、結果、前後 hash；不含本文、query、MAC 原值、token、code、配對碼或私鑰。

完整移植保留 rule/entity/decision/term、confirmed/inferred、aliases、code、型別化連結、中文檢索、路徑／影響半徑、lint、原子寫入、base_hash 與 indexed=false 部分成功語意。現行業務知識收錄政策不放寬；codegraph 仍須管理者按庫顯式設定唯讀索引，缺少可退化。

控制資料以 SQLite 交易及管理鎖處理，server 每 data root 單實例；每庫序列化知識寫入與索引同步，跨庫可並行。鎖、持有者中止、Windows rename／檔案開啟限制及索引重建都需跨平台驗證；不以「單執行緒 Node」推導跨程序安全。事件迴圈不承擔長時間 FTS 或磁碟工作。

舊版匯入先驗證來源、建立全新目標、複製 nodes 與設定、比對 byte hash 與重建後查詢／關係，來源不動。任何解析差異產出明確報告，不能靜默丟頁。舊 index 不當內容來源，舊 Bearer 憑證不自動轉成核准設備。Python 套件暫留 repository 作移植比對，但新版部署完全不呼叫它；通過相容驗收後另行決定是否移除舊碼。

control schema migration 必須受控交易，支援空庫、新庫、非空舊庫、失敗 rollback 及重跑；無法安全推導歸屬即中止，保留原結構與資料。安裝／升級／uninstall 不執行全表清空或遞迴刪除既有資料。

## 10. 公網防護、日誌與維運

固定 issuer/public URL、精確 Host／Origin 規則，拒絕不可信 proxy header；bridge 無 Origin 可用但仍須完整 OAuth/DPoP。第一版不提供任意 HTTP 反代模式。

預設 request body 1 MiB，header/body timeout 10 秒、工具 timeout 30 秒；工具逾時若已保存仍回報可辨識狀態，不盲目取消原子寫入或自動重送。公開 enroll／authorize／token endpoint 依來源及 device/request 做速率限制，預設每 IP 每分鐘 30 次、全域每分鐘 300 次（設定可調），pending 最多 1000、到期清理；不依未驗證設備名稱作唯一限流 key。過載回 429 並 fail closed。

OAuth UI 採安全 cookie、CSRF 與輸出 escaping、嚴格 CSP、防止 open redirect／任意 metadata URL fetch。錯誤與 audit 不洩漏秘密；health 只顯示存活，不公開知識庫清單或設備資料。token、authorize response、設備資訊都禁止共享快取。

每庫寫入成功但 audit／索引失敗，回傳新 hash 及安全警告，不自動再寫一次。權限與設備核准／撤銷在 control transaction 內同步保存控制稽核；不能保存安全狀態卻假稱稽核已完成。管理者可配置日誌容量與輪替，達容量上限明確回報，不能無界佔滿磁碟。

停止服務後備份整份 control.db、所有 nodes／設定、稽核與必要 server 設定，TLS 私鑰以安全獨立備份保存；索引可略過。還原只到全新目錄，驗證完整性後重建各庫索引，不能覆寫唯一原件。還原控制資料預設使全部 OAuth grants/codes/refresh family 失效，設備改為待重新核准，以免舊備份復活已撤銷權限。這是設計者的安全預設，需使用者審閱。

## 11. 驗收與交付界線

1. Node.js-only install、CLI、bridge；server 啟動及正常工具操作不呼叫 Python。
2. Ubuntu 24.04 systemd 與 Windows SCM 真實安裝／啟停／重開機自啟／失敗重啟；非 root／非 LocalSystem 運行；port 碰撞、缺憑證、權限不足有明確結果。
3. 同一外部 HTTPS port 完成 metadata、登錄、OAuth code+PKCE、token、refresh、MCP；callback 僅 client loopback，不額外開公網 port。
4. Codex 與 Claude Code 實際啟動 stdio bridge、授權後搜尋／寫入；token 過期刷新、bridge 並行、離線重連與 server restart 不繞過安全檢查。
5. pending／拒絕／過期／撤銷設備不能查知識；核准後只取得交集 ACL；主體停用、設備撤銷、ACL 撤銷及庫停用對新請求立即生效。
6. 錯 issuer/resource/key、過期 code/token、錯 state/verifier/redirect、重放 proof/code/refresh、錯 htm/htu/ath/nonce、偽造 MAC 同值皆不放行；重啟後重放仍拒絕。
7. 多庫同名頁保留庫身分，聯合搜尋先授權；無權庫的命中／鄰居／統計不洩漏；寫入缺目標拒絕，不能指定任意路徑。
8. 跨平台多 agent 同頁 hash 競爭只一成功，跨庫互不覆寫；斷線／逾時後重查可確認現況，audit/index 部分失敗保持已保存內容。
9. 舊 Markdown/設定匯入、中文搜尋、別名、圖與 lint 的 golden fixtures 對照現有語意；非空資料升級 rollback 實測；備份還原到新目錄且不復活舊 tokens。
10. 公網／公司 CA、TLS 憑證失敗、Host/Origin、限流、巨量 header/body、秘密儲存與日誌不洩漏都有實測。

測試證據按 OS／場景分開記錄；沒有真實 service manager、Windows 或 Linux 環境時明確列未測，不能以 mock 或靜態設定通過取代。沒有實際安裝服務、變更防火牆、發行 npm 或推送部署的授權前，只交付可審閱的程式與命令。

## 12. 留給實作者的選擇

在不減少上述安全與資料保留要求下，可決定 npm 套件名、模組切分、測試框架、相容 SQLite／FTS5 adapter、OAuth／DPoP 程式庫及確切修補版、WinSW 固定版本、秘密儲存 adapter、效能工作執行緒策略與 service 診斷訊息。依賴必須鎖版、驗證 Linux／Windows 支援，原生模組不得要求使用者自行安裝完整編譯環境而不提供明確替代方案。

本文件已完成獨立 builder check，結論為「nothing important left」；使用者核准後才產生實作計畫，尚未寫產品程式或安裝相依。

## 參考與查核

查核日期：2026-10-28。

- [MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [OAuth native apps RFC 8252](https://www.rfc-editor.org/rfc/rfc8252.html)
- [PKCE RFC 7636](https://www.rfc-editor.org/rfc/rfc7636.html)
- [OAuth security BCP RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html)
- [DPoP RFC 9449](https://www.rfc-editor.org/rfc/rfc9449.html)
- [Node.js releases](https://nodejs.org/en/about/previous-releases)
- [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
- [Windows service requirements](https://learn.microsoft.com/en-us/windows/win32/services/service-programs)
- [WinSW](https://github.com/winsw/winsw)
- [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
