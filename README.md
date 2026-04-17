# Knowledge Shredder — 知識碎片化微學習內容生成平台

## 專案簡介

本專案為凱基金融科技數位儲備幹部的開發項目，旨在解決金融業培訓中的「遺忘曲線」問題。研究顯示，員工在未經強化複習的情況下，數天內會遺忘高達 80% 的培訓內容。

**Knowledge Shredder** 讓培訓人員能夠：
1. 上傳訓練文件（PDF、DOCX、TXT）
2. 為文件標註多個知識領域標籤（如壽險、CRM、法規遵循等）
3. 透過 AI 自動將長篇文件拆解為 **2 分鐘學習微模組（Sprint）**
4. 以左右分割畫面預覽原文與 AI 生成結果

## 技術架構

| 層級 | 技術 |
|------|------|
| 後端 | Python + Flask |
| 資料庫 | SQLite |
| AI 引擎 | Claude API（claude-haiku-4-5-20251001） |
| 文件解析 | pdfplumber、python-docx |
| 前端 | HTML + Bootstrap 5 + Vanilla JS |

## 資料庫設計

採用多對多（Many-to-Many）關聯設計：

- **KnowledgeDomains** — 知識領域分類表
- **SourceDocuments** — 來源文件表
- **Document_Domain_Map** — 文件與領域的關聯表（Junction Table）
- **MicroModules** — AI 生成的微學習模組表

## 快速開始

### 前置條件

- Python 3.9 以上版本
- [Anthropic API 金鑰](https://console.anthropic.com/)

### macOS / Linux

```bash
# 1. 安裝依賴
pip3 install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env，填入你的 ANTHROPIC_API_KEY

# 3. 啟動伺服器
python3 app.py
```

### Windows

```bat
:: 1. 安裝依賴
pip install -r requirements.txt

:: 2. 設定環境變數（複製範本後編輯）
copy .env.example .env

:: 3. 啟動伺服器
python app.py
```

> **Windows 注意事項：**
> - 使用 `python` 和 `pip`，而非 `python3` 和 `pip3`
> - 若 5000 埠被 Windows Defender 防火牆封鎖，請允許 Python 存取網路，或改用其他埠：`python app.py --port 8080`（需在 `app.py` 末行調整）
> - 建議使用 PowerShell 或 Command Prompt，避免使用 Git Bash 開啟瀏覽器連結

開啟瀏覽器前往 http://127.0.0.1:5000

## 操作流程

1. **上傳文件** — 拖放或點擊選擇 PDF / DOCX / TXT 檔案（最大 16 MB）
2. **選擇領域標籤** — 至少選擇一個知識領域（支援多選與搜尋）
3. **生成微模組** — 點擊「生成學習微模組」按鈕，AI 將自動拆解內容

生成完成後，畫面左側顯示原始文件內容，右側顯示 AI 生成的 Sprint 卡片，每張卡片包含標題、內容、重點摘要及閱讀時間。

## API 路由

| Method | Path | 說明 |
|--------|------|------|
| GET | `/` | 主頁面 |
| GET | `/api/domains` | 取得所有知識領域 |
| POST | `/api/upload` | 上傳並解析文件 |
| POST | `/api/generate` | AI 生成微學習模組 |
| GET | `/api/document/<id>` | 取得文件及其模組 |

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
├── file_parser.py      # PDF / DOCX / TXT 文字擷取
├── llm.py              # Claude API 封裝與 Prompt 建構
├── requirements.txt    # Python 依賴套件
├── .env                # API 金鑰（不納入版控）
├── .env.example        # 環境變數範本
├── .gitignore          # Git 排除規則
├── .gitattributes      # 跨平台換行符號設定
├── templates/
│   └── index.html      # 前端頁面
├── static/
│   ├── css/style.css   # 自訂樣式
│   └── js/app.js       # 前端互動邏輯
└── uploads/            # 上傳檔案暫存目錄
```
