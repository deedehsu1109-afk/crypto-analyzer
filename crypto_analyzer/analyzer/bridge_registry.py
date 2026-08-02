"""
跨鏈橋接協議靜態登錄資料

不發送任何網路請求，僅供 crosschain_tracer 比對標籤與提供調查員
人工查證時可用的橋接瀏覽器清單。
"""

# ── 跨鏈瀏覽器清單 ────────────────────────────────────────────────────────────
# 僅提供首頁網址：多數橋接瀏覽器（如 Bitget Bridge）是網頁內搜尋框輸入，
# 並非 URL query string，實測對外部自動化請求有反爬蟲保護，故不做深層連結，
# 交由調查員人工貼上 Hash 查詢。
#
# 注意：ChainID (chainid.network) 是「Chain ID 註冊表」，用於查各鏈的 Chain ID
# 編號，不是跨鏈交易查詢瀏覽器，故不收錄於此清單。
BRIDGE_EXPLORERS: dict[str, str] = {
    "AllChain Bridge/SWFT": "https://explorer.allchainbridge.com",
    "Bitget Bridge":        "https://web3.bitget.com/explorer",
    "Bridgers XYZ":         "https://explorer.bridgers.xyz",
    "OmniBridge":           "https://explorer.omnibridge.pro",
    "Transit":              "https://explorer.transit.finance",
    "THORChain":            "https://thorchain.net/dashboard",
    "LayerZeroScan":        "https://layerzeroscan.com",
    "HiFiSwap Browser":     "https://hifiswap.io/search",
    "ButterSwap":           "https://explorer.butterswap.io",
    "Relay Link":           "https://relay.link",
}

# ── 地址標籤關鍵字比對表 ──────────────────────────────────────────────────────
# key：關鍵字（小寫，比對時對 label 字串做 substring 搜尋）
# value：顯示用橋名稱
# 順序即比對順序，較具體的關鍵字排在前面；最後保留通用 "bridge" 關鍵字
# 作為低信心 fallback（許多 DEX 標籤也含 swap，故不收錄 "swap" 作獨立訊號）。
BRIDGE_LABEL_KEYWORDS: dict[str, str] = {
    "bitget":           "Bitget Wallet: Swap Bridge",
    "layerzero":        "LayerZero",
    "stargate":         "Stargate (LayerZero)",
    "multichain":       "Multichain (Anyswap)",
    "anyswap":          "Multichain (Anyswap)",
    "wormhole":         "Wormhole",
    "debridge":         "deBridge",
    "synapse":          "Synapse Protocol",
    "thorchain":        "THORChain",
    "cbridge":          "cBridge (Celer)",
    "celer":            "cBridge (Celer)",
    "allbridge":        "Allbridge",
    "swft":             "SWFT Bridge",
    "butterswap":       "ButterSwap",
    "hifiswap":         "HiFiSwap",
    "transit finance":  "Transit Finance",
    "omnibridge":       "OmniBridge",
    "bridgers":         "Bridgers.xyz",
    "relay link":       "Relay Link",
    "router protocol":  "Router Protocol",
    "bridge":           "未知橋接協議（標籤含 bridge 關鍵字，需人工確認）",
}


def match_bridge_label(label: str | None) -> str | None:
    """比對地址標籤字串，回傳命中的橋名稱；無命中或 label 為空回傳 None。"""
    if not label:
        return None
    lowered = label.lower()
    for keyword, bridge_name in BRIDGE_LABEL_KEYWORDS.items():
        if keyword in lowered:
            return bridge_name
    return None
