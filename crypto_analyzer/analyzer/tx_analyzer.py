from __future__ import annotations
import datetime

_UTC8 = datetime.timezone(datetime.timedelta(hours=8))


def _ts_to_str(ts) -> str:
    if not ts:
        return "N/A"
    try:
        return datetime.datetime.fromtimestamp(int(ts), tz=_UTC8).strftime("%Y-%m-%d %H:%M:%S UTC+8")
    except (ValueError, TypeError):
        return str(ts)


def _hex_to_int(h) -> int:
    if not h:
        return 0
    try:
        return int(h, 16) if isinstance(h, str) else int(h)
    except (ValueError, TypeError):
        return 0


# ── Ethereum ──────────────────────────────────────────────────────────────────

def analyze_eth_tx(raw: dict) -> dict:
    source = raw.get("source", "etherscan")
    if source == "blockscout":
        return _analyze_eth_tx_blockscout(raw)
    return _analyze_eth_tx_etherscan(raw)


def _analyze_eth_tx_etherscan(raw: dict) -> dict:
    tx      = raw.get("tx", {})
    receipt = raw.get("receipt", {})
    tokens  = raw.get("token_transfers", [])

    value_wei  = _hex_to_int(tx.get("value", "0x0"))
    gas_price  = _hex_to_int(tx.get("gasPrice", "0x0"))
    gas_used   = _hex_to_int(receipt.get("gasUsed", "0x0"))
    gas_limit  = _hex_to_int(tx.get("gas", "0x0"))
    fee_eth    = gas_price * gas_used / 1e18
    value_eth  = value_wei / 1e18
    status_raw = receipt.get("status", "")
    status     = "✅ 成功" if status_raw == "0x1" else ("❌ 失敗" if status_raw == "0x0" else "⏳ 待確認")

    block_ts = tx.get("blockTimestamp") or receipt.get("blockTimestamp")
    time_str = _ts_to_str(block_ts) if block_ts else "N/A"

    token_rows = _parse_etherscan_tokens(tokens)

    return {
        "chain":    "ETH",
        "hash":     tx.get("hash", ""),
        "狀態":      status,
        "區塊":      str(_hex_to_int(tx.get("blockNumber", "0x0"))),
        "時間":      time_str,
        "發送方":    tx.get("from", "N/A"),
        "接收方":    tx.get("to", "N/A"),
        "ETH 金額":  f"{value_eth:,.8f} ETH",
        "Gas 上限":  f"{gas_limit:,}",
        "Gas 使用":  f"{gas_used:,}",
        "Gas 單價":  f"{gas_price / 1e9:.4f} Gwei",
        "手續費":    f"{fee_eth:,.8f} ETH",
        "Input Data": tx.get("input", "0x")[:200] + ("..." if len(tx.get("input","")) > 200 else ""),
        "token_transfers": token_rows,
    }


def _parse_etherscan_tokens(tokens: list) -> list:
    rows = []
    for t in tokens:
        decimals = int(t.get("tokenDecimal", 18) or 18)
        try:
            amount = int(t.get("value", 0)) / (10 ** decimals)
        except (ValueError, TypeError):
            amount = 0
        rows.append({
            "Token": f"{t.get('tokenName','')} ({t.get('tokenSymbol','')})",
            "從": t.get("from", ""),
            "至": t.get("to", ""),
            "金額": f"{amount:,.6f}",
            "合約": t.get("contractAddress", ""),
        })
    return rows


def _analyze_eth_tx_blockscout(raw: dict) -> dict:
    tx     = raw.get("tx", {})
    tokens = raw.get("token_transfers", [])

    status_raw = tx.get("status", "")
    status = "✅ 成功" if status_raw == "ok" else ("❌ 失敗" if status_raw == "error" else "⏳ 待確認")

    # Blockscout 時間格式：ISO 8601
    ts_str = tx.get("timestamp", "")
    if ts_str:
        try:
            import datetime as _dt
            dt = _dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            _UTC8 = _dt.timezone(_dt.timedelta(hours=8))
            time_str = dt.astimezone(_UTC8).strftime("%Y-%m-%d %H:%M:%S UTC+8")
        except Exception:
            time_str = ts_str
    else:
        time_str = "N/A"

    try:
        value_eth = int(tx.get("value", "0")) / 1e18
    except (ValueError, TypeError):
        value_eth = 0

    fee_info  = tx.get("fee", {})
    try:
        fee_eth = int(fee_info.get("value", 0)) / 1e18
    except (ValueError, TypeError):
        fee_eth = 0

    gas_used  = int(tx.get("gas_used", 0) or 0)
    gas_limit = int(tx.get("gas_limit", 0) or 0)
    try:
        gas_price_wei = int(tx.get("gas_price", "0"))
        gas_price_gwei = gas_price_wei / 1e9
    except (ValueError, TypeError):
        gas_price_gwei = 0

    from_addr = (tx.get("from") or {}).get("hash", "N/A")
    to_addr   = (tx.get("to")   or {}).get("hash", "N/A")

    # Token 轉帳（Blockscout v2 格式）
    token_rows = []
    for t in tokens:
        token_info = t.get("token", {})
        decimals = int(token_info.get("decimals", 18) or 18)
        try:
            amount = int(t.get("total", {}).get("value", 0)) / (10 ** decimals)
        except (ValueError, TypeError):
            amount = 0
        token_rows.append({
            "Token": f"{token_info.get('name','')} ({token_info.get('symbol','')})",
            "從":    (t.get("from") or {}).get("hash", ""),
            "至":    (t.get("to")   or {}).get("hash", ""),
            "金額":  f"{amount:,.6f}",
            "合約":  token_info.get("address", ""),
        })

    raw_input = tx.get("raw_input", "0x") or "0x"

    return {
        "chain":     "ETH",
        "hash":      tx.get("hash", ""),
        "狀態":       status,
        "區塊":       str(tx.get("block", "")),
        "時間":       time_str,
        "發送方":     from_addr,
        "接收方":     to_addr,
        "ETH 金額":   f"{value_eth:,.8f} ETH",
        "Gas 上限":   f"{gas_limit:,}",
        "Gas 使用":   f"{gas_used:,}",
        "Gas 單價":   f"{gas_price_gwei:.4f} Gwei",
        "手續費":     f"{fee_eth:,.8f} ETH",
        "Input Data": raw_input[:200] + ("..." if len(raw_input) > 200 else ""),
        "資料來源":   "Blockscout",
        "token_transfers": token_rows,
    }


# ── TRON ──────────────────────────────────────────────────────────────────────

def analyze_trx_tx(raw: dict) -> dict:
    tx     = raw.get("tx", {})
    tokens = raw.get("token_transfers", [])

    status_raw = tx.get("contractRet") or tx.get("contractInfo", {}).get("result", "")
    status = "✅ 成功" if status_raw == "SUCCESS" else (f"❌ {status_raw}" if status_raw else "⏳ 待確認")

    ts = tx.get("timestamp", 0)
    if ts:
        ts = ts // 1000 if ts > 1e12 else ts
    time_str = _ts_to_str(ts)

    cost    = tx.get("cost", {})
    fee_sun = int(cost.get("fee", 0) or tx.get("fee", 0) or 0)
    fee_trx = fee_sun / 1_000_000

    contract = tx.get("contractData", {})
    amount_sun = int(contract.get("amount", 0) or 0)
    amount_trx = amount_sun / 1_000_000

    token_rows = []
    for t in tokens:
        try:
            decimals = int(t.get("tokenDecimal", 6) or 6)
            amount = int(t.get("amount", 0)) / (10 ** decimals)
        except (ValueError, TypeError):
            amount = 0
        token_rows.append({
            "Token":    f"{t.get('tokenName','')} ({t.get('tokenAbbr','')})",
            "從":       t.get("from_address", ""),
            "至":       t.get("to_address", ""),
            "金額":     f"{amount:,.6f}",
            "合約":     t.get("contract_address", ""),
        })

    return {
        "chain":    "TRX",
        "hash":     tx.get("hash", tx.get("txID", "")),
        "狀態":      status,
        "區塊":      str(tx.get("block", "")),
        "時間":      time_str,
        "發送方":    contract.get("owner_address", tx.get("ownerAddress", "N/A")),
        "接收方":    contract.get("to_address",    tx.get("toAddress",    "N/A")),
        "TRX 金額":  f"{amount_trx:,.6f} TRX",
        "Energy 使用": str(cost.get("energy_usage", 0)),
        "頻寬使用":   str(cost.get("net_usage", 0)),
        "手續費":    f"{fee_trx:,.6f} TRX",
        "合約類型":  tx.get("contractType", ""),
        "token_transfers": token_rows,
    }


# ── Bitcoin ───────────────────────────────────────────────────────────────────

def analyze_btc_tx(raw: dict) -> dict:
    if not raw:
        return {"chain": "BTC", "hash": "", "狀態": "查無資料", "token_transfers": []}

    inputs  = raw.get("inputs", [])
    outputs = raw.get("out", [])
    fee_sat = raw.get("fee", 0)

    in_total  = sum(i.get("prev_out", {}).get("value", 0) for i in inputs)
    out_total = sum(o.get("value", 0) for o in outputs)

    senders   = list({i.get("prev_out", {}).get("addr", "") for i in inputs if i.get("prev_out", {}).get("addr")})
    receivers = [{"地址": o.get("addr",""), "BTC": f"{o.get('value',0)/1e8:,.8f}"} for o in outputs]

    confirmed = raw.get("block_height", 0)
    status    = "✅ 已確認" if confirmed else "⏳ 未確認"
    time_str  = _ts_to_str(raw.get("time"))

    return {
        "chain":    "BTC",
        "hash":     raw.get("hash", ""),
        "狀態":      status,
        "區塊":      str(confirmed) if confirmed else "未確認",
        "時間":      time_str,
        "發送方":    "、".join(senders) if senders else "N/A",
        "接收方（明細）": receivers,
        "輸入總額":  f"{in_total/1e8:,.8f} BTC",
        "輸出總額":  f"{out_total/1e8:,.8f} BTC",
        "手續費":    f"{fee_sat/1e8:,.8f} BTC",
        "確認數":    str(raw.get("confirmations", 0)),
        "交易大小":  f"{raw.get('size', 0)} bytes",
        "token_transfers": [],
    }
