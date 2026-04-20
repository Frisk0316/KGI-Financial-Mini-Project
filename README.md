# Knowledge Shredder — 知識碎片化微學習內容生成平台

## 專案簡介

本專案為凱基金融科技數位儲備幹部的開發項目，旨在解決金融業培訓中的「遺忘曲線」問題。研究顯示，員工在未經強化複習的情況下，數天內會遺忘高達 80% 的培訓內容。

**Knowledge Shredder** 讓培訓人員能夠：
1. 以 `trainer_id` 模擬文件 owner 與存取隔離
2. 上傳訓練文件（PDF、DOCX、TXT）
3. 為文件標註多個知識領域標籤（如壽險、CRM、法規遵循等）
4. 透過 LLM 自動將長篇文件拆解為 **2 分鐘學習微模組（Sprint）**
5. 以背景工作建立生成任務，並輪詢 job status
6. 以左右分割畫面預覽去識別化原文與 LLM 生成結果

## 技術架構

| 層級 | 技術 |
|------|------|
| 後端 | Python + Flask |
| 資料庫 | SQLite |
| 內容生成 | OpenAI Responses API + `gpt-5.4-mini` + background job polling |
| 文件解析 | pdfplumber、python-docx |
| 前端 | HTML + Bootstrap 5 + Vanilla JS |

## 生成邏輯

系統使用 OpenAI 的 `gpt-5.4-mini` 搭配 structured outputs：

1. 上傳時以 `trainer_id` 建立文件 owner，前端只顯示去識別化 preview
2. 後端將文件全文與 selected domains 組成 prompt，建立 generation job
3. 背景工作呼叫 Responses API，要求模型輸出固定 JSON schema
4. 後端驗證 `document_summary / domains / modules` 結構，並標準化 2 分鐘 sprint metadata
5. 驗證通過後寫回資料庫，保留文件與 domain 的關聯，再由前端輪詢 job status 顯示結果

預設模型是 `gpt-5.4-mini`，因為它很適合這種「明確格式、文字整理、成本敏感」的任務；若要切換模型，可在 `.env` 設定 `OPENAI_MODEL`。

## 題目符合性檢核

以下依兩份題目檔逐項對照目前系統建制：

| 題目需求 | 目前實作 | 狀態 |
|------|------|------|
| 支援上傳 PDF / Word / TXT 訓練文件 | `app.py` 限制副檔名為 `pdf` / `docx` / `txt`，`file_parser.py` 負責解析三種格式 | 符合 |
| Drag-and-drop Upload Zone | `templates/index.html` + `static/js/app.js` 提供拖放與點擊上傳 | 符合 |
| 可搜尋的多選 Domain Tagging | 前端有搜尋框、checkbox 多選、已選標籤 pills | 符合 |
| 至少選一個 domain 才能生成 | 前端按鈕鎖定；後端 `/api/generate` 再次驗證 `domain_ids` 不可為空 | 符合 |
| 一份文件可對應不限數量的 domain | `domain_ids` 接受陣列；資料表 `Document_Domain_Map` 為多對多 Junction Table | 符合 |
| 後端 prompt 必須注入 selected domains | `llm.py` 的 `_build_user_prompt()` 會把 domain names 注入 prompt，並強調 selected domains | 符合 |
| LLM 輸出 structured JSON micro-modules | `llm.py` 使用 Responses API JSON schema，驗證 `document_summary / domains / total_modules / modules` | 符合 |
| Backend 將 modules 關聯回文件與 domains | `save_generated_content()` 同時寫入 `Document_Domain_Map` 與 `MicroModules` | 符合 |
| Database 需為 Many-to-Many 設計 | `KnowledgeDomains`、`SourceDocuments`、`Document_Domain_Map`、`MicroModules` 已建立 | 符合 |
| Split-screen Preview：左原文、右生成結果 | 前端預覽區左側 `raw-text-panel`、右側 `sprint-panel` | 符合 |
| 生成結果需清楚顯示 selected Domain Tags | 右側 summary header 與每張 sprint card 都會顯示 domain badges | 符合 |

## 本次補強

針對面試時最容易被追問的弱點，這一版額外補上：

- `trainer_id` 驗證與文件 owner 隔離，避免所有 trainer 共用同一批文件
- `GenerationJobs` 背景工作表，生成流程改為 job queue + polling
- `domain_ids` 去重與上限控制，避免重複 tag 造成資料一致性問題
- 去識別化 preview，遮罩 email / phone / 台灣身分證字號 / 數字序列
- 更嚴格的 sprint 驗證，限制 `reading_time_minutes` 範圍並統一標準化成 2 分鐘

補充說明：

- `The Forgetting Curve...docx` 偏向商業背景與願景描述，文中提到 7 分鐘學習與 2 分鐘回憶測驗。
- `Project 1 The Knowledge Shredder Domain Taxonomy.docx` 才是本題的功能規格，明確要求輸出 2-minute sprints。
- 因此目前系統以「2 分鐘微模組」為主，是符合規格文件的做法。

## 資料庫設計

採用多對多（Many-to-Many）關聯設計：

- **KnowledgeDomains** — 知識領域分類表
- **SourceDocuments** — 來源文件表
- **Document_Domain_Map** — 文件與領域的關聯表（Junction Table）
- **MicroModules** — LLM 生成的微學習模組表
- **GenerationJobs** — 背景生成任務表，用於追蹤 queued / running / completed / failed 狀態

## 快速開始

### 前置條件

- Python 3.9 以上版本
- OpenAI API 金鑰

### macOS / Linux

```bash
# 1. 安裝依賴
pip3 install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env，填入你的 OPENAI_API_KEY

# 3. 啟動伺服器
python3 app.py

# 如需指定埠號或開啟 debug
python3 app.py --port 8080 --debug
```

### Windows

```bat
:: 1. 安裝依賴
pip install -r requirements.txt

:: 2. 設定環境變數（複製範本後編輯）
copy .env.example .env

:: 3. 啟動伺服器
python app.py

:: 如需指定埠號或開啟 debug
python app.py --port 8080 --debug
```

> **Windows 注意事項：**
> - 使用 `python` 和 `pip`，而非 `python3` 和 `pip3`
> - 若 5000 埠被 Windows Defender 防火牆封鎖，請允許 Python 存取網路，或改用其他埠：`python app.py --port 8080`
> - 建議使用 PowerShell 或 Command Prompt，避免使用 Git Bash 開啟瀏覽器連結

開啟瀏覽器前往 http://127.0.0.1:5000

## 操作流程

1. **輸入 Trainer ID** — 模擬文件 owner，後續存取與生成都會帶入相同 `trainer_id`
2. **上傳文件** — 拖放或點擊選擇 PDF / DOCX / TXT 檔案（最大 16 MB）
3. **選擇領域標籤** — 至少選擇一個知識領域（支援多選與搜尋）
4. **生成微模組** — 點擊「生成學習微模組」按鈕，系統會先建立 job，再由背景工作產生 sprint

生成完成後，畫面左側顯示去識別化文件預覽，右側顯示 LLM 生成的 Sprint 卡片；每張卡片包含標題、內容、重點摘要、閱讀時間，以及本次套用的知識領域標籤。

## 測試方式

### 自動化測試

先安裝依賴後，可直接執行：

```bash
pytest -q
```

若想使用 Python 內建測試執行器，也可以：

```bash
python3 -m unittest discover -s tests -q
```

目前測試覆蓋的重點包含：

- 重新生成時，會正確覆蓋舊的 domains 與 modules
- 重複的 `domain_ids` 會被自動去重，不會破壞 junction table
- LLM 回傳格式錯誤時，既有資料不會被破壞
- 文件預覽會遮罩常見敏感資訊
- 不同 `trainer_id` 不能互相讀取彼此文件
- `domain_ids` 必須是整數陣列
- 缺少 `OPENAI_API_KEY` 時，job 會標記為 failed 並保留錯誤原因
- prompt 會帶入 selected domains，且回傳 domains 必須與選取內容一致
- sprint 閱讀時間會被限制在 2 分鐘範圍內並標準化

### 手動功能測試

1. 啟動伺服器：`python3 app.py`
2. 開啟 `http://127.0.0.1:5000`
3. 上傳一份 `PDF`、`DOCX` 或 `TXT`
4. 確認「生成學習微模組」按鈕在未選 domain 前不可按
5. 搜尋並勾選一個以上 domain
6. 點擊生成後確認畫面出現 job status card，並從 queued / running 進入 completed
7. 確認左側只顯示去識別化 preview，不直接暴露原始敏感資訊
8. 確認每張 sprint card 都有顯示 domain tags、標題、內容、重點摘要與閱讀時間
9. 切換不同 `trainer_id` 後，確認不能讀到其他 trainer 的文件

## API 路由

| Method | Path | 說明 |
|--------|------|------|
| GET | `/` | 主頁面 |
| GET | `/api/domains` | 取得所有知識領域 |
| POST | `/api/upload` | 上傳並解析文件，回傳去識別化 preview |
| POST | `/api/generate` | 建立 generation job，由背景工作呼叫 LLM |
| GET | `/api/jobs/<id>` | 取得 job 狀態與生成結果 |
| GET | `/api/document/<id>` | 依 `trainer_id` 取得文件與模組 |

## 預設知識領域

| 領域 | 說明 |
|------|------|
| LifeInsurance | 定期、終身及儲蓄型壽險商品 |
| InvestmentLinked | 投資型保單與基金選擇 |
| CRM | 客戶關係管理與服務策略 |
| Compliance | FSC 法規、AML、KYC 及內部合規政策 |
| WealthManagement | 高資產客戶規劃、投資組合與遺產規劃 |
| TaxRegulations | 保險稅務、資本利得及遺產稅處理 |

## 專案結構

```
├── app.py              # Flask 應用程式 + API 路由
├── database.py         # SQLite 資料庫初始化與查詢
├── file_parser.py      # PDF / DOCX / TXT 文字擷取與敏感資料遮罩
├── llm.py              # OpenAI LLM 封裝、JSON schema 驗證與 2 分鐘 sprint 標準化
├── requirements.txt    # Python 依賴套件
├── .env                # API 金鑰（不納入版控）
├── .env.example        # 環境變數範本
├── .gitignore          # Git 排除規則
├── .gitattributes      # 跨平台換行符號設定
├── templates/
│   └── index.html      # 前端頁面
├── static/
│   ├── css/style.css   # 自訂樣式
│   └── js/app.js       # 前端互動邏輯與 job polling
├── HARDENING_PLAN.md   # 補強企劃與弱點對照
└── uploads/            # 上傳檔案暫存目錄
```
