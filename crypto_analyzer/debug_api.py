import requests, json

API_KEY = input("請輸入 Etherscan API Key: ").strip()
ADDRESS = input("請輸入錢包地址: ").strip()

def test(base_url, label, extra_params=None):
    params = {
        "module": "account",
        "action": "tokentx",
        "address": ADDRESS,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": 3,
        "sort": "asc",
        "apikey": API_KEY,
    }
    if extra_params:
        params.update(extra_params)
    r = requests.get(base_url, params=params, timeout=15)
    data = r.json()
    print(f"\n=== {label} ===")
    print(f"HTTP: {r.status_code}")
    print(f"status:  {data.get('status')}")
    print(f"message: {data.get('message')}")
    result = data.get("result", [])
    if isinstance(result, list):
        print(f"result:  [{len(result)} 筆]")
        if result:
            print(json.dumps(result[0], indent=2, ensure_ascii=False)[:400])
    else:
        print(f"result:  {repr(result)}")

# V1
test("https://api.etherscan.io/api", "V1 API (舊版)")

# V2
test("https://api.etherscan.io/v2/chainquery", "V2 API (新版)", {"chainid": 1})
