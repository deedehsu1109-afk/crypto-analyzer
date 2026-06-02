# 資料來源說明文件

## 概覽

本程式支援三條區塊鏈（Ethereum、TRON、Bitcoin）的錢包分析與交易查詢。
各鏈採用不同的公開 API 服務取得資料，部分功能提供備用來源以確保可用性。

---

## 一、Ethereum（ETH）

### 主要來源：Etherscan API

| 項目 | 說明 |
|------|------|
| 服務商 | [Etherscan](https://etherscan.io) |
| 官網 | https://etherscan.io |
| API 文件 | https://docs.etherscan.io |
| 需要 API Key | **是**（免費申請） |
| API Key 申請 | https://etherscan.io/myapikey |

#### 使用的 API 端點

| 功能 | API 端點 | 說明 |
|------|---------|------|
| 一般交易 | `module=account&action=txlist` | 抓取 ETH 原生轉帳記錄 |
| Internal 交易 | `module=account&action=txlistinternal` | 智能合約內部呼叫 |
| ERC-20 轉帳 | `module=account&action=tokentx` | Token 轉帳（USDC、USDT 等） |
| 交易詳情（Hash） | `module=proxy&action=eth_getTransactionByHash` | 單筆交易完整資料 |
| 交易收據 | `module=proxy&action=eth_getTransactionReceipt` | 交易狀態、Gas 使用量 |
| 餘額查詢 | `module=account&action=balance` | 當前 ETH 餘額 |

#### API 版本自動偵測機制

程式啟動時會依序測試以下端點，選用第一個可用的：

```
優先順序：
  1. Etherscan V2  →  https://api.etherscan.io/v2/chainquery?chainid=1&...
  2. Etherscan V1  →  https://api.etherscan.io/api?...
  3. Blockscout    →  https://eth.blockscout.com/api?...（備用，無需 Key）
```

> Etherscan V1 已於 2024 年底宣布停用，建議使用 V2 或 Blockscout 備用。

---

### 備用來源：Blockscout API

| 項目 | 說明 |
|------|------|
| 服務商 | [Blockscout](https://eth.blockscout.com) |
| 官網 | https://eth.blockscout.com |
| API 文件 | https://eth.blockscout.com/api-docs |
| 需要 API Key | **否**（完全免費） |
| 使用時機 | Etherscan API Key 無效或 V2 回傳異常時自動切換 |

#### 使用的 API 端點（v2 REST）

| 功能 | API 端點 |
|------|---------|
| 交易詳情 | `GET /api/v2/transactions/{tx_hash}` |
| Token 轉帳 | `GET /api/v2/transactions/{tx_hash}/token-transfers` |
| 帳戶餘額 | `GET /api?module=account&action=balance` |

---

## 二、TRON（TRX）

### 來源：TronScan API

| 項目 | 說明 |
|------|------|
| 服務商 | [TronScan](https://tronscan.org) |
| 官網 | https://tronscan.org |
| API 文件 | https://github.com/tronscan/tronscan-frontend/blob/master/document/api.md |
| 需要 API Key | **否**（公開存取） |
| API Base URL | `https://apilist.tronscanapi.com/api` |

#### 使用的 API 端點

| 功能 | API 端點 | 說明 |
|------|---------|------|
| 帳戶資訊 | `/account?address={addr}` | 餘額、資源等帳戶資訊 |
| 一般交易 | `/transaction?address={addr}` | TRX 原生轉帳與合約呼叫 |
| TRC-20 轉帳 | `/token_trc20/transfers?relatedAddress={addr}` | USDT、USDC 等 Token 轉帳 |
| 交易詳情 | `/transaction-info?hash={hash}` | 單筆交易完整資料 |

#### TRON 地址格式

TRON 地址採用 **Base58Check** 編碼，固定以 `T` 開頭，共 34 個字元。
範例：`TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE`

---

## 三、Bitcoin（BTC）

### 來源：Blockchain.com API

| 項目 | 說明 |
|------|------|
| 服務商 | [Blockchain.com](https://blockchain.com) |
| 官網 | https://blockchain.com |
| API 文件 | https://www.blockchain.com/explorer/api/blockchain_api |
| 需要 API Key | **否**（公開存取） |
| API Base URL | `https://blockchain.info` |

#### 使用的 API 端點

| 功能 | API 端點 | 說明 |
|------|---------|------|
| 地址資訊 | `/rawaddr/{address}` | 交易歷史與餘額 |
| 餘額查詢 | `/balance?active={address}` | 當前 BTC 餘額（Satoshi） |
| 交易詳情 | `/rawtx/{tx_hash}` | 單筆交易完整資料 |

#### BTC 地址格式

| 格式 | 前綴 | 說明 |
|------|------|------|
| Legacy | `1` | 最早期格式 |
| P2SH | `3` | 多簽與腳本 |
| Bech32 | `bc1` | SegWit 格式，手續費較低 |

---

## 四、資料抓取限制

| 區塊鏈 | 每次最多筆數 | 速率限制 | 備註 |
|--------|------------|---------|------|
| ETH（Etherscan） | 10,000 筆/頁 | 5 次/秒（免費版） | 多頁自動翻頁 |
| ETH（Blockscout） | 無官方限制 | 請勿頻繁請求 | 備用來源 |
| TRX | 10,000 筆 | 無官方說明 | 每 0.3 秒一次請求 |
| BTC | 5,000 筆 | 無官方說明 | 每 0.5 秒一次請求 |

---

## 五、本地資料儲存

所有抓取的資料會自動儲存至本地 SQLite 資料庫：

| 檔案路徑 | 說明 |
|---------|------|
| `crypto_data.db` | SQLite 資料庫檔案 |

### 資料庫資料表

| 資料表 | 儲存內容 |
|--------|---------|
| `wallets` | 錢包摘要（統計數據、首末時間、資金來源） |
| `transactions` | 原始交易記錄（含 ERC-20 / TRC-20） |
| `approvals` | Token 授權記錄 |
| `tx_lookups` | 交易 Hash 查詢歷史 |

> `config.json`（含 API Key）與 `crypto_data.db` 已加入 `.gitignore`，**不會上傳至 GitHub**。

---

## 六、授權交易（Approval）偵測方式

### ETH / TRX

透過篩選交易的 `input data` 欄位：
- ERC-20 / TRC-20 的 `approve()` 函數選擇器為 `0x095ea7b3`
- 若交易 input 以此開頭，且發送方為查詢地址，則判定為授權交易
- 從 input data 中解碼出被授權對象（spender）地址

```
Input Data 結構（approve 函數）：
  0x095ea7b3                           → 函數選擇器（4 bytes）
  000000000000000000000000{spender}    → 被授權地址（32 bytes）
  {amount}                             → 授權金額（32 bytes）
```

---

## 七、跨鏈橋偵測（規劃中）

目前版本尚未實作跨鏈交易自動識別，未來計劃透過以下方式偵測：
- 比對已知跨鏈橋合約地址（如 Multichain、Wormhole、LayerZero）
- 分析相同時間區間內不同鏈上的對應交易

---

## 八、資料準確性說明

| 項目 | 說明 |
|------|------|
| 資料即時性 | 依各 API 服務商的更新頻率，通常為即時或數秒延遲 |
| 歷史資料 | 完整鏈上歷史，理論上無缺漏 |
| Token 金額精度 | 依各 Token 合約設定的 decimals 換算 |
| 手續費計算 | ETH = gasUsed × gasPrice；TRX = fee（sun）；BTC = inputs - outputs |
| 時間顯示 | 所有時間統一轉換為 **UTC+8（台灣標準時間）** |
