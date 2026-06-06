# CLAUDE.md — crypto-analyzer 開發指引

> 這份文件是給 Claude Code 的專案說明文件，放在 repo 根目錄後，
> Claude Code 每次啟動都會自動讀取，確保每次對話都有完整的專案脈絡。

---

## 專案概述

**專案名稱：** 虛擬貨幣錢包分析工具（crypto-analyzer）
**目標：** 互動式教學型區塊鏈金流追查桌面軟體，供執法/調查人員使用
**語言：** Python 3.11+
**UI 框架：** CustomTkinter（深色主題）
**資料庫：** SQLite（透過 `database/db.py`）

---

## 目前架構

```
crypto_analyzer/
├── main.py                  # 入口點：init_db() + App().mainloop()
├── config.py                # API Key 設定讀寫（JSON）
├── debug_api.py             # API 除錯工具
├── requirements.txt         # 依賴套件
├── DATA_SOURCES.md          # 資料來源說明
│
├── analyzer/
│   ├── wallet_profiler.py   # ETH/TRX/BTC 錢包摘要分析
│   ├── tx_analyzer.py       # 單筆交易 Hash 分析
│   ├── time_filter.py       # 時間範圍/置中篩選邏輯
│   └── doc_transaction_extractor.py  # OCR 文件交易提取（Tesseract）
│
├── api/
│   ├── etherscan.py         # Etherscan API（ETH，支援 V1/V2）
│   ├── tronscan.py          # TronScan API（TRX）
│   └── bitcoin.py           # Bitcoin API（BTC，blockchain.com）
│
├── database/
│   └── db.py                # SQLite CRUD：錢包、案件、交易、Hash查詢
│
├── exporter/
│   └── report.py            # Excel/CSV 匯出（openpyxl）
│
└── gui/
    ├── main_window.py        # 主視窗 App 類別（ctk.CTk）
    ├── case_window.py        # 案件建立/編輯對話框
    ├── victim_tx_panel.py    # 被害人陳述交易紀錄面板
    └── ...（其他 GUI 元件）
```

---

## 已完成功能

| 功能 | 狀態 | 說明 |
|------|------|------|
| 多鏈查詢 | ✅ 完成 | ETH / TRX / BTC，自動偵測地址格式 |
| 錢包摘要 | ✅ 完成 | 發送/接收次數、金額、首次資金來源、Token 統計 |
| 交易表格 | ✅ 完成 | 原始交易 + Token 轉帳分頁，右鍵複製 |
| Hash 分析 | ✅ 完成 | 單筆交易詳細資訊 + Token 明細 |
| 時間篩選 | ✅ 完成 | 範圍模式 + 置中模式，前後各 N 筆 |
| 案件管理 | ✅ 完成 | 建立/編輯/刪除，關聯錢包與 Hash |
| 授權追蹤 | ✅ 完成 | ERC-20 Approval 紀錄（ETH） |
| 查詢歷史 | ✅ 完成 | 錢包分析 + Hash 查詢歷史，可搜尋/刪除 |
| OCR 匯入 | ✅ 完成 | 從文件資料夾提取交易資料（Tesseract） |
| Excel 匯出 | ✅ 完成 | openpyxl |
| CSV 匯出 | ✅ 完成 | 多檔案分類匯出 |
| 一般/專案查詢模式 | ✅ 完成 | 專案模式自動儲存並關聯案件 |

---

## 待開發功能（優先順序）

### 🥇 第一優先：金流圖視覺化

**目標：** 在 GUI 新增「金流圖」分頁，以有向節點圖顯示地址間的資金流向

**需要安裝的套件：**
```
networkx>=3.2
matplotlib>=3.8.0  # 已有
```

**建議實作位置：**
- 新增 `gui/flow_graph_panel.py`
- 在 `main_window.py` 的分頁列表加入「金流圖」
- 新增 `analyzer/flow_builder.py` 負責從 `raw_txs` 建立圖結構

**功能規格：**
- 節點：每個唯一地址為一個節點，大小依交易量縮放
- 邊：每筆交易為一條有向箭頭，標示金額
- 顏色：起始查詢地址（紅色）、流入地址（綠色）、流出地址（橘色）
- 互動：點擊節點可自動填入主視窗地址欄並查詢（追查下一層）
- 工具列：縮放、平移、存圖（PNG）

**技術參考：**
```python
import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

G = nx.DiGraph()
G.add_edge("地址A", "地址B", weight=1.5, label="1.5 ETH")
fig, ax = plt.subplots(figsize=(10, 7))
nx.draw_networkx(G, ax=ax, with_labels=True, arrows=True)
canvas = FigureCanvasTkAgg(fig, master=parent_frame)
canvas.get_tk_widget().pack(fill="both", expand=True)
```

---

### 🥈 第二優先：PDF 報告產出

**目標：** 將分析結果（摘要 + 交易表 + 金流圖）整合為一份正式 PDF 報告

**需要安裝的套件：**
```
reportlab>=4.0
```

**建議實作位置：**
- 新增 `exporter/pdf_report.py`
- 在主視窗工具列加入「產出報告」按鈕

**報告結構：**
1. 封面（案件編號、地址、分析日期、承辦人）
2. 錢包摘要表
3. 金流圖（matplotlib 圖片嵌入）
4. 交易明細表（分頁，每頁最多 50 筆）
5. 重要發現摘要（首次資金來源、最大交易、授權紀錄）

---

### 🥉 第三優先：教學嚮導模式

**目標：** 新增「追查嚮導」模式，引導新手一步步完成金流追查

**建議實作位置：**
- 新增 `gui/wizard_panel.py`
- 在主視窗頂部加入「嚮導模式」切換按鈕

**嚮導流程：**
```
步驟 1：輸入可疑地址
  └─ 說明：什麼是錢包地址？如何取得？
步驟 2：查看錢包摘要
  └─ 說明：解讀首次資金來源、大額交易
步驟 3：金流圖追蹤
  └─ 說明：如何識別可疑流向？中轉地址特徵
步驟 4：深入追查（選擇節點）
  └─ 說明：點擊可疑地址繼續追查
步驟 5：產出報告
  └─ 說明：如何撰寫金流追查報告
```

---

## 開發規範

### 程式碼風格
- 所有 UI 文字使用**繁體中文**
- 使用 `threading.Thread(daemon=True)` 執行 API 呼叫，避免 UI 凍結
- API 呼叫結果用 `self.after(0, callback)` 回到主執行緒更新 UI
- 錯誤處理一律顯示 `messagebox.showerror()`

### GUI 規範
- 統一使用 `customtkinter`（ctk）元件，**不要混用原生 tk 元件**（除了 ttk.Treeview）
- 深色主題：`ctk.set_appearance_mode("dark")`
- 字型：中文用 `Microsoft JhengHei`，程式碼/地址用 `Consolas`
- 新增任何按鈕都要考慮操作進行中的 `state="disabled"` 狀態

### 資料庫規範
- 所有 DB 操作都在 `database/db.py` 集中管理
- 函式命名規則：`get_xxx`、`save_xxx`、`update_xxx`、`delete_xxx`
- 不要在 GUI 層直接操作 SQLite

### API 規範
- API Key 從 `config.py` 的 `load_config()` 取得
- 所有 API 呼叫要有 timeout（建議 15 秒）
- 失敗要拋出帶有中文說明的 Exception

---

## 套件依賴（完整版）

```
# 現有
customtkinter>=5.2.2
requests>=2.31.0
openpyxl>=3.1.2
matplotlib>=3.8.0
Pillow>=10.0.0
pypdf>=4.0.0
pdfplumber>=0.11.0
pytesseract>=0.3.13

# 待加入（配合待開發功能）
networkx>=3.2        # 金流圖
reportlab>=4.0       # PDF 報告
```

---

## 常見問題與注意事項

1. **Tesseract 路徑**：Windows 需在 `config.py` 或環境變數設定 `pytesseract.pytesseract.tesseract_cmd`
2. **Etherscan API 速率限制**：免費版每秒 5 次，V2 API 建議加 `time.sleep(0.25)`
3. **BTC 地址格式**：支援 Legacy（1...）、P2SH（3...）、Bech32（bc1...）
4. **大量交易**：超過 5000 筆時 `_rebuild_tree()` 只顯示前 5000 筆，注意告知用戶
5. **多執行緒安全**：tkinter 不是 thread-safe，所有 UI 更新必須透過 `self.after()`

---

## Git 提交規範

```
feat: 新增功能
fix: 修正錯誤
ui: UI/UX 調整
refactor: 重構（不影響功能）
docs: 文件更新
```

範例：`feat: 新增金流圖分頁（networkx + matplotlib）`
