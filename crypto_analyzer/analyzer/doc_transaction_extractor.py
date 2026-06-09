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

# 日期正則（西元 2025/03/13 或 民國 114年3月13日 或純 3/13）
_DATE_GREGORIAN = re.compile(
    r"(20\d{2})[/\-年](\d{1,2})[/\-月](\d{1,2})[日]?"
    r"(?:[T\s](\d{1,2}):(\d{2})(?::(\d{2}))?)?",
)
# 民國年（1~3位）→ 轉換為西元
_DATE_ROC = re.compile(
    r"(?:民國\s*)?(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"
    r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
)
# 時間單獨出現（搭配前一行的日期使用）
_TIME_ALONE = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b")

_UTC8 = datetime.timezone(datetime.timedelta(hours=8))

# ── 涉案地址 / 金融帳戶提取 ──────────────────────────────────────────────────

# 台灣銀行帳號正則
# 格式一：兩段式  007-20168333308（銀行代碼3位 + 帳號7~14位，一個破折號）
# 使用 (?<!\d)/(?!\d) 取代 \b，避免中文字（屬 \w）使 \b 失效
_BANK_ACCT_2SEG = re.compile(
    r"(?<!\d)(0\d{2})[-](\d{7,14})(?!\d)"
)
# 格式二：三段式  007-100-1234567（銀行代碼3位 + 分行2~4位 + 帳號6~10位）
_BANK_ACCT_3SEG = re.compile(
    r"(?<!\d)(0\d{2})[-\s](\d{2,4})[-\s](\d{6,10})(?!\d)"
)
# 格式三：關鍵字後緊接數字（各種分隔符）
_BANK_ACCT_KEYWORD = re.compile(
    r"(?:帳號|帳戶|存款帳號|金融帳戶|銀行帳號|帳戶號碼|匯款帳號|轉帳帳號|虛擬帳號)"
    r"[：:＊\s]*(\d[\d\s\-]{9,17}\d)"
)

# 台灣常見銀行代碼 → 名稱（用於自動識別機構）
_BANK_CODES: dict[str, str] = {
    "004": "台灣銀行", "005": "土地銀行", "006": "合作金庫",
    "007": "第一銀行", "008": "華南銀行", "009": "彰化銀行",
    "011": "上海銀行", "012": "台北富邦", "013": "國泰世華",
    "017": "兆豐銀行", "021": "花旗銀行", "050": "台灣中小企業銀行",
    "052": "渣打銀行", "053": "台中銀行", "054": "京城銀行",
    "101": "瑞興銀行", "102": "華泰銀行", "103": "台灣新光",
    "108": "陽信銀行", "803": "聯邦銀行", "806": "元大銀行",
    "807": "永豐銀行", "808": "玉山銀行", "809": "凱基銀行",
    "812": "台新銀行", "822": "中國信託",
}
# 銀行中文名稱（用於前後文識別機構）
_BANK_NAME_RE = re.compile(
    r"(" + "|".join(re.escape(v) for v in _BANK_CODES.values()) +
    r"|[^\s]{2,6}銀行)"
)

# 角色關鍵字偵測
_ROLE_SUSPECT  = re.compile(r"嫌疑人|被告|犯罪|詐騙|詐欺|洗錢|收款|涉案人|行為人")
_ROLE_VICTIM   = re.compile(r"被害人|受害者|告訴人|報案人|受害")
_ROLE_MIDDLE   = re.compile(r"中間人|車手|人頭|協助|轉帳人")

# 加密貨幣交易所關鍵字（識別機構）
_EXCHANGE_RE = re.compile(
    r"(OKX|幣安|Binance|MAX|幣託|BitoPro|BITO|Coinbase|Kraken|Bybit"
    r"|KuCoin|Gate\.io|Huobi|火幣|幣交所|交易所|虛擬通貨平台)",
    re.IGNORECASE
)


def _detect_role(context: str) -> str:
    """依前後文關鍵字推斷持有人角色"""
    if _ROLE_SUSPECT.search(context):
        return "嫌疑人"
    if _ROLE_VICTIM.search(context):
        return "被害人"
    if _ROLE_MIDDLE.search(context):
        return "中間人"
    return "不明"


def _detect_institution(context: str, bank_code: str = "") -> str:
    """從前後文或銀行代碼識別機構名稱"""
    if bank_code and bank_code in _BANK_CODES:
        return _BANK_CODES[bank_code]
    m = _BANK_NAME_RE.search(context)
    if m:
        return m.group(1)
    m = _EXCHANGE_RE.search(context)
    if m:
        return m.group(1).upper()
    return ""


def extract_addresses_accounts(text: str, source_doc: str = "") -> list[dict]:
    """
    從文件文字中提取：
    1. 加密錢包地址（ETH / TRX / BTC），含角色與機構偵測
    2. 台灣金融帳號，含角色與銀行名稱偵測
    回傳 list[dict]，每筆含：
        addr_type, chain_institution, address, holder_role, label, source_doc
    """
    results: list[dict] = []
    seen: set[str] = set()
    lines = text.split("\n")
    n = len(lines)

    def _ctx(i: int, before: int = 3, after: int = 3) -> str:
        return "\n".join(lines[max(0, i - before): min(n, i + after + 1)])

    # ── 加密錢包地址 ──
    for i, line in enumerate(lines):
        ctx = _ctx(i)
        for addr in _ADDR_ETH.findall(line):
            if addr in seen:
                continue
            seen.add(addr)
            results.append({
                "addr_type":         "加密錢包",
                "chain_institution": "ETH",
                "address":           addr,
                "holder_role":       _detect_role(ctx),
                "label":             _detect_institution(ctx),
                "source_doc":        source_doc,
                "notes":             "",
            })
        for addr in _ADDR_TRX.findall(line):
            if addr in seen:
                continue
            seen.add(addr)
            inst = _detect_institution(ctx)
            results.append({
                "addr_type":         "加密錢包",
                "chain_institution": "TRX",
                "address":           addr,
                "holder_role":       _detect_role(ctx),
                "label":             inst,
                "source_doc":        source_doc,
                "notes":             "",
            })
        for addr in _ADDR_BTC.findall(line):
            if addr in seen:
                continue
            seen.add(addr)
            results.append({
                "addr_type":         "加密錢包",
                "chain_institution": "BTC",
                "address":           addr,
                "holder_role":       _detect_role(ctx),
                "label":             _detect_institution(ctx),
                "source_doc":        source_doc,
                "notes":             "",
            })

    # ── 金融帳號（兩段式 / 三段式） ──
    for i, line in enumerate(lines):
        ctx = _ctx(i)
        # 兩段式：007-20168333308
        for m in _BANK_ACCT_2SEG.finditer(line):
            bank_code = m.group(1)
            acct_norm = m.group(0).replace(" ", "")   # 保留破折號（方便識別格式）
            acct_key  = re.sub(r"\D", "", acct_norm)  # 純數字用於去重
            if acct_key in seen:
                continue
            seen.add(acct_key)
            institution = _BANK_CODES.get(bank_code,
                          _detect_institution(ctx, bank_code) or f"銀行{bank_code}")
            results.append({
                "addr_type":         "金融帳戶",
                "chain_institution": institution,
                "address":           acct_norm,
                "holder_role":       _detect_role(ctx),
                "label":             "",
                "source_doc":        source_doc,
                "notes":             "",
            })
        # 三段式：007-100-1234567
        for m in _BANK_ACCT_3SEG.finditer(line):
            bank_code = m.group(1)
            acct_norm = m.group(0).replace(" ", "")
            acct_key  = re.sub(r"\D", "", acct_norm)
            if acct_key in seen:
                continue
            seen.add(acct_key)
            institution = _BANK_CODES.get(bank_code,
                          _detect_institution(ctx, bank_code) or f"銀行{bank_code}")
            results.append({
                "addr_type":         "金融帳戶",
                "chain_institution": institution,
                "address":           acct_norm,
                "holder_role":       _detect_role(ctx),
                "label":             "",
                "source_doc":        source_doc,
                "notes":             "",
            })

    # ── 金融帳號（關鍵字後接數字） ──
    for i, line in enumerate(lines):
        ctx = _ctx(i)
        for m in _BANK_ACCT_KEYWORD.finditer(line):
            acct_raw = re.sub(r"[\s\-]", "", m.group(1))
            if len(acct_raw) < 10 or acct_raw in seen:
                continue
            seen.add(acct_raw)
            # 嘗試從前三碼辨識銀行代碼
            bank_code_guess = acct_raw[:3] if acct_raw[:3] in _BANK_CODES else ""
            institution = (_BANK_CODES.get(bank_code_guess)
                           or _detect_institution(ctx)
                           or "不明銀行")
            results.append({
                "addr_type":         "金融帳戶",
                "chain_institution": institution,
                "address":           acct_raw,
                "holder_role":       _detect_role(ctx),
                "label":             "",
                "source_doc":        source_doc,
                "notes":             "",
            })

    return results


def _parse_date_line(line: str) -> tuple[str | None, str | None]:
    """
    解析單行日期。回傳 (date_str, time_str)，未找到時回傳 (None, None)。
    支援西元 / 民國 / 無年份日期。
    """
    # 西元年
    m = _DATE_GREGORIAN.search(line)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        hh = m.group(4) or "00"
        mm = m.group(5) or "00"
        ss = m.group(6) or "00"
        return (f"{y}-{int(mo):02d}-{int(d):02d}",
                f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}")
    # 民國年（+1911 轉西元）
    m = _DATE_ROC.search(line)
    if m:
        roc = int(m.group(1))
        y = roc + 1911
        mo, d = m.group(2), m.group(3)
        hh = m.group(4) or "00"
        mm = m.group(5) or "00"
        ss = m.group(6) or "00"
        return (f"{y}-{int(mo):02d}-{int(d):02d}",
                f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}")
    return None, None


def _extract_doc_via_word(path: str) -> str:
    """使用 Windows COM（需安裝 Microsoft Word）讀取 .doc 檔案文字"""
    try:
        import win32com.client
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc  = word.Documents.Open(os.path.abspath(path))
        text = doc.Content.Text
        doc.Close(False)
        word.Quit()
        return text or ""
    except Exception:
        return ""


def extract_text_from_file(path: str) -> str:
    """呼叫 extract.py 提取文件文字，回傳純文字。
    .doc 格式若 extract.py 不支援，自動改用 Word COM 讀取。"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXT:
        return ""

    # .doc 舊格式：直接走 Word COM，不透過 extract.py
    if ext == ".doc":
        return _extract_doc_via_word(path)

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, EXTRACT_SCRIPT, path, "--pretty"],
            capture_output=True, timeout=60,
            env=env
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        if result.returncode != 0:
            return ""
        data = json.loads(stdout)
        # extract.py 回傳錯誤時，若是 .docx 嘗試用 Word COM 備援
        if "error" in data:
            if ext == ".docx":
                return _extract_doc_via_word(path)
            return ""
        # 整合文字（PDF 頁面 / DOCX 段落 / ODF content / XLSX 表格）
        parts = []
        for page in data.get("pages", []):
            parts.append(page.get("text", ""))
        for para in data.get("paragraphs", []):
            parts.append(para.get("text", ""))
        for tbl in data.get("tables", []):
            for row in tbl.get("rows", []):
                parts.append("  ".join(c for c in row if c))
        for block in data.get("content", []):
            parts.append(str(block))
        for sheet in data.get("sheets", []):
            for row in sheet.get("data", []):
                parts.append("  ".join(str(c) for c in row if c))
        text = "\n".join(p for p in parts if p.strip())
        if data.get("ocr_used") and text.strip():
            text = f"[OCR 識別結果]\n{text}"
        if data.get("warning") and not text.strip():
            return f"[提取警告] {data['warning']}"
        return text
    except Exception:
        if ext == ".txt":
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception:
                return ""
        return ""


def _find_addresses(text: str) -> list[str]:
    """依出現順序回傳不重複地址（保留 FROM 在前、TO 在後的語意順序）。"""
    seen: list[str] = []
    for addr in (_ADDR_ETH.findall(text) +
                 _ADDR_TRX.findall(text) +
                 _ADDR_BTC.findall(text)):
        if addr not in seen:
            seen.append(addr)
    return seen


def _parse_transactions(text: str) -> list[dict]:
    """
    從自由文字中嘗試提取交易記錄。
    策略：
    1. 掃描每一行找日期（支援西元 / 民國）
    2. 對找到日期的行，往後取 12 行作為上下文
    3. 若同一日期行後緊接著有獨立時間行，以該時間覆蓋
    4. 在上下文中找幣種+金額+地址
    5. 對「同一日期出現多次不同時間」的情況分別建立記錄（不合併）
    """
    txs = []
    lines = text.split("\n")
    n = len(lines)

    i = 0
    while i < n:
        line = lines[i]
        date_str, time_str = _parse_date_line(line)

        if date_str is None:
            i += 1
            continue

        # 往後最多 12 行作為上下文（涵蓋跨行表格格式）
        ctx_end = min(i + 13, n)
        context_lines = lines[i: ctx_end]
        context = "\n".join(context_lines)

        # 若時間仍是 00:00:00，嘗試在上下文中找獨立時間
        if time_str == "00:00:00":
            for cl in context_lines[1:4]:
                tm = _TIME_ALONE.search(cl)
                if tm:
                    hh, mm = tm.group(1), tm.group(2)
                    ss = tm.group(3) or "00"
                    time_str = f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}"
                    break

        # 幣種 + 數量（優先找「數字+幣種符號」格式）
        currency, quantity = None, None
        cm = _CRYPTO_AMOUNT.search(context)
        if cm:
            quantity = float(cm.group(1).replace(",", ""))
            currency = cm.group(2).upper()
        else:
            for sym in _CURRENCIES:
                if re.search(r"\b" + sym + r"\b", context, re.IGNORECASE):
                    currency = sym
                    break

        # NT 金額（過濾掉年份、頁碼等誤判）
        amount_ntd = None
        for am in _AMOUNT.finditer(context):
            val_str = am.group(1).replace(",", "")
            try:
                val = float(val_str)
                if val > 100:
                    amount_ntd = val
                    break
            except ValueError:
                pass

        # 錢包地址（在整個上下文中找）
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

        # 跳過已處理的上下文行，避免在同一區塊內因為另一個日期字串重複解析
        # 但若下一行也有日期（例如表格中每行都有日期），不要跳過太多
        i += 1

    # 去除完全空白的記錄
    txs = [t for t in txs if any([
        t["from_addr"], t["to_addr"],
        t["amount_ntd"], t["quantity"], t["currency"]
    ])]

    # 去除重複（同日期+時間+幣種+數量）
    seen: set[tuple] = set()
    unique: list[dict] = []
    for t in txs:
        key = (t["tx_date"], t["tx_time"], t["currency"],
               str(t["quantity"]), t["to_addr"])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def analyze_files(file_paths: list) -> dict:
    """處理指定的文件列表（使用者直接選取檔案）"""
    all_text  = []
    all_txs   = []
    processed = []
    errors    = []

    all_addrs: list[dict] = []

    for fpath in file_paths:
        fname = os.path.basename(fpath)
        ext   = os.path.splitext(fname)[1].lower()
        if ext not in SUPPORTED_EXT or not os.path.isfile(fpath):
            errors.append(fname)
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
        addrs = extract_addresses_accounts(text, source_doc=fname)
        all_addrs.extend(addrs)

    return {
        "processed_files":  processed,
        "error_files":      errors,
        "raw_text":         "\n\n".join(all_text)[:5000],
        "transactions":     all_txs,
        "addresses":        all_addrs,
    }


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
