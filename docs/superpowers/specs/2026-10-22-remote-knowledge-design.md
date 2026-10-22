# 遠端共用知識庫設計

日期：2026-10-22。狀態：設計已核准，遠端功能與交付文件已實作。Windows Python 驗證完成；本機沒有 Docker／Podman，Linux 鎖競爭、映像建置、HTTPS 與容器／新卷還原尚未實測，不列為已通過。

## 需求與已確認範圍

使用者需要多人與 agent 共用同一份業務知識，已確認第一版一個服務綁定一份知識庫。本機 stdio 模式繼續支援。Obsidian 同步與即時共筆不在本次範圍。

## 架構

新增 Streamable HTTP 啟動模式，三個業務工具共用現有儲存與查詢引擎。服務端以啟動參數綁定絕對知識庫路徑，遠端工具不接受 project_path；不可依客戶端提供的檔案系統位置尋找知識庫。本機模式維持原介面與目錄搜尋規則。

HTTP 模式的工作目錄不可成為隱含知識庫。設定不完整時啟動失敗，不能自動落回其他目錄。顯式初始化可建立空知識庫；現存資料不得在啟動或升級時清除。

Markdown 仍為可攜的知識內容來源，SQLite 為可重建索引。第一版採單一服務實例與本機持久化磁碟；不使用多主機共享 SQLite 或共享網路磁碟鎖。

## 存取與部署

每位使用者或 agent 使用獨立、可撤銷的憑證，映射成主體與 read/write 權限。read 可使用 explore、lint；write 另可使用 write。授權必須在每次工具呼叫檢查，不能只在連線或工具列表隱藏時檢查。read 權限代表不可修改知識節點，但讀取仍可能更新衍生索引與查詢統計。

部署以 HTTPS 反向代理對外，應用只在內部網路或 loopback 接收請求。驗證 Origin、限制請求大小；未授權與權限不足請求不可進入儲存引擎。健康檢查不回傳知識內容。憑證不寫入知識庫、映像檔或日誌。

第一版部署對象是支援自訂 Authorization header 的 MCP 客戶端；使用個別 Bearer 憑證。這不是完整 OAuth 授權伺服器，不宣稱所有遠端 MCP 客戶端均可直接連線。需要 OAuth 探索與互動登入的客戶端另列後續整合範圍，交付文件明列限制。

Docker Compose 包含應用、持久化資料與反向代理設定範例；部署者提供網域、TLS 與憑證。應用不自動註冊外部服務。

## 並行、錯誤與維護

沿用 workspace lock，並驗證 Linux 上同一行程多執行緒與不同連線的鎖競爭行為。讀取、索引同步與寫入遵守同一互斥邊界。hash 比對與寫檔必須位於同一鎖範圍內，避免檢查後資料已被另一請求修改。

更新必須提供最新 base_hash；衝突回傳現況，要求 agent 重新閱讀與整合，不自動覆寫或盲目重試。並行建立同名節點只允許一方成功。網路中斷後結果可能未知，呼叫者先查現況再決定是否重送。保留既有 indexed=false 語意，明確區分內容已保存與索引失敗。

第一版只保證單節點更新一致性，不提供多節點原子交易。agent 整合多頁時須逐頁檢查結果，失敗不得聲稱整批完成。對同一服務的直接檔案編輯不得繞過寫入鎖；Obsidian 編輯與檔案同步不列為支援的並行寫入方式。

記錄主體、工具、節點識別、前後 hash 與結果的操作紀錄，與業務節點分開保存；不記錄憑證或全文。此紀錄用於操作追溯，不作為業務事實依據。檔案寫入後若紀錄失敗須明確回報，不得假稱整個操作未發生。

備份在服務停止或持有知識庫鎖時產生一致快照，包含 nodes 與設定；憑證以獨立安全流程管理。還原至新資料目錄並重建索引後驗證節點數量、內容與關係，不覆蓋唯一現存資料。Git 歷史與自動提交不是第一版寫入成功的必要條件。

## codegraph 與工具說明

遠端不能解析使用者本機的 codegraph。只有管理者顯式配置服務端程式碼工作區時才解析其唯讀索引；否則保留符號並標示未解析。不得誤查服務本身的程式碼索引。工具說明按本機／遠端模式調整，不能繼續指示 agent 傳入本機路徑。

## 驗證與驗收

- 原 stdio 工具與目錄搜尋回歸通過。
- HTTP MCP 客戶端完成初始化、列工具、讀取、新增、更新與 lint。
- 無憑證、錯誤或已撤銷憑證不能存取；read 主體直接呼叫 write 也被拒絕。
- 遠端不能選擇其他目錄；驗證不同 Origin 與過大請求遭拒。
- 兩個獨立客戶端以相同 hash 更新，僅一方成功；同名建立、鎖逾時與多執行緒競爭有實測。
- 索引失敗、網路重試與重啟不造成靜默覆寫或現存節點遺失。
- Docker 重建後資料保留，備份可在新目錄還原並重建查詢能力。
- 驗證 codegraph 缺少時正常退化，且不讀取未配置的工作區。

## Karpathy LLM Wiki 對照

參考：https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f 。該文是可調整的方法描述，不是規定 YAML 欄位或內文標題的格式標準。

本專案已有持久化 Markdown、agent 查詢後累積與更正、型別化關係、查詢與 lint 入口；整體方向相符。未完整涵蓋的部分為不可變來源層、來源引用、內容目錄、操作時間軸與語意矛盾檢查。現有 lint 檢查結構與時效，不代表已驗證跨頁語意一致。

保留業務規則、實體、決策、術語四種類型。現行政策禁止存放 issue 資訊與工作紀錄，因此不直接新增 issue 摘要、raw issue 備份或把操作日誌當節點。來源追溯若要擴充，需另定受控證據層與存取政策，不能藉本次範本調整解除既有限制。

現有 [[depends_on:Target]] 等語法是本系統的型別化關係；Obsidian 一般 wikilink 不會自動將它當作連到 Target。故 Markdown 可讀不等於 Obsidian 原生圖譜完全相容；本次不改寫語法或承諾完整相容。

## 建議的知識內文範本（提案，未變更既有指引）

Front matter 繼續由 server 管理。內文建議按知識型別選擇有意義的段落，避免空標題與捏造資訊。英文為現有預設，中文術語加入 aliases。

```markdown
## Summary
Explain the business concept or rule directly.

## Scope and conditions
State when and to whom it applies, including effective periods when known.

## Definition or behavior
Describe the rule, entity constraints, decision and rationale, or term meaning.

## Exceptions and examples
Include known exceptions and a concrete example when useful.

## Related knowledge
Explain each dependency or exception beside its typed link.

## Uncertainty
State unresolved business questions when they affect interpretation.
```

rule 著重條件、結果與例外；entity 著重定義、欄位語意與限制；decision 著重政策、理由與替代方案；term 著重定義、別名與易混淆概念。未知的日期、依據或例外不得補猜。confirmed 仍限業務方確認，不能用新增段落取代 status。

範本改善與來源層擴充應分開評估。使用者本次要求分析範本，尚未授權變更收錄政策；遠端第一版不以完整實現 LLM Wiki 所有選項為驗收條件。
