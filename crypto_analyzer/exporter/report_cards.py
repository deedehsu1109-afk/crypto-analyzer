"""
report_cards.py
把案件的各類查詢紀錄（地址分析、Hash 查詢、涉案地址、被害人陳述交易、網站溯源、
幣流圖圖片快照）轉換成可編輯的「卡片」，供案件分析報告的卡片編排介面使用。

每張卡片：{"id": str, "source": str, "kind": "text"|"image", "title": str,
          "text": str, "image_path": str（僅 kind=="image" 時有值）}
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
    "image":        "圖片卡片",
    "wallet_list_table":    "電子錢包列表",
    "flow_table":           "幣流分析表",
    "offchain_tx_table":    "鏈下交易匯差",
    "onchain_stated_table": "鏈上交易資訊",
}


def _card(source: str, key, title: str, text: str, kind: str = "text", **extra) -> dict:
    card = {"id": f"{source}:{key}", "source": source, "kind": kind,
            "title": title, "text": text}
    card.update(extra)
    return card


def _short(s: str, head: int = 10, tail: int = 6) -> str:
    if not s:
        return ""
    return f"{s[:head]}…{s[-tail:]}" if len(s) > head + tail else s


def _encode_table(headers: list[str], rows: list[list]) -> str:
    """把表格資料編碼成 pipe 分隔文字，讓表格卡片能沿用既有的
    「唯讀預設＋明確編輯＋草稿暫存不落地」文字卡片編輯機制（見 case_report_card_editor.py）。"""
    lines = [" | ".join(str(h) for h in headers)]
    for row in rows:
        lines.append(" | ".join("" if v is None or v == "" else str(v) for v in row))
    return "\n".join(lines)


def decode_table(text: str) -> tuple[list[str], list[list[str]]]:
    """把 _encode_table() 產生（或使用者編輯過）的 pipe 分隔文字解析回 (headers, rows)，
    供 case_template_builder.build_case_doc_from_cards() 產製 Word 表格用。"""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return [], []
    headers = [c.strip() for c in lines[0].split("|")]
    rows = [[c.strip() for c in ln.split("|")] for ln in lines[1:]]
    return headers, rows


def _table_card(source: str, key, title: str, headers: list[str], rows: list[list]) -> dict:
    return _card(source, key, title, _encode_table(headers, rows), kind="table")


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


def flow_graph_image_to_card(row: dict) -> dict:
    title = row.get("title") or "幣流圖快照"
    return _card("image", row.get("id"), title, title,
                 kind="image", image_path=row.get("image_path"))


def wallet_list_table_card(case_id: int) -> dict | None:
    """電子錢包列表卡片（source: case_addresses，僅列出加密錢包），
    對應「幣流報告相關表格.xlsx」的「電子錢包列表」工作表。"""
    from database import db as _db
    wallets = [r for r in _db.get_case_addresses(case_id)
               if (r.get("addr_type") or "") == "加密錢包"]
    if not wallets:
        return None
    headers = ["項次", "鏈別", "錢包地址", "持有人角色", "標籤/說明", "資料來源"]
    rows = [
        [i, r.get("chain_institution") or "", r.get("address") or "",
         r.get("holder_role") or "不明", r.get("label") or "", r.get("source_doc") or ""]
        for i, r in enumerate(wallets, start=1)
    ]
    return _table_card("wallet_list_table", case_id, "電子錢包列表", headers, rows)


def flow_analysis_table_card(case_id: int) -> dict | None:
    """幣流分析表卡片（source: 最新一筆已儲存的幣流圖快照），
    對應「幣流報告相關表格.xlsx」的「幣流分析表」工作表。
    「方法論」欄留白，因追蹤方法論（FIFO/LIFO/比例原則等）需調查員專業判斷，無法從資料自動推論；
    「層數」以 role=="seed" 的節點為起點做 BFS 跳數估算，僅供參考，仍需人工核對。
    需先在幣流圖頁面按「儲存快照」才會有資料，否則不產生此卡片。"""
    from database import db as _db
    snapshots = _db.get_graph_snapshots(case_id)
    if not snapshots:
        return None
    snap = snapshots[0]  # 依 saved_at DESC，取最新一筆
    edges = snap.get("edges") or []
    if not edges:
        return None

    nodes = {n.get("address"): n for n in (snap.get("nodes") or [])}
    hop = {addr: 0 for addr, n in nodes.items() if n.get("role") == "seed"}
    if hop:
        adjacency: dict[str, list[str]] = {}
        for e in edges:
            adjacency.setdefault(e.get("source", ""), []).append(e.get("target", ""))
        frontier = list(hop.keys())
        while frontier:
            nxt = []
            for addr in frontier:
                for nb in adjacency.get(addr, []):
                    if nb not in hop:
                        hop[nb] = hop[addr] + 1
                        nxt.append(nb)
            frontier = nxt

    headers = ["層數", "方法論", "交易Hash", "交易時間", "發送方", "接收方", "數量(幣種)"]
    rows = []
    for e in edges:
        target = e.get("target", "")
        amount = (f"{e['token_amount']:,.4f} {e['token_symbol']}" if e.get("token_symbol")
                  else f"{e.get('value_native', 0):,.6f}")
        rows.append([
            hop.get(target, ""), "", e.get("tx_hash") or "", e.get("tx_time") or "",
            e.get("source") or "", target, amount,
        ])
    title = f"幣流分析表：{snap.get('label') or snap.get('chain', '')}"
    return _table_card("flow_table", f"{case_id}:{snap.get('id')}", title, headers, rows)


def offchain_tx_table_card(case_id: int) -> dict | None:
    """鏈下交易明細匯差表卡片（source: case_stated_transactions），
    對應「幣流報告相關表格.xlsx」的「鏈下交易明細匯差表」工作表。
    資料庫未儲存個別「被害人／告訴人」姓名及「交易匯率／市價匯率」欄位（原範本才有），
    故僅帶入資料庫實際擁有的欄位，其餘留白供人工填寫查證。"""
    from database import db as _db
    rows_src = _db.get_stated_transactions(case_id)
    if not rows_src:
        return None
    headers = ["陳述方式", "方向", "時間", "金額", "幣別", "對象描述", "我方帳號", "對方帳號"]
    rows = []
    for r in rows_src:
        when = r.get("tx_date") or r.get("time_desc") or ""
        if r.get("tx_time"):
            when = f"{when} {r['tx_time']}".strip()
        rows.append([
            r.get("method") or "不明", r.get("direction") or "不明", when,
            r.get("amount") if r.get("amount") is not None else "",
            r.get("currency") or "", r.get("counterpart_desc") or "",
            r.get("account_no") or "", r.get("counterpart_account") or "",
        ])
    return _table_card("offchain_tx_table", case_id, "鏈下交易明細匯差表", headers, rows)


def onchain_stated_table_card(case_id: int) -> dict | None:
    """被害人鏈上交易資訊卡片（source: case_stated_transactions，篩選有鏈上足跡的陳述），
    對應「幣流報告相關表格.xlsx」的「被害人鏈上交易資訊」工作表。"""
    from database import db as _db
    rows_src = [r for r in _db.get_stated_transactions(case_id)
                if r.get("chain") or r.get("tx_hash") or r.get("from_addr") or r.get("to_addr")]
    if not rows_src:
        return None
    headers = ["時間", "方向", "鏈別", "交易Hash", "發送方", "接收方", "金額/數量", "幣別"]
    rows = []
    for r in rows_src:
        when = r.get("tx_date") or r.get("time_desc") or ""
        if r.get("tx_time"):
            when = f"{when} {r['tx_time']}".strip()
        rows.append([
            when, r.get("direction") or "不明", r.get("chain") or "",
            r.get("tx_hash") or "", r.get("from_addr") or "", r.get("to_addr") or "",
            r.get("amount") if r.get("amount") is not None else "", r.get("currency") or "",
        ])
    return _table_card("onchain_stated_table", case_id, "被害人鏈上交易資訊", headers, rows)


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
    for row in _db.get_report_images(case_id):
        cards.append(flow_graph_image_to_card(row))
    for table_card_fn in (wallet_list_table_card, flow_analysis_table_card,
                          offchain_tx_table_card, onchain_stated_table_card):
        table_card = table_card_fn(case_id)
        if table_card is not None:
            cards.append(table_card)
    return cards
