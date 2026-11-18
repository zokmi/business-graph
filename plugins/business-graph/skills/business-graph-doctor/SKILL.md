---
name: business-graph-doctor
description: business-graph 本機 HTTP MCP 出現 ECONNREFUSED、Couldn't reconnect、連線中斷，或工具因傳輸故障失敗時，執行 doctor 自動診斷及恢復 MCP Server。也可手動執行診斷。
---

# business-graph MCP doctor

遇到本機 business-graph MCP 連線故障時，直接執行一次：

```powershell
node "${CLAUDE_PLUGIN_ROOT}/skills/business-graph-doctor/doctor.mjs"
```

doctor 讀取 Claude Code 使用者層級 HTTP 設定，使用原啟動參數及權杖啟動已停止的 Windows 本機服務。已識別的程序若未監聽或持續回傳 HTTP 502／503，核對程序身分、啟動設定、權杖及連接埠後自動重啟一次。修復成功需通過帶權杖的 MCP 工具驗證。

1. 恢復後，重試原本的唯讀呼叫一次。若客戶端仍斷線，請使用者在 Claude Code 執行 `/mcp` 重新連線。
2. 若中斷的是 `business_write`，先用 `business_explore` 確認該操作是否已寫入。確認尚未寫入時才重送一次。無法確認時回報寫入狀態未知。
3. 若 doctor 回報連接埠占用、程序身分不符、設定或權杖變更，回報具體診斷並結束此次修復。避免輸出權杖及完整設定。
