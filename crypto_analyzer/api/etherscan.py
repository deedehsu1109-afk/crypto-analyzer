import requests
import time

_V2_URL        = "https://api.etherscan.io/v2/chainquery"
_V1_URL        = "https://api.etherscan.io/api"
_BLOCKSCOUT_URL = "https://eth.blockscout.com/api"   # 免費備用，無需 Key
_NO_DATA = {"No transactions found", "No records found", ""}


class EtherscanAPI:
    def __init__(self, api_key: str):
        self.api_key  = api_key
        self.session  = requests.Session()
        self.session.headers.update({"User-Agent": "CryptoAnalyzer/1.0"})
        self._base_url, self._use_v2, self._source = self._detect_endpoint()

    # ── 自動偵測可用端點 ───────────────────────────────────────────────────────

    def _probe(self, url: str, extra: dict = None) -> bool:
        params = {
            "module": "account", "action": "balance",
            "address": "0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe",
            "tag": "latest", "apikey": self.api_key,
        }
        if extra:
            params.update(extra)
        try:
            r = self.session.get(url, params=params, timeout=10)
            data = r.json()
            msg = data.get("message", "")
            return msg not in ("NOTOK", "Exception") and data.get("status") == "1"
        except Exception:
            return False

    def _detect_endpoint(self) -> tuple[str, bool, str]:
        if self.api_key:
            if self._probe(_V2_URL, {"chainid": 1}):
                return _V2_URL, True, "Etherscan V2"
            if self._probe(_V1_URL):
                return _V1_URL, False, "Etherscan V1"
        # 任何情況下 Blockscout 都能用（不需 Key）
        return _BLOCKSCOUT_URL, False, "Blockscout（備用）"

    # ── 共用請求 ──────────────────────────────────────────────────────────────

    def _get(self, params: dict) -> dict:
        if self._source != "Blockscout（備用）":
            params["apikey"] = self.api_key
        if self._use_v2:
            params["chainid"] = 1

        for attempt in range(3):
            try:
                resp = self.session.get(self._base_url, params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                status  = data.get("status", "1")
                message = data.get("message", "")
                result  = data.get("result", [])

                if status == "0":
                    if message in ("NOTOK", "Exception"):
                        raise ValueError(
                            f"API 錯誤（{self._source}）：{result or message}\n"
                            "請至 etherscan.io/myapikey 重新建立 API Key"
                        )
                    if isinstance(result, str) and result not in _NO_DATA:
                        raise ValueError(f"{message}: {result}")
                return data
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2)
        return {}

    # ── 公開方法 ─────────────────────────────────────────────────────────────

    def get_balance(self, address: str) -> float:
        data = self._get({"module": "account", "action": "balance",
                          "address": address, "tag": "latest"})
        try:
            return int(data.get("result", 0)) / 1e18
        except (ValueError, TypeError):
            return 0.0

    def get_normal_transactions(self, address: str) -> list[dict]:
        # Etherscan Free tier 2026-07-01 起每次最多回傳 1000 筆
        results, page = [], 1
        while True:
            data = self._get({
                "module": "account", "action": "txlist",
                "address": address, "startblock": 0,
                "endblock": 99999999, "page": page,
                "offset": 1000, "sort": "asc",
            })
            txs = data.get("result", [])
            if not isinstance(txs, list) or not txs:
                break
            results.extend(txs)
            if len(txs) < 1000:
                break
            page += 1
            time.sleep(0.3)
        return results

    def get_internal_transactions(self, address: str) -> list[dict]:
        # Etherscan Free tier 2026-07-01 起每次最多回傳 1000 筆，需分頁
        results, page = [], 1
        while True:
            time.sleep(0.3)
            data = self._get({
                "module": "account", "action": "txlistinternal",
                "address": address, "startblock": 0,
                "endblock": 99999999, "page": page,
                "offset": 1000, "sort": "asc",
            })
            txs = data.get("result", [])
            if not isinstance(txs, list) or not txs:
                break
            results.extend(txs)
            if len(txs) < 1000:
                break
            page += 1
            time.sleep(0.3)
        return results

    def get_erc20_transfers(self, address: str) -> list[dict]:
        # Etherscan Free tier 2026-07-01 起每次最多回傳 1000 筆
        results, page = [], 1
        while True:
            time.sleep(0.3)
            data = self._get({
                "module": "account", "action": "tokentx",
                "address": address, "startblock": 0,
                "endblock": 99999999, "page": page,
                "offset": 1000, "sort": "asc",
            })
            txs = data.get("result", [])
            if not isinstance(txs, list) or not txs:
                break
            results.extend(txs)
            if len(txs) < 1000:
                break
            page += 1
            time.sleep(0.3)
        return results

    def get_token_approvals(self, existing_txs: list[dict], address: str) -> list[dict]:
        addr = address.lower()
        return [
            tx for tx in existing_txs
            if tx.get("input", "").startswith("0x095ea7b3")
            and tx.get("from", "").lower() == addr
        ]

    def get_transaction(self, tx_hash: str) -> dict:
        """抓取單筆交易詳情；依來源自動選擇端點"""
        if self._source == "Blockscout（備用）":
            return self._get_tx_blockscout(tx_hash)
        return self._get_tx_etherscan(tx_hash)

    def _get_tx_etherscan(self, tx_hash: str) -> dict:
        tx_data = self._get({
            "module": "proxy", "action": "eth_getTransactionByHash",
            "txhash": tx_hash,
        })
        tx = tx_data.get("result") or {}
        time.sleep(0.3)
        receipt_data = self._get({
            "module": "proxy", "action": "eth_getTransactionReceipt",
            "txhash": tx_hash,
        })
        receipt = receipt_data.get("result") or {}
        time.sleep(0.3)
        token_data = self._get({
            "module": "account", "action": "tokentx",
            "txhash": tx_hash, "page": 1, "offset": 100,
        })
        token_transfers = token_data.get("result", [])
        if not isinstance(token_transfers, list):
            token_transfers = []
        return {"source": "etherscan", "tx": tx, "receipt": receipt,
                "token_transfers": token_transfers}

    def _get_tx_blockscout(self, tx_hash: str) -> dict:
        """使用 Blockscout v2 REST API"""
        base = "https://eth.blockscout.com/api/v2/transactions"
        try:
            resp = self.session.get(f"{base}/{tx_hash}", timeout=20)
            resp.raise_for_status()
            tx = resp.json()
        except Exception as e:
            raise ValueError(f"Blockscout 查詢失敗：{e}")

        # Token 轉帳
        try:
            time.sleep(0.3)
            tr = self.session.get(f"{base}/{tx_hash}/token-transfers",
                                  params={"type": "ERC-20"}, timeout=20)
            token_transfers = tr.json().get("items", []) if tr.ok else []
        except Exception:
            token_transfers = []

        return {"source": "blockscout", "tx": tx,
                "token_transfers": token_transfers}
