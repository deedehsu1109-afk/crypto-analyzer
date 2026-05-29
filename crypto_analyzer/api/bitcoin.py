import requests
import time

BASE_URL = "https://blockchain.info"


class BitcoinAPI:
    def __init__(self):
        self.session = requests.Session()

    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params or {}, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(1.5)
        return {}

    def get_address_info(self, address: str) -> dict:
        return self._get(f"rawaddr/{address}", {"limit": 50})

    def get_balance(self, address: str) -> float:
        data = self._get("balance", {"active": address})
        satoshi = data.get(address, {}).get("final_balance", 0)
        return satoshi / 1e8

    def get_transactions(self, address: str, limit: int = 5000) -> list[dict]:
        results = []
        offset = 0
        page_size = 50
        while len(results) < limit:
            data = self._get(f"rawaddr/{address}", {
                "limit": page_size,
                "offset": offset,
            })
            txs = data.get("txs", [])
            if not txs:
                break
            results.extend(txs)
            if len(txs) < page_size:
                break
            offset += page_size
            time.sleep(0.5)
        return results[:limit]

    def get_transaction(self, tx_hash: str) -> dict:
        """抓取單筆 BTC 交易詳情"""
        return self._get(f"rawtx/{tx_hash}")
