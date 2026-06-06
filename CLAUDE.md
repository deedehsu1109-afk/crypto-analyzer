# CLAUDE.md — LumenTracker 開發指引

> 本文件供 Claude Code 自動讀取，確保每次 session 都有完整的專案脈絡。
> 放置於 repo 根目錄，每次啟動 Claude Code 時會自動載入。

---

## 專案定位

**系統名稱：** LumenTracker（程式碼庫名稱：crypto-analyzer）
**論文題目：** 從偵查初勘到法庭舉證：以本機端溯源系統落實區塊鏈數位證據之規範化
**研究方法：** 設計科學研究法（DSR）
**核心定位：** 零預算、單機版、BYOK 架構的區塊鏈鑑識工具，專為臺灣執法機關第一線調查員設計

**設計哲學：**
- 不是商用黑箱軟體（Chainalysis），而是透明、可驗證的鑑識工具
- 不上傳至雲端，完全本機端運行，確保「偵查不公開」原則
- 產出符合 ISO/IEC 27037 標準與 ARRJ 原則的法庭級報告

---

## 目前架構

```
crypto_analyzer/
├── main.py                        # 入口點：init_db() + App().mainloop()
├── config.py                      # API Key 設定讀寫（JSON）
├── requirements.txt               # 依賴套件
├── DATA_SOURCES.md                # 資料來源說明
│
├── analyzer/
│   ├── wallet_profiler.py         # ETH/TRX/BTC 錢包摘要分析（Target Profiler 核心）
│   ├── tx_analyzer.py             # 單筆交易 Hash 分析
│   ├── time_filter.py             # 時間範圍/置中篩選邏輯
│   └── doc_transaction_extractor.py  # OCR 文件交易提取（Tesseract）
│
├── api/
│   ├── etherscan.py               # Etherscan API（ETH，支援 V1/V2）
│   ├── tronscan.py                # TronScan API（TRX）
│   └── bitcoin.py                 # Bitcoin API（BTC）
│
├── database/
│   └── db.py                      # SQLite CRUD：錢包、案件、交易、Hash查詢
│
├── exporter/
│   └── report.py                  # Excel/CSV 匯出（openpyxl）
│
└── gui/
    ├── main_window.py             # 主視窗 App 類別（ctk.CTk）
    ├── case_window.py             # 案件建立/編輯對話框
    └── victim_tx_panel.py         # 被害人陳述交易紀錄面板
```

---

## 已完成功能

| 功能模組 | 完成度 | 對應論文目標 |
|---|---|---|
| 多鏈查詢（ETH/TRX/BTC） | ✅ 完成 | BYOK 架構 |
| 自動地址格式偵測 | ✅ 完成 | 防呆機制 |
| 錢包摘要分析（Target Profiler） | ✅ 完成 | 初勘側寫 |
| 單筆 Hash 交易分析 | ✅ 完成 | 鑑識分析 |
| 時間篩選（範圍/置中模式） | ✅ 完成 | 時序重建 |
| 案件管理（建立/編輯/刪除） | ✅ 完成 | 案件沙盒 |
| ERC-20 授權追蹤（Approval） | ✅ 完成 | 異常授權偵測 |
| 查詢歷史（錢包/Hash） | ✅ 完成 | 稽核紀錄 |
| OCR 文件交易提取 | ✅ 完成 | 文件鑑識 |
| Excel/CSV 匯出 | ✅ 完成 | 報告產出 |
| 一般/專案查詢模式 | ✅ 完成 | 案件關聯 |
| 被害人陳述交易對照 | ✅ 完成 | 雙欄對照 |

---

## 待開發功能（論文核心，依優先順序）

### 第一優先：幣流關聯圖（Tracing Graph）

**論文意義：** 直接對應論文第三章判決需求——新竹地院 115 年度聲字第 18 號裁定中，警方的「鏈上溯源分析報告」（含資金匯聚拓撲圖）直接影響了 286,624 顆 USDT 的沒收裁定。

新增套件：networkx>=3.2
新增檔案：analyzer/flow_builder.py、gui/flow_graph_panel.py

功能要求：
- 節點：每個唯一地址為一個節點，大小依交易量縮放
- 邊：每筆交易為有向箭頭，標示金額與時間
- 顏色：起始地址（紅）、流入（綠）、流出（橘）、已知交易所（藍）
- 點擊節點可自動填入主視窗地址欄繼續追查（互動式教學核心）
- 支援「混同視覺化」：多名被害人資金匯聚至同一節點的拓撲呈現
- 工具列：縮放、平移、儲存圖片（供報告使用）

### 第二優先：SHA-256 證據封裝機制

**論文意義：** 對應研究目的第 3 點與 ISO/IEC 27037 ARRJ 原則，是 LumenTracker 區別於普通查詢工具的核心法學特性。

新增檔案：analyzer/evidence_hasher.py

功能要求：
- 每次 API 查詢後立即計算 SHA-256，存入 SQLite
- Hash 內容包含：原始數據、時間戳、API 來源版本、調查員 ID
- GUI 上顯示「證據指紋」欄位
- 匯出報告時自動附上 Hash 值與驗證方式

### 第三優先：法庭級 PDF 報告

新增套件：reportlab>=4.0
新增檔案：exporter/pdf_report.py

報告結構：
1. 封面（案件編號、查詢地址、分析時間、調查員 ID）
2. 證據指紋區（SHA-256 Hash、API 來源、時間戳）
3. 錢包摘要表
4. 幣流關聯圖（matplotlib 圖片嵌入）
5. 交易明細表（每頁最多 50 筆）
6. 匯率換算說明（雙軌高低均價法計算過程）
7. 操作稽核日誌

### 第四優先：雙軌匯率引擎

**論文意義：** 「雙軌高低均價計算核定法（Dual-Track High/Low Average Method）」是本論文提出的創新方法，解決法庭上虛擬貨幣法幣換算客觀性問題。

新增套件：yfinance>=0.2.0
新增檔案：analyzer/exchange_rate.py

計算邏輯：
- 第一軌：抓取 Crypto-USD 當日最高價與最低價，取算術平均
- 第二軌：抓取 USDTWD=X 當日最高價與最低價，取算術平均
- 結果：兩軌均價相乘得當日參考匯率
- 嚴格摒棄單一收盤價，確保法學正當性（Justifiability）

### 第五優先：操作稽核日誌（Audit Logger）

**論文意義：** 對應 ARRJ 可稽核性（Auditability）原則。

新增檔案：analyzer/audit_logger.py

記錄內容：操作類型、目標地址/Hash、結果 SHA-256、時間戳、調查員 ID、案件 ID

---

## 套件依賴（完整版）

現有套件：
customtkinter>=5.2.2、requests>=2.31.0、openpyxl>=3.1.2、matplotlib>=3.8.0
Pillow>=10.0.0、pypdf>=4.0.0、pdfplumber>=0.11.0、pytesseract>=0.3.13

待加入：
networkx>=3.2（幣流關聯圖）
reportlab>=4.0（PDF 法庭報告）
yfinance>=0.2.0（雙軌匯率引擎）

---

## 論文對應功能驗證表

| 論文研究目的 | 對應系統功能 | 目前狀態 |
|---|---|---|
| 目的1：建構風險評分模型 | wallet_profiler 異常指標量化 | 部分完成（需加強量化評分） |
| 目的2：BYOK 單機版工具 | 現有架構 | 已完成 |
| 目的3：SHA-256 雜湊封裝 | evidence_hasher（待實作） | 待開發 |
| 目的4：幣流關聯圖+法庭展演 | flow_graph_panel（待實作） | 待開發 |
| 雙軌匯率換算 | exchange_rate（待實作） | 待開發 |
| ARRJ 可稽核性 | audit_logger（待實作） | 待開發 |
| PDF 法庭報告 | pdf_report（待實作） | 待開發 |

---

## 開發規範

程式碼風格：
- 所有 UI 文字使用繁體中文
- 使用 threading.Thread(daemon=True) 執行 API 呼叫
- API 呼叫結果用 self.after(0, callback) 回到主執行緒更新 UI
- 錯誤處理一律顯示 messagebox.showerror()，附上中文說明

GUI 規範：
- 統一使用 customtkinter（ctk）元件，深色主題
- 字型：中文用 Microsoft JhengHei，程式碼/地址用 Consolas
- 新增按鈕時考慮操作中的 state="disabled" 狀態

證據完整性規範（論文要求）：
- 所有 API 回傳資料必須記錄來源（API 名稱、版本、查詢時間戳）
- Hash 計算在 API 呼叫完成後立即執行，不可在資料處理後才計算
- Audit Log 需包含：操作類型、目標地址/Hash、結果 Hash、時間戳、調查員 ID

資料庫規範：
- 所有 DB 操作集中在 database/db.py
- 函式命名：get_xxx、save_xxx、update_xxx、delete_xxx

---

## Git 提交規範

feat: 新增功能
fix: 修正錯誤
ui: UI/UX 調整
forensic: 鑑識功能相關（Hash封裝、Audit Log等）
refactor: 重構
docs: 文件更新

範例：
- feat: 新增幣流關聯圖分頁（networkx + matplotlib）
- forensic: 實作 SHA-256 證據封裝機制
- feat: 新增雙軌高低均價匯率引擎
