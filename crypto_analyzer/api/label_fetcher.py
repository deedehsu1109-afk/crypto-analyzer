"""
地址標籤查詢（免費來源）

ETH  → Blockscout v2 API（name / ENS / 合約名稱）
TRX  → TronScan account API（addressTag）
BTC  → WalletExplorer API（交易所歸因）
"""

import requests
import time

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "CryptoAnalyzer/1.0"})


def get_eth_label(address: str) -> dict:
    """
    回傳格式：
    {
        "name":        "Binance: Hot Wallet",   # None 表示無標籤
        "ens":         "vitalik.eth",           # None 表示無 ENS
        "is_contract": True,
        "source":      "Blockscout"
    }
    """
    try:
        r = _SESSION.get(
            f"https://eth.blockscout.com/api/v2/addresses/{address}",
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        name = (
            data.get("name")
            or data.get("implementation_name")
            or None
        )
        ens = data.get("ens_domain_name") or None

        return {
            "name":        name,
            "ens":         ens,
            "is_contract": data.get("is_contract", False),
            "source":      "Blockscout",
        }
    except Exception:
        return {"name": None, "ens": None, "is_contract": False, "source": "Blockscout"}


def get_trx_label(address: str, api_key: str = "") -> dict:
    """
    回傳格式：
    {
        "name":   "Binance",   # None 表示無標籤
        "risk":   "low",       # TronScan 風險等級（部分地址有）
        "source": "TronScan"
    }
    """
    headers = {"User-Agent": "CryptoAnalyzer/1.0"}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    try:
        r = requests.get(
            "https://apilist.tronscanapi.com/api/account",
            params={"address": address},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        name = data.get("name") or None
        risk = data.get("riskLevel") or None

        return {"name": name, "risk": risk, "source": "TronScan"}
    except Exception:
        return {"name": None, "risk": None, "source": "TronScan"}


def get_btc_label(address: str) -> dict:
    """
    回傳格式：
    {
        "name":   "Binance.com",  # None 表示無標籤
        "type":   "exchange",
        "source": "WalletExplorer"
    }
    """
    try:
        r = _SESSION.get(
            "https://www.walletexplorer.com/api/1/address",
            params={"address": address, "from": 0, "count": 1, "caller": "CryptoAnalyzer"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        wallet = data.get("wallet") or {}

        name = data.get("label") or None  # e.g. "Binance.com"
        wtype = None

        return {"name": name, "type": wtype, "source": "WalletExplorer"}
    except Exception:
        return {"name": None, "type": None, "source": "WalletExplorer"}


def get_label(address: str, chain: str, trx_api_key: str = "") -> str | None:
    """
    統一入口：根據鏈別自動選擇來源，回傳最具代表性的標籤字串。
    無標籤時回傳 None。
    """
    chain = chain.upper()
    if chain in ("ETH", "ERC20", "BSC", "MATIC", "ARB", "OP"):
        info = get_eth_label(address)
        return info.get("ens") or info.get("name")
    elif chain in ("TRX", "TRC20"):
        info = get_trx_label(address, trx_api_key)
        return info.get("name")
    elif chain == "BTC":
        info = get_btc_label(address)
        return info.get("name")
    return None


def batch_get_labels(addresses: list[str], chain: str,
                     trx_api_key: str = "",
                     delay: float = 0.3) -> dict[str, str | None]:
    """
    批次查詢多個地址標籤，回傳 {address: label} 字典。
    delay: 每次請求間隔秒數，避免觸發速率限制。
    """
    result = {}
    for addr in addresses:
        result[addr] = get_label(addr, chain, trx_api_key)
        if delay:
            time.sleep(delay)
    return result
