from __future__ import annotations
import datetime
from collections import defaultdict
from typing import Any


def _wei_to_eth(wei: str | int) -> float:
    try:
        return int(wei) / 1e18
    except (ValueError, TypeError):
        return 0.0


def _sun_to_trx(sun: str | int) -> float:
    try:
        return int(sun) / 1_000_000
    except (ValueError, TypeError):
        return 0.0


def _sat_to_btc(sat: int) -> float:
    return sat / 1e8


# ── Ethereum ──────────────────────────────────────────────────────────────────

def profile_eth(address: str, txs: list[dict], internal_txs: list[dict],
                erc20_txs: list[dict], approvals: list[dict]) -> dict:
    addr = address.lower()

    # ── ETH 原生交易 ──
    out_txs = [t for t in txs if t.get("from", "").lower() == addr and t.get("isError", "0") == "0"]
    in_txs  = [t for t in txs if t.get("to",   "").lower() == addr and t.get("isError", "0") == "0"]

    eth_out_count = len(out_txs)
    eth_in_count  = len(in_txs)
    eth_out_total = sum(_wei_to_eth(t.get("value", 0)) for t in out_txs)
    eth_in_total  = sum(_wei_to_eth(t.get("value", 0)) for t in in_txs)

    for t in internal_txs:
        if t.get("to", "").lower() == addr and t.get("isError", "0") == "0":
            eth_in_total += _wei_to_eth(t.get("value", 0))
            eth_in_count += 1

    # ── ERC-20 Token 交易 ──
    erc20_out = [t for t in erc20_txs if t.get("from", "").lower() == addr]
    erc20_in  = [t for t in erc20_txs if t.get("to",   "").lower() == addr]

    def _erc20_amount(t: dict) -> float:
        try:
            decimals = int(t.get("tokenDecimal", 18) or 18)
            return int(t.get("value", 0)) / (10 ** decimals)
        except (ValueError, TypeError):
            return 0.0

    # 依 token 分組統計 ERC-20 金額
    erc20_out_by_token: dict[str, float] = defaultdict(float)
    erc20_in_by_token:  dict[str, float] = defaultdict(float)
    for t in erc20_out:
        erc20_out_by_token[t.get("tokenSymbol", "?")] += _erc20_amount(t)
    for t in erc20_in:
        erc20_in_by_token[t.get("tokenSymbol", "?")] += _erc20_amount(t)

    # 合計發起/接受（ETH + ERC-20 筆數）
    out_count = eth_out_count + len(erc20_out)
    in_count  = eth_in_count  + len(erc20_in)

    # ── 手續費（僅算 ETH 發起交易，因為 ERC-20 手續費已含在 ETH out txs）──
    fee_by_addr: dict[str, float] = defaultdict(float)
    total_fee = 0.0
    for t in out_txs:
        try:
            fee = int(t.get("gasUsed", 0)) * int(t.get("gasPrice", 0)) / 1e18
        except (ValueError, TypeError):
            fee = 0.0
        total_fee += fee
        fee_by_addr[t.get("to", "unknown")] += fee
    # ERC-20 的 gas 費用（gas 付給合約，但實際 from 是本人）
    for t in erc20_out:
        try:
            fee = int(t.get("gasUsed", 0)) * int(t.get("gasPrice", 0)) / 1e18
        except (ValueError, TypeError):
            fee = 0.0
        total_fee += fee
        fee_by_addr[t.get("contractAddress", "unknown")] += fee
    top_fee_dest = max(fee_by_addr, key=fee_by_addr.get) if fee_by_addr else "N/A"

    # ── 首次來源（ETH 優先，否則查 ERC-20）──
    all_eth_sorted = sorted(txs + internal_txs, key=lambda t: int(t.get("timeStamp", 0)))
    first_source = "N/A"
    for t in all_eth_sorted:
        if t.get("to", "").lower() == addr and _wei_to_eth(t.get("value", 0)) > 0:
            first_source = t.get("from", "N/A")
            break
    if first_source == "N/A" and erc20_in:
        erc20_in_sorted = sorted(erc20_in, key=lambda t: int(t.get("timeStamp", 0)))
        first_source = erc20_in_sorted[0].get("from", "N/A") + "（首筆 ERC-20 入帳）"

    # ── 首次 / 最後交易時間（含 ERC-20）──
    all_ts = (
        [int(t.get("timeStamp", 0)) for t in txs if t.get("timeStamp")] +
        [int(t.get("timeStamp", 0)) for t in erc20_txs if t.get("timeStamp")]
    )
    first_ts = min(all_ts) if all_ts else None
    last_ts  = max(all_ts) if all_ts else None

    # ── 授權對象 ──
    approval_targets: list[dict] = []
    for t in approvals:
        spender = "0x" + t.get("input", "")[34:74] if len(t.get("input", "")) >= 74 else "unknown"
        approval_targets.append({
            "contract": t.get("to", ""),
            "spender": spender,
            "tx_hash": t.get("hash", ""),
            "time": _ts_to_str(int(t.get("timeStamp", 0))),
        })

    return {
        "chain": "ETH",
        "address": address,
        # ETH 原生
        "eth_out_count": eth_out_count,
        "eth_in_count": eth_in_count,
        "out_total_eth": round(eth_out_total, 8),
        "in_total_eth":  round(eth_in_total,  8),
        # ERC-20
        "erc20_out_count": len(erc20_out),
        "erc20_in_count":  len(erc20_in),
        "erc20_out_by_token": dict(erc20_out_by_token),
        "erc20_in_by_token":  dict(erc20_in_by_token),
        # 合計（ETH + ERC-20）
        "out_count": out_count,
        "in_count":  in_count,
        "total_fee_eth": round(total_fee, 8),
        "top_fee_dest": top_fee_dest,
        "first_source": first_source,
        "first_tx_time": _ts_to_str(first_ts),
        "last_tx_time":  _ts_to_str(last_ts),
        "approval_targets": approval_targets,
        "erc20_transfer_count": len(erc20_txs),
        "raw_txs": txs,
        "raw_erc20": erc20_txs,
    }


# ── TRON ──────────────────────────────────────────────────────────────────────

def profile_trx(address: str, txs: list[dict], trc20_txs: list[dict],
                approvals: list[dict]) -> dict:
    addr = address

    out_txs = [t for t in txs if t.get("ownerAddress", "") == addr]
    in_txs  = [t for t in txs if t.get("toAddress",    "") == addr]

    def _trx_amount(t: dict) -> float:
        amount = t.get("amount", 0) or t.get("contractData", {}).get("amount", 0)
        return _sun_to_trx(amount)

    out_total = sum(_trx_amount(t) for t in out_txs)
    in_total  = sum(_trx_amount(t) for t in in_txs)

    # ── TRC-20 Token 交易 ──
    trc20_out = [t for t in trc20_txs if t.get("from_address", "") == addr]
    trc20_in  = [t for t in trc20_txs if t.get("to_address",   "") == addr]

    def _trc20_amount(t: dict) -> float:
        try:
            ti = t.get("tokenInfo") or {}
            decimals = int(ti.get("tokenDecimal", 6) or 6)
            return int(t.get("quant", 0)) / (10 ** decimals)
        except (ValueError, TypeError):
            return 0.0

    def _trc20_symbol(t: dict) -> str:
        return (t.get("tokenInfo") or {}).get("tokenAbbr", "?")

    trc20_out_by_token: dict[str, float] = defaultdict(float)
    trc20_in_by_token:  dict[str, float] = defaultdict(float)
    for t in trc20_out:
        trc20_out_by_token[_trc20_symbol(t)] += _trc20_amount(t)
    for t in trc20_in:
        trc20_in_by_token[_trc20_symbol(t)] += _trc20_amount(t)

    # 合計（原生 TRX + TRC-20 筆數）
    out_count = len(out_txs) + len(trc20_out)
    in_count  = len(in_txs)  + len(trc20_in)

    # 手續費
    fee_by_addr: dict[str, float] = defaultdict(float)
    total_fee = 0.0
    for t in out_txs:
        fee = _sun_to_trx(t.get("fee", 0) or t.get("cost", {}).get("fee", 0))
        total_fee += fee
        fee_by_addr[t.get("toAddress", "unknown")] += fee
    top_fee_dest = max(fee_by_addr, key=fee_by_addr.get) if fee_by_addr else "N/A"

    # 首次來源（原生 TRX 優先，否則查 TRC-20）
    sorted_in = sorted(in_txs, key=lambda t: t.get("timestamp", 0))
    first_source = sorted_in[0].get("ownerAddress", "N/A") if sorted_in else "N/A"
    if first_source == "N/A" and trc20_in:
        sorted_trc20_in = sorted(trc20_in, key=lambda t: t.get("block_ts", 0))
        first_source = sorted_trc20_in[0].get("from_address", "N/A") + "（首筆 TRC-20 入帳）"

    # 時間戳（原生 TRX 單位 ms；TRC-20 block_ts 也是 ms）
    timestamps = [t.get("timestamp", 0) // 1000 for t in txs if t.get("timestamp")]
    trc20_ts   = [t.get("block_ts", 0)   // 1000 for t in trc20_txs if t.get("block_ts")]
    all_ts = timestamps + trc20_ts
    first_ts = min(all_ts) if all_ts else None
    last_ts  = max(all_ts) if all_ts else None

    approval_targets = []
    for a in approvals:
        approval_targets.append({
            "contract": a.get("contract_address", ""),
            "spender": a.get("spender", ""),
            "amount": a.get("amount", ""),
        })

    return {
        "chain": "TRX",
        "address": address,
        # 原生 TRX
        "trx_out_count": len(out_txs),
        "trx_in_count":  len(in_txs),
        "out_total_trx": round(out_total, 6),
        "in_total_trx":  round(in_total,  6),
        # TRC-20 Token
        "trc20_out_count":    len(trc20_out),
        "trc20_in_count":     len(trc20_in),
        "trc20_out_by_token": dict(trc20_out_by_token),
        "trc20_in_by_token":  dict(trc20_in_by_token),
        # 合計（TRX + TRC-20 筆數）
        "out_count": out_count,
        "in_count":  in_count,
        "total_fee_trx": round(total_fee, 6),
        "top_fee_dest": top_fee_dest,
        "first_source": first_source,
        "first_tx_time": _ts_to_str(first_ts),
        "last_tx_time": _ts_to_str(last_ts),
        "approval_targets": approval_targets,
        "trc20_transfer_count": len(trc20_txs),
        "raw_txs": txs,
        "raw_trc20": trc20_txs,
    }


# ── Bitcoin ───────────────────────────────────────────────────────────────────

def profile_btc(address: str, txs: list[dict]) -> dict:
    out_count = 0
    in_count  = 0
    out_total = 0.0
    in_total  = 0.0
    total_fee = 0.0
    fee_by_dest: dict[str, float] = defaultdict(float)

    for tx in txs:
        inputs  = tx.get("inputs", [])
        outputs = tx.get("out", [])
        is_sender = any(
            inp.get("prev_out", {}).get("addr", "") == address for inp in inputs
        )
        if is_sender:
            out_count += 1
            fee = _sat_to_btc(tx.get("fee", 0))
            total_fee += fee
            for o in outputs:
                dest = o.get("addr", "unknown")
                if dest != address:
                    out_total += _sat_to_btc(o.get("value", 0))
                    fee_by_dest[dest] += fee
        for o in outputs:
            if o.get("addr", "") == address:
                in_count += 1
                in_total += _sat_to_btc(o.get("value", 0))

    top_fee_dest = max(fee_by_dest, key=fee_by_dest.get) if fee_by_dest else "N/A"

    # 首次來源
    sorted_txs = sorted(txs, key=lambda t: t.get("time", 0))
    first_source = "N/A"
    for tx in sorted_txs:
        for o in tx.get("out", []):
            if o.get("addr", "") == address:
                inputs = tx.get("inputs", [])
                if inputs:
                    first_source = inputs[0].get("prev_out", {}).get("addr", "N/A")
                break
        if first_source != "N/A":
            break

    timestamps = [t.get("time", 0) for t in txs if t.get("time")]
    first_ts = min(timestamps) if timestamps else None
    last_ts  = max(timestamps) if timestamps else None

    return {
        "chain": "BTC",
        "address": address,
        "out_count": out_count,
        "out_total_btc": round(out_total, 8),
        "in_count": in_count,
        "in_total_btc": round(in_total, 8),
        "total_fee_btc": round(total_fee, 8),
        "top_fee_dest": top_fee_dest,
        "first_source": first_source,
        "first_tx_time": _ts_to_str(first_ts),
        "last_tx_time": _ts_to_str(last_ts),
        "approval_targets": [],
        "raw_txs": txs,
    }


_UTC8 = datetime.timezone(datetime.timedelta(hours=8))

def _ts_to_str(ts: int | None) -> str:
    if not ts:
        return "N/A"
    return datetime.datetime.fromtimestamp(ts, tz=_UTC8).strftime("%Y-%m-%d %H:%M:%S UTC+8")
