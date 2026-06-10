import requests
import time

BASE_URL = "https://apilist.tronscanapi.com/api"


class TronScanAPI:
    def __init__(self, api_key: str = ""):
        self.session = requests.Session()
        headers = {"User-Agent": "CryptoAnalyzer/1.0"}
        if api_key:
            headers["TRON-PRO-API-KEY"] = api_key
        self.session.headers.update(headers)

    def _get(self, endpoint: str, params: dict) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(1.5)
        return {}

    def get_account(self, address: str) -> dict:
        return self._get("account", {"address": address})

    def get_balance(self, address: str) -> float:
        data = self.get_account(address)
        balance_sun = data.get("balance", 0)
        return balance_sun / 1_000_000

    def get_transactions(self, address: str, limit: int = 10000,
                         start_ts: int = None, end_ts: int = None) -> list[dict]:
        results = []
        start = 0
        page_size = 50
        # 移除 sort=-timestamp（已不被接受），端點預設即為時間降序
        params_base: dict = {
            "address": address,
            "limit": page_size,
        }
        if start_ts:
            params_base["min_timestamp"] = start_ts * 1000
        if end_ts:
            params_base["max_timestamp"] = end_ts * 1000

        while len(results) < limit:
            data = self._get("transaction", {**params_base, "start": start})
            txs = data.get("data", [])
            if not txs:
                break
            results.extend(txs)
            if len(txs) < page_size:
                break
            start += page_size
            time.sleep(0.3)
        return results[:limit]

    def get_trc20_transfers(self, address: str, limit: int = 10000,
                            start_ts: int = None, end_ts: int = None) -> list[dict]:
        results = []
        start = 0
        page_size = 50
        # 移除 sort=-block_ts（已不被接受），端點預設即為時間降序
        params_base: dict = {
            "relatedAddress": address,
            "limit": page_size,
        }
        if start_ts:
            params_base["start_timestamp"] = start_ts * 1000
        if end_ts:
            params_base["end_timestamp"] = end_ts * 1000

        while len(results) < limit:
            data = self._get("token_trc20/transfers", {**params_base, "start": start})
            txs = data.get("token_transfers", [])
            if not txs:
                break
            results.extend(txs)
            if len(txs) < page_size:
                break
            start += page_size
            time.sleep(0.3)
        return results[:limit]

    def get_token_approvals(self, existing_txs: list[dict], address: str) -> list[dict]:
        """從已抓取的交易中篩選 TRC-20 Approval（approve function selector = 095ea7b3）"""
        approvals = []
        for tx in existing_txs:
            # TronScan 交易格式：contractData 內有 data 欄位
            input_data = (
                tx.get("contractData", {}).get("data", "") or
                tx.get("data", "") or ""
            )
            owner = (
                tx.get("ownerAddress", "") or
                tx.get("contractData", {}).get("owner_address", "")
            )
            if input_data.startswith("095ea7b3") and owner == address:
                spender = "0x" + input_data[32:72] if len(input_data) >= 72 else "unknown"
                approvals.append({
                    "contract": tx.get("toAddress", tx.get("contractAddress", "")),
                    "spender": spender,
                    "amount": tx.get("contractData", {}).get("amount", ""),
                    "tx_hash": tx.get("hash", tx.get("txID", "")),
                    "time": tx.get("timestamp", ""),
                })
        return approvals

    def get_transaction(self, tx_hash: str) -> dict:
        """抓取單筆 TRX 交易詳情"""
        tx = self._get("transaction-info", {"hash": tx_hash})
        # TRC-20 轉帳
        token_data = self._get("token_trc20/transfers", {
            "txHash": tx_hash, "limit": 50,
        })
        token_transfers = token_data.get("token_transfers", [])
        return {"tx": tx, "token_transfers": token_transfers}
