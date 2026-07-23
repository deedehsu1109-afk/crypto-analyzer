"""
report_cards.py
把案件的各類查詢紀錄（地址分析、Hash 查詢、涉案地址、被害人陳述交易、網站溯源）
轉換成可編輯的「卡片」，供案件分析報告的卡片編排介面使用。

每張卡片：{"id": str, "source": str, "title": str, "text": str}
"""
from __future__ import annotations

_SOURCE_LABELS = {
    "transcript":   "筆錄內容",
    "case_summary": "案件摘要",
    "wallet":       "錢包分析",
    "tx_lookup":    "Hash查詢",
    "case_address": "涉案地址",
    "stated_tx":    "陳述交易",
    "domain_scan":  "網站溯源",
}


def _card(source: str, key, title: str, text: str) -> dict:
    return {"id": f"{source}:{key}", "source": source, "title": title, "text": text}


def _short(s: str, head: int = 10, tail: int = 6) -> str:
    if not s:
        return ""
    return f"{s[:head]}…{s[-tail:]}" if len(s) > head + tail else s


def transcript_to_card(case_row: dict) -> dict:
    text = (case_row.get("transcript") or "").strip()
    return _card("transcript", case_row.get("id"), "筆錄內容", text)


def case_summary_to_card(case_row: dict) -> dict:
    text = (case_row.get("description") or "").strip()
    return _card("case_summary", case_row.get("id"), "案件摘要", text)


def wallet_to_card(row: dict) -> dict:
    chain = row.get("chain", "")
    addr  = row.get("address", "")
    label = row.get("label") or ""
    title = f"錢包分析：{chain} {_short(addr)}"

    parts = [f"對 {chain} 錢包地址 {addr}" + (f"（標記：{label}）" if label else "") + " 進行分析："]
    if row.get("first_tx_time"):
        parts.append(f"首筆交易時間為 {row['first_tx_time']}，"
                      f"最後交易時間為 {row.get('last_tx_time') or '不明'}。")
    parts.append(
        f"該地址共發起 {row.get('out_count', 0)} 筆交易（總金額約 {row.get('out_total', 0)}），"
        f"接收 {row.get('in_count', 0)} 筆交易（總金額約 {row.get('in_total', 0)}）。"
    )
    if row.get("total_fee"):
        parts.append(f"累計支付手續費約 {row['total_fee']}。")
    if row.get("top_fee_dest"):
        parts.append(f"手續費支出最多流向地址：{row['top_fee_dest']}。")
    return _card("wallet", row.get("id"), title, "".join(parts))


def tx_lookup_to_card(row: dict) -> dict:
    chain = row.get("chain", "")
    h     = row.get("tx_hash", "")
    title = f"交易查詢：{chain} {_short(h, head=10, tail=4)}"
    text = (
        f"查詢 {chain} 交易 Hash：{h}，狀態為「{row.get('status') or '不明'}」，"
        f"發生時間 {row.get('tx_time') or '不明'}。"
        f"發送方：{row.get('from_addr') or '不明'}；接收方：{row.get('to_addr') or '不明'}；"
        f"金額：{row.get('value_str') or '不明'}；手續費：{row.get('fee_str') or '不明'}。"
    )
    return _card("tx_lookup", row.get("id"), title, text)


def case_address_to_card(row: dict) -> dict:
    addr = row.get("address", "")
    atype = row.get("addr_type", "")
    title = f"涉案{atype}：{_short(addr, head=14, tail=6)}"
    text = (
        f"{atype}「{addr}」"
        + (f"（{row.get('chain_institution')}）" if row.get("chain_institution") else "")
        + f"，持有人角色：{row.get('holder_role') or '不明'}。"
    )
    if row.get("label"):
        text += f" 標記說明：{row['label']}。"
    if row.get("notes"):
        text += f" 備註：{row['notes']}。"
    return _card("case_address", row.get("id"), title, text)


def stated_tx_to_card(row: dict) -> dict:
    method = row.get("method") or "不明"
    when   = row.get("tx_date") or ""
    title  = f"陳述交易：{method}" + (f" {when}" if when else "")

    if row.get("time_precision") == "精確時間" and row.get("tx_date"):
        time_part = f"於 {row['tx_date']} {row.get('tx_time') or ''} "
    elif row.get("time_desc"):
        time_part = f"約於「{row['time_desc']}」"
    elif row.get("tx_date"):
        time_part = f"約於 {row['tx_date']} "
    else:
        time_part = "於時間不詳之際"

    direction = row.get("direction")
    verb = "支出" if direction == "支出" else ("收到" if direction == "收入" else "進行")
    text = (
        f"被害人陳述{time_part}，透過「{method}」方式，"
        f"{verb}金額 {row.get('amount') if row.get('amount') is not None else '不明'} "
        f"{row.get('currency') or ''}。"
    )
    if row.get("counterpart_desc"):
        text += f" 對象：{row['counterpart_desc']}。"
    if row.get("bank_name") or row.get("account_no") or row.get("counterpart_account"):
        text += (f" 銀行資訊：{row.get('bank_name') or ''} "
                  f"我方帳號 {row.get('account_no') or '—'}，"
                  f"對方帳號 {row.get('counterpart_account') or '—'}。")
    if row.get("chain") or row.get("tx_hash"):
        text += (f" 區塊鏈資訊：{row.get('chain') or ''} "
                  f"Hash：{row.get('tx_hash') or '—'}，"
                  f"發送地址 {row.get('from_addr') or '—'} → 接收地址 {row.get('to_addr') or '—'}。")
    if row.get("notes"):
        text += f" 備註：{row['notes']}。"
    return _card("stated_tx", row.get("id"), title, text)


def domain_scan_to_card(row: dict) -> dict:
    target = row.get("target", "")
    title  = f"網站溯源：{target}"
    cf = "受 Cloudflare/CDN 保護" if row.get("is_cloudflare") else "未偵測到 Cloudflare 保護"
    text = f"對可疑網域「{target}」進行溯源掃描，{cf}。"
    if row.get("resolved_ip"):
        text += f" 解析 IP：{row['resolved_ip']}。"
    if row.get("has_wildcard"):
        text += " 偵測到萬用字元 DNS 設定。"
    text += f" 掃描時間：{row.get('created_at') or '不明'}。"
    return _card("domain_scan", row.get("id"), title, text)


def build_available_cards(case_id: int) -> list[dict]:
    """彙整案件的所有查詢紀錄，轉換成可編輯卡片清單（未經篩選，供編排介面呈現）。"""
    from database import db as _db

    cards: list[dict] = []
    case_row = _db.get_case(case_id)
    if case_row:
        if (case_row.get("transcript") or "").strip():
            cards.append(transcript_to_card(case_row))
        if (case_row.get("description") or "").strip():
            cards.append(case_summary_to_card(case_row))
    for row in _db.get_case_wallets(case_id):
        cards.append(wallet_to_card(row))
    for row in _db.get_case_tx_lookups(case_id):
        cards.append(tx_lookup_to_card(row))
    for row in _db.get_case_addresses(case_id):
        cards.append(case_address_to_card(row))
    for row in _db.get_stated_transactions(case_id):
        cards.append(stated_tx_to_card(row))
    for row in _db.get_domain_scans(case_id):
        cards.append(domain_scan_to_card(row))
    return cards
