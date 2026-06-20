from __future__ import annotations
import sqlite3
import json
import os
import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "crypto_data.db")


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    """建立所有資料表（若不存在），並執行欄位遷移"""
    with _conn() as con:
        con.executescript("""
        -- ── 案件主表 ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS cases (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            case_number   TEXT    NOT NULL UNIQUE,
            case_name     TEXT    NOT NULL,
            case_type     TEXT    DEFAULT '一般',
            status        TEXT    DEFAULT '進行中',
            investigator  TEXT,
            created_at    TEXT    DEFAULT (datetime('now','localtime')),
            updated_at    TEXT    DEFAULT (datetime('now','localtime')),
            transcript    TEXT,   -- 筆錄原文（從文件匯入的完整內容）
            description   TEXT,   -- 案件摘要（使用者自行填寫，顯示為「說明」）
            notes         TEXT
        );

        -- ── 錢包摘要 ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS wallets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id       INTEGER REFERENCES cases(id) ON DELETE SET NULL,
            chain         TEXT    NOT NULL,
            address       TEXT    NOT NULL,
            label         TEXT,
            analyzed_at   TEXT    DEFAULT (datetime('now','localtime')),
            first_tx_time TEXT,
            last_tx_time  TEXT,
            first_source  TEXT,
            out_count     INTEGER DEFAULT 0,
            in_count      INTEGER DEFAULT 0,
            eth_out_count INTEGER DEFAULT 0,
            eth_in_count  INTEGER DEFAULT 0,
            erc20_out_count INTEGER DEFAULT 0,
            erc20_in_count  INTEGER DEFAULT 0,
            out_total     REAL    DEFAULT 0,
            in_total      REAL    DEFAULT 0,
            total_fee     REAL    DEFAULT 0,
            top_fee_dest  TEXT,
            token_transfer_count INTEGER DEFAULT 0,
            UNIQUE(chain, address)
        );

        -- ── 交易明細 ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_id    INTEGER REFERENCES wallets(id) ON DELETE CASCADE,
            chain        TEXT NOT NULL,
            address      TEXT NOT NULL,
            tx_hash      TEXT,
            block_number TEXT,
            tx_time      TEXT,
            from_addr    TEXT,
            to_addr      TEXT,
            value_raw    TEXT,
            value_native REAL DEFAULT 0,
            gas_used     TEXT,
            gas_price    TEXT,
            fee_native   REAL DEFAULT 0,
            is_error     INTEGER DEFAULT 0,
            tx_type      TEXT,
            token_name   TEXT,
            token_symbol TEXT,
            token_contract TEXT,
            token_amount REAL DEFAULT 0,
            raw_json     TEXT
        );

        -- ── 授權記錄 ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS approvals (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_id  INTEGER REFERENCES wallets(id) ON DELETE CASCADE,
            chain      TEXT NOT NULL,
            address    TEXT NOT NULL,
            tx_hash    TEXT,
            contract   TEXT,
            spender    TEXT,
            amount     TEXT,
            time       TEXT
        );

        -- ── Hash 查詢歷史 ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS tx_lookups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id     INTEGER REFERENCES cases(id) ON DELETE SET NULL,
            chain       TEXT NOT NULL,
            tx_hash     TEXT NOT NULL,
            queried_at  TEXT DEFAULT (datetime('now','localtime')),
            status      TEXT,
            from_addr   TEXT,
            to_addr     TEXT,
            value_str   TEXT,
            fee_str     TEXT,
            block       TEXT,
            tx_time     TEXT,
            raw_json    TEXT,
            UNIQUE(chain, tx_hash)
        );

        -- ── 被害人陳述交易紀錄 ───────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS victim_transactions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id       INTEGER REFERENCES cases(id) ON DELETE CASCADE,
            tx_date       TEXT,         -- 日期 YYYY-MM-DD
            tx_time       TEXT,         -- 時間 HH:MM:SS（UTC+8）
            from_addr     TEXT,         -- FROM 錢包地址（全碼）
            to_addr       TEXT,         -- TO 錢包地址（全碼）
            amount_ntd    REAL,         -- 金額（新台幣 NT）
            quantity      REAL,         -- 數量（幣種單位）
            currency      TEXT,         -- 幣種（BTC/ETH/USDT/...）
            exchange_rate REAL,         -- 交易匯率（NT/幣種）
            daily_avg     REAL,         -- 當日均價（(最高+最低)/2，NT）
            daily_high    REAL,         -- 當日最高價（NT）
            daily_low     REAL,         -- 當日最低價（NT）
            source_doc    TEXT,         -- 來源文件路徑
            notes         TEXT,         -- 備註
            created_at    TEXT DEFAULT (datetime('now','localtime')),
            updated_at    TEXT DEFAULT (datetime('now','localtime'))
        );

        -- ── 索引 ──────────────────────────────────────────────────────────────
        -- ── 幣流圖快照（證據模式用）────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS graph_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id     INTEGER REFERENCES cases(id) ON DELETE CASCADE,
            chain       TEXT    NOT NULL,
            label       TEXT,
            nodes_json  TEXT    NOT NULL,   -- JSON: [{id, address, role, custom_label, ...}]
            edges_json  TEXT    NOT NULL,   -- JSON: [{source, target, tx_hash, amount, ...}]
            saved_at    TEXT    DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_txs_wallet    ON transactions(wallet_id);
        CREATE INDEX IF NOT EXISTS idx_txs_hash      ON transactions(tx_hash);
        CREATE INDEX IF NOT EXISTS idx_txs_addr      ON transactions(address);
        CREATE INDEX IF NOT EXISTS idx_approvals_wid ON approvals(wallet_id);
        CREATE INDEX IF NOT EXISTS idx_lookup_hash   ON tx_lookups(tx_hash);
        CREATE INDEX IF NOT EXISTS idx_victim_tx_case ON victim_transactions(case_id);
        CREATE INDEX IF NOT EXISTS idx_graph_snap_case ON graph_snapshots(case_id);

        -- ── 涉案錢包地址 / 金融帳戶 ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS case_addresses (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id           INTEGER REFERENCES cases(id) ON DELETE CASCADE,
            addr_type         TEXT    DEFAULT '加密錢包',  -- '加密錢包' / '金融帳戶'
            chain_institution TEXT,   -- TRX / ETH / BTC 或 銀行名稱（如 玉山銀行808）
            address           TEXT    NOT NULL,           -- 錢包地址或帳號
            holder_role       TEXT    DEFAULT '不明',     -- 被害人 / 嫌疑人 / 中間人 / 不明
            label             TEXT,                       -- 標記說明（如「被害人OKX帳戶」）
            source_doc        TEXT,                       -- 來源文件
            notes             TEXT,
            created_at        TEXT    DEFAULT (datetime('now','localtime')),
            updated_at        TEXT    DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_case_addr_case ON case_addresses(case_id);

        -- ── 一般帳戶交易紀錄 ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS case_bank_transactions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id             INTEGER REFERENCES cases(id) ON DELETE CASCADE,
            tx_date             TEXT,           -- YYYY-MM-DD
            tx_time             TEXT,           -- HH:MM
            direction           TEXT DEFAULT '不明',  -- 入帳 / 出帳 / 不明
            bank_name           TEXT,           -- 銀行名稱
            account_no          TEXT,           -- 帳戶號碼
            counterpart_name    TEXT,           -- 對方戶名
            counterpart_account TEXT,           -- 對方帳號
            amount              REAL,           -- 金額
            currency            TEXT DEFAULT 'TWD',
            balance             REAL,           -- 餘額（選填）
            notes               TEXT,
            source_doc          TEXT,
            created_at          TEXT DEFAULT (datetime('now','localtime')),
            updated_at          TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_bank_tx_case ON case_bank_transactions(case_id);

        -- ── 區塊鏈交易紀錄 ────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS case_chain_transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id      INTEGER REFERENCES cases(id) ON DELETE CASCADE,
            chain        TEXT NOT NULL DEFAULT 'TRX',
            direction    TEXT DEFAULT '不明',   -- 入帳 / 出帳 / 不明
            tx_datetime  TEXT,                  -- YYYY-MM-DD HH:MM:SS
            tx_hash      TEXT,
            from_addr    TEXT,
            to_addr      TEXT,
            amount       REAL,
            token_symbol TEXT,                  -- USDT / TRX / ETH …
            notes        TEXT,
            source_doc   TEXT,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            updated_at   TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_chain_tx_case ON case_chain_transactions(case_id);
        """)
        # 遷移：對舊資料庫補欄位（若尚未存在）
        _migrate(con)


def _migrate(con: sqlite3.Connection):
    existing_w = {r[1] for r in con.execute("PRAGMA table_info(wallets)").fetchall()}
    if "case_id" not in existing_w:
        con.execute("ALTER TABLE wallets ADD COLUMN case_id INTEGER REFERENCES cases(id) ON DELETE SET NULL")
    if "label" not in existing_w:
        con.execute("ALTER TABLE wallets ADD COLUMN label TEXT")
    existing_l = {r[1] for r in con.execute("PRAGMA table_info(tx_lookups)").fetchall()}
    if "case_id" not in existing_l:
        con.execute("ALTER TABLE tx_lookups ADD COLUMN case_id INTEGER REFERENCES cases(id) ON DELETE SET NULL")
    existing_c = {r[1] for r in con.execute("PRAGMA table_info(cases)").fetchall()}
    if "transcript" not in existing_c:
        con.execute("ALTER TABLE cases ADD COLUMN transcript TEXT")
    # case_id 欄位存在後才能建索引
    con.execute("CREATE INDEX IF NOT EXISTS idx_wallets_case ON wallets(case_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_lookups_case ON tx_lookups(case_id)")


# ── 儲存錢包分析結果 ──────────────────────────────────────────────────────────

def save_wallet_profile(profile: dict) -> int:
    """
    插入或更新錢包摘要，回傳 wallet_id。
    """
    chain = profile["chain"]
    addr  = profile["address"]

    def _key(eth_k, trx_k, btc_k):
        return {"ETH": eth_k, "TRX": trx_k, "BTC": btc_k}.get(chain, eth_k)

    out_total = profile.get(_key("out_total_eth","out_total_trx","out_total_btc"), 0)
    in_total  = profile.get(_key("in_total_eth", "in_total_trx", "in_total_btc"),  0)
    fee_total = profile.get(_key("total_fee_eth","total_fee_trx","total_fee_btc"), 0)
    token_cnt = profile.get("erc20_transfer_count",
                profile.get("trc20_transfer_count", 0))

    with _conn() as con:
        con.execute("""
            INSERT INTO wallets
                (chain, address, first_tx_time, last_tx_time, first_source,
                 out_count, in_count, eth_out_count, eth_in_count,
                 erc20_out_count, erc20_in_count,
                 out_total, in_total, total_fee, top_fee_dest,
                 token_transfer_count, analyzed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))
            ON CONFLICT(chain, address) DO UPDATE SET
                first_tx_time = excluded.first_tx_time,
                last_tx_time  = excluded.last_tx_time,
                first_source  = excluded.first_source,
                out_count     = excluded.out_count,
                in_count      = excluded.in_count,
                eth_out_count = excluded.eth_out_count,
                eth_in_count  = excluded.eth_in_count,
                erc20_out_count = excluded.erc20_out_count,
                erc20_in_count  = excluded.erc20_in_count,
                out_total     = excluded.out_total,
                in_total      = excluded.in_total,
                total_fee     = excluded.total_fee,
                top_fee_dest  = excluded.top_fee_dest,
                token_transfer_count = excluded.token_transfer_count,
                analyzed_at   = excluded.analyzed_at
        """, (
            chain, addr,
            profile.get("first_tx_time"), profile.get("last_tx_time"),
            profile.get("first_source"),
            profile.get("out_count", 0), profile.get("in_count", 0),
            profile.get("eth_out_count", 0), profile.get("eth_in_count", 0),
            profile.get("erc20_out_count", 0), profile.get("erc20_in_count", 0),
            out_total, in_total, fee_total,
            profile.get("top_fee_dest"), token_cnt,
        ))
        wallet_id = con.execute(
            "SELECT id FROM wallets WHERE chain=? AND address=?", (chain, addr)
        ).fetchone()["id"]

    # 儲存交易與 Token 轉帳（背景批次，不阻塞 UI）
    _save_transactions(wallet_id, profile)
    _save_approvals(wallet_id, profile)
    return wallet_id


def _save_transactions(wallet_id: int, profile: dict):
    chain = profile["chain"]
    addr  = profile["address"]
    rows  = []

    def _wei(v):
        try: return int(v) / 1e18
        except: return 0.0

    def _sun(v):
        try: return int(v) / 1e6
        except: return 0.0

    if chain == "ETH":
        for tx in profile.get("raw_txs", []):
            fee = 0.0
            try:
                fee = int(tx.get("gasUsed",0)) * int(tx.get("gasPrice",0)) / 1e18
            except: pass
            rows.append((
                wallet_id, chain, addr,
                tx.get("hash",""), tx.get("blockNumber",""),
                tx.get("timeStamp",""), tx.get("from",""), tx.get("to",""),
                tx.get("value","0"), _wei(tx.get("value",0)),
                tx.get("gasUsed",""), tx.get("gasPrice",""), fee,
                int(tx.get("isError","0")), "normal",
                None, None, None, 0.0,
                json.dumps(tx, ensure_ascii=False),
            ))
        for tx in profile.get("raw_erc20", []):
            decimals = int(tx.get("tokenDecimal", 18) or 18)
            try: amount = int(tx.get("value",0)) / (10**decimals)
            except: amount = 0.0
            rows.append((
                wallet_id, chain, addr,
                tx.get("hash",""), tx.get("blockNumber",""),
                tx.get("timeStamp",""), tx.get("from",""), tx.get("to",""),
                tx.get("value","0"), 0.0,
                tx.get("gasUsed",""), tx.get("gasPrice",""), 0.0,
                0, "erc20",
                tx.get("tokenName",""), tx.get("tokenSymbol",""),
                tx.get("contractAddress",""), amount,
                json.dumps(tx, ensure_ascii=False),
            ))

    elif chain == "TRX":
        for tx in profile.get("raw_txs", []):
            amt = _sun(tx.get("contractData",{}).get("amount",0))
            fee = _sun(tx.get("cost",{}).get("fee", tx.get("fee",0)))
            rows.append((
                wallet_id, chain, addr,
                tx.get("hash", tx.get("txID","")), str(tx.get("block","")),
                str(tx.get("timestamp","")), tx.get("ownerAddress",""),
                tx.get("toAddress",""), str(tx.get("contractData",{}).get("amount",0)),
                amt, "", "", fee, 0, "normal",
                None, None, None, 0.0,
                json.dumps(tx, ensure_ascii=False),
            ))
        for tx in profile.get("raw_trc20", []):
            try:
                decimals = int(tx.get("tokenDecimal", 6) or 6)
                amount = int(tx.get("amount",0)) / (10**decimals)
            except: amount = 0.0
            rows.append((
                wallet_id, chain, addr,
                tx.get("transactionId",""), "",
                str(tx.get("block_ts","")), tx.get("from_address",""),
                tx.get("to_address",""), str(tx.get("amount","0")),
                0.0, "", "", 0.0, 0, "trc20",
                tx.get("tokenName",""), tx.get("tokenAbbr",""),
                tx.get("contract_address",""), amount,
                json.dumps(tx, ensure_ascii=False),
            ))

    elif chain == "BTC":
        for tx in profile.get("raw_txs", []):
            fee = tx.get("fee", 0) / 1e8
            rows.append((
                wallet_id, chain, addr,
                tx.get("hash",""), str(tx.get("block_height","")),
                str(tx.get("time","")), "", "",
                "", 0.0, "", "", fee,
                0, "btc",
                None, None, None, 0.0,
                json.dumps(tx, ensure_ascii=False),
            ))

    if not rows:
        return
    with _conn() as con:
        con.execute("DELETE FROM transactions WHERE wallet_id=?", (wallet_id,))
        con.executemany("""
            INSERT INTO transactions
                (wallet_id,chain,address,tx_hash,block_number,tx_time,
                 from_addr,to_addr,value_raw,value_native,gas_used,gas_price,
                 fee_native,is_error,tx_type,token_name,token_symbol,
                 token_contract,token_amount,raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)


def _save_approvals(wallet_id: int, profile: dict):
    chain = profile["chain"]
    addr  = profile["address"]
    rows  = []
    for a in profile.get("approval_targets", []):
        rows.append((
            wallet_id, chain, addr,
            a.get("tx_hash",""), a.get("contract",""),
            a.get("spender",""), str(a.get("amount","")), a.get("time",""),
        ))
    if not rows:
        return
    with _conn() as con:
        con.execute("DELETE FROM approvals WHERE wallet_id=?", (wallet_id,))
        con.executemany("""
            INSERT INTO approvals
                (wallet_id,chain,address,tx_hash,contract,spender,amount,time)
            VALUES (?,?,?,?,?,?,?,?)
        """, rows)


# ── 儲存 Hash 查詢結果 ────────────────────────────────────────────────────────

def save_tx_lookup(result: dict, case_id: int = None):
    chain   = result.get("chain", "")
    tx_hash = result.get("hash", "")
    if not tx_hash:
        return
    with _conn() as con:
        con.execute("""
            INSERT INTO tx_lookups
                (chain,tx_hash,case_id,status,from_addr,to_addr,
                 value_str,fee_str,block,tx_time,raw_json,queried_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))
            ON CONFLICT(chain,tx_hash) DO UPDATE SET
                case_id   = COALESCE(excluded.case_id, case_id),
                status    = excluded.status,
                from_addr = excluded.from_addr,
                to_addr   = excluded.to_addr,
                value_str = excluded.value_str,
                fee_str   = excluded.fee_str,
                block     = excluded.block,
                tx_time   = excluded.tx_time,
                raw_json  = excluded.raw_json,
                queried_at= excluded.queried_at
        """, (
            chain, tx_hash, case_id, result.get("狀態",""),
            result.get("發送方",""), result.get("接收方",""),
            result.get("ETH 金額", result.get("TRX 金額", result.get("輸出總額",""))),
            result.get("手續費",""), result.get("區塊",""),
            result.get("時間",""),
            json.dumps(result, ensure_ascii=False),
        ))


# ── 查詢 ──────────────────────────────────────────────────────────────────────

def get_all_wallets() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM wallets ORDER BY analyzed_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_wallet_transactions(wallet_id: int, tx_type: str = None) -> list[dict]:
    sql = "SELECT * FROM transactions WHERE wallet_id=?"
    params = [wallet_id]
    if tx_type:
        sql += " AND tx_type=?"
        params.append(tx_type)
    sql += " ORDER BY tx_time ASC"
    with _conn() as con:
        rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_wallet_approvals(wallet_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM approvals WHERE wallet_id=?", (wallet_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_wallet_by_address(chain: str, address: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM wallets WHERE chain=? AND address=?", (chain, address)
        ).fetchone()
    return dict(row) if row else None


def load_profile_from_db(wallet_id: int, chain: str, address: str, wallet_row: dict) -> dict:
    """從資料庫重建 profile dict，供 _update_ui 直接呼叫（無需重新查詢 API）。"""
    txs = get_wallet_transactions(wallet_id)
    approvals_rows = get_wallet_approvals(wallet_id)

    raw_txs, raw_erc20, raw_trc20 = [], [], []
    erc20_out: dict = {}
    erc20_in:  dict = {}
    trc20_out: dict = {}
    trc20_in:  dict = {}
    trx_out_count = trx_in_count = 0
    trc20_out_count = trc20_in_count = 0

    addr_lower = address.lower()
    for row in txs:
        try:
            original = json.loads(row.get("raw_json") or "{}")
        except Exception:
            original = {}
        tx_type = row.get("tx_type", "")
        from_l  = (row.get("from_addr") or "").lower()

        if tx_type in ("normal", "btc"):
            raw_txs.append(original)
            if chain == "TRX":
                if from_l == addr_lower:
                    trx_out_count += 1
                else:
                    trx_in_count += 1
        elif tx_type == "erc20":
            raw_erc20.append(original)
            sym = row.get("token_symbol") or ""
            amt = float(row.get("token_amount") or 0)
            if from_l == addr_lower:
                erc20_out[sym] = erc20_out.get(sym, 0.0) + amt
            else:
                erc20_in[sym] = erc20_in.get(sym, 0.0) + amt
        elif tx_type == "trc20":
            raw_trc20.append(original)
            sym = row.get("token_symbol") or ""
            amt = float(row.get("token_amount") or 0)
            if from_l == addr_lower:
                trc20_out[sym] = trc20_out.get(sym, 0.0) + amt
                trc20_out_count += 1
            else:
                trc20_in[sym] = trc20_in.get(sym, 0.0) + amt
                trc20_in_count += 1

    approval_targets = [
        {"tx_hash": r["tx_hash"], "contract": r["contract"],
         "spender": r["spender"], "amount": r["amount"], "time": r["time"]}
        for r in approvals_rows
    ]

    out_k, in_k, fee_k = {
        "ETH": ("out_total_eth", "in_total_eth",  "total_fee_eth"),
        "TRX": ("out_total_trx", "in_total_trx",  "total_fee_trx"),
        "BTC": ("out_total_btc", "in_total_btc",  "total_fee_btc"),
    }.get(chain, ("out_total_eth", "in_total_eth", "total_fee_eth"))

    profile = {
        "chain":           chain,
        "address":         address,
        "first_tx_time":   wallet_row.get("first_tx_time"),
        "last_tx_time":    wallet_row.get("last_tx_time"),
        "first_source":    wallet_row.get("first_source"),
        "out_count":       wallet_row.get("out_count", 0),
        "in_count":        wallet_row.get("in_count",  0),
        "eth_out_count":   wallet_row.get("eth_out_count", 0),
        "eth_in_count":    wallet_row.get("eth_in_count",  0),
        "erc20_out_count": wallet_row.get("erc20_out_count", 0),
        "erc20_in_count":  wallet_row.get("erc20_in_count",  0),
        "trx_out_count":   trx_out_count,
        "trx_in_count":    trx_in_count,
        "trc20_out_count": trc20_out_count,
        "trc20_in_count":  trc20_in_count,
        out_k:             wallet_row.get("out_total", 0),
        in_k:              wallet_row.get("in_total",  0),
        fee_k:             wallet_row.get("total_fee", 0),
        "top_fee_dest":    wallet_row.get("top_fee_dest"),
        "erc20_out_by_token": erc20_out,
        "erc20_in_by_token":  erc20_in,
        "trc20_out_by_token": trc20_out,
        "trc20_in_by_token":  trc20_in,
        "raw_txs":         raw_txs,
        "raw_erc20":       raw_erc20,
        "raw_trc20":       raw_trc20,
        "approval_targets": approval_targets,
        "_from_db":        True,
    }
    return profile


def get_all_tx_lookups() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM tx_lookups ORDER BY queried_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_wallet(wallet_id: int):
    with _conn() as con:
        con.execute("DELETE FROM wallets WHERE id=?", (wallet_id,))


def delete_tx_lookup(lookup_id: int):
    with _conn() as con:
        con.execute("DELETE FROM tx_lookups WHERE id=?", (lookup_id,))


def search_address(keyword: str) -> list[dict]:
    kw = f"%{keyword}%"
    with _conn() as con:
        wallets = con.execute(
            "SELECT * FROM wallets WHERE address LIKE ? ORDER BY analyzed_at DESC",
            (kw,)
        ).fetchall()
        txs = con.execute(
            "SELECT DISTINCT address, chain FROM transactions WHERE from_addr LIKE ? OR to_addr LIKE ?",
            (kw, kw)
        ).fetchall()
    return {"wallets": [dict(r) for r in wallets],
            "related_txs": [dict(r) for r in txs]}


# ── 案件 CRUD ─────────────────────────────────────────────────────────────────

def create_case(case_number: str, case_name: str, case_type: str = "一般",
                status: str = "進行中", investigator: str = "",
                transcript: str = "", description: str = "",
                notes: str = "") -> int:
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO cases (case_number, case_name, case_type,
                               status, investigator, transcript, description, notes)
            VALUES (?,?,?,?,?,?,?,?)
        """, (case_number, case_name, case_type, status,
              investigator, transcript, description, notes))
        return cur.lastrowid


def update_case(case_id: int, **kwargs):
    allowed = {"case_name", "case_type", "status", "investigator",
               "transcript", "description", "notes"}
    fields  = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [case_id]
    with _conn() as con:
        con.execute(
            f"UPDATE cases SET {sets}, updated_at=datetime('now','localtime') WHERE id=?",
            vals
        )


def delete_case(case_id: int):
    with _conn() as con:
        con.execute("DELETE FROM cases WHERE id=?", (case_id,))


def get_all_cases() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM cases ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_case(case_id: int) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    return dict(row) if row else None


def get_case_wallets(case_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM wallets WHERE case_id=? ORDER BY analyzed_at DESC",
            (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_case_tx_lookups(case_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM tx_lookups WHERE case_id=? ORDER BY queried_at DESC",
            (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def link_wallet_to_case(wallet_id: int, case_id: int, label: str = ""):
    with _conn() as con:
        con.execute(
            "UPDATE wallets SET case_id=?, label=COALESCE(NULLIF(?,''), label) WHERE id=?",
            (case_id, label, wallet_id)
        )
        con.execute(
            "UPDATE cases SET updated_at=datetime('now','localtime') WHERE id=?",
            (case_id,)
        )


def link_tx_lookup_to_case(lookup_id: int, case_id: int):
    with _conn() as con:
        con.execute(
            "UPDATE tx_lookups SET case_id=? WHERE id=?",
            (case_id, lookup_id)
        )
        con.execute(
            "UPDATE cases SET updated_at=datetime('now','localtime') WHERE id=?",
            (case_id,)
        )


def unlink_wallet_from_case(wallet_id: int):
    with _conn() as con:
        con.execute("UPDATE wallets SET case_id=NULL WHERE id=?", (wallet_id,))


def unlink_tx_lookup_from_case(lookup_id: int):
    with _conn() as con:
        con.execute("UPDATE tx_lookups SET case_id=NULL WHERE id=?", (lookup_id,))


def next_case_number() -> str:
    """自動產生案件編號，格式：CASE-YYYYMMDD-NNN"""
    today = datetime.datetime.now().strftime("%Y%m%d")
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM cases WHERE case_number LIKE ?",
            (f"CASE-{today}-%",)
        ).fetchone()
    seq = (row[0] or 0) + 1
    return f"CASE-{today}-{seq:03d}"


def get_case_by_number(case_number: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM cases WHERE case_number=?", (case_number,)
        ).fetchone()
    return dict(row) if row else None


# ── 被害人陳述交易紀錄 CRUD ────────────────────────────────────────────────────

def get_victim_transactions(case_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM victim_transactions WHERE case_id=? "
            "ORDER BY tx_date, tx_time",
            (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_victim_transaction(case_id: int, data: dict) -> int:
    """新增或更新一筆被害人陳述交易。含 id 則更新，否則新增。"""
    row_id = data.get("id")
    fields = ["tx_date", "tx_time", "from_addr", "to_addr",
              "amount_ntd", "quantity", "currency",
              "exchange_rate", "daily_avg", "daily_high", "daily_low",
              "source_doc", "notes"]
    if row_id:
        sets = ", ".join(f"{f}=?" for f in fields)
        sets += ", updated_at=datetime('now','localtime')"
        vals = [data.get(f) for f in fields] + [row_id]
        with _conn() as con:
            con.execute(f"UPDATE victim_transactions SET {sets} WHERE id=?", vals)
        return row_id
    else:
        cols = ", ".join(["case_id"] + fields)
        qs   = ", ".join(["?"] * (len(fields) + 1))
        vals = [case_id] + [data.get(f) for f in fields]
        with _conn() as con:
            cur = con.execute(
                f"INSERT INTO victim_transactions ({cols}) VALUES ({qs})", vals)
            return cur.lastrowid


def delete_victim_transaction(tx_id: int):
    with _conn() as con:
        con.execute("DELETE FROM victim_transactions WHERE id=?", (tx_id,))


# ── 涉案錢包地址 / 金融帳戶 CRUD ──────────────────────────────────────────────

def get_case_addresses(case_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM case_addresses WHERE case_id=? "
            "ORDER BY addr_type, holder_role, id",
            (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_case_address(case_id: int, data: dict) -> int:
    """新增或更新一筆涉案地址/帳戶。含 id 則更新，否則新增。"""
    row_id = data.get("id")
    fields = ["addr_type", "chain_institution", "address",
              "holder_role", "label", "source_doc", "notes"]
    if row_id:
        sets = ", ".join(f"{f}=?" for f in fields)
        sets += ", updated_at=datetime('now','localtime')"
        vals = [data.get(f) for f in fields] + [row_id]
        with _conn() as con:
            con.execute(f"UPDATE case_addresses SET {sets} WHERE id=?", vals)
        return row_id
    else:
        cols = ", ".join(["case_id"] + fields)
        qs   = ", ".join(["?"] * (len(fields) + 1))
        vals = [case_id] + [data.get(f) for f in fields]
        with _conn() as con:
            cur = con.execute(
                f"INSERT INTO case_addresses ({cols}) VALUES ({qs})", vals)
            return cur.lastrowid


def delete_case_address(addr_id: int):
    with _conn() as con:
        con.execute("DELETE FROM case_addresses WHERE id=?", (addr_id,))


# ── 一般帳戶交易紀錄 ──────────────────────────────────────────────────────────

def get_bank_transactions(case_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM case_bank_transactions WHERE case_id=? "
            "ORDER BY tx_date DESC, tx_time DESC, id DESC",
            (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_bank_transaction(case_id: int, data: dict) -> int:
    fields = ["tx_date", "tx_time", "direction", "bank_name", "account_no",
              "counterpart_name", "counterpart_account", "amount", "currency",
              "balance", "notes", "source_doc"]
    row_id = data.get("id")
    if row_id:
        sets = ", ".join(f"{f}=?" for f in fields)
        sets += ", updated_at=datetime('now','localtime')"
        vals = [data.get(f) for f in fields] + [row_id]
        with _conn() as con:
            con.execute(f"UPDATE case_bank_transactions SET {sets} WHERE id=?", vals)
        return row_id
    else:
        cols = ", ".join(["case_id"] + fields)
        qs   = ", ".join(["?"] * (len(fields) + 1))
        vals = [case_id] + [data.get(f) for f in fields]
        with _conn() as con:
            cur = con.execute(
                f"INSERT INTO case_bank_transactions ({cols}) VALUES ({qs})", vals)
            return cur.lastrowid


def delete_bank_transaction(tx_id: int):
    with _conn() as con:
        con.execute("DELETE FROM case_bank_transactions WHERE id=?", (tx_id,))


# ── 區塊鏈交易紀錄 ────────────────────────────────────────────────────────────

def get_chain_transactions(case_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM case_chain_transactions WHERE case_id=? "
            "ORDER BY tx_datetime DESC, id DESC",
            (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_chain_transaction(case_id: int, data: dict) -> int:
    fields = ["chain", "direction", "tx_datetime", "tx_hash",
              "from_addr", "to_addr", "amount", "token_symbol",
              "notes", "source_doc"]
    row_id = data.get("id")
    if row_id:
        sets = ", ".join(f"{f}=?" for f in fields)
        sets += ", updated_at=datetime('now','localtime')"
        vals = [data.get(f) for f in fields] + [row_id]
        with _conn() as con:
            con.execute(f"UPDATE case_chain_transactions SET {sets} WHERE id=?", vals)
        return row_id
    else:
        cols = ", ".join(["case_id"] + fields)
        qs   = ", ".join(["?"] * (len(fields) + 1))
        vals = [case_id] + [data.get(f) for f in fields]
        with _conn() as con:
            cur = con.execute(
                f"INSERT INTO case_chain_transactions ({cols}) VALUES ({qs})", vals)
            return cur.lastrowid


def delete_chain_transaction(tx_id: int):
    with _conn() as con:
        con.execute("DELETE FROM case_chain_transactions WHERE id=?", (tx_id,))


# ── 幣流圖：邊資料接口 ────────────────────────────────────────────────────────

def get_edges_for_graph(case_id: int = None, chain: str = None,
                        address: str = None) -> list[dict]:
    """
    回傳適合 networkx 建邊的交易清單。
    可依 case_id（案件所有錢包）、chain、單一 address 組合篩選。
    每筆回傳：from_addr, to_addr, value_native, token_symbol, token_amount,
              tx_time, tx_hash, tx_type, chain, wallet_id
    """
    conditions = ["t.is_error = 0", "t.from_addr != ''", "t.to_addr != ''"]
    params: list = []

    if case_id is not None:
        conditions.append("w.case_id = ?")
        params.append(case_id)
    if chain:
        conditions.append("t.chain = ?")
        params.append(chain)
    if address:
        conditions.append("(t.from_addr = ? OR t.to_addr = ?)")
        params.extend([address, address])

    where = " AND ".join(conditions)
    sql = f"""
        SELECT t.from_addr, t.to_addr, t.value_native, t.token_symbol,
               t.token_amount, t.tx_time, t.tx_hash, t.tx_type, t.chain,
               t.wallet_id
        FROM   transactions t
        LEFT JOIN wallets w ON t.wallet_id = w.id
        WHERE  {where}
        ORDER BY t.tx_time ASC
    """
    with _conn() as con:
        rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── 幣流圖快照 CRUD ───────────────────────────────────────────────────────────

def save_graph_snapshot(case_id: int, chain: str, nodes: list, edges: list,
                        label: str = "") -> int:
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO graph_snapshots (case_id, chain, label, nodes_json, edges_json)
            VALUES (?, ?, ?, ?, ?)
        """, (case_id, chain, label,
              json.dumps(nodes, ensure_ascii=False),
              json.dumps(edges, ensure_ascii=False)))
        return cur.lastrowid


def get_graph_snapshots(case_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM graph_snapshots WHERE case_id=? ORDER BY saved_at DESC",
            (case_id,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["nodes"] = json.loads(d.pop("nodes_json", "[]"))
        d["edges"] = json.loads(d.pop("edges_json", "[]"))
        result.append(d)
    return result


def delete_graph_snapshot(snapshot_id: int):
    with _conn() as con:
        con.execute("DELETE FROM graph_snapshots WHERE id=?", (snapshot_id,))
