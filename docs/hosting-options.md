# 遠端知識庫的免費主機評估

查核日期：2026-10-22。此文件是方案分析，尚未建立雲端資源或選定供應商。額度與限制以申請時的官方頁面為準。

## 建議

目前服務以 Python、Markdown、SQLite 與單機檔案鎖運作，需要可保留的本機資料目錄。現有電腦、NAS 或公司 Docker 主機最容易沿用；若需要雲端免費 VM，可評估 Oracle Cloud Always Free，但須接受容量與閒置資源回收限制，並獨立備份。

Cloudflare Workers + D1 是可研究的另一種架構，不能將目前 Docker 映像直接視為可用於 Workers。外部資料庫與物件儲存會改變資料來源、鎖定與一致性設計，應另行設計與驗證。

## 免費選項比較

| 方案 | 官方免費限制 | 對目前架構的判斷 |
|---|---|---|
| 既有電腦／NAS／公司主機 | 沒有新增雲端主機費，仍有硬體、電力、網路與維護成本 | 可直接部署；資料目錄掛載持久化磁碟 |
| Oracle Cloud Always Free | 免費 VM、合計 200 GB 區塊儲存；須在 home region，可能缺容量，低使用率實例可能被回收 | 可保留現有架構；需要管理 VM 與外部備份 |
| Cloudflare Workers + D1 | Workers Free 每日 100,000 請求；D1 Free 每日 5,000,000 列讀取、100,000 列寫入，總儲存 5 GB | 需要重新設計儲存與執行方式；這些額度不是完整系統零成本承諾 |
| Render Free | 無流量 15 分鐘後休眠；免費 web service 不能掛持久磁碟；重新部署、重啟或休眠會丟失本機變更 | 不適合將唯一 Markdown／SQLite 知識庫直接放上去 |
| Koyeb Free | free instance 不能附加 Volume | 需另接可靠儲存；目前檔案模式不能直接符合持久化需求 |
| GitHub Codespaces | 個人免費計算與儲存額度有限，且有閒置停止與停止後保留期限 | 適合開發／展示，團隊長期共用需另評估可用性 |

官方來源：[Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)、[Workers limits](https://developers.cloudflare.com/workers/platform/limits/)、[D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)、[Render Free](https://render.com/docs/free)、[Koyeb Volumes](https://www.koyeb.com/docs/reference/volumes)、[Codespaces billing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)。

## Codespaces 作為 server

可以執行本服務與轉發 HTTP 埠，但其定位是開發環境。預設閒置 30 分鐘停止，可設定的閒置門檻最長 240 分鐘；這不是每次啟動只能運作四小時。依賴使用者活動與終端輸出的行為，不代表具有持續運行保證，不應把假造活動當作服務設計。

轉發網址形式為 `https://CODESPACENAME-PORT.app.github.dev`。私有埠的非瀏覽器呼叫需額外 GitHub 驗證，並不能用本應用的 Bearer 憑證取代。官方說明使用 `X-Github-Token`，其 codespace session token 重啟後會變更且不應分享。公開埠則省去這道 GitHub 門檻，但必須保留本服務逐次請求的獨立憑證驗證；組織政策可能禁止公開埠。沒有在本次評估公開任何埠。

`/workspaces` 內資料可跨停止／啟動及 dev container 重建保留。此目錄的持久化仍依附該 Codespace；停止且未使用的 Codespace 預設 30 天後刪除，保留期限可受個人或組織設定影響。資料應另備份，不能把「存在 repo 工作目錄」當作已經 push 至 GitHub。

目前 2-core 計算價格是每小時 US$0.18，儲存為每 GB-month US$0.07。假設一個月運行 720 小時，計算費原價為 US$129.60，未扣任何免費額度且未含儲存。GitHub Free 個人額度是每月 120 core-hours，換成 2-core 機器為 60 小時；不是 120 小時的該機器運行時間。帳戶實際計費還受其他 Codespaces 共用額度與預算設定影響。

若未來選用 Codespaces 試跑，應將服務端 workspace、audit 等可保留資料放在 `/workspaces` 的獨立資料目錄，憑證獨立管理；啟動時設定該轉發網址的明確 Host／Origin，避免任意萬用字元。服務仍只使用一份綁定的知識庫，不因部署平台改變而接受遠端檔案路徑。

官方來源：[閒置門檻](https://docs.github.com/en/codespaces/setting-your-user-preferences/setting-your-timeout-period-for-github-codespaces)、[埠轉發與驗證](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)、[重建與持久化](https://docs.github.com/en/codespaces/developing-in-a-codespace/rebuilding-the-container-in-a-codespace)、[生命週期](https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle)、[計費](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)。
