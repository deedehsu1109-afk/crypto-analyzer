from __future__ import annotations
import json
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
from typing import Callable

import customtkinter as ctk
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.transforms as mtransforms
from matplotlib.path import Path
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
        self._pos_network: dict = {}   # 地址關係圖佈局快取（spring layout）
        self._pos_maltego: dict = {}   # 司法金流圖佈局快取（BFS 階層）
        self._edge_waypoints: dict = {}   # 地址關係圖邊折點 {(from,to): [(x,y), ...]}
        self._selected_node:  str | None = None
        # 拖曳 / 複選狀態
        self._selected_nodes: set       = set()
        self._drag_node:      str | None = None
        self._drag_press_xy:  tuple | None = None  # 拖曳起點（資料座標）
        self._drag_start_pos: dict       = {}       # 拖曳起點時 _pos 的快照
        self._is_dragging:    bool       = False
        # 折點拖曳狀態
        self._drag_waypoint:       tuple | None = None  # (edge_key, wp_index)
        self._drag_waypoint_press: bool = False
        # 每次繪製後重建，供點擊命中測試用：[(edge_key, wp_index, (x,y)), ...]
        self._wp_hit_list: list = []

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
        self._pos_network = {}
        self._pos_maltego = {}
        self._edge_waypoints = {}
        self._render()

    def load_from_case(self, case_id: int, chain: str = None):
        """案件模式：優先從已儲存的幣流圖快照還原；若無快照則從交易記錄重建。"""
        from database import db as _db
        self._current_case_id = case_id

        snaps = _db.get_graph_snapshots(case_id)
        if snaps:
            # ── 有快照：還原節點、交易邊、佈局座標 ──────────────────────────
            snap = snaps[0]   # 最新一筆（ORDER BY saved_at DESC）
            self._state = GraphState.from_snapshot(
                snap["nodes"], snap["edges"],
                chain=snap.get("chain", chain or "ETH"), mode="evidence",
            )
            self._pos_network = {k: tuple(v)
                                 for k, v in snap.get("pos_network", {}).items()}
            self._pos_maltego = {k: tuple(v)
                                 for k, v in snap.get("pos_maltego", {}).items()}
            self._edge_waypoints = self._deserialize_edge_waypoints(
                snap.get("edge_waypoints", {}))
        else:
            # ── 無快照：從交易記錄重建（不限鏈別，chain=None 全部取出）────────
            rows = _db.get_edges_for_graph(case_id=case_id, chain=chain)
            detected = ({r.get("chain") for r in rows} - {None, ""}) or {"ETH"}
            use_chain = detected.pop() if len(detected) == 1 else (chain or "ETH")
            self._state = GraphState(chain=use_chain, mode="evidence")
            self._state.add_edges_from_db_rows(rows)
            wallets = _db.get_case_wallets(case_id)
            for w in wallets:
                addr = w.get("address", "")
                if addr and addr in self._state.nodes:
                    self._state.nodes[addr].expanded = True
                    self._state.nodes[addr].custom_label = w.get("label", "")
            self._pos_network = {}
            self._pos_maltego = {}
            self._edge_waypoints = {}

        # ── 涉案地址標記（每次都從 DB 取最新值，不依賴快照內容）────────────
        for ca in _db.get_case_addresses(case_id):
            addr = ca.get("address", "")
            if addr and addr in self._state.nodes:
                n = self._state.nodes[addr]
                n.holder_role = ca.get("holder_role") or ""
                n.case_label  = ca.get("label")       or ""
                n.case_notes  = ca.get("notes")        or ""

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
        self._render()

    def clear(self):
        self._state = None
        self._pos_network = {}
        self._pos_maltego = {}
        self._edge_waypoints = {}
        self._selected_node  = None
        self._selected_nodes = set()
        self._drag_node      = None
        self._drag_press_xy  = None
        self._drag_start_pos = {}
        self._is_dragging    = False
        self._drag_waypoint       = None
        self._drag_waypoint_press = False
        self._wp_hit_list = []
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

        ctk.CTkButton(bar, text="更新案件圖", width=88,
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
                      command=self._import_csv_dialog).grid(row=0, column=13, padx=2, pady=8)

        ctk.CTkButton(bar, text="💾 存檔", width=72,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#2a3a1e",
                      command=self._save_graph_json).grid(row=0, column=14, padx=2, pady=8)

        ctk.CTkButton(bar, text="📂 讀檔", width=72,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#1e2a3a",
                      command=self._load_graph_json).grid(row=0, column=15, padx=(2, 12), pady=8)

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

        # 隱藏式 NavigationToolbar2Tk — 不顯示，僅保留 pan/zoom/home 等功能
        _nav_host = tk.Frame(frame, height=0, bg=BG_DARK)
        self._nav_toolbar = NavigationToolbar2Tk(self._mpl_canvas, _nav_host)
        self._nav_toolbar.update()
        # _nav_host 不加入 grid，故工具列不顯示

        # 自訂中文工具列
        nav_bar = ctk.CTkFrame(frame, fg_color="#16213e", corner_radius=4)
        nav_bar.grid(row=1, column=0, sticky="w", padx=6, pady=(2, 4))

        def _nav(method: str):
            return lambda: getattr(self._nav_toolbar, method)()

        _NAV_BTNS = [
            ("⌂  還原視圖", "#1e3a5f", "#2a5a8a", _nav("home")),
            ("←  退回",    "#1e2e1e", "#2e4a2e", _nav("back")),
            ("前進  →",    "#1e2e1e", "#2e4a2e", _nav("forward")),
            ("✥  平移移動", "#2a1e4a", "#4a2e7a", _nav("pan")),
            ("⊕  框選縮放", "#1a3a2a", "#2a5a3a", _nav("zoom")),
            ("💾  儲存圖片", "#4a1e1e", "#7a2e2e", _nav("save_figure")),
        ]
        for text, fg, hov, cmd in _NAV_BTNS:
            ctk.CTkButton(nav_bar, text=text, width=90, height=28,
                          font=("Microsoft JhengHei", 10, "bold"),
                          fg_color=fg, hover_color=hov,
                          command=cmd).pack(side="left", padx=3, pady=4)

        # 點擊、拖曳、滾輪事件
        self._mpl_canvas.mpl_connect("button_press_event",   self._on_canvas_click)
        self._mpl_canvas.mpl_connect("button_release_event", self._on_mouse_release)
        self._mpl_canvas.mpl_connect("motion_notify_event",  self._on_mouse_motion)
        self._mpl_canvas.mpl_connect("scroll_event",         self._on_scroll)

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

    def _render(self, preserve_view: bool = False):
        if self._state is None or not self._state.nodes:
            self.clear()
            return
        xlim = self._ax.get_xlim() if preserve_view else None
        ylim = self._ax.get_ylim() if preserve_view else None
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
        if preserve_view and xlim is not None:
            self._ax.set_xlim(xlim)
            self._ax.set_ylim(ylim)
        self._mpl_canvas.draw()
        self._update_stats()

    def _draw_network(self):
        """地址關係圖：節點 = 地址，邊 = 聚合交易。"""
        G = self._state.G
        if not G.nodes:
            return

        g_nodes = set(G.nodes)
        if not self._pos_network:
            self._pos_network = nx.spring_layout(G, k=2.5, iterations=50, seed=42)
        else:
            new_nodes = g_nodes - set(self._pos_network)
            if new_nodes:
                # 有新增節點：固定現有節點座標，只計算新節點位置
                fixed_pos = {n: self._pos_network[n] for n in set(self._pos_network) & g_nodes}
                full_pos  = nx.spring_layout(
                    G, k=2.5, iterations=50, seed=42,
                    pos=fixed_pos,
                    fixed=list(fixed_pos.keys()) or None,
                )
                self._pos_network = full_pos
            # 清理已移除節點的座標快取
            for n in set(self._pos_network) - g_nodes:
                del self._pos_network[n]

        colors  = [G.nodes[n].get("color", "#AAAAAA") for n in G.nodes]
        labels  = {n: G.nodes[n].get("display_label", n[:8]) for n in G.nodes}

        # 邊寬度依交易數量縮放
        edge_widths = []
        for u, v, d in G.edges(data=True):
            edge_widths.append(max(0.5, min(4.0, d.get("tx_count", 1) * 0.4)))

        # 複選高亮：先畫較大的白色圓環（當作外框）
        sel_in_g = [n for n in self._selected_nodes if n in G.nodes]
        if sel_in_g:
            nx.draw_networkx_nodes(G, self._pos_network, nodelist=sel_in_g,
                                   ax=self._ax, node_color="#ffffff",
                                   node_size=900, alpha=0.9)

        nx.draw_networkx_nodes(G, self._pos_network, ax=self._ax,
                               node_color=colors, node_size=600, alpha=0.92)
        nx.draw_networkx_labels(G, self._pos_network, labels=labels, ax=self._ax,
                                font_size=7, font_color=TEXT_COL,
                                font_family="Microsoft JhengHei")

        # 涉案標記次標籤：持有人角色 / 標記說明 / 備註 — 垂直置中疊排，固定像素間距
        if self._state:
            _CHIPS = [
                ("holder_role", "#ffcc55", lambda v: not v or v in ("不明", "unknown")),
                ("case_label",  "#88ccff", lambda v: not v),
                ("case_notes",  "#aaaaaa", lambda v: not v),
            ]
            _FIRST_PTS = 18   # 節點中心到第一行頂部（points，固定，不隨縮放變化）
            _LINE_PTS  = 13   # 每行之間的固定間距（points）

            for n in G.nodes:
                ni = self._state.nodes.get(n)
                if not ni or n not in self._pos_network:
                    continue

                chips = []
                for attr, color, skip in _CHIPS:
                    val = getattr(ni, attr, "")
                    if not skip(val):
                        chips.append((val, color))

                if not chips:
                    continue

                nx_, ny_ = self._pos_network[n]

                for i, (text, color) in enumerate(chips):
                    # offset_copy：以資料座標 (nx_, ny_) 為錨點，向下平移固定 points
                    trans = mtransforms.offset_copy(
                        self._ax.transData, fig=self._fig,
                        x=0, y=-(_FIRST_PTS + i * _LINE_PTS),
                        units="points",
                    )
                    self._ax.text(
                        nx_, ny_, text,
                        transform=trans,
                        fontsize=6, color=color,
                        ha="center", va="top",
                        fontfamily="Microsoft JhengHei",
                        bbox=dict(
                            facecolor="#0d0d1a",
                            edgecolor=color,
                            linewidth=0.5,
                            alpha=0.90,
                            pad=2.0,
                            boxstyle="round,pad=0.25",
                        ),
                        clip_on=True,
                        zorder=4,
                    )

        # 邊：直線（可能經過使用者新增的折點），取代原本固定弧度的 nx.draw_networkx_edges
        self._wp_hit_list = []
        for (u, v, d), lw in zip(G.edges(data=True), edge_widths):
            if u not in self._pos_network or v not in self._pos_network:
                continue
            key = (u, v)
            wpts = self._edge_waypoints.get(key, [])
            verts = [self._pos_network[u], *wpts, self._pos_network[v]]
            arrow = mpatches.FancyArrowPatch(
                path=Path(verts), arrowstyle="-|>", mutation_scale=15,
                color="#4a6fa5", linewidth=lw,
                shrinkA=18, shrinkB=18, zorder=1,
            )
            self._ax.add_patch(arrow)
            for i, (wx, wy) in enumerate(wpts):
                self._ax.plot(wx, wy, marker="D", markersize=5,
                              color="#f5a623", markeredgecolor="#1a1a2e",
                              markeredgewidth=0.8, zorder=2)
                self._wp_hit_list.append((key, i, (wx, wy)))

        # 邊標籤：每筆交易固定2行（時間＋數量＋幣種 / tx hash），固定像素間距
        _EL_H   = 11   # 每行高度（points，不隨縮放改變）
        _EL_MAX = 5    # 每條邊最多顯示幾筆，超過則補截斷說明

        # 按 (source, target) 分組 _state.edges
        _edge_grp: dict[tuple, list] = {}
        for _ei in self._state.edges:
            _k = (_ei.source, _ei.target)
            if _k not in _edge_grp:
                _edge_grp[_k] = []
            _edge_grp[_k].append(_ei)

        for (_eu, _ev), _txs in _edge_grp.items():
            if _eu not in self._pos_network or _ev not in self._pos_network:
                continue
            # 邊中點：沿折線路徑（含折點）以弧長置中，而非單純頭尾幾何中點
            _mx, _my = self._polyline_midpoint(
                [self._pos_network[_eu], *self._edge_waypoints.get((_eu, _ev), []),
                 self._pos_network[_ev]])

            _show  = _txs[:_EL_MAX]
            _extra = len(_txs) - len(_show)

            # 總行數：每筆 2 行，截斷提示再 +1
            _n_lines = len(_show) * 2 + (1 if _extra else 0)

            # 最上行 y offset，使整個標籤塊垂直置中於邊中點
            _y0  = (_n_lines - 1) * _EL_H / 2.0
            _row = 0

            for _tx in _show:
                # 行 1：時間  數量 幣種
                _ts    = (_tx.tx_time or "").strip()
                _line1 = f"{_ts}  {_tx.amount_display}" if _ts else _tx.amount_display

                _tr1 = mtransforms.offset_copy(
                    self._ax.transData, fig=self._fig,
                    x=0, y=_y0 - _row * _EL_H, units="points")
                self._ax.text(
                    _mx, _my, _line1,
                    transform=_tr1, fontsize=6, color="#cccccc",
                    ha="center", va="center",
                    fontfamily="Microsoft JhengHei",
                    bbox=dict(facecolor="#0d0d1a", edgecolor="#4a6fa5",
                              linewidth=0.4, alpha=0.88, pad=1.5,
                              boxstyle="round,pad=0.2"),
                    clip_on=True, zorder=3)
                _row += 1

                # 行 2：完整 tx hash
                _tr2 = mtransforms.offset_copy(
                    self._ax.transData, fig=self._fig,
                    x=0, y=_y0 - _row * _EL_H, units="points")
                self._ax.text(
                    _mx, _my, _tx.tx_hash or "—",
                    transform=_tr2, fontsize=5, color="#7a9cc0",
                    ha="center", va="center",
                    fontfamily="Consolas",
                    bbox=dict(facecolor="#0d0d1a", edgecolor="none",
                              alpha=0.82, pad=1.0,
                              boxstyle="square,pad=0.15"),
                    clip_on=True, zorder=3)
                _row += 1

            if _extra:
                _trx = mtransforms.offset_copy(
                    self._ax.transData, fig=self._fig,
                    x=0, y=_y0 - _row * _EL_H, units="points")
                self._ax.text(
                    _mx, _my, f"…另 {_extra} 筆",
                    transform=_trx, fontsize=5, color="#888888",
                    ha="center", va="center",
                    fontfamily="Microsoft JhengHei",
                    bbox=dict(facecolor="#0d0d1a", edgecolor="none",
                              alpha=0.80, pad=1.0,
                              boxstyle="square,pad=0.15"),
                    clip_on=True, zorder=3)

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

    def _on_scroll(self, event):
        """滾輪縮放：以游標位置為中心放大／縮小。"""
        if event.inaxes != self._ax or event.xdata is None:
            return
        cx, cy = event.xdata, event.ydata
        # 每一格滾輪縮放 15%；step > 0 為向上（放大），< 0 為向下（縮小）
        factor = 0.85 ** event.step
        xl = self._ax.get_xlim()
        yl = self._ax.get_ylim()
        self._ax.set_xlim([cx - (cx - xl[0]) * factor,
                           cx + (xl[1] - cx) * factor])
        self._ax.set_ylim([cy - (cy - yl[0]) * factor,
                           cy + (yl[1] - cy) * factor])
        self._mpl_canvas.draw_idle()

    # ── 節點找尋 ─────────────────────────────────────────────────────────────

    def _find_nearest_node(self, x, y):
        """回傳 (最近節點, 距離平方)；無節點時回傳 (None, inf)。"""
        if x is None or y is None or not self._pos_network:
            return None, float("inf")
        best, best_d = None, float("inf")
        for node, (nx_, ny_) in self._pos_network.items():
            d = (nx_ - x) ** 2 + (ny_ - y) ** 2
            if d < best_d:
                best_d = d
                best = node
        return best, best_d

    @staticmethod
    def _polyline_midpoint(verts: list) -> tuple:
        """回傳沿多段折線（依弧長）置中的座標點。"""
        if len(verts) == 1:
            return verts[0]
        seg_lens = [
            ((verts[i + 1][0] - verts[i][0]) ** 2
             + (verts[i + 1][1] - verts[i][1]) ** 2) ** 0.5
            for i in range(len(verts) - 1)
        ]
        total = sum(seg_lens)
        if total == 0:
            return verts[0]
        target = total / 2.0
        acc = 0.0
        for i, seg_len in enumerate(seg_lens):
            if acc + seg_len >= target or i == len(seg_lens) - 1:
                t = 0.0 if seg_len == 0 else (target - acc) / seg_len
                x0, y0 = verts[i]
                x1, y1 = verts[i + 1]
                return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            acc += seg_len
        return verts[-1]

    def _find_nearest_waypoint(self, x, y):
        """回傳 (edge_key, wp_index, 距離平方)；無折點時回傳 (None, None, inf)。"""
        if x is None or y is None or not self._wp_hit_list:
            return None, None, float("inf")
        best_key, best_idx, best_d = None, None, float("inf")
        for key, idx, (wx, wy) in self._wp_hit_list:
            d = (wx - x) ** 2 + (wy - y) ** 2
            if d < best_d:
                best_d = d
                best_key, best_idx = key, idx
        return best_key, best_idx, best_d

    def _find_edge_insert_point(self, x, y):
        """在所有邊的折線段中，找出離 (x,y) 最近的線段。
        回傳 (edge_key, 插入位置索引, 距離平方)；找不到時回傳 (None, None, inf)。"""
        if x is None or y is None or self._state is None:
            return None, None, float("inf")
        best_key, best_idx, best_d = None, None, float("inf")
        seen = set()
        for _ei in self._state.edges:
            key = (_ei.source, _ei.target)
            if key in seen:
                continue
            seen.add(key)
            u, v = key
            if u not in self._pos_network or v not in self._pos_network:
                continue
            verts = [self._pos_network[u], *self._edge_waypoints.get(key, []),
                     self._pos_network[v]]
            for i in range(len(verts) - 1):
                d = self._point_segment_dist_sq((x, y), verts[i], verts[i + 1])
                if d < best_d:
                    best_d = d
                    best_key, best_idx = key, i
        return best_key, best_idx, best_d

    @staticmethod
    def _serialize_edge_waypoints(edge_waypoints: dict) -> dict:
        """{(from,to): [(x,y),...]} → {"from→to": [[x,y],...]}（JSON 物件鍵須為字串）。"""
        return {f"{u}→{v}": [list(map(float, p)) for p in pts]
                for (u, v), pts in edge_waypoints.items()}

    @staticmethod
    def _deserialize_edge_waypoints(data: dict) -> dict:
        """{"from→to": [[x,y],...]} → {(from,to): [(x,y),...]}。"""
        result = {}
        for k, v in (data or {}).items():
            parts = k.split("→")
            if len(parts) != 2:
                continue
            result[(parts[0], parts[1])] = [tuple(p) for p in v]
        return result

    @staticmethod
    def _point_segment_dist_sq(p, a, b) -> float:
        """點 p 到線段 a-b 的最短距離平方。"""
        px, py = p
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return (px - ax) ** 2 + (py - ay) ** 2
        t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        cx, cy = ax + t * dx, ay + t * dy
        return (px - cx) ** 2 + (py - cy) ** 2

    # ── 滑鼠事件 ─────────────────────────────────────────────────────────────

    def _node_click_thresh(self) -> float:
        """動態命中閾值（距離平方）：約等於視圖短邊的 4% 作為節點半徑。"""
        xl = self._ax.get_xlim()
        yl = self._ax.get_ylim()
        r = 0.04 * min(xl[1] - xl[0], yl[1] - yl[0])
        return r * r

    def _on_canvas_click(self, event):
        if event.inaxes != self._ax or self._view_mode.get() != VIEW_NETWORK:
            return
        if not self._pos_network or self._state is None:
            return

        nearest, dist = self._find_nearest_node(event.xdata, event.ydata)
        on_node = nearest is not None and dist < self._node_click_thresh()

        wp_key, wp_idx, wp_dist = self._find_nearest_waypoint(event.xdata, event.ydata)
        on_waypoint = (not on_node and wp_key is not None
                       and wp_dist < self._node_click_thresh())

        # 右鍵：選單（節點 > 折點 > 邊線新增折點）
        if event.button == 3:
            if on_node:
                self._show_node_menu(nearest)
            elif on_waypoint:
                self._show_waypoint_menu(wp_key, wp_idx)
            else:
                edge_key, insert_idx, edge_dist = self._find_edge_insert_point(
                    event.xdata, event.ydata)
                if edge_key is not None and edge_dist < self._node_click_thresh():
                    self._show_add_waypoint_menu(
                        edge_key, insert_idx, (event.xdata, event.ydata))
            return

        if event.button != 1:
            return

        # 左鍵雙擊：選單
        if event.dblclick and on_node:
            self._show_node_menu(nearest)
            return

        shift = "shift" in (event.key or "")

        if on_node:
            if shift:
                # Shift 複選：切換
                if nearest in self._selected_nodes:
                    self._selected_nodes.discard(nearest)
                else:
                    self._selected_nodes.add(nearest)
            else:
                # 若點擊已選節點，保留現有複選（方便群組拖曳）
                if nearest not in self._selected_nodes:
                    self._selected_nodes = {nearest}

            self._on_node_selected(nearest, dblclick=False)

            # 準備拖曳
            self._drag_node      = nearest
            self._drag_press_xy  = (event.xdata, event.ydata)
            self._drag_start_pos = {k: (v[0], v[1]) for k, v in self._pos_network.items()}
            self._is_dragging    = False
            self._draw_network_fast()   # 立即顯示選取高亮
        elif on_waypoint:
            # 準備拖曳折點（單點拖曳，直接以滑鼠座標更新，不需 delta 運算）
            self._drag_waypoint       = (wp_key, wp_idx)
            self._drag_waypoint_press = False
        else:
            # 點擊空白區域：取消選取
            if not shift:
                self._selected_nodes.clear()
                self._selected_node = None
                self._sel_lbl.configure(text="")
            self._drag_node     = None
            self._drag_press_xy = None
            self._draw_network_fast()

    def _on_mouse_motion(self, event):
        """拖曳移動：更新節點/折點座標並快速重繪。"""
        if (self._drag_waypoint is not None and event.inaxes == self._ax
                and event.xdata is not None and event.ydata is not None):
            self._drag_waypoint_press = True
            key, idx = self._drag_waypoint
            wpts = self._edge_waypoints.get(key)
            if wpts and 0 <= idx < len(wpts):
                wpts[idx] = (event.xdata, event.ydata)
                self._draw_network_fast()
            return

        if (self._drag_node is None or event.inaxes != self._ax
                or event.xdata is None or event.ydata is None):
            return

        dx = event.xdata - self._drag_press_xy[0]
        dy = event.ydata - self._drag_press_xy[1]

        # 超過最小移動量才啟動拖曳（避免誤觸）
        if not self._is_dragging and dx * dx + dy * dy < 1e-6:
            return
        self._is_dragging = True

        # 拖曳已選節點群組（若拖曳節點不在選取集合內，僅移動該節點）
        to_move = (self._selected_nodes
                   if self._drag_node in self._selected_nodes
                   else {self._drag_node})

        for n in to_move:
            if n in self._drag_start_pos:
                ox, oy = self._drag_start_pos[n]
                self._pos_network[n] = (ox + dx, oy + dy)

        self._draw_network_fast()

    def _on_mouse_release(self, event):
        """任意按鍵放開：清除拖曳狀態；左鍵拖曳結束時完整重繪並保留視圖。"""
        was_left_drag = (event.button == 1 and self._is_dragging)
        was_wp_drag   = (event.button == 1 and self._drag_waypoint is not None
                         and self._drag_waypoint_press)
        self._drag_node      = None
        self._drag_press_xy  = None
        self._drag_start_pos = {}
        self._is_dragging    = False
        self._drag_waypoint       = None
        self._drag_waypoint_press = False
        if was_left_drag or was_wp_drag:
            self._render(preserve_view=True)

    def _draw_network_fast(self):
        """拖曳時的輕量重繪：保留視圖範圍，不呼叫 tight_layout。"""
        if self._state is None:
            return
        xlim = self._ax.get_xlim()
        ylim = self._ax.get_ylim()
        self._ax.clear()
        self._ax.set_facecolor(BG_DARK)
        self._ax.axis("off")
        self._draw_network()
        self._ax.set_xlim(xlim)
        self._ax.set_ylim(ylim)
        self._mpl_canvas.draw_idle()

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

        from database import db as _db
        _case_id = getattr(self, "_current_case_id", None)
        if _case_id:
            _ca_list = _db.get_case_addresses(_case_id)
            _ca_row  = next(
                (r for r in _ca_list
                 if r.get("address", "").lower() == address.lower()),
                None,
            )
        else:
            _ca_row = None
        if _ca_row:
            menu.add_command(
                label="📝 編輯涉案紀錄",
                command=lambda r=_ca_row: self._open_case_address_dialog(address, r))
        else:
            menu.add_command(
                label="➕ 加入涉案錢包",
                command=lambda: self._open_case_address_dialog(address, None))
        menu.add_separator()
        menu.add_command(label="📋 複製地址",
                         command=lambda: self._copy_to_clipboard(address))

        try:
            px, py = self.winfo_pointerxy()
            menu.tk_popup(px, py)
        finally:
            menu.grab_release()

    def _show_add_waypoint_menu(self, edge_key: tuple, insert_idx: int, xy: tuple):
        """在連接線上按右鍵：提供「新增折點」選單。"""
        menu = tk.Menu(self, tearoff=0, bg="#2a2a3e", fg=TEXT_COL,
                       activebackground="#3a3a5e", activeforeground="white",
                       font=("Microsoft JhengHei", 10))
        menu.add_command(
            label="➕ 在此新增折點",
            command=lambda: self._add_waypoint(edge_key, insert_idx, xy))
        try:
            px, py = self.winfo_pointerxy()
            menu.tk_popup(px, py)
        finally:
            menu.grab_release()

    def _add_waypoint(self, edge_key: tuple, insert_idx: int, xy: tuple):
        self._edge_waypoints.setdefault(edge_key, []).insert(insert_idx, xy)
        self._render(preserve_view=True)

    def _show_waypoint_menu(self, edge_key: tuple, wp_idx: int):
        """在既有折點上按右鍵：提供「刪除折點」選單。"""
        menu = tk.Menu(self, tearoff=0, bg="#2a2a3e", fg=TEXT_COL,
                       activebackground="#3a3a5e", activeforeground="white",
                       font=("Microsoft JhengHei", 10))
        menu.add_command(
            label="🗑 刪除此折點",
            command=lambda: self._delete_waypoint(edge_key, wp_idx))
        try:
            px, py = self.winfo_pointerxy()
            menu.tk_popup(px, py)
        finally:
            menu.grab_release()

    def _delete_waypoint(self, edge_key: tuple, wp_idx: int):
        wpts = self._edge_waypoints.get(edge_key)
        if wpts and 0 <= wp_idx < len(wpts):
            wpts.pop(wp_idx)
            if not wpts:
                del self._edge_waypoints[edge_key]
            self._render(preserve_view=True)

    def _open_case_address_dialog(self, address: str, row: dict | None):
        """開啟涉案地址對話框（編輯或新增），儲存後同步更新圖上的節點標記。"""
        from database import db as _db
        from gui.case_address_panel import AddressDialog
        case_id = getattr(self, "_current_case_id", None)
        if not case_id:
            messagebox.showwarning("未開啟案件", "請先在案件分頁中開啟案件後再操作。", parent=self)
            return
        chain = self._state.chain if self._state else "ETH"

        def _on_save():
            updated = _db.get_case_addresses(case_id)
            for ca in updated:
                if ca.get("address", "").lower() == address.lower():
                    if self._state and address in self._state.nodes:
                        n = self._state.nodes[address]
                        n.holder_role = ca.get("holder_role") or ""
                        n.case_label  = ca.get("label")       or ""
                        n.case_notes  = ca.get("notes")        or ""
                    break
            self._render(preserve_view=True)

        if row:
            AddressDialog(self, case_id=case_id, row=row, on_save=_on_save)
        else:
            prefill = {"address": address, "addr_type": "加密錢包",
                       "chain_institution": chain}
            AddressDialog(self, case_id=case_id, prefill=prefill, on_save=_on_save)

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
            self._render()

    def _on_view_change(self, _=None):
        self._render()

    # ── 工具列操作 ────────────────────────────────────────────────────────────

    def _relayout(self):
        self._pos_network = {}
        self._pos_maltego = {}
        self._edge_waypoints = {}
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
        """將目前幣流圖（含佈局座標）存入案件資料。有舊快照則覆蓋，無則新增。"""
        if not self._state or not self._state.nodes:
            messagebox.showinfo("提示", "尚無幣流圖資料。", parent=self)
            return
        case_id = getattr(self, "_current_case_id", None)
        if not case_id:
            messagebox.showwarning(
                "未開啟案件",
                "請先在步驟 3 選擇或建立案件後再儲存幣流圖。",
                parent=self)
            return

        from database import db as _db
        nodes = [
            {"address": n.address, "chain": n.chain, "role": n.role,
             "custom_label": n.custom_label, "known_label": n.known_label,
             "color": n.color, "expanded": n.expanded,
             "holder_role": n.holder_role, "case_label": n.case_label,
             "case_notes": n.case_notes}
            for n in self._state.nodes.values()
        ]
        _, edges = self._state.to_snapshot()
        pos_net = {k: list(map(float, v)) for k, v in self._pos_network.items()}
        pos_mal = {k: list(map(float, v)) for k, v in self._pos_maltego.items()}
        edge_wp = self._serialize_edge_waypoints(self._edge_waypoints)

        existing = _db.get_graph_snapshots(case_id)
        if existing:
            _db.update_graph_snapshot(
                existing[0]["id"], nodes, edges,
                pos_network=pos_net, pos_maltego=pos_mal, edge_waypoints=edge_wp,
                chain=self._state.chain,
            )
        else:
            _db.save_graph_snapshot(
                case_id, self._state.chain, nodes, edges,
                pos_network=pos_net, pos_maltego=pos_mal, edge_waypoints=edge_wp,
            )
        messagebox.showinfo("已儲存", "幣流圖已更新至案件資料。", parent=self)

    def _export_image(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            title="匯出幣流圖",
            defaultextension=".png",
            filetypes=[("PNG 圖片", "*.png"), ("PDF 文件", "*.pdf"),
                       ("SVG 向量圖", "*.svg"), ("所有檔案", "*.*")])
        if not path:
            return
        try:
            is_pdf = path.lower().endswith(".pdf")
            self._fig.savefig(
                path,
                dpi=150,
                bbox_inches="tight",
                facecolor="white" if is_pdf else BG_DARK,
            )
            messagebox.showinfo("匯出完成", f"圖片已儲存至：\n{path}", parent=self)
        except Exception as e:
            messagebox.showerror("匯出失敗", str(e), parent=self)

    def _save_graph_json(self):
        """儲存幣流圖完整狀態（節點、交易、佈局位置）至 JSON 檔案。"""
        if not self._state or not self._state.nodes:
            messagebox.showinfo("提示", "尚無幣流圖資料。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="存檔幣流圖",
            defaultextension=".json",
            filetypes=[("幣流圖檔案", "*.json"), ("所有檔案", "*.*")],
        )
        if not path:
            return
        # 含涉案標記三欄位（to_snapshot 未包含）
        nodes = [
            {"address": n.address, "chain": n.chain, "role": n.role,
             "custom_label": n.custom_label, "known_label": n.known_label,
             "color": n.color, "expanded": n.expanded,
             "holder_role": n.holder_role, "case_label": n.case_label,
             "case_notes": n.case_notes}
            for n in self._state.nodes.values()
        ]
        _, edges = self._state.to_snapshot()
        data = {
            "version": 1,
            "chain": self._state.chain,
            "mode":  self._state.mode,
            "nodes": nodes,
            "edges": edges,
            "pos_network": {k: list(map(float, v))
                            for k, v in self._pos_network.items()},
            "pos_maltego": {k: list(map(float, v))
                            for k, v in self._pos_maltego.items()},
            "edge_waypoints": self._serialize_edge_waypoints(self._edge_waypoints),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("存檔完成", f"幣流圖已儲存至：\n{path}", parent=self)
        except Exception as e:
            messagebox.showerror("存檔失敗", str(e), parent=self)

    def _load_graph_json(self):
        """從 JSON 檔案還原幣流圖（節點、交易、佈局位置）。"""
        path = filedialog.askopenfilename(
            parent=self,
            title="讀取幣流圖",
            filetypes=[("幣流圖檔案", "*.json"), ("所有檔案", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("讀檔失敗", str(e), parent=self)
            return
        chain = data.get("chain", "ETH")
        mode  = data.get("mode",  "explore")
        self._state = GraphState.from_snapshot(
            data.get("nodes", []),
            data.get("edges", []),
            chain=chain, mode=mode,
        )
        self._pos_network = {k: tuple(v)
                             for k, v in data.get("pos_network", {}).items()}
        self._pos_maltego = {k: tuple(v)
                             for k, v in data.get("pos_maltego", {}).items()}
        self._edge_waypoints = self._deserialize_edge_waypoints(
            data.get("edge_waypoints", {}))
        self._gen_mode.set(mode)
        self._update_mode_label()
        self._render()

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
        """由主視窗在切換案件時傳入；case_id 改變時自動從快照還原；None 代表清除。"""
        if case_id == getattr(self, "_current_case_id", None):
            return   # 同一案件（如加入交易後再呼叫），不重新載入
        self._current_case_id = case_id
        if case_id is not None:
            self.load_from_case(case_id)

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

        if not self._pos_maltego or set(self._pos_maltego) != set(G.nodes):
            self._pos_maltego = self._maltego_layout(G)
        pos = self._pos_maltego

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
                self._pos_network = {}
                self._pos_maltego = {}
                self._edge_waypoints = {}
            node = self._state.add_node(addr, role=role_var.get(),
                                        custom_label=e_label.get().strip())
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

        was_fresh = (self._state is None)
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

        if was_fresh:
            self._pos_network = {}
            self._pos_maltego = {}
            self._edge_waypoints = {}
        self._view_mode.set(VIEW_MALTEGO)
        self._render()

        msg = f"成功匯入 {imported} 筆交易，共 {len(self._state.nodes)} 個節點。"
        if skipped:
            msg += f"\n\n略過 {len(skipped)} 筆：\n" + "\n".join(skipped[:5])
            if len(skipped) > 5:
                msg += f"\n…（共 {len(skipped)} 筆錯誤）"
        messagebox.showinfo("匯入完成", msg, parent=self)
