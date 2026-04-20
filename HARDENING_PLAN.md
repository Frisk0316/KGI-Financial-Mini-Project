# Knowledge Shredder Hardening Plan

## 目標

這份補強企劃是依據兩個面向展開：

1. 最值得優先補的事情
2. 高優先弱點

目的不是把 demo 包裝成 production-ready，而是把最容易被面試官質疑的風險，收斂成一個「有設計判斷、也有實際落地」的 MVP 強化版。

## 這次優先補強的四件事

### 1. 文件 owner 與存取隔離

原始弱點：

- `trainer_id` 為硬編碼，無法區分不同 trainer
- 任一使用者理論上都能存取任意文件 id

這次實作：

- 透過 `X-Trainer-Id` header、form 欄位或 JSON 欄位解析 `trainer_id`
- 加入格式驗證，只允許英數字、底線與連字號
- 所有 `/api/upload`、`/api/generate`、`/api/document/<id>`、`/api/jobs/<id>` 都會帶入 trainer scope
- 若 trainer 不符，回傳 404，避免暴露其他使用者資料存在與否

為什麼優先：

- 這是最容易被面試官直接點名的安全與資料隔離問題
- 即使是 demo，也應該能說清楚資料 ownership 的基本概念

### 2. 同步生成改為背景工作

原始弱點：

- LLM 呼叫是同步 blocking
- 使用者等待時間長時沒有狀態回饋
- 架構上很難回答並發與 timeout 問題

這次實作：

- 新增 `GenerationJobs` table
- `/api/generate` 改為建立 job，回傳 `job_id`
- 背景 thread 執行 LLM 生成並更新 job 狀態
- 前端輪詢 `/api/jobs/<id>`，顯示 `queued / running / completed / failed`
- 測試模式支援 inline jobs，確保自動化測試穩定

為什麼優先：

- 這能把原本最脆弱的「長請求 + UI 無感知」改成可辯護的架構
- 面試時若被問到未來如何擴充到 queue/worker，回答會非常順

### 3. 輸入驗證與資料一致性

原始弱點：

- `domain_ids` 若重複，可能觸發 junction table unique constraint
- request validation 不夠集中
- 失敗時資料一致性與錯誤敘事不夠清楚

這次實作：

- `domain_ids` 統一做整數轉換、去重與上限控制
- trainer id 驗證抽成共用 helper
- job 錯誤統一收斂到 `failed` 狀態與 `error_message`
- 保留舊資料直到新生成結果成功落地，避免失敗時污染既有內容

為什麼優先：

- 這是最容易被拿來測「你有沒有處理 edge cases」的地方
- 比起只補更多 if/else，集中式 validation 也讓後續維護更穩

### 4. 去識別化預覽與 sprint 品質約束

原始弱點：

- 上傳後直接回傳完整 raw text 到前端
- 缺少對 PII 的最小保護
- 2 分鐘 sprint 只有 prompt 引導，沒有後端驗證

這次實作：

- 前端改顯示 `preview_text`，不直接暴露完整原文
- 對 email、台灣手機、台灣身分證字號與長數字序列做遮罩
- `reading_time_minutes` 必須落在 1 至 3 分鐘範圍內
- 成功驗證後，統一標準化成 `2.0` 分鐘 metadata
- prompt 額外要求對敏感內容做 generic handling

為什麼優先：

- 這能同時回應兩種常見追問：資安與內容品質
- 「有後端驗證」會比「我 prompt 寫很清楚」更有說服力

## 高優先弱點與本次處理對照

| 弱點 | 風險 | 本次處理 | 仍可再強化 |
|------|------|------|------|
| 缺少身份與權限概念 | 任意 trainer 讀到別人文件 | trainer scope + owner-aware routes | 串接真正登入機制、audit log |
| 同步 LLM blocking | UX 差、難回答擴充性 | job queue + polling | 改成 Celery / Redis / message queue |
| Domain 重複輸入 | DB unique constraint 失敗 | 去重與上限控制 | schema 層再補 request idempotency token |
| 文件包含敏感資料 | PII 外露 | safe preview + masking | 欄位級加密、DLP、保存期限政策 |
| 2 分鐘 sprint 無硬保證 | 內容不穩定 | reading_time 驗證與標準化 | 加上字數/段落密度驗證 |
| 錯誤處理分散 | 使用者不易理解失敗原因 | job error_message 統一輸出 | 增加 observability、error codes |

## 這次實際修改的模組

### `app.py`

- 新增 trainer 解析與驗證
- 新增 generation job 建立、啟動與查詢 API
- `/api/upload` 改回傳安全 preview
- `/api/document/<id>` 改為 owner-aware 且不回傳 raw text

### `database.py`

- 新增 `GenerationJobs` schema
- 新增 create / update / fetch generation job functions
- `get_document_with_modules()` 支援 trainer scope

### `file_parser.py`

- 新增 `redact_sensitive_text()`
- 新增 `build_safe_preview()`

### `llm.py`

- 加強 sequence order 驗證
- 限制 `reading_time_minutes` 必須落在 sprint 範圍
- 將成功模組的時間 metadata 統一標準化為 2 分鐘

### `static/js/app.js` / `templates/index.html`

- 新增 trainer input
- 新增 job status card
- 前端改成建立 job 後輪詢狀態
- 左側 split panel 改為顯示去識別化 preview

### `tests/test_app.py`

- 補 trainer 隔離測試
- 補 safe preview 測試
- 補 async job / failed job / dedupe domain_ids 測試
- 補 LLM sprint 標準化測試

## 驗證結果

已執行：

- `pytest -q`
- `python3 -m unittest discover -s tests -q`

目前通過：

- 9 個自動化測試

## 仍然刻意保留為下一階段的項目

這些不是沒想到，而是這一輪刻意沒有一起做進來，避免把 demo 複雜度拉得過高：

- 真正的登入驗證與 session / JWT
- OCR 流程，用來處理掃描 PDF
- 正式的 background worker 與訊息佇列
- 更完整的 observability：request id、job latency、token cost metrics
- 文件版本歷史與刪除/重跑管理介面
- source-grounded 引用與人工審核狀態

## 面試時建議的定位說法

最穩的說法不是「這套已經可以直接上線」，而是：

「我先把題目要求的核心能力做成可驗證的 MVP，再把最容易被挑出的幾個風險收斂掉：資料 ownership、非同步生成、輸入一致性、敏感資訊保護，以及 2 分鐘 sprint 的後端約束。這樣即使它還不是 production-ready，也已經是一個有明確演進路線的版本。」
