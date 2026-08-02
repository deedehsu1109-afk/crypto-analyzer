"""
跨鏈交易偵測與獨立驗證

設計原則（對應論文「透明、可驗證鑑識工具」定位，見 CLAUDE.md）：
  1. 只用免費公開資料來源，不依賴 MistTrack / Chainalysis 等付費地址標籤服務。
  2. 自動偵測只給「信心等級 + 具體證據」，不武斷宣稱「已確認為跨鏈交易」——
     公開資料常常查不到資金池標籤（例如本工具開發過程中實測 TronScan 對已知
     的 Bitget Wallet 資金池地址完全沒有公開標籤），過度自信的判定反而誤導調查。
  3. 找不到來源鏈交易是預期中的常態，不是工具的失敗：跨鏈橋接的兩段交易之間
     的對應關係，通常只存在於橋接服務商自己的私有訂單資料庫（例如 Bitget
     Bridge 網頁的搜尋查詢），不是公開鏈上資料能推導出來的。此函式庫因此採取
     「工具自動偵測疑似資金池 → 調查員至對應橋接瀏覽器人工查詢來源鏈 Hash →
     工具用既有 API 獨立重新查一次來源鏈交易來覆核」的兩段式設計，而不是嘗試
     自動爬取橋接網站（多數有反爬蟲保護且無公開文件化 API）。

回傳值一律附帶 method_log，逐步記錭使用的工具／API 與查詢結果，供之後存入
crosschain_traces.method_log 欄位，滿足鑑識稽核可追溯性要求。
"""
from __future__ import annotations

import datetime

from api.etherscan import EtherscanAPI
from api.tronscan import TronScanAPI
from api.bitcoin import BitcoinAPI
from api import label_fetcher
from analyzer.tx_analyzer import analyze_eth_tx, analyze_trx_tx, analyze_btc_tx
from analyzer.bridge_registry import match_bridge_label

# 各鏈「疑似資金池」交易數門檻。刻意保持簡單、明列常數，不做加權評分公式，
# 避免製造虛假的精確度。使用者可依實務調整。
_POOL_TX_THRESHOLD = {"ETH": 5000, "TRX": 50000, "BTC": 5000}

_UTC8 = datetime.timezone(datetime.timedelta(hours=8))


def _now_str() -> str:
    return datetime.datetime.now(tz=_UTC8).strftime("%Y-%m-%d %H:%M:%S UTC+8")


def _log(steps: list, tool: str, action: str, result: str, by: str = "system") -> None:
    steps.append({
        "time": _now_str(), "tool": tool, "action": action,
        "result": result, "by": by,
    })


# ── 單一地址活躍度查詢 ────────────────────────────────────────────────────────

def _address_activity(chain: str, address: str, trx_api_key: str = "",
                      method_log: list | None = None) -> dict:
    """回傳 {"tx_count": int, "label": str|None}，並將查詢步驟寫入 method_log。"""
    steps = method_log if method_log is not None else []
    chain = chain.upper()

    if chain == "ETH":
        label_info = label_fetcher.get_eth_label(address)
        label = label_info.get("ens") or label_info.get("name")
        _log(steps, "Blockscout API (get_eth_label)", f"查詢 {address} 標籤",
             label or "無標籤")

        tx_count = 0
        try:
            import requests
            r = requests.get(
                f"https://eth.blockscout.com/api/v2/addresses/{address}/counters",
                timeout=10, headers={"User-Agent": "CryptoAnalyzer/1.0"})
            if r.ok:
                tx_count = int(r.json().get("transactions_count", 0) or 0)
        except Exception:
            pass
        _log(steps, "Blockscout API (/addresses/{addr}/counters)",
             f"查詢 {address} 交易總數", f"{tx_count:,} 筆")
        return {"tx_count": tx_count, "label": label}

    if chain == "TRX":
        api = TronScanAPI(trx_api_key)
        data = api.get_account(address)
        tx_count = int(data.get("totalTransactionCount", 0) or 0)
        label = data.get("name") or None
        _log(steps, "TronScan API (get_account)",
             f"查詢 {address} 帳戶概況",
             f"totalTransactionCount={tx_count:,}，標籤：{label or '無'}")
        return {"tx_count": tx_count, "label": label}

    if chain == "BTC":
        api = BitcoinAPI()
        info = label_fetcher.get_btc_label(address)
        label = info.get("name")
        tx_count = 0
        try:
            data = api.get_address_info(address)
            tx_count = int(data.get("n_tx", 0) or 0)
        except Exception:
            pass
        _log(steps, "blockchain.info + WalletExplorer API",
             f"查詢 {address} 交易數與標籤",
             f"n_tx={tx_count:,}，標籤：{label or '無'}")
        return {"tx_count": tx_count, "label": label}

    return {"tx_count": 0, "label": None}


def _confidence_for(chain: str, activity: dict) -> tuple[str, str | None]:
    """依標籤/交易數判定信心等級，回傳 (confidence, matched_bridge)。"""
    matched = match_bridge_label(activity.get("label"))
    if matched:
        return "HIGH", matched
    threshold = _POOL_TX_THRESHOLD.get(chain.upper(), 5000)
    if activity.get("tx_count", 0) >= threshold:
        return "MEDIUM", None
    return "NONE", None


# ── 主要偵測函式 ──────────────────────────────────────────────────────────────

def detect_pool_candidate(chain: str, from_addr: str, to_addr: str,
                          trx_api_key: str = "") -> dict:
    """
    對交易的發送方／接收方分別查詢活躍度，判斷哪一側「疑似」為跨鏈橋接資金池。

    回傳：
    {
      "發送方": {"address":.., "label":.., "tx_count":.., "confidence":.., "matched_bridge":..},
      "接收方": {同上},
      "pool_side": "發送方" | "接收方" | None,
      "overall_confidence": "HIGH" | "MEDIUM" | "NONE",
      "method_log": [...],
    }
    """
    method_log: list = []
    _log(method_log, "crosschain_tracer.detect_pool_candidate",
         "開始偵測", f"chain={chain}, 發送方={from_addr}, 接收方={to_addr}")

    sides = {}
    for label_key, addr in (("發送方", from_addr), ("接收方", to_addr)):
        if not addr or addr in ("N/A", ""):
            sides[label_key] = {
                "address": addr, "label": None, "tx_count": 0,
                "confidence": "NONE", "matched_bridge": None,
            }
            continue
        activity = _address_activity(chain, addr, trx_api_key, method_log)
        confidence, matched_bridge = _confidence_for(chain, activity)
        sides[label_key] = {
            "address": addr,
            "label": activity.get("label"),
            "tx_count": activity.get("tx_count", 0),
            "confidence": confidence,
            "matched_bridge": matched_bridge,
        }

    # 信心等級排序：HIGH > MEDIUM > NONE，兩側都命中時取較高者為 pool_side
    rank = {"HIGH": 2, "MEDIUM": 1, "NONE": 0}
    pool_side = None
    overall = "NONE"
    for key in ("發送方", "接收方"):
        c = sides[key]["confidence"]
        if rank[c] > rank[overall]:
            overall = c
            pool_side = key

    reasoning = (
        f"{pool_side} 信心等級 {overall}"
        + (f"，命中橋接標籤：{sides[pool_side]['matched_bridge']}"
           if pool_side and sides[pool_side]["matched_bridge"]
           else f"，交易數 {sides[pool_side]['tx_count']:,} 超過門檻（疑似資金池，無公開標籤）"
           if pool_side and overall == "MEDIUM" else "，兩側皆未達疑似資金池門檻")
    )
    _log(method_log, "crosschain_tracer.detect_pool_candidate", "偵測結束", reasoning)

    return {
        "發送方": sides["發送方"],
        "接收方": sides["接收方"],
        "pool_side": pool_side if overall != "NONE" else None,
        "overall_confidence": overall,
        "method_log": method_log,
    }


# ── 來源鏈交易獨立驗證 ────────────────────────────────────────────────────────

def _fetch_and_analyze(chain: str, tx_hash: str, api_keys: dict) -> dict:
    chain = chain.upper()
    if chain == "ETH":
        api = EtherscanAPI(api_keys.get("etherscan_api_key", ""))
        raw = api.get_transaction(tx_hash)
        return analyze_eth_tx(raw)
    if chain == "TRX":
        api = TronScanAPI(api_keys.get("trongrid_api_key", ""))
        raw = api.get_transaction(tx_hash)
        return analyze_trx_tx(raw)
    api = BitcoinAPI()
    raw = api.get_transaction(tx_hash)
    return analyze_btc_tx(raw)


def _parse_time_str(s: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(s.replace(" UTC+8", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC8)
    except Exception:
        return None


def effective_transfer(result: dict) -> dict:
    """
    從 tx_analyzer.analyze_*_tx() 的結果中，取出「實際經濟意義上」的收付雙方與金額。

    重要：ERC20 / TRC20 代幣轉帳時，交易本身的「發送方」/「接收方」欄位是
    合約呼叫層級的欄位——「接收方」實際上是代幣合約位址（例如 USDT 合約），
    不是真正收到代幣的人；真正的收款方在 token_transfers 明細裡。
    這裡優先取 token_transfers[0]，只有原生轉帳（token_transfers 為空）才
    使用頂層的發送方/接收方/金額欄位。

    回傳：{"from": str, "to": str, "amount_str": str, "asset": str, "note": str}
    """
    tokens = result.get("token_transfers", [])
    if tokens:
        t = tokens[0]
        note = "" if len(tokens) == 1 else f"（此交易含 {len(tokens)} 筆代幣轉帳，僅取第一筆，其餘請人工核對）"
        return {
            "from": t.get("從", ""), "to": t.get("至", ""),
            "amount_str": t.get("金額", ""), "asset": t.get("Token", ""),
            "note": note,
        }

    # 原生轉帳
    if "接收方（明細）" in result:  # BTC：多輸出，無單一「接收方」
        receivers = result.get("接收方（明細）", [])
        if len(receivers) == 1:
            return {"from": result.get("發送方", ""), "to": receivers[0].get("地址", ""),
                    "amount_str": receivers[0].get("BTC", ""), "asset": "BTC", "note": ""}
        return {"from": result.get("發送方", ""), "to": "N/A",
                "amount_str": result.get("輸出總額", ""), "asset": "BTC",
                "note": f"（此交易有 {len(receivers)} 個輸出位址，需人工指定實際收款方）"}

    amount_key = next((k for k in ("ETH 金額", "TRX 金額") if k in result), None)
    return {
        "from": result.get("發送方", ""), "to": result.get("接收方", ""),
        "amount_str": result.get(amount_key, "") if amount_key else "",
        "asset": result.get("chain", ""), "note": "",
    }


def verify_origin_claim(dest_chain: str, dest_pool_addr: str, dest_time_str: str,
                        dest_amount_str: str, origin_chain: str, origin_tx_hash: str,
                        api_keys: dict, trx_api_key: str = "") -> dict:
    """
    調查員在橋接瀏覽器人工查得來源鏈交易後回填，此函式用既有 API「獨立重新查詢」
    該來源鏈交易，而非採信調查員或橋接網站的說法。

    checks 只陳述事實（地址是否也命中資金池判定、時序先後、金額落差百分比），
    不輸出「已證實／偽造」這類武斷結論——是否採信留給調查員判斷。

    注意：dest_pool_addr / dest_time_str / dest_amount_str 呼叫端請一律傳入
    先經過 effective_transfer(dest_result) 取出的值，不要直接傳目的鏈交易的
    頂層「發送方」/「接收方」/「XX 金額」欄位——代幣轉帳時那些欄位是合約
    呼叫層級的資料，不是實際收付雙方與金額。
    """
    method_log: list = []
    _log(method_log, "crosschain_tracer.verify_origin_claim", "開始獨立驗證",
         f"來源鏈={origin_chain}, 來源 Hash={origin_tx_hash}", by="investigator+system")

    origin_result = _fetch_and_analyze(origin_chain, origin_tx_hash, api_keys)
    origin_xfer = effective_transfer(origin_result)
    _log(method_log,
         f"{'Etherscan/Blockscout' if origin_chain.upper()=='ETH' else 'TronScan' if origin_chain.upper()=='TRX' else 'blockchain.info'} API",
         f"獨立重新查詢來源鏈交易 {origin_tx_hash}",
         f"狀態：{origin_result.get('狀態','N/A')}，實際收款方（已排除代幣合約位址）："
         f"{origin_xfer['to'] or 'N/A'}{origin_xfer['note']}")

    origin_to = origin_xfer["to"]
    origin_time_str = origin_result.get("時間", "")

    # 地址比對：來源交易的「實際」收款方（token_transfers 明細，非合約呼叫層級的
    # 接收方欄位）本身是否也命中資金池／標籤判定（不可能與 dest_pool_addr 字串
    # 相等，因為那是不同鏈上的不同位址）
    pool_check = detect_pool_candidate(
        origin_chain, origin_xfer["from"], origin_to, trx_api_key)
    origin_to_is_pool = pool_check["接收方"]["confidence"] != "NONE"
    address_match = {
        "pass": origin_to_is_pool,
        "detail": (
            f"來源交易收款方 {origin_to} "
            + (f"命中資金池判定（{pool_check['接收方']['confidence']}"
               + (f"，橋名稱：{pool_check['接收方']['matched_bridge']}）"
                  if pool_check['接收方']['matched_bridge'] else "）")
               if origin_to_is_pool else "未命中任何資金池／標籤判定，請人工確認此地址是否確為目的鏈資金池的對應入金位址")
        ),
    }
    for step in pool_check["method_log"]:
        method_log.append(step)

    # 時序比對
    dt_origin = _parse_time_str(origin_time_str)
    dt_dest = _parse_time_str(dest_time_str)
    if dt_origin and dt_dest:
        delta_min = (dt_dest - dt_origin).total_seconds() / 60
        time_order = {
            "pass": delta_min >= 0,
            "detail": (f"來源交易早於目的鏈交易 {delta_min:.1f} 分鐘"
                       if delta_min >= 0 else
                       f"⚠ 來源交易晚於目的鏈交易 {-delta_min:.1f} 分鐘，時序不合理"),
        }
    else:
        time_order = {"pass": None, "detail": "時間格式無法解析，請人工核對"}

    # 金額比對（僅陳述落差，不判定通過/失敗，因手續費比例因橋而異）
    # 一律用 effective_transfer() 取出的「實際轉帳金額」比對，代幣轉帳才不會
    # 誤用交易層級的原生幣金額（該欄位對代幣轉帳而言恆為 0）。
    def _extract_num(s: str) -> float | None:
        try:
            return float(str(s).split()[0].replace(",", ""))
        except Exception:
            return None
    origin_amt = _extract_num(origin_xfer["amount_str"])
    dest_amt = _extract_num(dest_amount_str)
    if origin_amt and dest_amt and origin_amt > 0:
        diff_pct = (origin_amt - dest_amt) / origin_amt * 100
        amount_note = {
            "detail": (f"來源金額 {origin_amt:,.6f} {origin_xfer['asset']} vs "
                       f"目的金額 {dest_amt:,.6f}，落差 {diff_pct:.2f}%"
                       f"（請人工確認是否為合理橋接手續費／滑點）"),
        }
    else:
        amount_note = {"detail": "金額無法解析比對，請人工核對"}

    _log(method_log, "crosschain_tracer.verify_origin_claim", "驗證結束",
         f"address_match={address_match['pass']}, time_order={time_order['pass']}")

    return {
        "origin_result": origin_result,
        "checks": {
            "address_match": address_match,
            "time_order": time_order,
            "amount_note": amount_note,
        },
        "verdict": (
            "地址與時序均支持此為同一筆跨鏈資金流的兩段交易，"
            + amount_note["detail"]
            if address_match["pass"] and time_order.get("pass")
            else "部分檢查未通過或無法自動核對，請調查員逐項人工複核下列細節後再下結論"
        ),
        "method_log": method_log,
    }
