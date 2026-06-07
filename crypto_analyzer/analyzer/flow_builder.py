from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from typing import Callable

import networkx as nx

# ── 已知地址登錄表 ────────────────────────────────────────────────────────────

_KNOWN_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "known_addresses.json")
_known_db: dict = {}


def _load_known():
    global _known_db
    if _known_db:
        return
    try:
        with open(_KNOWN_PATH, encoding="utf-8") as f:
            _known_db = json.load(f)
    except Exception:
        _known_db = {}


def lookup_address(address: str, chain: str) -> dict | None:
    """
    回傳已知地址資訊 dict，含 label / color / risk / category；
    若不在登錄表則回傳 None。
    chain 接受 'ETH'/'TRX'/'BTC'。
    """
    _load_known()
    chain_key = {"ETH": "eth", "TRX": "trx", "BTC": "btc"}.get(chain, "eth")
    addr_lower = address.lower()
    for category, entries in _known_db.items():
        if category.startswith("_"):
            continue
        for name, info in entries.items():
            addrs = [a.lower() for a in info.get(chain_key, [])]
            if addr_lower in addrs:
                return {
                    "label":    info.get("label", name),
                    "color":    info.get("color", "#1E90FF"),
                    "risk":     info.get("risk", "low"),
                    "category": category,
                    "name":     name,
                }
    return None


# ── 節點角色定義 ──────────────────────────────────────────────────────────────

# role → (顏色, 中文說明)
ROLE_STYLES: dict[str, tuple[str, str]] = {
    "seed":     ("#FF4444", "起始/種子地址"),
    "suspect":  ("#FF6B00", "嫌疑人"),
    "victim":   ("#44BB44", "被害人"),
    "exchange": ("#1E90FF", "已知交易所"),
    "mixer":    ("#9B59B6", "混幣器"),
    "bridge":   ("#E67E22", "跨鏈橋"),
    "unknown":  ("#AAAAAA", "未知地址"),
}


# ── GraphState：幣流圖的核心狀態物件 ─────────────────────────────────────────

@dataclass
class NodeInfo:
    address: str
    chain:   str
    role:    str = "unknown"         # 見 ROLE_STYLES
    custom_label: str = ""           # 調查員手動標記
    known_label:  str = ""           # 來自登錄表的標籤
    color:   str = "#AAAAAA"
    expanded: bool = False           # 是否已展開（查詢過它的交易）
    in_db:    bool = False           # 是否已存入 DB

    @property
    def display_label(self) -> str:
        if self.custom_label:
            return self.custom_label
        if self.known_label:
            return self.known_label
        return self.address[:8] + "…" + self.address[-6:]


@dataclass
class EdgeInfo:
    source:       str
    target:       str
    tx_hash:      str = ""
    value_native: float = 0.0
    token_symbol: str = ""
    token_amount: float = 0.0
    tx_time:      str = ""
    tx_type:      str = "normal"

    @property
    def amount_display(self) -> str:
        if self.tx_type in ("erc20", "trc20") and self.token_symbol:
            return f"{self.token_amount:,.4f} {self.token_symbol}"
        return f"{self.value_native:,.6f}"


@dataclass
class GraphState:
    """
    幣流圖的狀態容器。
    - nodes: address → NodeInfo
    - edges: list[EdgeInfo]
    - G: networkx DiGraph（由 rebuild_graph() 同步）
    """
    chain:   str = "ETH"
    mode:    str = "explore"         # "explore" | "evidence"
    nodes:   dict[str, NodeInfo] = field(default_factory=dict)
    edges:   list[EdgeInfo]       = field(default_factory=list)
    G:       nx.DiGraph           = field(default_factory=nx.DiGraph)
    on_node_expand: Callable | None = field(default=None, repr=False)

    # ── 節點操作 ──────────────────────────────────────────────────────────

    def add_node(self, address: str, role: str = "unknown",
                 custom_label: str = "") -> NodeInfo:
        if address in self.nodes:
            n = self.nodes[address]
            if role != "unknown":
                n.role = role
                n.color = ROLE_STYLES.get(role, ("", ""))[0] or n.color
            if custom_label:
                n.custom_label = custom_label
            return n

        known = lookup_address(address, self.chain)
        if known:
            role  = known["category"].rstrip("s")   # exchanges→exchange 等
            color = known["color"]
            klabel = known["label"]
        else:
            color  = ROLE_STYLES.get(role, ("#AAAAAA", ""))[0]
            klabel = ""

        info = NodeInfo(
            address=address, chain=self.chain, role=role,
            custom_label=custom_label, known_label=klabel,
            color=color,
        )
        self.nodes[address] = info
        return info

    def set_role(self, address: str, role: str, custom_label: str = ""):
        n = self.nodes.get(address)
        if n:
            n.role = role
            n.color = ROLE_STYLES.get(role, ("#AAAAAA", ""))[0]
            if custom_label:
                n.custom_label = custom_label

    # ── 邊操作 ────────────────────────────────────────────────────────────

    def add_edges_from_db_rows(self, rows: list[dict]):
        """接收 db.get_edges_for_graph() 的結果並批次加入。"""
        existing_hashes = {e.tx_hash for e in self.edges if e.tx_hash}
        for r in rows:
            frm = r.get("from_addr", "")
            to  = r.get("to_addr", "")
            if not frm or not to:
                continue
            tx_hash = r.get("tx_hash", "")
            if tx_hash and tx_hash in existing_hashes:
                continue
            self.add_node(frm)
            self.add_node(to)
            edge = EdgeInfo(
                source=frm, target=to,
                tx_hash=tx_hash,
                value_native=r.get("value_native", 0.0) or 0.0,
                token_symbol=r.get("token_symbol") or "",
                token_amount=r.get("token_amount", 0.0) or 0.0,
                tx_time=r.get("tx_time", "") or "",
                tx_type=r.get("tx_type", "normal") or "normal",
            )
            self.edges.append(edge)
            if tx_hash:
                existing_hashes.add(tx_hash)

    def add_edges_from_profile(self, profile: dict):
        """
        接收 wallet_profiler 的 profile dict（記憶體資料，探索模式用）。
        支援 ETH / TRX / BTC。
        """
        chain = profile.get("chain", self.chain)
        addr  = profile.get("address", "")
        rows  = []

        if chain == "ETH":
            for tx in profile.get("raw_txs", []):
                if tx.get("isError", "0") != "0":
                    continue
                rows.append({
                    "from_addr":    tx.get("from", ""),
                    "to_addr":      tx.get("to", ""),
                    "value_native": int(tx.get("value", 0)) / 1e18,
                    "token_symbol": "",
                    "token_amount": 0.0,
                    "tx_time":      tx.get("timeStamp", ""),
                    "tx_hash":      tx.get("hash", ""),
                    "tx_type":      "normal",
                })
            for tx in profile.get("raw_erc20", []):
                decimals = int(tx.get("tokenDecimal", 18) or 18)
                try:
                    amt = int(tx.get("value", 0)) / (10 ** decimals)
                except Exception:
                    amt = 0.0
                rows.append({
                    "from_addr":    tx.get("from", ""),
                    "to_addr":      tx.get("to", ""),
                    "value_native": 0.0,
                    "token_symbol": tx.get("tokenSymbol", ""),
                    "token_amount": amt,
                    "tx_time":      tx.get("timeStamp", ""),
                    "tx_hash":      tx.get("hash", ""),
                    "tx_type":      "erc20",
                })

        elif chain == "TRX":
            for tx in profile.get("raw_txs", []):
                rows.append({
                    "from_addr":    tx.get("ownerAddress", ""),
                    "to_addr":      tx.get("toAddress", ""),
                    "value_native": int(tx.get("contractData", {}).get("amount", 0)) / 1e6,
                    "token_symbol": "",
                    "token_amount": 0.0,
                    "tx_time":      str(tx.get("timestamp", "")),
                    "tx_hash":      tx.get("hash", tx.get("txID", "")),
                    "tx_type":      "normal",
                })
            for tx in profile.get("raw_trc20", []):
                try:
                    decimals = int(tx.get("tokenDecimal", 6) or 6)
                    amt = int(tx.get("amount", 0)) / (10 ** decimals)
                except Exception:
                    amt = 0.0
                rows.append({
                    "from_addr":    tx.get("from_address", ""),
                    "to_addr":      tx.get("to_address", ""),
                    "value_native": 0.0,
                    "token_symbol": tx.get("tokenAbbr", ""),
                    "token_amount": amt,
                    "tx_time":      str(tx.get("block_ts", "")),
                    "tx_hash":      tx.get("transactionId", ""),
                    "tx_type":      "trc20",
                })

        elif chain == "BTC":
            for tx in profile.get("raw_txs", []):
                inputs  = tx.get("inputs", [])
                outputs = tx.get("out", tx.get("outputs", []))
                in_addrs = [i.get("prev_out", {}).get("addr", i.get("addr", ""))
                            for i in inputs if i]
                for out in outputs:
                    out_addr = out.get("addr", "")
                    if not out_addr:
                        continue
                    for ia in in_addrs:
                        if ia and ia != out_addr:
                            rows.append({
                                "from_addr":    ia,
                                "to_addr":      out_addr,
                                "value_native": out.get("value", 0) / 1e8,
                                "token_symbol": "",
                                "token_amount": 0.0,
                                "tx_time":      str(tx.get("time", "")),
                                "tx_hash":      tx.get("hash", ""),
                                "tx_type":      "btc",
                            })

        if addr:
            self.nodes.setdefault(addr, self.add_node(addr))
            if addr in self.nodes:
                self.nodes[addr].expanded = True

        self.add_edges_from_db_rows(rows)

    # ── networkx 圖同步 ───────────────────────────────────────────────────

    def rebuild_graph(self):
        """將 nodes/edges 同步到 self.G（DiGraph）。"""
        self.G.clear()
        for addr, info in self.nodes.items():
            self.G.add_node(addr, **{
                "role":          info.role,
                "color":         info.color,
                "display_label": info.display_label,
                "expanded":      info.expanded,
            })
        for e in self.edges:
            key = (e.source, e.target)
            if self.G.has_edge(*key):
                # 同一對地址多筆：累加金額，保留最新 tx_time
                d = self.G.edges[key]
                d["weight"] = d.get("weight", 0) + (
                    e.token_amount if e.token_amount else e.value_native)
                d["tx_count"] = d.get("tx_count", 1) + 1
            else:
                self.G.add_edge(e.source, e.target,
                    weight=e.token_amount if e.token_amount else e.value_native,
                    tx_hash=e.tx_hash,
                    tx_time=e.tx_time,
                    tx_type=e.tx_type,
                    token_symbol=e.token_symbol,
                    tx_count=1,
                )

    # ── 圖分析輔助 ────────────────────────────────────────────────────────

    def get_aggregated_edges(self) -> list[dict]:
        """
        回傳地址關係圖用的聚合邊（同一對地址的所有交易合併）。
        """
        agg: dict[tuple, dict] = {}
        for e in self.edges:
            key = (e.source, e.target)
            if key not in agg:
                agg[key] = {
                    "source": e.source, "target": e.target,
                    "total_native": 0.0, "tx_count": 0,
                    "token_summary": {}, "earliest": e.tx_time, "latest": e.tx_time,
                }
            d = agg[key]
            d["tx_count"] += 1
            d["total_native"] += e.value_native
            if e.token_symbol:
                d["token_summary"][e.token_symbol] = (
                    d["token_summary"].get(e.token_symbol, 0) + e.token_amount)
            if e.tx_time and (not d["earliest"] or e.tx_time < d["earliest"]):
                d["earliest"] = e.tx_time
            if e.tx_time and e.tx_time > d["latest"]:
                d["latest"] = e.tx_time
        return list(agg.values())

    def find_paths(self, source: str, target: str) -> list[list[str]]:
        """找出 source → target 的所有簡單路徑（上限 50 條）。"""
        self.rebuild_graph()
        try:
            return list(nx.all_simple_paths(self.G, source, target, cutoff=6))[:50]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    # ── 序列化（供快照存入 DB）────────────────────────────────────────────

    def to_snapshot(self) -> tuple[list, list]:
        nodes = [
            {"address": n.address, "chain": n.chain, "role": n.role,
             "custom_label": n.custom_label, "known_label": n.known_label,
             "color": n.color, "expanded": n.expanded}
            for n in self.nodes.values()
        ]
        edges = [
            {"source": e.source, "target": e.target, "tx_hash": e.tx_hash,
             "value_native": e.value_native, "token_symbol": e.token_symbol,
             "token_amount": e.token_amount, "tx_time": e.tx_time,
             "tx_type": e.tx_type}
            for e in self.edges
        ]
        return nodes, edges

    @classmethod
    def from_snapshot(cls, nodes: list, edges: list,
                      chain: str = "ETH", mode: str = "evidence") -> "GraphState":
        gs = cls(chain=chain, mode=mode)
        for n in nodes:
            info = NodeInfo(**{k: v for k, v in n.items()
                               if k in NodeInfo.__dataclass_fields__})
            gs.nodes[info.address] = info
        for e in edges:
            gs.edges.append(EdgeInfo(**{k: v for k, v in e.items()
                                        if k in EdgeInfo.__dataclass_fields__}))
        gs.rebuild_graph()
        return gs

    # ── 統計摘要 ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        roles = {}
        for n in self.nodes.values():
            roles[n.role] = roles.get(n.role, 0) + 1
        return {
            "node_count":  len(self.nodes),
            "edge_count":  len(self.edges),
            "roles":       roles,
            "chains":      [self.chain],
            "mode":        self.mode,
        }
