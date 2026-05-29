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
    """建立所有資料表（若不存在）"""
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS wallets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            chain         TEXT    NOT NULL,
            address       TEXT    NOT NULL,
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

        CREATE TABLE IF NOT EXISTS tx_lookups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
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

        CREATE INDEX IF NOT EXISTS idx_txs_wallet   ON transactions(wallet_id);
        CREATE INDEX IF NOT EXISTS idx_txs_hash     ON transactions(tx_hash);
        CREATE INDEX IF NOT EXISTS idx_txs_addr     ON transactions(address);
        CREATE INDEX IF NOT EXISTS idx_approvals_wid ON approvals(wallet_id);
        CREATE INDEX IF NOT EXISTS idx_lookup_hash  ON tx_lookups(tx_hash);
        """)


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

def save_tx_lookup(result: dict):
    chain   = result.get("chain", "")
    tx_hash = result.get("hash", "")
    if not tx_hash:
        return
    with _conn() as con:
        con.execute("""
            INSERT INTO tx_lookups
                (chain,tx_hash,status,from_addr,to_addr,
                 value_str,fee_str,block,tx_time,raw_json,queried_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))
            ON CONFLICT(chain,tx_hash) DO UPDATE SET
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
            chain, tx_hash, result.get("狀態",""),
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
