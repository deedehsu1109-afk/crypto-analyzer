from __future__ import annotations
import requests
import time
import datetime

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "CryptoAnalyzer/1.0"})

# 幣種名稱對應 CoinGecko ID
CURRENCY_MAP: dict[str, str] = {
    "BTC":   "bitcoin",
    "ETH":   "ethereum",
    "USDT":  "tether",
    "USDC":  "usd-coin",
    "BNB":   "binancecoin",
    "TRX":   "tron",
    "SOL":   "solana",
    "XRP":   "ripple",
    "ADA":   "cardano",
    "DOGE":  "dogecoin",
    "MATIC": "matic-network",
    "DOT":   "polkadot",
    "LINK":  "chainlink",
    "LTC":   "litecoin",
    "BCH":   "bitcoin-cash",
    "BUSD":  "binance-usd",
    "DAI":   "dai",
    "SHIB":  "shiba-inu",
    "UNI":   "uniswap",
    "AVAX":  "avalanche-2",
}

_UTC8 = datetime.timezone(datetime.timedelta(hours=8))


def _coingecko_get(url: str, params: dict = None) -> dict:
    for attempt in range(3):
        try:
            r = _SESSION.get(url, params=params or {}, timeout=15)
            if r.status_code == 429:
                time.sleep(60)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2)
    return {}


def get_coin_id(symbol: str) -> str | None:
    """將幣種符號轉為 CoinGecko coin ID，不在預設表中則查詢 API"""
    sym = symbol.upper().strip()
    if sym in CURRENCY_MAP:
        return CURRENCY_MAP[sym]
    # 嘗試搜尋
    try:
        data = _coingecko_get(
            "https://api.coingecko.com/api/v3/search",
            {"query": sym}
        )
        coins = data.get("coins", [])
        for c in coins:
            if c.get("symbol", "").upper() == sym:
                return c["id"]
    except Exception:
        pass
    return None


def get_daily_twd_price(coin_id: str, date_str: str) -> dict:
    """
    取得指定日期的台幣 (TWD) 高低均價。
    date_str 格式：YYYY-MM-DD
    回傳 {"high": float, "low": float, "avg": float, "close": float}
    """
    try:
        # 將日期轉為 Unix 時間戳（UTC+8 當天 00:00 ~ 23:59）
        dt_start = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
            tzinfo=_UTC8)
        ts_from  = int(dt_start.timestamp())
        ts_to    = ts_from + 86400 - 1

        # CoinGecko market_chart/range：以 5 分鐘精度回傳 OHLC
        data = _coingecko_get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range",
            {
                "vs_currency": "twd",
                "from": ts_from,
                "to":   ts_to,
            }
        )
        prices = data.get("prices", [])  # [[ts_ms, price], ...]
        if not prices:
            return {"high": None, "low": None, "avg": None, "close": None}

        price_vals = [p[1] for p in prices]
        high  = max(price_vals)
        low   = min(price_vals)
        close = price_vals[-1]
        avg   = round((high + low) / 2, 4)

        return {
            "high":  round(high,  4),
            "low":   round(low,   4),
            "avg":   avg,
            "close": round(close, 4),
        }

    except Exception as e:
        # fallback：使用 history endpoint 取單一收盤價
        try:
            dd_mm_yyyy = datetime.datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y")
            hist = _coingecko_get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}/history",
                {"date": dd_mm_yyyy, "localization": "false"}
            )
            price = (hist.get("market_data", {})
                     .get("current_price", {})
                     .get("twd"))
            if price:
                return {"high": price, "low": price, "avg": price, "close": price}
        except Exception:
            pass
        return {"high": None, "low": None, "avg": None, "close": None}


def fetch_exchange_rate(currency: str, date_str: str,
                        quantity: float = None,
                        amount_ntd: float = None) -> dict:
    """
    查詢指定幣種在指定日期的台幣價格。
    若提供 quantity 與 amount_ntd 可計算實際交易匯率。

    回傳：
    {
        "daily_high": float,   # 當日最高價 (NT)
        "daily_low":  float,   # 當日最低價 (NT)
        "daily_avg":  float,   # 當日均價 = (high+low)/2
        "exchange_rate": float # 實際交易匯率（amount_ntd/quantity）或使用均價
    }
    """
    coin_id = get_coin_id(currency)
    if not coin_id:
        return {
            "error": f"無法識別幣種：{currency}",
            "daily_high": None, "daily_low": None,
            "daily_avg": None, "exchange_rate": None,
        }

    prices = get_daily_twd_price(coin_id, date_str)

    exchange_rate = None
    if quantity and amount_ntd and quantity > 0:
        exchange_rate = round(amount_ntd / quantity, 4)
    elif prices.get("avg"):
        exchange_rate = prices["avg"]

    return {
        "daily_high":    prices.get("high"),
        "daily_low":     prices.get("low"),
        "daily_avg":     prices.get("avg"),
        "exchange_rate": exchange_rate,
    }
