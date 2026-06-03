from __future__ import annotations
import datetime

_UTC8 = datetime.timezone(datetime.timedelta(hours=8))


# ── 時間字串解析 ──────────────────────────────────────────────────────────────

def parse_datetime_str(s: str) -> int | None:
    """
    將 'YYYY-MM-DD HH:MM:SS' 字串（UTC+8）轉為 Unix timestamp。
    支援省略時間部分（補 00:00:00）。
    回傳 None 表示解析失敗。
    """
    s = s.strip()
    if not s:
        return None
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.datetime.strptime(s, fmt)
            dt = dt.replace(tzinfo=_UTC8)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def ts_to_str(ts: int) -> str:
    dt = datetime.datetime.fromtimestamp(ts, tz=_UTC8)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── 從各鏈交易取得 Unix timestamp ────────────────────────────────────────────

def get_tx_ts(tx: dict, chain: str) -> int:
    """從交易 dict 取得 Unix timestamp（秒）"""
    if chain == "ETH":
        try:
            return int(tx.get("timeStamp", 0))
        except (ValueError, TypeError):
            return 0
    elif chain == "TRX":
        raw = tx.get("timestamp", 0)
        try:
            ts = int(raw)
            return ts // 1000 if ts > 1e12 else ts
        except (ValueError, TypeError):
            return 0
    elif chain == "BTC":
        try:
            return int(tx.get("time", 0))
        except (ValueError, TypeError):
            return 0
    return 0


# ── 範圍篩選模式（起迄均設定） ────────────────────────────────────────────────

def filter_by_range(txs: list[dict], chain: str,
                    start_ts: int, end_ts: int) -> list[dict]:
    """回傳 start_ts <= tx_time <= end_ts 的交易，依時間升序排列"""
    result = [t for t in txs
              if start_ts <= get_tx_ts(t, chain) <= end_ts]
    result.sort(key=lambda t: get_tx_ts(t, chain))
    return result


# ── 置中模式（僅設定起始時間） ────────────────────────────────────────────────

def filter_centered(txs: list[dict], chain: str,
                    center_ts: int, each_side: int) -> dict:
    """
    以 center_ts 為軸心，取之前 each_side 筆與之後 each_side 筆。
    回傳 dict：
        "result"   : 最終清單（依時間升序）
        "before"   : 軸心前實際筆數
        "after"    : 軸心後實際筆數
        "pivot_idx": 軸心在 result 中的索引（-1 表示無完全吻合）
        "pivot_tx" : 最接近 center_ts 的那筆交易
    """
    sorted_txs = sorted(txs, key=lambda t: get_tx_ts(t, chain))
    if not sorted_txs:
        return {"result": [], "before": 0, "after": 0,
                "pivot_idx": -1, "pivot_tx": None}

    # 找最近軸心（絕對時間差最小）
    pivot_idx = min(range(len(sorted_txs)),
                    key=lambda i: abs(get_tx_ts(sorted_txs[i], chain) - center_ts))
    pivot_tx  = sorted_txs[pivot_idx]

    before_txs = sorted_txs[max(0, pivot_idx - each_side): pivot_idx]
    after_txs  = sorted_txs[pivot_idx: pivot_idx + each_side + 1]  # 含軸心本身

    result = before_txs + after_txs
    new_pivot = len(before_txs)  # 軸心在 result 中的位置

    return {
        "result":    result,
        "before":    len(before_txs),
        "after":     len(after_txs) - 1,  # 不含軸心自身
        "pivot_idx": new_pivot,
        "pivot_tx":  pivot_tx,
        "total_available_before": pivot_idx,
        "total_available_after":  len(sorted_txs) - pivot_idx - 1,
    }


# ── 超量警告判斷 ──────────────────────────────────────────────────────────────

MAX_TOTAL = 1000

def check_overflow(count: int, limit: int) -> str | None:
    """
    若 count > limit，回傳建議訊息；否則回傳 None。
    建議的增加量不超過 MAX_TOTAL。
    """
    if count <= limit:
        return None
    suggested = min(count, MAX_TOTAL)
    return (
        f"符合條件的交易共 {count} 筆，超過目前設定的 {limit} 筆。\n\n"
        f"建議：可將筆數上限調整為 {suggested} 筆（系統上限 {MAX_TOTAL} 筆）。\n"
        f"是否要調整為 {suggested} 筆並重新篩選？"
    )


def suggest_increase(each_side: int, available_before: int,
                     available_after: int) -> str | None:
    """置中模式下，若可用筆數超過 each_side，回傳建議訊息"""
    max_side  = MAX_TOTAL // 2
    can_more  = available_before > each_side or available_after > each_side
    if not can_more:
        return None
    suggested = min(max(available_before, available_after), max_side)
    return (
        f"在設定時間附近還有更多交易可供參考：\n"
        f"  • 時間前：共 {available_before} 筆（目前取 {each_side} 筆）\n"
        f"  • 時間後：共 {available_after} 筆（目前取 {each_side} 筆）\n\n"
        f"建議：可將「前後各」調整為 {suggested} 筆（上限各 {max_side} 筆）。\n"
        f"是否要調整為前後各 {suggested} 筆重新篩選？"
    )
