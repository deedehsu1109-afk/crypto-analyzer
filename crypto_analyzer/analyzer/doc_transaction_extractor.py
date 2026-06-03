from __future__ import annotations
import re
import json
import subprocess
import sys
import os
import datetime

EXTRACT_SCRIPT = os.path.join(os.path.dirname(__file__),
                              "..", "..", "doc_analyzer", "extract.py")
EXTRACT_SCRIPT = os.path.normpath(EXTRACT_SCRIPT)

SUPPORTED_EXT = {".pdf", ".docx", ".xlsx", ".odt", ".txt", ".doc"}

# 台灣常見加密幣種
_CURRENCIES = ["BTC", "ETH", "USDT", "USDC", "BNB", "TRX",
               "SOL", "XRP", "ADA", "DOGE", "MATIC"]

# 錢包地址正則
_ADDR_ETH = re.compile(r"0x[0-9a-fA-F]{40}")
_ADDR_TRX = re.compile(r"T[0-9a-zA-Z]{33}")
_ADDR_BTC = re.compile(r"(?:bc1[0-9a-z]{25,39}|[13][0-9a-zA-Z]{25,34})")

# 金額正則（含千分位）
_AMOUNT = re.compile(
    r"(?:NT\$?|新台幣|台幣|TWD|NTD)?\s*"
    r"([\d,]+(?:\.\d+)?)\s*"
    r"(?:元|NT|NTD|新台幣|台幣)?",
    re.IGNORECASE
)
_CRYPTO_AMOUNT = re.compile(
    r"([\d,]+(?:\.\d{2,10})?)\s*"
    r"(" + "|".join(_CURRENCIES) + r")",
    re.IGNORECASE
)

# 日期正則（多種格式）
_DATE = re.compile(
    r"(\d{4})[/\-年](\d{1,2})[/\-月](\d{1,2})[日]?"
    r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
)

_UTC8 = datetime.timezone(datetime.timedelta(hours=8))


def extract_text_from_file(path: str) -> str:
    """呼叫 extract.py 提取文件文字，回傳純文字"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXT:
        return ""
    try:
        result = subprocess.run(
            [sys.executable, EXTRACT_SCRIPT, path, "--pretty"],
            capture_output=True, text=True, timeout=60, encoding="utf-8"
        )
        if result.returncode != 0:
            return ""
        data = json.loads(result.stdout)
        # 依格式整合文字
        parts = []
        for page in data.get("pages", []):
            parts.append(page.get("text", ""))
        for para in data.get("paragraphs", []):
            parts.append(para.get("text", ""))
        for block in data.get("content", []):
            parts.append(str(block))
        for sheet in data.get("sheets", []):
            for row in sheet.get("data", []):
                parts.append("  ".join(str(c) for c in row if c))
        return "\n".join(p for p in parts if p.strip())
    except Exception:
        # txt 直接讀
        if ext == ".txt":
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception:
                return ""
        return ""


def _find_addresses(text: str) -> list[str]:
    addrs = set()
    addrs.update(_ADDR_ETH.findall(text))
    addrs.update(_ADDR_TRX.findall(text))
    addrs.update(_ADDR_BTC.findall(text))
    return list(addrs)


def _parse_transactions(text: str) -> list[dict]:
    """
    從自由文字中嘗試提取交易記錄。
    採用滑動視窗：每次找到日期後，在其後 500 字元內尋找金額、幣種、地址。
    """
    txs = []
    lines = text.split("\n")

    for i, line in enumerate(lines):
        # 找日期
        dm = _DATE.search(line)
        if not dm:
            continue

        y, mo, d = dm.group(1), dm.group(2), dm.group(3)
        hh = dm.group(4) or "00"
        mm = dm.group(5) or "00"
        ss = dm.group(6) or "00"
        date_str = f"{y}-{int(mo):02d}-{int(d):02d}"
        time_str = f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}"

        # 取該行及後續 5 行作為上下文
        context = "\n".join(lines[i: i + 6])

        # 幣種 + 數量
        currency, quantity = None, None
        cm = _CRYPTO_AMOUNT.search(context)
        if cm:
            quantity = float(cm.group(1).replace(",", ""))
            currency = cm.group(2).upper()
        else:
            for sym in _CURRENCIES:
                if sym in context.upper():
                    currency = sym
                    break

        # NT 金額
        amount_ntd = None
        for am in _AMOUNT.finditer(context):
            val_str = am.group(1).replace(",", "")
            try:
                val = float(val_str)
                if val > 100:   # 過濾掉太小的數字
                    amount_ntd = val
                    break
            except ValueError:
                pass

        # 錢包地址
        addrs = _find_addresses(context)
        from_addr = addrs[0] if len(addrs) > 0 else None
        to_addr   = addrs[1] if len(addrs) > 1 else None

        txs.append({
            "tx_date":       date_str,
            "tx_time":       time_str,
            "from_addr":     from_addr or "",
            "to_addr":       to_addr or "",
            "amount_ntd":    amount_ntd,
            "quantity":      quantity,
            "currency":      currency or "",
            "exchange_rate": None,
            "daily_avg":     None,
            "daily_high":    None,
            "daily_low":     None,
            "source_doc":    "",
        })

    # 去除完全空白的記錄
    txs = [t for t in txs if any([
        t["from_addr"], t["to_addr"],
        t["amount_ntd"], t["quantity"], t["currency"]
    ])]
    return txs


def analyze_folder(folder: str) -> dict:
    """
    掃描資料夾內所有支援文件，提取：
    1. 合併文字摘要（用於案件描述）
    2. 交易記錄列表
    """
    all_text  = []
    all_txs   = []
    processed = []
    errors    = []

    for fname in sorted(os.listdir(folder)):
        fpath = os.path.join(folder, fname)
        ext   = os.path.splitext(fname)[1].lower()
        if ext not in SUPPORTED_EXT or not os.path.isfile(fpath):
            continue
        text = extract_text_from_file(fpath)
        if not text:
            errors.append(fname)
            continue
        processed.append(fname)
        all_text.append(f"【{fname}】\n{text[:3000]}")
        txs = _parse_transactions(text)
        for t in txs:
            t["source_doc"] = fname
        all_txs.extend(txs)

    summary_text = "\n\n".join(all_text)[:5000]  # 摘要截斷

    return {
        "processed_files":  processed,
        "error_files":      errors,
        "raw_text":         summary_text,
        "transactions":     all_txs,
    }


def summarize_for_case(raw_text: str, max_chars: int = 1000) -> str:
    """
    將提取的文字整理為案件描述摘要（簡單截斷＋清理）。
    實際使用時可接 Claude API 進行 AI 摘要。
    """
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    # 移除重複行
    seen = set()
    unique_lines = []
    for l in lines:
        if l not in seen:
            seen.add(l)
            unique_lines.append(l)
    summary = "\n".join(unique_lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "……（截斷，請依原始文件補充）"
    return summary
