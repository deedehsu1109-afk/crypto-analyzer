from __future__ import annotations
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
from typing import Callable

import customtkinter as ctk
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import networkx as nx

from analyzer.flow_builder import GraphState, ROLE_STYLES, NodeInfo, EdgeInfo

# ── 顏色常數（深色主題配色）────────────────────────────────────────────────────

BG_DARK   = "#1a1a2e"
BG_PANEL  = "#16213e"
BG_NODE   = "#0f3460"
TEXT_COL  = "#e0e0e0"
ACCENT    = "#4fc3f7"
WARN_COL  = "#f5a623"

# ── 視圖模式 ──────────────────────────────────────────────────────────────────

VIEW_NETWORK  = "地址關係圖"
VIEW_FLOW     = "幣流流水圖"
VIEW_TIMELINE = "時序泳道圖"
VIEW_MALTEGO  = "司法金流圖"


class FlowGraphPanel(ctk.CTkFrame):
    """
    幣流關聯圖面板，嵌入主視窗的分頁中。

    外部呼叫方式：
        panel.load_from_profile(profile_dict)       # 探索模式：從目前查詢結果載入
        panel.load_from_case(case_id, chain)         # 案件模式：從 DB 載入
        panel.set_node_click_callback(fn)            # 節點被點擊時回呼主視窗

    主視窗回呼：
        fn(address: str, chain: str)  →  填入地址欄並觸發查詢
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color=BG_DARK)

        self._state: GraphState | None = None
        self._view_mode = ctk.StringVar(value=VIEW_NETWORK)
        self._gen_mode  = ctk.StringVar(value="explore")   # explore | evidence
        self._node_click_cb: Callable | None = None

        # matplotlib 物件
        self._fig: plt.Figure | None = None
        self._ax:  plt.Axes   | None = None
        self._mpl_canvas: FigureCanvasTkAgg | None = None
        self._pos: dict = {}        # networkx 佈局快取
        self._selected_node: str | None = None

        self._build_ui()

    # ── 公開 API ──────────────────────────────────────────────────────────────

    def set_node_click_callback(self, fn: Callable):
        """fn(address, chain) 會在使用者點擊節點時被呼叫。"""
        self._node_click_cb = fn

    def load_from_profile(self, profile: dict):
        """探索模式：從記憶體中的 profile dict 載入（不寫 DB）。"""
        chain = profile.get("chain", "ETH")
        self._state = GraphState(chain=chain, mode="explore")
        addr = profile.get("address", "")
        if addr:
            node = self._state.add_node(addr, role="seed")
            node.expanded = True
        self._state.add_edges_from_profile(profile)
        self._gen_mode.set("explore")
        self._update_mode_label()
        self._render()

    def load_from_case(self, case_id: int, chain: str = "ETH"):
        """案件模式：從 DB 載入整個案件的交易圖。"""
        from database import db as _db
        self._state = GraphState(chain=chain, mode="evidence")
        rows = _db.get_edges_for_graph(case_id=case_id, chain=chain)
        self._state.add_edges_from_db_rows(rows)
        # 把案件錢包標為已展開
        wallets = _db.get_case_wallets(case_id)
        for w in wallets:
            addr = w.get("address", "")
            if addr and addr in self._state.nodes:
                self._state.nodes[addr].expanded = True
                self._state.nodes[addr].custom_label = w.get("label", "")
        self._gen_mode.set("evidence")
        self._update_mode_label()
        self._render()

    def add_profile_to_graph(self, profile: dict):
        """點擊展開節點後，將新查詢結果追加至現有圖（不重置）。"""
        if self._state is None:
            self.load_from_profile(profile)
            return
        addr = profile.get("address", "")
        self._state.add_edges_from_profile(profile)
        if addr and addr in self._state.nodes:
            self._state.nodes[addr].expanded = True
        self._pos = {}   # 清除佈局快取，觸發重新計算
        self._render()

    def add_address_node(self, address: str, chain: str, label: str = ""):
        """地址模式：僅新增單一節點，不加入任何交易邊。"""
        if not address:
            return
        if self._state is None:
            self._state = GraphState(chain=chain, mode="explore")
            self._gen_mode.set("explore")
            self._update_mode_label()
        node = self._state.add_node(address)
        if label:
            node.custom_label = label
        self._pos = {}
        self._render()

    def add_hash_edge(self, result: dict):
        """Hash 模式：依交易分析結果新增發送方→接收方的交易邊。"""
        chain    = result.get("chain", "ETH")
        tx_hash  = result.get("hash", "")
        time_str = result.get("時間", "")

        if self._state is None:
            self._state = GraphState(chain=chain, mode="explore")
            self._gen_mode.set("explore")
            self._update_mode_label()

        rows: list[dict] = []

        if chain == "ETH":
            from_addr = result.get("發送方", "")
            to_addr   = result.get("接收方", "")
            try:
                value_native = float(
                    result.get("ETH 金額", "0").split()[0].replace(",", ""))
            except (ValueError, IndexError):
                value_native = 0.0
            if from_addr and to_addr and "N/A" not in (from_addr, to_addr):
                rows.append({
                    "from_addr": from_addr, "to_addr": to_addr,
                    "value_native": value_native, "token_symbol": "",
                    "token_amount": 0.0, "tx_time": time_str,
                    "tx_hash": tx_hash, "tx_type": "normal",
                })
            for t in result.get("token_transfers", []):
                sym = ""
                token_str = t.get("Token", "")
                if "(" in token_str:
                    sym = token_str.rsplit("(", 1)[-1].rstrip(")")
                try:
                    token_amt = float(t.get("金額", "0").replace(",", "") or 0)
                except ValueError:
                    token_amt = 0.0
                f, to = t.get("從", ""), t.get("至", "")
                if f and to:
                    rows.append({
                        "from_addr": f, "to_addr": to,
                        "value_native": 0.0, "token_symbol": sym,
                        "token_amount": token_amt, "tx_time": time_str,
                        "tx_hash": tx_hash, "tx_type": "erc20",
                    })

        elif chain == "TRX":
            from_addr = result.get("發送方", "")
            to_addr   = result.get("接收方", "")
            try:
                value_native = float(
                    result.get("TRX 金額", "0").split()[0].replace(",", ""))
            except (ValueError, IndexError):
                value_native = 0.0
            if from_addr and to_addr and "N/A" not in (from_addr, to_addr):
                rows.append({
                    "from_addr": from_addr, "to_addr": to_addr,
                    "value_native": value_native, "token_symbol": "",
                    "token_amount": 0.0, "tx_time": time_str,
                    "tx_hash": tx_hash, "tx_type": "normal",
                })
            for t in result.get("token_transfers", []):
                sym = ""
                token_str = t.get("Token", "")
                if "(" in token_str:
                    sym = token_str.rsplit("(", 1)[-1].rstrip(")")
                try:
                    token_amt = float(t.get("金額", "0").replace(",", "") or 0)
                except ValueError:
                    token_amt = 0.0
                f, to = t.get("從", ""), t.get("至", "")
                if f and to:
                    rows.append({
                        "from_addr": f, "to_addr": to,
                        "value_native": 0.0, "token_symbol": sym,
                        "token_amount": token_amt, "tx_time": time_str,
                        "tx_hash": tx_hash, "tx_type": "trc20",
                    })

        elif chain == "BTC":
            senders_str = result.get("發送方", "")
            senders = [s.strip() for s in senders_str.split("、")
                       if s.strip() and s.strip() != "N/A"]
            for recv in result.get("接收方（明細）", []):
                to_addr = recv.get("地址", "")
                try:
                    value_native = float(recv.get("BTC", "0").replace(",", ""))
                except (ValueError, TypeError):
                    value_native = 0.0
                for from_addr in (senders or []):
                    if from_addr and to_addr:
                        rows.append({
                            "from_addr": from_addr, "to_addr": to_addr,
                            "value_native": value_native, "token_symbol": "",
                            "token_amount": 0.0, "tx_time": time_str,
                            "tx_hash": tx_hash, "tx_type": "btc",
                        })

        if rows:
            self._state.add_edges_from_db_rows(rows)
        self._pos = {}
        self._render()

    def add_row_edge(self, from_addr: str, to_addr: str, tx_hash: str,
                     time_str: str, value_native: float,
                     token_symbol: str, token_amount: float, chain: str):
        """從 treeview 單列資料直接新增一條交易邊。"""
        if not from_addr or not to_addr:
            return
        if self._state is None:
            self._state = GraphState(chain=chain, mode="explore")
            self._gen_mode.set("explore")
            self._update_mode_label()
        tx_type = "token" if token_symbol else "normal"
        self._state.add_edges_from_db_rows([{
            "from_addr":    from_addr,
            "to_addr":      to_addr,
            "value_native": value_native,
            "token_symbol": token_symbol,
            "token_amount": token_amount,
            "tx_time":      time_str,
            "tx_hash":      tx_hash,
            "tx_type":      tx_type,
        }])
        self._pos = {}
        self._render()

    def clear(self):
        self._state = None
        self._pos   = {}
        self._selected_node = None
        if self._ax:
            self._ax.clear()
            self._ax.set_facecolor(BG_DARK)
            self._ax.text(0.5, 0.5, "尚無資料\n請查詢錢包後點擊「加入幣流圖」",
                          ha="center", va="center",
                          fontsize=14, color="#999999",
                          transform=self._ax.transAxes,
                          fontfamily="Microsoft JhengHei")
            self._mpl_canvas.draw()
        self._update_stats()

    # ── UI 建構 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_toolbar()
        self._build_canvas_area()
        self._build_statusbar()

    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=8)
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        # 左側：生成模式指示
        self._mode_lbl = ctk.CTkLabel(
            bar, text="⚠ 探索模式（資料不具法庭效力）",
            font=("Microsoft JhengHei", 11, "bold"),
            text_color=WARN_COL)
        self._mode_lbl.grid(row=0, column=0, padx=(12, 16), pady=8, sticky="w")

        # 視圖切換
        ctk.CTkLabel(bar, text="視圖：",
                     font=("Microsoft JhengHei", 11),
                     text_color=TEXT_COL).grid(row=0, column=1, padx=(0, 4), pady=8)
        view_seg = ctk.CTkSegmentedButton(
            bar, values=[VIEW_NETWORK, VIEW_FLOW, VIEW_TIMELINE, VIEW_MALTEGO],
            variable=self._view_mode,
            font=("Microsoft JhengHei", 11),
            width=410,
            command=self._on_view_change)
        view_seg.grid(row=0, column=2, padx=4, pady=8)

        # 間隔
        bar.grid_columnconfigure(3, weight=1)

        # 右側操作按鈕
        ctk.CTkButton(bar, text="重新佈局", width=80,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#2d4a6a",
                      command=self._relayout).grid(row=0, column=4, padx=4, pady=8)

        ctk.CTkButton(bar, text="尋找路徑", width=80,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#3a5a4a",
                      command=self._find_path_dialog).grid(row=0, column=5, padx=4, pady=8)

        ctk.CTkButton(bar, text="標記節點", width=80,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#5a3a6a",
                      command=self._label_node_dialog).grid(row=0, column=6, padx=4, pady=8)

        ctk.CTkButton(bar, text="儲存快照", width=80,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#6a4a2a",
                      command=self._save_snapshot).grid(row=0, column=7, padx=4, pady=8)

        ctk.CTkButton(bar, text="匯出圖片", width=80,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#2d6a4f",
                      command=self._export_image).grid(row=0, column=8, padx=4, pady=8)

        ctk.CTkButton(bar, text="清除圖", width=70,
                      font=("Microsoft JhengHei", 11),
                      fg_color="gray30",
                      command=self.clear).grid(row=0, column=9, padx=(4, 4), pady=8)

        ctk.CTkLabel(bar, text="│", text_color="gray40",
                     font=("Arial", 16)).grid(row=0, column=10, padx=2)

        ctk.CTkButton(bar, text="＋ 節點", width=72,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#1e3a5f",
                      command=self._add_node_manual_dialog).grid(row=0, column=11, padx=2, pady=8)

        ctk.CTkButton(bar, text="＋ 交易", width=72,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#3a1e5f",
                      command=self._add_edge_manual_dialog).grid(row=0, column=12, padx=2, pady=8)

        ctk.CTkButton(bar, text="↑ CSV", width=65,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#1e3a2a",
                      command=self._import_csv_dialog).grid(row=0, column=13, padx=(2, 12), pady=8)

    def _build_canvas_area(self):
        frame = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        self._fig = plt.Figure(figsize=(12, 7), dpi=100, facecolor=BG_DARK)
        self._ax  = self._fig.add_subplot(111)
        self._ax.set_facecolor(BG_DARK)
        self._ax.axis("off")

        self._mpl_canvas = FigureCanvasTkAgg(self._fig, master=frame)
        self._mpl_canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # matplotlib 工具列（縮放/平移）
        toolbar_frame = tk.Frame(frame, bg=BG_DARK)
        toolbar_frame.grid(row=1, column=0, sticky="ew")
        toolbar = NavigationToolbar2Tk(self._mpl_canvas, toolbar_frame)
        toolbar.config(background=BG_DARK)
        for child in toolbar.winfo_children():
            try:
                child.config(background=BG_DARK, foreground=TEXT_COL)
            except Exception:
                pass
        toolbar.update()

        # 點擊事件
        self._mpl_canvas.mpl_connect("button_press_event", self._on_canvas_click)

        self.clear()

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=8)
        bar.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 8))
        bar.grid_columnconfigure(0, weight=1)

        self._stat_lbl = ctk.CTkLabel(
            bar, text="節點：0　邊：0",
            font=("Microsoft JhengHei", 10),
            text_color="#999999", anchor="w")
        self._stat_lbl.grid(row=0, column=0, padx=12, pady=4, sticky="w")

        self._sel_lbl = ctk.CTkLabel(
            bar, text="",
            font=("Consolas", 10),
            text_color=ACCENT, anchor="e")
        self._sel_lbl.grid(row=0, column=1, padx=12, pady=4, sticky="e")

    # ── 渲染 ──────────────────────────────────────────────────────────────────

    def _render(self):
        if self._state is None or not self._state.nodes:
            self.clear()
            return
        self._state.rebuild_graph()
        mode = self._view_mode.get()
        self._ax.clear()
        self._ax.set_facecolor(BG_DARK)
        self._ax.axis("off")

        if mode == VIEW_NETWORK:
            self._draw_network()
        elif mode == VIEW_FLOW:
            self._draw_flow()
        elif mode == VIEW_MALTEGO:
            self._draw_maltego()
        else:
            self._draw_timeline()

        self._fig.tight_layout(pad=0.5)
        self._mpl_canvas.draw()
        self._update_stats()

    def _draw_network(self):
        """地址關係圖：節點 = 地址，邊 = 聚合交易。"""
        G = self._state.G
        if not G.nodes:
            return

        if not self._pos or set(self._pos) != set(G.nodes):
            self._pos = nx.spring_layout(G, k=2.5, iterations=50, seed=42)

        colors  = [G.nodes[n].get("color", "#AAAAAA") for n in G.nodes]
        labels  = {n: G.nodes[n].get("display_label", n[:8]) for n in G.nodes}

        # 邊寬度依交易數量縮放
        edge_widths = []
        for u, v, d in G.edges(data=True):
            edge_widths.append(max(0.5, min(4.0, d.get("tx_count", 1) * 0.4)))

        nx.draw_networkx_nodes(G, self._pos, ax=self._ax,
                               node_color=colors, node_size=600, alpha=0.92)
        nx.draw_networkx_labels(G, self._pos, labels=labels, ax=self._ax,
                                font_size=7, font_color=TEXT_COL,
                                font_family="Microsoft JhengHei")
        nx.draw_networkx_edges(G, self._pos, ax=self._ax,
                               edge_color="#4a6fa5", arrows=True,
                               arrowsize=15, width=edge_widths,
                               connectionstyle="arc3,rad=0.1",
                               min_source_margin=18, min_target_margin=18)

        # 邊標籤（金額或筆數）
        edge_labels = {}
        for u, v, d in G.edges(data=True):
            cnt = d.get("tx_count", 1)
            sym = d.get("token_symbol", "")
            w   = d.get("weight", 0)
            if sym:
                edge_labels[(u, v)] = f"{w:,.2f} {sym}" if cnt == 1 else f"{cnt}筆"
            else:
                edge_labels[(u, v)] = f"{w:,.4f}" if cnt == 1 else f"{cnt}筆"
        nx.draw_networkx_edge_labels(
            G, self._pos, edge_labels=edge_labels, ax=self._ax,
            font_size=6, font_color="#aaaaaa",
            bbox=dict(facecolor=BG_DARK, edgecolor="none", alpha=0.7))

        self._draw_legend()

    def _draw_flow(self):
        """
        幣流流水圖：DAG，依時間排序，各地址用垂直泳道。
        節點 = 每一筆交易，X 軸 = 時間，Y 軸 = 地址泳道。
        """
        edges = sorted(self._state.edges, key=lambda e: e.tx_time)
        if not edges:
            self._ax.text(0.5, 0.5, "無交易資料", ha="center", va="center",
                          transform=self._ax.transAxes,
                          color="#999999", fontsize=14,
                          fontfamily="Microsoft JhengHei")
            return

        # 收集所有出現過的地址，分配 Y 座標
        addr_order: list[str] = []
        seen = set()
        for e in edges:
            for a in (e.source, e.target):
                if a not in seen:
                    addr_order.append(a)
                    seen.add(a)

        y_pos = {a: i for i, a in enumerate(reversed(addr_order))}
        n_addr = len(addr_order)

        # 繪製水平泳道線
        for i in range(n_addr):
            self._ax.axhline(y=i, color="#2a2a4a", linewidth=0.5, zorder=0)

        # Y 軸標籤（地址縮寫）
        self._ax.set_yticks(range(n_addr))
        self._ax.set_yticklabels(
            [self._state.nodes[a].display_label if a in self._state.nodes
             else (a[:8] + "…") for a in reversed(addr_order)],
            fontsize=7, color=TEXT_COL,
            fontfamily="Microsoft JhengHei")
        self._ax.tick_params(axis="y", length=0)
        self._ax.yaxis.set_tick_params(labelcolor=TEXT_COL)
        self._ax.spines[:].set_visible(False)

        # 繪製交易箭頭
        max_edges = min(len(edges), 200)   # 最多顯示 200 筆，避免過密
        for i, e in enumerate(edges[:max_edges]):
            x = i
            y1 = y_pos.get(e.source, 0)
            y2 = y_pos.get(e.target, 0)
            color = self._state.nodes.get(e.source, NodeInfo(e.source, "ETH")).color
            self._ax.annotate(
                "", xy=(x, y2), xytext=(x, y1),
                arrowprops=dict(arrowstyle="->", color=color, lw=1.2))

            # 金額標籤
            label = e.amount_display
            mid_y = (y1 + y2) / 2
            self._ax.text(x + 0.05, mid_y, label,
                          fontsize=5.5, color="#cccccc",
                          va="center", ha="left",
                          fontfamily="Microsoft JhengHei")

        if len(edges) > max_edges:
            self._ax.text(0.5, -0.06,
                          f"（僅顯示前 {max_edges} 筆，共 {len(edges)} 筆）",
                          ha="center", transform=self._ax.transAxes,
                          fontsize=8, color=WARN_COL,
                          fontfamily="Microsoft JhengHei")

        self._ax.set_xlabel("交易順序（時間）", color=TEXT_COL,
                            fontfamily="Microsoft JhengHei")
        self._ax.tick_params(axis="x", colors=TEXT_COL)

    def _draw_timeline(self):
        """時序泳道圖：X 軸 = 時間，各地址橫向延伸，交易為連接點。"""
        import datetime

        edges = [e for e in self._state.edges if e.tx_time]
        if not edges:
            self._ax.text(0.5, 0.5, "無時間戳資料\n（BTC/TRX 部分交易無時間戳）",
                          ha="center", va="center",
                          transform=self._ax.transAxes,
                          color="#999999", fontsize=13,
                          fontfamily="Microsoft JhengHei")
            return

        # 解析時間戳（Unix 或 ISO 格式）
        def parse_ts(s):
            if not s:
                return None
            try:
                return datetime.datetime.fromtimestamp(int(s))
            except Exception:
                pass
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.datetime.strptime(s, fmt)
                except Exception:
                    pass
            return None

        timed = [(e, parse_ts(e.tx_time)) for e in edges]
        timed = [(e, t) for e, t in timed if t is not None]
        if not timed:
            self._ax.text(0.5, 0.5, "無法解析時間格式",
                          ha="center", va="center",
                          transform=self._ax.transAxes,
                          color="#999999", fontsize=13,
                          fontfamily="Microsoft JhengHei")
            return

        timed.sort(key=lambda x: x[1])

        addr_order: list[str] = []
        seen = set()
        for e, _ in timed:
            for a in (e.source, e.target):
                if a not in seen:
                    addr_order.append(a)
                    seen.add(a)

        y_pos    = {a: i for i, a in enumerate(reversed(addr_order))}
        n_addr   = len(addr_order)
        ts_vals  = [t for _, t in timed]
        t_min, t_max = ts_vals[0], ts_vals[-1]
        span = max((t_max - t_min).total_seconds(), 1)

        # 泳道線
        for i in range(n_addr):
            color = self._state.nodes.get(addr_order[-(i+1)],
                                          NodeInfo("", "ETH")).color
            self._ax.axhline(y=i, color=color, linewidth=0.8, alpha=0.3, zorder=0)

        # Y 軸標籤
        self._ax.set_yticks(range(n_addr))
        self._ax.set_yticklabels(
            [self._state.nodes[a].display_label if a in self._state.nodes
             else (a[:8] + "…") for a in reversed(addr_order)],
            fontsize=7, color=TEXT_COL,
            fontfamily="Microsoft JhengHei")
        self._ax.tick_params(axis="y", length=0)
        self._ax.spines[:].set_visible(False)

        # 繪製交易弧線
        max_draw = min(len(timed), 150)
        for e, t in timed[:max_draw]:
            x  = (t - t_min).total_seconds() / span
            y1 = y_pos.get(e.source, 0)
            y2 = y_pos.get(e.target, 0)
            color = self._state.nodes.get(e.source, NodeInfo(e.source, "ETH")).color

            if y1 == y2:
                self._ax.plot(x, y1, "o", color=color, markersize=4, zorder=3)
            else:
                self._ax.annotate(
                    "", xy=(x, y2), xytext=(x, y1),
                    arrowprops=dict(arrowstyle="->", color=color,
                                   lw=1.0, connectionstyle="arc3,rad=0.2"))

        # X 軸時間標籤
        tick_count = min(6, len(timed))
        tick_xs    = [i / max(tick_count - 1, 1) for i in range(tick_count)]
        tick_ts    = [t_min + (t_max - t_min) * x for x in tick_xs]
        self._ax.set_xticks(tick_xs)
        self._ax.set_xticklabels(
            [t.strftime("%m/%d\n%H:%M") for t in tick_ts],
            fontsize=7, color=TEXT_COL)
        self._ax.tick_params(axis="x", colors=TEXT_COL)

        self._draw_legend()

    def _draw_legend(self):
        patches = [
            mpatches.Patch(color=color, label=label)
            for role, (color, label) in ROLE_STYLES.items()
        ]
        legend = self._ax.legend(
            handles=patches, loc="lower right",
            fontsize=7, framealpha=0.7,
            facecolor=BG_PANEL, edgecolor="none",
            labelcolor=TEXT_COL,
            prop={"family": "Microsoft JhengHei", "size": 7})

    # ── 事件處理 ──────────────────────────────────────────────────────────────

    def _on_canvas_click(self, event):
        if event.inaxes != self._ax:
            return
        if self._view_mode.get() != VIEW_NETWORK:
            return
        if not self._pos or self._state is None:
            return

        # 找最近節點
        ex, ey = event.xdata, event.ydata
        min_dist = float("inf")
        nearest  = None
        for node, (nx_, ny_) in self._pos.items():
            dist = (nx_ - ex) ** 2 + (ny_ - ey) ** 2
            if dist < min_dist:
                min_dist = dist
                nearest  = node

        CLICK_THRESHOLD = 0.05
        if nearest and min_dist < CLICK_THRESHOLD:
            self._on_node_selected(nearest, event.dblclick)

    def _on_node_selected(self, address: str, dblclick: bool):
        self._selected_node = address
        node = self._state.nodes.get(address)
        label = node.display_label if node else address
        self._sel_lbl.configure(
            text=f"已選取：{label}  ({address[:12]}…{address[-6:]})")

        if dblclick:
            self._show_node_menu(address)

    def _show_node_menu(self, address: str):
        node = self._state.nodes.get(address)
        chain = self._state.chain if self._state else "ETH"

        menu = tk.Menu(self, tearoff=0, bg="#2a2a3e", fg=TEXT_COL,
                       activebackground="#3a3a5e", activeforeground="white",
                       font=("Microsoft JhengHei", 10))

        if self._node_click_cb:
            menu.add_command(
                label="🔍 查詢此地址（填入主視窗）",
                command=lambda: self._node_click_cb(address, chain))

        if node and not node.expanded:
            menu.add_command(
                label="➕ 展開此節點（查詢並追加）",
                command=lambda: self._expand_node(address))

        menu.add_separator()

        roles = list(ROLE_STYLES.keys())
        role_menu = tk.Menu(menu, tearoff=0, bg="#2a2a3e", fg=TEXT_COL,
                            font=("Microsoft JhengHei", 10))
        for role in roles:
            _, desc = ROLE_STYLES[role]
            role_menu.add_command(
                label=desc,
                command=lambda r=role: self._set_node_role(address, r))
        menu.add_cascade(label="🏷 標記角色", menu=role_menu)

        menu.add_command(label="✏ 自訂標籤",
                         command=lambda: self._label_node_dialog(address))
        menu.add_separator()
        menu.add_command(label="📋 複製地址",
                         command=lambda: self._copy_to_clipboard(address))

        try:
            x = self.winfo_rootx() + self._mpl_canvas.get_tk_widget().winfo_x()
            y = self.winfo_rooty() + self._mpl_canvas.get_tk_widget().winfo_y()
            menu.tk_popup(x + 200, y + 200)
        finally:
            menu.grab_release()

    def _expand_node(self, address: str):
        """點擊展開：呼叫主視窗的查詢 callback，結果由 add_profile_to_graph() 追加。"""
        if self._node_click_cb:
            self._node_click_cb(address, self._state.chain if self._state else "ETH")
        else:
            messagebox.showinfo("提示", "請先在主視窗查詢此地址，查詢完成後點擊「加入幣流圖」。",
                                parent=self)

    def _set_node_role(self, address: str, role: str):
        if self._state:
            self._state.set_role(address, role)
            self._pos = {}
            self._render()

    def _on_view_change(self, _=None):
        self._render()

    # ── 工具列操作 ────────────────────────────────────────────────────────────

    def _relayout(self):
        self._pos = {}
        self._render()

    def _find_path_dialog(self):
        if not self._state or len(self._state.nodes) < 2:
            messagebox.showinfo("提示", "請先載入幣流圖資料。", parent=self)
            return

        src = simpledialog.askstring("尋找路徑", "起點地址（可輸入前幾碼）：",
                                     parent=self)
        if not src:
            return
        dst = simpledialog.askstring("尋找路徑", "終點地址（可輸入前幾碼）：",
                                     parent=self)
        if not dst:
            return

        # 模糊匹配地址
        def fuzzy(prefix):
            for a in self._state.nodes:
                if a.lower().startswith(prefix.lower()):
                    return a
            return None

        src_full = fuzzy(src) or src
        dst_full = fuzzy(dst) or dst
        paths = self._state.find_paths(src_full, dst_full)

        if not paths:
            messagebox.showinfo("路徑搜尋",
                                f"找不到從\n{src_full[:20]}…\n到\n{dst_full[:20]}…\n的路徑。",
                                parent=self)
            return

        msg = f"找到 {len(paths)} 條路徑（最短路徑優先顯示）：\n\n"
        for i, p in enumerate(paths[:5], 1):
            msg += f"路徑 {i}：\n" + " → ".join(
                self._state.nodes[a].display_label if a in self._state.nodes
                else a[:10] for a in p) + "\n\n"
        messagebox.showinfo("路徑搜尋結果", msg, parent=self)

    def _label_node_dialog(self, address: str = None):
        if address is None:
            address = self._selected_node
        if not address or not self._state:
            messagebox.showinfo("提示", "請先在地址關係圖上點選一個節點。",
                                parent=self)
            return
        node = self._state.nodes.get(address)
        current = node.custom_label if node else ""
        new_label = simpledialog.askstring(
            "自訂標籤",
            f"為此地址設定標籤：\n{address}",
            initialvalue=current, parent=self)
        if new_label is not None and self._state:
            self._state.set_role(address, node.role if node else "unknown",
                                 custom_label=new_label)
            self._render()

    def _save_snapshot(self):
        if not self._state:
            messagebox.showinfo("提示", "尚無幣流圖資料。", parent=self)
            return
        if self._state.mode != "evidence":
            messagebox.showwarning(
                "探索模式",
                "目前為探索模式，快照不會存入案件資料庫。\n"
                "請切換至「專案查詢 + 證據模式」後再儲存。",
                parent=self)
            return

        label = simpledialog.askstring("儲存快照", "快照名稱（選填）：", parent=self)
        if label is None:
            return

        # 需要 case_id，由外部在 load_from_case 時帶入
        case_id = getattr(self, "_current_case_id", None)
        if not case_id:
            messagebox.showwarning("提示", "無案件 ID，請確認已進入案件模式。",
                                   parent=self)
            return

        from database import db as _db
        nodes, edges = self._state.to_snapshot()
        snap_id = _db.save_graph_snapshot(case_id, self._state.chain,
                                          nodes, edges, label or "")
        messagebox.showinfo("已儲存", f"快照已儲存（ID: {snap_id}）。", parent=self)

    def _export_image(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            title="匯出幣流圖",
            defaultextension=".png",
            filetypes=[("PNG 圖片", "*.png"), ("SVG 向量圖", "*.svg"),
                       ("所有檔案", "*.*")])
        if not path:
            return
        try:
            self._fig.savefig(path, dpi=150, bbox_inches="tight",
                              facecolor=BG_DARK)
            messagebox.showinfo("匯出完成", f"圖片已儲存至：\n{path}", parent=self)
        except Exception as e:
            messagebox.showerror("匯出失敗", str(e), parent=self)

    # ── 輔助 ──────────────────────────────────────────────────────────────────

    def _update_mode_label(self):
        mode = self._gen_mode.get()
        if mode == "evidence":
            self._mode_lbl.configure(
                text="✓ 證據模式（資料已與案件關聯）",
                text_color="#44CC44")
        else:
            self._mode_lbl.configure(
                text="⚠ 探索模式（資料不具法庭效力）",
                text_color=WARN_COL)

    def _update_stats(self):
        if not hasattr(self, "_stat_lbl"):
            return
        if not self._state:
            self._stat_lbl.configure(text="節點：0　邊：0")
            return
        s = self._state.summary()
        roles_str = "　".join(
            f"{ROLE_STYLES.get(r, ('', r))[1]}×{c}"
            for r, c in s["roles"].items() if c > 0)
        self._stat_lbl.configure(
            text=f"節點：{s['node_count']}　邊：{s['edge_count']}　{roles_str}")

    def _copy_to_clipboard(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)

    def set_case_id(self, case_id: int | None):
        """由主視窗在切換案件時傳入；None 代表清除。"""
        self._current_case_id = case_id

    # ── 司法金流圖（Maltego 風格）────────────────────────────────────────────

    def _maltego_layout(self, G: nx.DiGraph) -> dict:
        """BFS 階層式佈局：種子節點在最左，依跳數向右排列。"""
        if not G.nodes:
            return {}

        seeds   = [n for n in G.nodes if G.nodes[n].get("role") == "seed"]
        no_pred = [n for n in G.nodes if G.in_degree(n) == 0 and n not in seeds]
        roots   = seeds + no_pred or list(G.nodes)[:1]

        layer_of: dict[str, int] = {}
        queue = list(roots)
        for r in roots:
            layer_of[r] = 0

        while queue:
            node = queue.pop(0)
            for nbr in G.successors(node):
                if nbr not in layer_of:
                    layer_of[nbr] = layer_of[node] + 1
                    queue.append(nbr)

        max_l = max(layer_of.values(), default=0)
        for n in G.nodes:
            if n not in layer_of:
                layer_of[n] = max_l + 1

        by_layer: dict[int, list] = {}
        for n, l in layer_of.items():
            by_layer.setdefault(l, []).append(n)

        n_layers = len(by_layer)
        pos: dict[str, tuple] = {}
        for l, nodes in sorted(by_layer.items()):
            x = l / max(n_layers - 1, 1) if n_layers > 1 else 0.5
            n = len(nodes)
            for i, node in enumerate(nodes):
                y = (i + 1) / (n + 1)
                pos[node] = (x, y)
        return pos

    @staticmethod
    def _fmt_ts(ts: str) -> str:
        """將 Unix 毫秒/秒或 ISO 字串統一格式化為 YYYY-MM-DD HH:MM:SS。"""
        if not ts:
            return ""
        import datetime
        try:
            v = int(ts)
            dt = datetime.datetime.fromtimestamp(v / 1000 if v > 1e10 else v)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError, OSError):
            pass
        return ts[:19] if len(ts) > 19 else ts

    def _draw_maltego(self):
        """司法金流圖：Maltego 風格階層式佈局，每筆交易獨立顯示時間與金額。"""
        ax = self._ax

        if not self._state or not self._state.nodes:
            ax.text(0.5, 0.5,
                    "尚無資料\n\n請使用「＋ 節點」手動建立，\n或查詢錢包後點擊「加入幣流圖」，\n或使用「↑ CSV」批次匯入。",
                    ha="center", va="center",
                    fontsize=12, color="#888888",
                    transform=ax.transAxes,
                    fontfamily="Microsoft JhengHei",
                    linespacing=1.8)
            return

        self._state.rebuild_graph()
        G = self._state.G

        if not self._pos or set(self._pos) != set(G.nodes):
            self._pos = self._maltego_layout(G)
        pos = self._pos

        ax.set_xlim(-0.20, 1.20)
        ax.set_ylim(-0.12, 1.12)
        ax.axis("off")

        # ── 繪製交易邊 ────────────────────────────────────────────────────────
        pair_count: dict[tuple, int] = {}
        for e in self._state.edges:
            key = (e.source, e.target)
            pair_count[key] = pair_count.get(key, 0) + 1

        pair_idx: dict[tuple, int] = {}
        edges_sorted = sorted(self._state.edges, key=lambda e: e.tx_time or "")

        for e in edges_sorted:
            sp = pos.get(e.source)
            tp = pos.get(e.target)
            if sp is None or tp is None:
                continue

            key   = (e.source, e.target)
            total = pair_count[key]
            idx   = pair_idx.get(key, 0)
            pair_idx[key] = idx + 1

            # 多邊時展開弧度
            rad = 0.12 + (idx - total / 2) * 0.10

            src_color = self._state.nodes.get(
                e.source, NodeInfo(e.source, "ETH")).color

            ax.annotate("", xy=tp, xytext=sp,
                        arrowprops=dict(arrowstyle="->",
                                        color=src_color, lw=1.5,
                                        connectionstyle=f"arc3,rad={rad:.2f}"),
                        zorder=2)

            # 邊標籤：時間 + 金額
            mid_x = (sp[0] + tp[0]) / 2
            mid_y = (sp[1] + tp[1]) / 2 + rad * 0.28
            ts    = self._fmt_ts(e.tx_time)
            label = f"{ts}\n{e.amount_display}" if ts else e.amount_display

            ax.text(mid_x, mid_y, label,
                    ha="center", va="center", fontsize=5.2,
                    color="#dddddd",
                    fontfamily="Microsoft JhengHei",
                    zorder=3,
                    bbox=dict(facecolor="#0d0d1a", edgecolor="none",
                              alpha=0.78, pad=1.8,
                              boxstyle="round,pad=0.25"))

        # ── 繪製節點方塊 ──────────────────────────────────────────────────────
        n_nodes = max(len(pos), 1)
        box_w   = max(0.08, min(0.13, 0.65 / n_nodes))
        box_h   = 0.052

        for node, (x, y) in pos.items():
            info  = self._state.nodes.get(node, NodeInfo(node, "ETH"))
            color = info.color

            rect = mpatches.FancyBboxPatch(
                (x - box_w / 2, y - box_h / 2), box_w, box_h,
                boxstyle="round,pad=0.006",
                facecolor=color, alpha=0.20,
                edgecolor=color, linewidth=1.8, zorder=4)
            ax.add_patch(rect)

            # 標籤（機構名 / 自訂標籤）
            ax.text(x, y + 0.010, info.display_label,
                    ha="center", va="center", fontsize=6.5,
                    color="white", fontweight="bold",
                    fontfamily="Microsoft JhengHei", zorder=5)

            # 地址縮寫
            short = (node[:6] + "…" + node[-4:]) if len(node) > 10 else node
            ax.text(x, y - 0.017, short,
                    ha="center", va="center", fontsize=4.8,
                    color="#aaaaaa", fontfamily="Consolas", zorder=5)

        self._draw_legend()

    # ── 手動建立金流圖 ────────────────────────────────────────────────────────

    def _add_node_manual_dialog(self):
        """手動新增節點（錢包地址）對話框。"""
        dlg = ctk.CTkToplevel(self)
        dlg.title("新增節點")
        dlg.geometry("460x320")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG_PANEL)
        dlg.transient(self.winfo_toplevel())
        dlg.lift()
        dlg.focus_force()
        dlg.after(100, dlg.grab_set)

        ctk.CTkLabel(dlg, text="新增錢包節點",
                     font=("Microsoft JhengHei", 14, "bold"),
                     text_color=ACCENT).pack(pady=(18, 8))

        def efield(label, hint="", mono=False):
            f = ctk.CTkFrame(dlg, fg_color="transparent")
            f.pack(fill="x", padx=22, pady=5)
            ctk.CTkLabel(f, text=label, width=72, anchor="e",
                         font=("Microsoft JhengHei", 11),
                         text_color=TEXT_COL).pack(side="left")
            e = ctk.CTkEntry(f,
                             font=("Consolas" if mono else "Microsoft JhengHei", 11),
                             width=300, placeholder_text=hint)
            e.pack(side="left", padx=(8, 0))
            return e

        e_addr  = efield("地址 *", "TRX / ETH / BTC 地址", mono=True)
        e_label = efield("標籤",   "e.g. Imtoken、被害人A")

        # Role + Chain
        rc = ctk.CTkFrame(dlg, fg_color="transparent")
        rc.pack(fill="x", padx=22, pady=5)
        ctk.CTkLabel(rc, text="角色", width=72, anchor="e",
                     font=("Microsoft JhengHei", 11),
                     text_color=TEXT_COL).pack(side="left")
        role_var = ctk.StringVar(value="unknown")
        ctk.CTkComboBox(rc, values=list(ROLE_STYLES.keys()), variable=role_var,
                        font=("Microsoft JhengHei", 11), width=150).pack(side="left", padx=(8, 16))
        ctk.CTkLabel(rc, text="鏈", font=("Microsoft JhengHei", 11),
                     text_color=TEXT_COL).pack(side="left")
        chain_var = ctk.StringVar(value=self._state.chain if self._state else "TRX")
        ctk.CTkComboBox(rc, values=["TRX", "ETH", "BTC"], variable=chain_var,
                        font=("Microsoft JhengHei", 11), width=100).pack(side="left", padx=(8, 0))

        def confirm():
            addr = e_addr.get().strip()
            if not addr:
                messagebox.showwarning("缺少資料", "地址不可空白。", parent=dlg)
                return
            if self._state is None:
                self._state = GraphState(chain=chain_var.get(), mode="explore")
                self._gen_mode.set("explore")
                self._update_mode_label()
            node = self._state.add_node(addr, role=role_var.get(),
                                        custom_label=e_label.get().strip())
            self._pos = {}
            self._view_mode.set(VIEW_MALTEGO)
            self._render()
            dlg.destroy()

        bf = ctk.CTkFrame(dlg, fg_color="transparent")
        bf.pack(pady=18)
        ctk.CTkButton(bf, text="新增節點", width=110, command=confirm,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#2d6a4f").pack(side="left", padx=8)
        ctk.CTkButton(bf, text="取消", width=90, command=dlg.destroy,
                      font=("Microsoft JhengHei", 11),
                      fg_color="gray30").pack(side="left", padx=8)

    def _add_edge_manual_dialog(self):
        """手動新增交易紀錄（金流邊）對話框。"""
        if self._state is None:
            self._state = GraphState(chain="TRX", mode="explore")
            self._gen_mode.set("explore")
            self._update_mode_label()

        dlg = ctk.CTkToplevel(self)
        dlg.title("新增交易紀錄")
        dlg.geometry("500x460")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG_PANEL)
        dlg.transient(self.winfo_toplevel())
        dlg.lift()
        dlg.focus_force()
        dlg.after(100, dlg.grab_set)

        ctk.CTkLabel(dlg, text="新增交易紀錄（金流邊）",
                     font=("Microsoft JhengHei", 14, "bold"),
                     text_color=ACCENT).pack(pady=(18, 8))

        existing = list(self._state.nodes.keys())
        addr_opts = [
            f"{self._state.nodes[a].display_label}  {a[:8]}…"
            for a in existing
        ] + existing

        label_to_addr = {}
        for a in existing:
            label_to_addr[f"{self._state.nodes[a].display_label}  {a[:8]}…"] = a

        def resolve(val):
            return label_to_addr.get(val.strip(), val.strip())

        def cbox(parent, label, hint=""):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.pack(fill="x", padx=22, pady=5)
            ctk.CTkLabel(f, text=label, width=90, anchor="e",
                         font=("Microsoft JhengHei", 11),
                         text_color=TEXT_COL).pack(side="left")
            var = ctk.StringVar()
            box = ctk.CTkComboBox(f, values=addr_opts or [], variable=var,
                                  font=("Consolas", 10), width=330,
                                  placeholder_text=hint)
            box.pack(side="left", padx=(8, 0))
            return var

        def efield(parent, label, default="", hint="", mono=False):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.pack(fill="x", padx=22, pady=5)
            ctk.CTkLabel(f, text=label, width=90, anchor="e",
                         font=("Microsoft JhengHei", 11),
                         text_color=TEXT_COL).pack(side="left")
            e = ctk.CTkEntry(f,
                             font=("Consolas" if mono else "Microsoft JhengHei", 11),
                             width=330, placeholder_text=hint)
            if default:
                e.insert(0, default)
            e.pack(side="left", padx=(8, 0))
            return e

        from_var = cbox(dlg, "FROM *",   "發送方地址或從下拉選取")
        to_var   = cbox(dlg, "TO *",     "接收方地址或從下拉選取")
        e_time   = efield(dlg, "時間",   "2025-03-27 19:48:00", "YYYY-MM-DD HH:MM:SS")
        e_amt    = efield(dlg, "金額 *", hint="例如 30311")

        cf = ctk.CTkFrame(dlg, fg_color="transparent")
        cf.pack(fill="x", padx=22, pady=5)
        ctk.CTkLabel(cf, text="幣種", width=90, anchor="e",
                     font=("Microsoft JhengHei", 11),
                     text_color=TEXT_COL).pack(side="left")
        cur_var = ctk.StringVar(value="USDT")
        ctk.CTkComboBox(cf, values=["USDT", "TRX", "ETH", "BTC", "USDC"],
                        variable=cur_var,
                        font=("Microsoft JhengHei", 11), width=130).pack(side="left", padx=(8, 0))

        e_hash = efield(dlg, "TX Hash", hint="（選填）", mono=True)

        def confirm():
            from_addr = resolve(from_var.get())
            to_addr   = resolve(to_var.get())
            if not from_addr or not to_addr:
                messagebox.showwarning("缺少資料", "FROM 和 TO 不可空白。", parent=dlg)
                return
            try:
                amount = float(e_amt.get().replace(",", "") or "0")
            except ValueError:
                messagebox.showwarning("格式錯誤", "金額請輸入數字。", parent=dlg)
                return

            currency = cur_var.get()
            is_token = currency in ("USDT", "USDC", "BUSD")

            self._state.add_node(from_addr)
            self._state.add_node(to_addr)
            self._state.edges.append(EdgeInfo(
                source=from_addr, target=to_addr,
                tx_hash=e_hash.get().strip(),
                value_native=0.0 if is_token else amount,
                token_symbol=currency if is_token else "",
                token_amount=amount if is_token else 0.0,
                tx_time=e_time.get().strip(),
                tx_type="trc20" if is_token else "normal",
            ))
            self._pos = {}
            self._view_mode.set(VIEW_MALTEGO)
            self._render()
            dlg.destroy()

        bf = ctk.CTkFrame(dlg, fg_color="transparent")
        bf.pack(pady=16)
        ctk.CTkButton(bf, text="新增交易", width=110, command=confirm,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#2d4a6a").pack(side="left", padx=8)
        ctk.CTkButton(bf, text="取消", width=90, command=dlg.destroy,
                      font=("Microsoft JhengHei", 11),
                      fg_color="gray30").pack(side="left", padx=8)

    def _import_csv_dialog(self):
        """從 CSV 批次匯入金流資料。

        支援欄位（彈性欄名）：
          必填: from_addr / from / source, to_addr / to / target, amount / 金額
          選填: datetime / timestamp / time / 時間,
                currency / symbol / 幣種,
                tx_hash / hash / txid,
                from_label / from_name, to_label / to_name,
                from_role, to_role
        """
        import csv as _csv

        path = filedialog.askopenfilename(
            parent=self,
            title="選擇 CSV 金流資料",
            filetypes=[("CSV 檔案", "*.csv"), ("文字檔案", "*.txt"),
                       ("所有檔案", "*.*")])
        if not path:
            return

        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = _csv.DictReader(f)
                rows   = list(reader)
        except Exception as exc:
            messagebox.showerror("讀取失敗", str(exc), parent=self)
            return

        if not rows:
            messagebox.showinfo("提示", "CSV 沒有資料列。", parent=self)
            return

        def pick(row: dict, *keys) -> str:
            for k in keys:
                for rk in row:
                    if rk.strip().lower() == k.lower():
                        return (row[rk] or "").strip()
            return ""

        if self._state is None:
            self._state = GraphState(chain="TRX", mode="explore")
            self._gen_mode.set("explore")
            self._update_mode_label()

        imported, skipped = 0, []

        for i, row in enumerate(rows, 1):
            from_addr  = pick(row, "from_addr", "from", "from_address", "發送方", "source")
            to_addr    = pick(row, "to_addr",   "to",   "to_address",   "接收方", "target")
            amt_str    = pick(row, "amount", "金額", "value", "數量")
            currency   = pick(row, "currency", "symbol", "幣種", "token") or "USDT"
            tx_time    = pick(row, "datetime", "timestamp", "tx_time", "time", "時間", "date")
            tx_hash    = pick(row, "tx_hash", "hash", "txhash", "txid")
            from_label = pick(row, "from_label", "from_name", "發送方標籤")
            to_label   = pick(row, "to_label",   "to_name",   "接收方標籤")
            from_role  = pick(row, "from_role") or "unknown"
            to_role    = pick(row, "to_role")   or "unknown"

            if not from_addr or not to_addr:
                skipped.append(f"第 {i} 列：缺少地址")
                continue
            try:
                amount = float(amt_str.replace(",", "") or "0")
            except ValueError:
                skipped.append(f"第 {i} 列：金額格式錯誤 ({amt_str!r})")
                continue

            self._state.add_node(from_addr, role=from_role, custom_label=from_label)
            self._state.add_node(to_addr,   role=to_role,   custom_label=to_label)

            is_token = currency.upper() in ("USDT", "USDC", "BUSD", "USDE", "DAI")
            self._state.edges.append(EdgeInfo(
                source=from_addr, target=to_addr,
                tx_hash=tx_hash,
                value_native=0.0 if is_token else amount,
                token_symbol=currency.upper() if is_token else "",
                token_amount=amount if is_token else 0.0,
                tx_time=self._fmt_ts(tx_time) or tx_time,
                tx_type="trc20" if is_token else "normal",
            ))
            imported += 1

        self._pos = {}
        self._view_mode.set(VIEW_MALTEGO)
        self._render()

        msg = f"成功匯入 {imported} 筆交易，共 {len(self._state.nodes)} 個節點。"
        if skipped:
            msg += f"\n\n略過 {len(skipped)} 筆：\n" + "\n".join(skipped[:5])
            if len(skipped) > 5:
                msg += f"\n…（共 {len(skipped)} 筆錯誤）"
        messagebox.showinfo("匯入完成", msg, parent=self)
