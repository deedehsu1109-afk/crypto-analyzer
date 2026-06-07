from __future__ import annotations
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk

from config import load_config, save_config
from api.etherscan import EtherscanAPI
from api.tronscan import TronScanAPI
from api.bitcoin import BitcoinAPI
from analyzer.wallet_profiler import profile_eth, profile_trx, profile_btc
from analyzer.tx_analyzer import analyze_eth_tx, analyze_trx_tx, analyze_btc_tx
from analyzer.time_filter import (parse_datetime_str, ts_to_str,
                                   filter_by_range, filter_centered,
                                   check_overflow, suggest_increase, MAX_TOTAL)
from exporter.report import export_excel, export_csv
from database import db as _db
from gui.case_window import CaseDialog, LinkToCaseDialog
from gui.victim_tx_panel import VictimTxPanel
from gui.flow_graph_panel import FlowGraphPanel

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("虛擬貨幣錢包分析工具 v1.0")
        self.geometry("1200x760")
        self.resizable(True, True)
        self.config_data  = load_config()
        self._profile: dict | None = None
        self._active_case: dict | None = None   # 目前選中的案件
        self._build_ui()

    # ── UI 建構 ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── 第一行：查詢模式 + 輸入 + 操作 ──
        top = ctk.CTkFrame(self, corner_radius=10)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top.grid_columnconfigure(4, weight=1)  # addr_entry 佔滿

        # 查詢模式切換
        self._query_mode = ctk.StringVar(value="一般查詢")
        mode_btn = ctk.CTkSegmentedButton(
            top, values=["一般查詢", "專案查詢"],
            variable=self._query_mode,
            width=190,
            font=("Microsoft JhengHei", 12),
            command=self._on_mode_change)
        mode_btn.grid(row=0, column=0, padx=(12, 6), pady=10)

        ctk.CTkLabel(top, text="鏈：", font=("Microsoft JhengHei", 12)).grid(
            row=0, column=1, padx=(4, 2), pady=10)
        self.chain_var = ctk.StringVar(value="ETH")
        ctk.CTkOptionMenu(top, variable=self.chain_var,
                          values=["ETH", "TRX", "BTC"], width=80,
                          font=("Microsoft JhengHei", 11),
                          command=self._on_chain_change).grid(
            row=0, column=2, padx=2, pady=10)

        ctk.CTkLabel(top, text="錢包地址 / Hash：",
                     font=("Microsoft JhengHei", 12)).grid(
            row=0, column=3, padx=(8, 4), pady=10)
        self.addr_entry = ctk.CTkEntry(
            top, placeholder_text="輸入錢包地址或交易 Hash（自動偵測類型）",
            font=("Consolas", 11))
        self.addr_entry.grid(row=0, column=4, padx=4, pady=10, sticky="ew")
        self.addr_entry.bind("<FocusOut>", self._on_addr_focusout)
        self.addr_entry.bind("<KeyRelease>", self._on_addr_keyrelease)
        self._bind_entry_context_menu(self.addr_entry)

        self.analyze_btn = ctk.CTkButton(
            top, text="開始查詢", width=100,
            font=("Microsoft JhengHei", 12, "bold"),
            command=self._start_smart_query)
        self.analyze_btn.grid(row=0, column=5, padx=(6, 2), pady=10)

        self._clear_btn = ctk.CTkButton(
            top, text="清除結果", width=85,
            font=("Microsoft JhengHei", 11),
            fg_color="#6b3a1f",
            command=self._clear_results)
        self._clear_btn.grid(row=0, column=6, padx=2, pady=10)

        self.export_excel_btn = ctk.CTkButton(
            top, text="匯出 Excel", width=95,
            font=("Microsoft JhengHei", 11),
            fg_color="#2d6a4f",
            command=self._export_excel)
        self.export_excel_btn.grid(row=0, column=7, padx=2, pady=10)

        self.export_csv_btn = ctk.CTkButton(
            top, text="匯出 CSV", width=85,
            font=("Microsoft JhengHei", 11),
            fg_color="#5e3a8a",
            command=self._export_csv)
        self.export_csv_btn.grid(row=0, column=8, padx=2, pady=10)

        self.flow_btn = ctk.CTkButton(
            top, text="加入幣流圖", width=100,
            font=("Microsoft JhengHei", 11),
            fg_color="#4a2d6a",
            command=self._add_to_flow_graph)
        self.flow_btn.grid(row=0, column=9, padx=2, pady=10)

        self.settings_btn = ctk.CTkButton(
            top, text="⚙ 設定", width=70,
            font=("Microsoft JhengHei", 11),
            fg_color="gray35",
            command=self._open_settings)
        self.settings_btn.grid(row=0, column=10, padx=(2, 12), pady=10)

        # ── 第二行：案件工具列（專案查詢模式下顯示）──
        self._case_bar = ctk.CTkFrame(top, corner_radius=6, fg_color="#1a2744")
        self._case_bar.grid(row=1, column=0, columnspan=10, sticky="ew",
                            padx=12, pady=(0, 6))
        self._case_bar.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(self._case_bar, text="目前案件：",
                     font=("Microsoft JhengHei", 12, "bold"),
                     text_color="#aac4ff").grid(
            row=0, column=0, padx=(12, 4), pady=5)

        self._case_label = ctk.CTkLabel(
            self._case_bar, text="（尚未選擇案件，專案查詢需先選擇案件）",
            font=("Microsoft JhengHei", 12),
            text_color="#f5a623", anchor="w")
        self._case_label.grid(row=0, column=1, padx=4, pady=5, sticky="w")

        ctk.CTkButton(self._case_bar, text="選擇 / 切換案件", width=130,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#2a4a8a",
                      command=self._pick_case).grid(
            row=0, column=3, padx=4, pady=5)

        ctk.CTkButton(self._case_bar, text="＋ 新建案件", width=110,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#1d6b3e",
                      command=self._new_case_quick).grid(
            row=0, column=4, padx=4, pady=5)

        ctk.CTkButton(self._case_bar, text="清除案件", width=80,
                      font=("Microsoft JhengHei", 11),
                      fg_color="gray35",
                      command=self._clear_case).grid(
            row=0, column=5, padx=(4, 12), pady=5)

        # 預設隱藏案件列（一般查詢模式）
        self._case_bar.grid_remove()

        # ── 時間篩選列（第三行，可折疊）──
        self._time_bar_visible = False
        self._time_toggle_btn = ctk.CTkButton(
            top, text="⏱ 時間篩選 ▼", width=120,
            font=("Microsoft JhengHei", 11),
            fg_color="#3a3a5a",
            command=self._toggle_time_bar)
        self._time_toggle_btn.grid(row=2, column=0, padx=(12, 4), pady=(0, 6), sticky="w")

        self._time_bar = ctk.CTkFrame(top, corner_radius=6, fg_color="#1e1e2e")
        self._time_bar.grid(row=3, column=0, columnspan=10, sticky="ew",
                            padx=12, pady=(0, 6))

        # 起始時間
        ctk.CTkLabel(self._time_bar, text="起始時間：",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#aaffaa").grid(
            row=0, column=0, padx=(12, 4), pady=6)
        self._time_start = ctk.CTkEntry(
            self._time_bar, width=160, font=("Consolas", 11),
            placeholder_text="YYYY-MM-DD HH:MM:SS")
        self._time_start.grid(row=0, column=1, padx=4, pady=6)
        self._bind_entry_context_menu(self._time_start)

        ctk.CTkLabel(self._time_bar, text="迄止時間：",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#ffccaa").grid(
            row=0, column=2, padx=(16, 4), pady=6)
        self._time_end = ctk.CTkEntry(
            self._time_bar, width=160, font=("Consolas", 11),
            placeholder_text="YYYY-MM-DD HH:MM:SS（選填）")
        self._time_end.grid(row=0, column=3, padx=4, pady=6)
        self._bind_entry_context_menu(self._time_end)

        # 置中模式下的前後各N筆
        ctk.CTkLabel(self._time_bar, text="（僅設起始時）前後各：",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray70").grid(
            row=0, column=4, padx=(16, 2), pady=6)
        self._time_each = ctk.CTkEntry(
            self._time_bar, width=55, font=("Consolas", 11))
        self._time_each.insert(0, "50")
        self._time_each.grid(row=0, column=5, padx=2, pady=6)
        ctk.CTkLabel(self._time_bar, text="筆",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray70").grid(row=0, column=6, padx=(2, 8), pady=6)

        # 模式說明標籤
        self._time_mode_lbl = ctk.CTkLabel(
            self._time_bar, text="尚未設定時間",
            font=("Microsoft JhengHei", 10), text_color="gray60")
        self._time_mode_lbl.grid(row=0, column=7, padx=8, pady=6, sticky="w")
        self._time_bar.grid_columnconfigure(7, weight=1)

        # 清除時間設定按鈕
        ctk.CTkButton(self._time_bar, text="清除時間", width=80,
                      font=("Microsoft JhengHei", 10),
                      fg_color="gray35",
                      command=self._clear_time_filter).grid(
            row=0, column=8, padx=(4, 12), pady=6)

        # 綁定即時更新
        self._time_start.bind("<KeyRelease>", self._on_time_change)
        self._time_end.bind("<KeyRelease>", self._on_time_change)
        self._time_each.bind("<KeyRelease>", self._on_time_change)

        # 預設隱藏
        self._time_bar.grid_remove()

        # 主內容（分頁）
        self.tabs = ctk.CTkTabview(self, corner_radius=10)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 4))
        for name in ["錢包摘要", "授權對象", "原始交易", "Token 轉帳",
                     "幣流關聯圖", "交易 Hash 分析", "查詢歷史", "案件管理"]:
            self.tabs.add(name)

        self._build_summary_tab()
        self._build_approvals_tab()
        self._build_tx_tab("原始交易", "_tx_tree")
        self._build_tx_tab("Token 轉帳", "_token_tree")
        self._build_flow_tab()
        self._build_hash_tab()
        self._build_history_tab()
        self._build_case_tab()

        # 底部狀態列
        self.status_var = tk.StringVar(value="就緒")
        status_bar = ctk.CTkLabel(self, textvariable=self.status_var,
                                  anchor="w", font=("Microsoft JhengHei", 11))
        status_bar.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))

        self.progress = ctk.CTkProgressBar(self, mode="indeterminate", height=6)
        self.progress.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.progress.stop()
        self.progress.grid_remove()

    def _build_summary_tab(self):
        tab = self.tabs.tab("錢包摘要")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        frame = ctk.CTkScrollableFrame(tab, corner_radius=8)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_columnconfigure(1, weight=1)

        self._summary_labels: list[tuple[ctk.CTkLabel, ctk.CTkLabel]] = []
        fields = [
            "區塊鏈", "錢包地址", "首次交易時間", "最後交易時間",
            "首次資金來源",
            "發起交易次數（合計）", "── ETH 發起次數", "── ERC-20 發起次數",
            "發起交易總金額（ETH）", "ERC-20 發出（依 Token）",
            "接受交易次數（合計）", "── ETH 接受次數", "── ERC-20 接受次數",
            "接受交易總金額（ETH）", "ERC-20 收入（依 Token）",
            "總手續費（ETH）", "最多手續費流向",
        ]
        for i, f in enumerate(fields):
            is_sub = f.startswith("──")
            lbl = ctk.CTkLabel(frame, text=f + "：",
                               font=("Microsoft JhengHei", 11 if is_sub else 12,
                                     "normal" if is_sub else "bold"),
                               text_color=("gray60" if is_sub else None),
                               anchor="e", width=200)
            lbl.grid(row=i, column=0, padx=(12, 6), pady=3 if is_sub else 5, sticky="e")
            val = ctk.CTkLabel(frame, text="—",
                               font=("Consolas", 11 if is_sub else 12), anchor="w",
                               wraplength=600)
            val.grid(row=i, column=1, padx=(4, 12), pady=3 if is_sub else 5, sticky="w")
            self._bind_label_copy_menu(val)
            self._summary_labels.append((lbl, val))

        # 時間篩選說明列
        self._tf_info_lbl = ctk.CTkLabel(
            frame, text="",
            font=("Microsoft JhengHei", 11), anchor="w",
            text_color="#ffcc55", wraplength=700)
        self._tf_info_lbl.grid(
            row=len(fields), column=0, columnspan=2,
            padx=12, pady=(4, 8), sticky="w")

    def _build_approvals_tab(self):
        tab = self.tabs.tab("授權對象")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        frame = ctk.CTkFrame(tab, corner_radius=8)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        cols = ("合約地址", "授權對象 (Spender)", "交易 Hash / 金額", "時間")
        self._approval_tree = self._make_treeview(frame, cols)

    def _build_flow_tab(self):
        tab = self.tabs.tab("幣流關聯圖")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        self._flow_panel = FlowGraphPanel(tab)
        self._flow_panel.grid(row=0, column=0, sticky="nsew")
        self._flow_panel.set_node_click_callback(self._on_flow_node_clicked)

    def _build_hash_tab(self):
        tab = self.tabs.tab("交易 Hash 分析")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # 輸入列
        input_frame = ctk.CTkFrame(tab, corner_radius=8)
        input_frame.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 4))
        input_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(input_frame, text="交易 Hash：",
                     font=("Microsoft JhengHei", 12, "bold")).grid(
            row=0, column=0, padx=(12, 6), pady=8)
        self.hash_entry = ctk.CTkEntry(input_frame,
                                       placeholder_text="輸入交易 Hash（0x... 或 64位十六進位）",
                                       font=("Consolas", 11))
        self.hash_entry.grid(row=0, column=1, padx=4, pady=8, sticky="ew")
        self._bind_entry_context_menu(self.hash_entry)
        self.hash_btn = ctk.CTkButton(input_frame, text="查詢", width=90,
                                      font=("Microsoft JhengHei", 12, "bold"),
                                      command=self._start_hash_analysis)
        self.hash_btn.grid(row=0, column=2, padx=(4, 12), pady=8)

        # 結果顯示（分上下兩區）
        result_frame = ctk.CTkFrame(tab, corner_radius=8)
        result_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        result_frame.grid_columnconfigure(0, weight=1)
        result_frame.grid_rowconfigure(1, weight=1)

        # 基本資料區（捲動）
        self._hash_detail_frame = ctk.CTkScrollableFrame(result_frame, corner_radius=0, height=260)
        self._hash_detail_frame.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        self._hash_detail_frame.grid_columnconfigure(1, weight=1)
        self._hash_detail_labels: list[tuple] = []

        # Token 轉帳清單
        token_frame = ctk.CTkFrame(result_frame, corner_radius=0)
        token_frame.grid(row=1, column=0, sticky="nsew", padx=2, pady=(0, 2))
        token_frame.grid_columnconfigure(0, weight=1)
        token_frame.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(token_frame, text="Token 轉帳明細",
                     font=("Microsoft JhengHei", 11, "bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        cols = ("Token", "從", "至", "金額", "合約")
        self._hash_token_tree = self._make_treeview(token_frame, cols)
        self._hash_token_tree.grid(row=1, column=0, sticky="nsew")

    def _build_history_tab(self):
        tab = self.tabs.tab("查詢歷史")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # 工具列
        bar = ctk.CTkFrame(tab, corner_radius=8)
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))

        ctk.CTkLabel(bar, text="顯示：", font=("Microsoft JhengHei", 12)).pack(
            side="left", padx=(12, 4), pady=8)
        self._hist_mode = ctk.StringVar(value="錢包分析")
        ctk.CTkSegmentedButton(bar, values=["錢包分析", "Hash 查詢"],
                               variable=self._hist_mode,
                               command=self._load_history).pack(
            side="left", padx=4, pady=8)

        ctk.CTkButton(bar, text="重新整理", width=90,
                      font=("Microsoft JhengHei", 11),
                      command=self._load_history).pack(side="left", padx=8)

        self._del_hist_btn = ctk.CTkButton(bar, text="刪除選取", width=90,
                                           font=("Microsoft JhengHei", 11),
                                           fg_color="#8b1a1a",
                                           command=self._delete_history_row)
        self._del_hist_btn.pack(side="left", padx=4)

        # 搜尋
        ctk.CTkLabel(bar, text="搜尋地址/Hash：",
                     font=("Microsoft JhengHei", 11)).pack(side="left", padx=(20, 4))
        self._hist_search = ctk.CTkEntry(bar, width=220, font=("Consolas", 11))
        self._hist_search.pack(side="left", padx=4)
        self._bind_entry_context_menu(self._hist_search)
        self._hist_search.bind("<Return>", lambda _: self._load_history())
        ctk.CTkButton(bar, text="搜尋", width=60,
                      font=("Microsoft JhengHei", 11),
                      command=self._load_history).pack(side="left", padx=4)

        # 表格
        frame = ctk.CTkFrame(tab, corner_radius=8)
        frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        wallet_cols = ("鏈", "地址", "發起次數", "接受次數", "Token 轉帳", "首次交易", "最後交易", "分析時間")
        self._hist_wallet_tree = self._make_treeview(frame, wallet_cols)

        hash_cols = ("鏈", "交易 Hash", "狀態", "發送方", "接收方", "金額", "手續費", "時間", "查詢時間")
        self._hist_hash_tree   = self._make_treeview(frame, hash_cols)
        self._hist_hash_tree.grid_remove()  # 預設隱藏

        self._load_history()

    def _load_history(self, _=None):
        mode   = self._hist_mode.get()
        kw     = self._hist_search.get().strip() if hasattr(self, "_hist_search") else ""

        if mode == "錢包分析":
            self._hist_hash_tree.grid_remove()
            self._hist_wallet_tree.grid(row=0, column=0, sticky="nsew")
            for iid in self._hist_wallet_tree.get_children():
                self._hist_wallet_tree.delete(iid)
            wallets = _db.get_all_wallets()
            if kw:
                wallets = [w for w in wallets if kw.lower() in w.get("address","").lower()]
            for w in wallets:
                self._hist_wallet_tree.insert("", "end", iid=str(w["id"]), values=(
                    w.get("chain",""), w.get("address",""),
                    w.get("out_count",0), w.get("in_count",0),
                    w.get("token_transfer_count",0),
                    w.get("first_tx_time",""), w.get("last_tx_time",""),
                    w.get("analyzed_at",""),
                ))
        else:
            self._hist_wallet_tree.grid_remove()
            self._hist_hash_tree.grid(row=0, column=0, sticky="nsew")
            for iid in self._hist_hash_tree.get_children():
                self._hist_hash_tree.delete(iid)
            lookups = _db.get_all_tx_lookups()
            if kw:
                lookups = [l for l in lookups
                           if kw.lower() in l.get("tx_hash","").lower()
                           or kw.lower() in l.get("from_addr","").lower()
                           or kw.lower() in l.get("to_addr","").lower()]
            for l in lookups:
                self._hist_hash_tree.insert("", "end", iid=str(l["id"]), values=(
                    l.get("chain",""), l.get("tx_hash",""),
                    l.get("status",""), l.get("from_addr",""),
                    l.get("to_addr",""), l.get("value_str",""),
                    l.get("fee_str",""), l.get("tx_time",""),
                    l.get("queried_at",""),
                ))

    def _delete_history_row(self):
        mode = self._hist_mode.get()
        tree = self._hist_wallet_tree if mode == "錢包分析" else self._hist_hash_tree
        sel  = tree.selection()
        if not sel:
            messagebox.showinfo("提示", "請先選取要刪除的列")
            return
        if not messagebox.askyesno("確認刪除", f"確定刪除選取的 {len(sel)} 筆記錄？"):
            return
        for iid in sel:
            try:
                row_id = int(iid)
                if mode == "錢包分析":
                    _db.delete_wallet(row_id)
                else:
                    _db.delete_tx_lookup(row_id)
                tree.delete(iid)
            except Exception:
                pass

    # ── 案件管理分頁 ───────────────────────────────────────────────────────────

    def _build_case_tab(self):
        tab = self.tabs.tab("案件管理")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        # 主容器：左右分割
        pane = ctk.CTkFrame(tab, corner_radius=0, fg_color="transparent")
        pane.grid(row=0, column=0, sticky="nsew")
        pane.grid_columnconfigure(0, weight=2)
        pane.grid_columnconfigure(1, weight=5)
        pane.grid_rowconfigure(0, weight=1)

        # ── 左側：案件清單 + 匯入工具 ──
        left = ctk.CTkFrame(pane, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 2), pady=4)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        # 標題 + 操作按鈕
        hdr = ctk.CTkFrame(left, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        ctk.CTkLabel(hdr, text="案件清單",
                     font=("Microsoft JhengHei", 13, "bold")).pack(side="left")
        for txt, color, cmd in [
            ("＋ 新建", "#1d6b3e", self._case_new),
            ("✎ 編輯",  "#2a4a8a", self._case_edit),
            ("🗑 刪除", "#7a1f1f", self._case_delete),
        ]:
            ctk.CTkButton(hdr, text=txt, width=68,
                          font=("Microsoft JhengHei", 10),
                          fg_color=color, command=cmd).pack(side="right", padx=1)

        # ── 匯入案件編號列 ──
        imp_bar = ctk.CTkFrame(left, fg_color="#1a2744", corner_radius=6)
        imp_bar.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
        imp_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(imp_bar, text="匯入案件編號：",
                     font=("Microsoft JhengHei", 10, "bold"),
                     text_color="#aac4ff").grid(
            row=0, column=0, padx=(8, 4), pady=5)
        self._import_num_entry = ctk.CTkEntry(
            imp_bar, font=("Consolas", 10),
            placeholder_text="輸入案件編號（如 CASE-20260603-001）")
        self._import_num_entry.grid(row=0, column=1, padx=4, pady=5, sticky="ew")
        ctk.CTkButton(imp_bar, text="匯入", width=60,
                      font=("Microsoft JhengHei", 10),
                      fg_color="#2a4a8a",
                      command=self._import_by_case_number).grid(
            row=0, column=2, padx=(4, 8), pady=5)

        # 案件表格
        lf = ctk.CTkFrame(left, corner_radius=6)
        lf.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 6))
        lf.grid_columnconfigure(0, weight=1)
        lf.grid_rowconfigure(0, weight=1)
        case_cols = ("案件編號", "案件名稱", "類型", "狀態", "承辦人")
        self._case_tree = self._make_treeview(lf, case_cols)
        for col, w in zip(case_cols, [110, 150, 60, 60, 80]):
            self._case_tree.column(col, width=w, minwidth=50)
        self._case_tree.bind("<<TreeviewSelect>>", self._on_case_select)
        self._case_tree.bind("<Double-1>",
                             lambda _: self._set_active_case_from_tree())

        # ── 右側：案件詳細（分頁式）──
        right = ctk.CTkFrame(pane, corner_radius=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(2, 4), pady=4)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        # 案件資訊列
        info_bar = ctk.CTkFrame(right, fg_color="transparent")
        info_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        info_bar.grid_columnconfigure(0, weight=1)

        self._case_info_lbl = ctk.CTkLabel(
            info_bar, text="← 點選左側案件查看詳情",
            font=("Microsoft JhengHei", 11), text_color="gray60", anchor="w")
        self._case_info_lbl.grid(row=0, column=0, sticky="w")

        op_bar = ctk.CTkFrame(info_bar, fg_color="transparent")
        op_bar.grid(row=1, column=0, sticky="w", pady=(2, 0))
        for txt, color, cmd in [
            ("設為目前案件",   "#1d5e8a", self._set_active_case_from_tree),
            ("從文件匯入描述", "#4a3a7a", self._import_doc_to_desc),
            ("移除錢包",       "gray35",  self._case_unlink_wallet),
            ("移除 Hash",      "gray35",  self._case_unlink_hash),
        ]:
            ctk.CTkButton(op_bar, text=txt, width=110,
                          font=("Microsoft JhengHei", 10),
                          fg_color=color, command=cmd).pack(side="left", padx=2)

        # 右側內容分頁
        right_tabs = ctk.CTkTabview(right, corner_radius=8)
        right_tabs.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 4))
        for tname in ["關聯錢包", "關聯 Hash", "被害人陳述交易紀錄"]:
            right_tabs.add(tname)
        self._right_tabs = right_tabs

        # 關聯錢包分頁
        wt = right_tabs.tab("關聯錢包")
        wt.grid_columnconfigure(0, weight=1)
        wt.grid_rowconfigure(0, weight=1)
        wf = ctk.CTkFrame(wt, corner_radius=6)
        wf.grid(row=0, column=0, sticky="nsew")
        wf.grid_columnconfigure(0, weight=1)
        wf.grid_rowconfigure(0, weight=1)
        w_cols = ("鏈", "地址", "標籤", "發起", "接受", "Token 轉帳", "分析時間")
        self._case_wallet_tree = self._make_treeview(wf, w_cols)
        for col, w in zip(w_cols, [50, 200, 80, 50, 50, 70, 130]):
            self._case_wallet_tree.column(col, width=w, minwidth=40)

        # 關聯 Hash 分頁
        ht = right_tabs.tab("關聯 Hash")
        ht.grid_columnconfigure(0, weight=1)
        ht.grid_rowconfigure(0, weight=1)
        hf = ctk.CTkFrame(ht, corner_radius=6)
        hf.grid(row=0, column=0, sticky="nsew")
        hf.grid_columnconfigure(0, weight=1)
        hf.grid_rowconfigure(0, weight=1)
        h_cols = ("鏈", "交易 Hash", "狀態", "發送方", "接收方", "金額", "查詢時間")
        self._case_hash_tree = self._make_treeview(hf, h_cols)
        for col, w in zip(h_cols, [50, 200, 60, 150, 150, 90, 130]):
            self._case_hash_tree.column(col, width=w, minwidth=40)

        # 被害人陳述交易記錄分頁（延遲建立，選到案件後初始化）
        self._victim_tx_panel: VictimTxPanel | None = None
        self._victim_tx_tab_frame = right_tabs.tab("被害人陳述交易紀錄")
        self._victim_tx_tab_frame.grid_columnconfigure(0, weight=1)
        self._victim_tx_tab_frame.grid_rowconfigure(0, weight=1)
        ctk.CTkLabel(
            self._victim_tx_tab_frame,
            text="← 請先在左側選取案件",
            font=("Microsoft JhengHei", 12), text_color="gray60"
        ).grid(row=0, column=0)

        self._selected_case_id: int | None = None
        self._reload_case_list()

    def _reload_case_list(self):
        for iid in self._case_tree.get_children():
            self._case_tree.delete(iid)
        for c in _db.get_all_cases():
            self._case_tree.insert("", "end", iid=str(c["id"]), values=(
                c.get("case_number",""), c.get("case_name",""),
                c.get("case_type",""), c.get("status",""),
                c.get("investigator",""),
            ))

    def _on_case_select(self, _event=None):
        sel = self._case_tree.selection()
        if not sel:
            return
        case_id = int(sel[0])
        self._selected_case_id = case_id
        case = _db.get_case(case_id)
        if not case:
            return
        # 更新資訊標籤
        self._case_info_lbl.configure(
            text=f"【{case['case_number']}】{case['case_name']}　"
                 f"類型：{case['case_type']}　狀態：{case['status']}　"
                 f"承辦：{case.get('investigator','—')}　"
                 f"建立：{case.get('created_at','')}"
        )
        # 關聯錢包
        for iid in self._case_wallet_tree.get_children():
            self._case_wallet_tree.delete(iid)
        for w in _db.get_case_wallets(case_id):
            self._case_wallet_tree.insert("", "end", iid=str(w["id"]), values=(
                w.get("chain",""), w.get("address",""),
                w.get("label",""), w.get("out_count",0),
                w.get("in_count",0), w.get("token_transfer_count",0),
                w.get("analyzed_at",""),
            ))
        # 關聯 Hash
        for iid in self._case_hash_tree.get_children():
            self._case_hash_tree.delete(iid)
        for h in _db.get_case_tx_lookups(case_id):
            self._case_hash_tree.insert("", "end", iid=str(h["id"]), values=(
                h.get("chain",""), h.get("tx_hash",""),
                h.get("status",""), h.get("from_addr",""),
                h.get("to_addr",""), h.get("value_str",""),
                h.get("queried_at",""),
            ))
        # 被害人陳述交易紀錄面板（建立或更新）
        if self._victim_tx_panel is None:
            for w in self._victim_tx_tab_frame.winfo_children():
                w.destroy()
            self._victim_tx_panel = VictimTxPanel(
                self._victim_tx_tab_frame, case_id)
            self._victim_tx_panel.grid(row=0, column=0, sticky="nsew")
        else:
            self._victim_tx_panel.set_case(case_id)

    def _set_active_case_from_tree(self, _event=None):
        if not self._selected_case_id:
            messagebox.showinfo("提示", "請先在左側選取一個案件")
            return
        case = _db.get_case(self._selected_case_id)
        if case:
            self._active_case = case
            self._case_label.configure(
                text=f"【{case['case_number']}】{case['case_name']}  "
                     f"（{case['case_type']} · {case['status']}）"
            )
            self.status_var.set(f"目前案件已設為：{case['case_number']} {case['case_name']}")

    def _import_by_case_number(self):
        """依案件編號匯入（選取）進行中的案件"""
        num = self._import_num_entry.get().strip()
        if not num:
            messagebox.showwarning("缺少輸入", "請輸入案件編號")
            return
        case = _db.get_case_by_number(num)
        if not case:
            messagebox.showerror("找不到案件",
                                 f"找不到案件編號：{num}\n請確認編號是否正確。")
            return
        # 在清單中選中並展開
        iid = str(case["id"])
        if iid in self._case_tree.get_children():
            self._case_tree.selection_set(iid)
            self._case_tree.see(iid)
            self._on_case_select()
        self._import_num_entry.delete(0, "end")
        self.status_var.set(f"已匯入案件：{case['case_number']} {case['case_name']}")

    def _import_doc_to_desc(self):
        """從文件資料夾分析內容，摘要填入案件描述"""
        if not self._selected_case_id:
            messagebox.showinfo("提示", "請先選取案件")
            return
        folder = filedialog.askdirectory(title="選擇文件資料夾")
        if not folder:
            return

        self.status_var.set("正在分析資料夾內文件，請稍候…")
        self.update_idletasks()

        def do_import():
            from analyzer.doc_transaction_extractor import (
                analyze_folder, summarize_for_case)
            result = analyze_folder(folder)
            summary = summarize_for_case(result["raw_text"])
            # 更新案件描述
            case = _db.get_case(self._selected_case_id)
            old_desc = case.get("description", "") or ""
            new_desc  = (old_desc + "\n\n【文件分析摘要】\n" + summary
                         if old_desc else "【文件分析摘要】\n" + summary)
            _db.update_case(self._selected_case_id, description=new_desc)
            # 匯入交易記錄
            imported = 0
            for t in result["transactions"]:
                _db.upsert_victim_transaction(self._selected_case_id, t)
                imported += 1
            proc  = len(result["processed_files"])
            err   = len(result["error_files"])
            self.after(0, self._finish_doc_import, proc, err, imported)

        threading.Thread(target=do_import, daemon=True).start()

    def _finish_doc_import(self, processed, errors, imported):
        self._reload_case_list()
        self._on_case_select()
        if self._victim_tx_panel:
            self._victim_tx_panel._load()
        self.status_var.set(
            f"文件分析完成：處理 {processed} 份，錯誤 {errors} 份，"
            f"匯入 {imported} 筆交易記錄")
        messagebox.showinfo("匯入完成",
                            f"已處理 {processed} 份文件（{errors} 份失敗）\n"
                            f"案件描述已更新\n"
                            f"交易記錄匯入 {imported} 筆（請逐一確認修正）")

    def _case_new(self):
        def on_save(c):
            self._reload_case_list()
        CaseDialog(self, on_save=on_save)

    def _case_edit(self):
        sel = self._case_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "請先選取要編輯的案件")
            return
        case = _db.get_case(int(sel[0]))
        if case:
            def on_save(_):
                self._reload_case_list()
                self._on_case_select()
            CaseDialog(self, case=case, on_save=on_save)

    def _case_delete(self):
        sel = self._case_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "請先選取要刪除的案件")
            return
        case = _db.get_case(int(sel[0]))
        if not case:
            return
        if not messagebox.askyesno("確認刪除",
                f"確定刪除案件【{case['case_number']}】{case['case_name']}？\n"
                "（關聯的錢包與 Hash 記錄不會被刪除，僅解除關聯）"):
            return
        _db.delete_case(int(sel[0]))
        if self._active_case and self._active_case["id"] == int(sel[0]):
            self._clear_case()
        self._reload_case_list()
        self._case_info_lbl.configure(text="← 點選左側案件查看詳情")

    def _case_unlink_wallet(self):
        sel = self._case_wallet_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "請先選取要移除的錢包")
            return
        for iid in sel:
            _db.unlink_wallet_from_case(int(iid))
        self._on_case_select()

    def _case_unlink_hash(self):
        sel = self._case_hash_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "請先選取要移除的 Hash 記錄")
            return
        for iid in sel:
            _db.unlink_tx_lookup_from_case(int(iid))
        self._on_case_select()

    # ── 案件工具列方法 ─────────────────────────────────────────────────────────

    def _pick_case(self):
        def on_select(case):
            self._active_case = case
            self._case_label.configure(
                text=f"【{case['case_number']}】{case['case_name']}  "
                     f"（{case['case_type']} · {case['status']}）"
            )
            self.status_var.set(f"已切換案件：{case['case_number']} {case['case_name']}")
            self._flow_panel.set_case_id(case["id"])
        LinkToCaseDialog(self, title="選擇目前作業案件", on_select=on_select)

    def _new_case_quick(self):
        def on_save(c):
            self._active_case = c
            self._case_label.configure(
                text=f"【{c['case_number']}】{c['case_name']}  "
                     f"（{c['case_type']} · {c['status']}）"
            )
            self._reload_case_list()
            self.status_var.set(f"新建案件：{c['case_number']} {c['case_name']}")
            self._flow_panel.set_case_id(c["id"])
        CaseDialog(self, on_save=on_save)

    def _clear_case(self):
        self._active_case = None
        self._case_label.configure(text="（尚未選擇案件）")
        self.status_var.set("已清除案件選擇")
        self._flow_panel.set_case_id(None)

    def _build_tx_tab(self, tab_name: str, attr: str):
        tab = self.tabs.tab(tab_name)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        frame = ctk.CTkFrame(tab, corner_radius=8)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        # 先用空 treeview，分析完後重建欄位
        tree = self._make_treeview(frame, ("請先執行分析",))
        setattr(self, attr, tree)

    def _make_treeview(self, parent, columns: tuple) -> ttk.Treeview:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#2b2b2b", foreground="white",
                        fieldbackground="#2b2b2b", rowheight=22,
                        font=("Consolas", 10))
        style.configure("Treeview.Heading", background="#1f1f2e",
                        foreground="white", font=("Microsoft JhengHei", 10, "bold"))
        style.map("Treeview", background=[("selected", "#1f538d")])

        vsb = ttk.Scrollbar(parent, orient="vertical")
        hsb = ttk.Scrollbar(parent, orient="horizontal")
        tree = ttk.Treeview(parent, columns=columns, show="headings",
                            yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.config(command=tree.yview)
        hsb.config(command=tree.xview)

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=160, minwidth=80, stretch=True)

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        self._bind_tree_context_menu(tree)
        return tree

    # ── 右鍵選單 ───────────────────────────────────────────────────────────────

    def _bind_entry_context_menu(self, widget):
        """為 CTkEntry 或 tk.Entry 加上右鍵選單"""
        inner = widget._entry if hasattr(widget, "_entry") else widget
        menu = tk.Menu(inner, tearoff=0,
                       bg="#2b2b2b", fg="white", activebackground="#1f538d",
                       activeforeground="white", font=("Microsoft JhengHei", 11))
        menu.add_command(label="複製",
                         command=lambda: inner.event_generate("<<Copy>>"))
        menu.add_command(label="貼上",
                         command=lambda: inner.event_generate("<<Paste>>"))
        menu.add_command(label="剪下",
                         command=lambda: inner.event_generate("<<Cut>>"))
        menu.add_separator()
        menu.add_command(label="全選",
                         command=lambda: (inner.select_range(0, "end"),
                                         inner.icursor("end")))
        def show(event):
            menu.tk_popup(event.x_root, event.y_root)
        inner.bind("<Button-3>", show)

    def _bind_tree_context_menu(self, tree: ttk.Treeview):
        """為 Treeview 加上右鍵選單（複製此格 / 複製整列）"""
        menu = tk.Menu(tree, tearoff=0,
                       bg="#2b2b2b", fg="white", activebackground="#1f538d",
                       activeforeground="white", font=("Microsoft JhengHei", 11))

        self._tree_ctx_col: str = ""  # 記錄右鍵點擊的欄位

        def copy_cell():
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0])["values"]
            cols = tree["columns"]
            try:
                idx  = list(cols).index(self._tree_ctx_col)
                text = str(vals[idx]) if idx < len(vals) else ""
            except (ValueError, IndexError):
                text = str(vals[0]) if vals else ""
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_var.set(f"已複製：{text[:60]}")

        def copy_row():
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0])["values"]
            text = "\t".join(str(v) for v in vals)
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_var.set("已複製整列資料")

        def copy_all():
            rows = []
            cols = tree["columns"]
            rows.append("\t".join(cols))
            for iid in tree.get_children():
                vals = tree.item(iid)["values"]
                rows.append("\t".join(str(v) for v in vals))
            self.clipboard_clear()
            self.clipboard_append("\n".join(rows))
            self.status_var.set(f"已複製全部 {len(rows)-1} 列資料")

        menu.add_command(label="複製此格", command=copy_cell)
        menu.add_command(label="複製整列", command=copy_row)
        menu.add_separator()
        menu.add_command(label="複製全部（含標題）", command=copy_all)

        def show(event):
            # 記錄點擊的欄位
            region = tree.identify_region(event.x, event.y)
            if region in ("cell", "heading"):
                self._tree_ctx_col = tree.identify_column(event.x)
                # 轉換為欄位名稱
                try:
                    col_idx = int(self._tree_ctx_col.replace("#", "")) - 1
                    self._tree_ctx_col = tree["columns"][col_idx]
                except (ValueError, IndexError):
                    self._tree_ctx_col = ""
                # 選中點擊的列
                iid = tree.identify_row(event.y)
                if iid:
                    tree.selection_set(iid)
            menu.tk_popup(event.x_root, event.y_root)

        tree.bind("<Button-3>", show)

    def _bind_label_copy_menu(self, label: ctk.CTkLabel):
        """為 CTkLabel 加上右鍵複製選單"""
        menu = tk.Menu(label, tearoff=0,
                       bg="#2b2b2b", fg="white", activebackground="#1f538d",
                       activeforeground="white", font=("Microsoft JhengHei", 11))
        menu.add_command(label="複製", command=lambda: (
            self.clipboard_clear(),
            self.clipboard_append(label.cget("text")),
            self.status_var.set(f"已複製：{label.cget('text')[:60]}")
        ))
        label.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    # ── 分析邏輯 ───────────────────────────────────────────────────────────────

    # ── 查詢模式 ───────────────────────────────────────────────────────────────

    # ── 時間篩選 UI 方法 ────────────────────────────────────────────────────────

    def _toggle_time_bar(self):
        self._time_bar_visible = not self._time_bar_visible
        if self._time_bar_visible:
            self._time_bar.grid()
            self._time_toggle_btn.configure(text="⏱ 時間篩選 ▲")
        else:
            self._time_bar.grid_remove()
            self._time_toggle_btn.configure(text="⏱ 時間篩選 ▼")

    def _on_time_change(self, _event=None):
        s_str = self._time_start.get().strip()
        e_str = self._time_end.get().strip()
        each  = self._time_each.get().strip()
        s_ts  = parse_datetime_str(s_str)
        e_ts  = parse_datetime_str(e_str)
        if not s_str:
            self._time_mode_lbl.configure(text="尚未設定時間", text_color="gray60")
        elif s_ts and e_ts:
            if e_ts < s_ts:
                self._time_mode_lbl.configure(text="⚠ 迄止時間不可早於起始時間",
                                              text_color="#ff7070")
            else:
                diff = e_ts - s_ts
                h, m = divmod(diff // 60, 60)
                d, h = divmod(h, 24)
                self._time_mode_lbl.configure(
                    text=f"範圍模式：{d}天{h}時{m}分  "
                         f"{ts_to_str(s_ts)} ～ {ts_to_str(e_ts)}",
                    text_color="#aaffaa")
        elif s_ts:
            try:
                n = int(each) if each else 50
                n = max(1, min(n, MAX_TOTAL // 2))
            except ValueError:
                n = 50
            self._time_mode_lbl.configure(
                text=f"置中模式：以 {ts_to_str(s_ts)} 為軸心，前後各 {n} 筆",
                text_color="#ffccaa")
        else:
            self._time_mode_lbl.configure(text="⚠ 時間格式錯誤",
                                          text_color="#ff7070")

    def _get_time_filter(self) -> dict | None:
        """
        回傳時間篩選設定 dict；未啟用回傳 None；格式錯誤顯示訊息並回傳 None。
        dict keys: mode ("range"|"center"), start_ts, end_ts, each_side
        """
        if not self._time_bar_visible:
            return None
        s_str = self._time_start.get().strip()
        e_str = self._time_end.get().strip()
        if not s_str:
            return None
        s_ts = parse_datetime_str(s_str)
        if not s_ts:
            messagebox.showerror("時間格式錯誤",
                                 f"起始時間格式錯誤：{s_str}\n"
                                 "正確格式：YYYY-MM-DD HH:MM:SS")
            return None
        e_ts = parse_datetime_str(e_str) if e_str else None
        if e_str and not e_ts:
            messagebox.showerror("時間格式錯誤",
                                 f"迄止時間格式錯誤：{e_str}\n"
                                 "正確格式：YYYY-MM-DD HH:MM:SS")
            return None
        if e_ts and e_ts < s_ts:
            messagebox.showerror("時間設定錯誤", "迄止時間不可早於起始時間")
            return None
        try:
            each = int(self._time_each.get().strip() or "50")
            each = max(1, min(each, MAX_TOTAL // 2))
        except ValueError:
            each = 50

        if e_ts:
            return {"mode": "range", "start_ts": s_ts, "end_ts": e_ts,
                    "each_side": each}
        return {"mode": "center", "start_ts": s_ts, "end_ts": None,
                "each_side": each}

    def _clear_time_filter(self):
        self._time_start.delete(0, "end")
        self._time_end.delete(0, "end")
        self._time_each.delete(0, "end")
        self._time_each.insert(0, "50")
        self._time_mode_lbl.configure(text="時間篩選已清除", text_color="gray60")

    def _apply_time_filter_to_profile(self, profile: dict,
                                       tf: dict) -> dict | None:
        """
        對已抓取的 profile 套用時間篩選。
        回傳修改後的 profile，若使用者取消則回傳 None。
        """
        chain = profile.get("chain", "ETH")
        raw   = profile.get("raw_txs", []) + profile.get("raw_erc20",
                            profile.get("raw_trc20", []))
        if not raw:
            return profile

        if tf["mode"] == "range":
            filtered = filter_by_range(raw, chain, tf["start_ts"], tf["end_ts"])
            warn = check_overflow(len(filtered), MAX_TOTAL)
            if warn:
                ans = messagebox.askyesno("交易筆數超過上限", warn)
                if not ans:
                    filtered = filtered[:MAX_TOTAL]
            profile["raw_txs"]  = [t for t in filtered
                                    if t.get("timeStamp") or t.get("timestamp")
                                    or t.get("time")]
            profile["_time_filter_applied"] = (
                f"範圍篩選：{ts_to_str(tf['start_ts'])} ～ "
                f"{ts_to_str(tf['end_ts'])}，共 {len(filtered)} 筆"
            )

        else:  # center
            res = filter_centered(raw, chain, tf["start_ts"], tf["each_side"])
            sug = suggest_increase(tf["each_side"],
                                   res["total_available_before"],
                                   res["total_available_after"])
            if sug:
                ans = messagebox.askyesno("建議增加筆數", sug)
                if ans:
                    new_each = min(
                        max(res["total_available_before"],
                            res["total_available_after"]),
                        MAX_TOTAL // 2)
                    res = filter_centered(raw, chain, tf["start_ts"], new_each)
                    tf["each_side"] = new_each
            pivot = res.get("pivot_tx")
            pivot_time = ts_to_str(
                int(pivot.get("timeStamp", pivot.get("timestamp",
                              pivot.get("time", 0))) or 0)
            ) if pivot else "N/A"
            profile["raw_txs"] = res["result"]
            profile["_time_filter_applied"] = (
                f"置中篩選：軸心 {ts_to_str(tf['start_ts'])}（最近交易 {pivot_time}），"
                f"前 {res['before']} 筆 + 後 {res['after']} 筆，共 {len(res['result'])} 筆"
            )

        return profile

    def _on_mode_change(self, mode: str):
        if mode == "專案查詢":
            self._case_bar.grid()
            if self._active_case:
                self._case_label.configure(
                    text=f"【{self._active_case['case_number']}】"
                         f"{self._active_case['case_name']}",
                    text_color="#7eb8f7")
            else:
                self._case_label.configure(
                    text="（尚未選擇案件，專案查詢需先選擇案件）",
                    text_color="#f5a623")
        else:
            self._case_bar.grid_remove()
        self.status_var.set(f"已切換為【{mode}】模式" + (
            "（結果不儲存至資料庫）" if mode == "一般查詢" else
            "（結果將儲存並關聯案件）"))

    def _start_smart_query(self):
        """統一入口：自動判斷輸入為地址或 Hash，並依模式執行"""
        text = self.addr_entry.get().strip()
        if not text:
            from tkinter import messagebox
            messagebox.showwarning("缺少輸入", "請輸入錢包地址或交易 Hash")
            return
        # 專案查詢必須選擇案件
        if self._query_mode.get() == "專案查詢" and not self._active_case:
            from tkinter import messagebox
            if not messagebox.askyesno("尚未選擇案件",
                    "專案查詢需要先選擇案件。\n\n"
                    "是否現在選擇案件？\n"
                    "（選「否」將改以一般查詢執行，結果不儲存）"):
                self._query_mode.set("一般查詢")
                self._on_mode_change("一般查詢")
            else:
                self._pick_case()
                return
        # 判斷輸入類型
        if self._is_tx_hash(text):
            self._start_hash_analysis()
        else:
            self._start_analysis()

    def _clear_results(self):
        """清除目前所有查詢結果（一般查詢用）"""
        self._profile = None
        # 清除摘要
        for _, val_lbl in self._summary_labels:
            val_lbl.configure(text="—")
        # 清除授權表格
        for iid in self._approval_tree.get_children():
            self._approval_tree.delete(iid)
        # 清除交易/Token 表格（重設為空）
        for attr in ("_tx_tree", "_token_tree"):
            tree = getattr(self, attr, None)
            if tree:
                for iid in tree.get_children():
                    tree.delete(iid)
        # 清除 Hash 分析結果
        for w in self._hash_detail_frame.winfo_children():
            w.destroy()
        for iid in self._hash_token_tree.get_children():
            self._hash_token_tree.delete(iid)
        # 清除輸入框
        self.addr_entry.delete(0, "end")
        if hasattr(self, "hash_entry"):
            self.hash_entry.delete(0, "end")
        self.status_var.set("查詢結果已清除")

    def _on_addr_focusout(self, _event=None):
        address = self.addr_entry.get().strip()
        if not address:
            return
        detected = self._detect_chain(address)
        if detected:
            if detected != self.chain_var.get():
                self.chain_var.set(detected)
                self.status_var.set(f"已自動偵測並切換為 {detected} 鏈")
            else:
                self.status_var.set(f"地址格式正確（{detected}）")
        else:
            self.status_var.set("⚠ 無法識別地址格式，請確認是否正確")

    def _on_addr_keyrelease(self, _event=None):
        address = self.addr_entry.get().strip()
        if len(address) >= 10:
            detected = self._detect_chain(address)
            if detected and detected != self.chain_var.get():
                self.chain_var.set(detected)
                self.status_var.set(f"已自動偵測並切換為 {detected} 鏈")

    @staticmethod
    def _detect_chain(address: str) -> str | None:
        """根據地址格式自動偵測鏈，無法識別回傳 None"""
        a = address.strip()
        if a.startswith("0x") and len(a) == 42:
            return "ETH"
        if a.startswith("T") and len(a) == 34:
            return "TRX"
        if a.startswith(("1", "3", "bc1")):
            return "BTC"
        return None

    @staticmethod
    def _validate_address(chain: str, address: str) -> str | None:
        """回傳錯誤訊息字串；格式正確回傳 None"""
        a = address.strip()
        if chain == "ETH":
            if not (a.startswith("0x") and len(a) == 42):
                detected = App._detect_chain(a)
                hint = f"\n\n（您輸入的地址格式像是 {detected} 地址）" if detected else ""
                return f"ETH 地址格式錯誤。\n正確格式：0x 開頭，共 42 個字元。{hint}"
        elif chain == "TRX":
            if not (a.startswith("T") and len(a) == 34):
                detected = App._detect_chain(a)
                hint = f"\n\n（您輸入的地址格式像是 {detected} 地址）" if detected else ""
                return f"TRX 地址格式錯誤。\n正確格式：T 開頭，共 34 個字元。{hint}"
        elif chain == "BTC":
            if not a.startswith(("1", "3", "bc1")):
                detected = App._detect_chain(a)
                hint = f"\n\n（您輸入的地址格式像是 {detected} 地址）" if detected else ""
                return f"BTC 地址格式錯誤。\n正確格式：1 / 3 / bc1 開頭。{hint}"
        return None

    def _on_chain_change(self, _):
        # 切換鏈時自動驗證目前輸入的地址
        address = self.addr_entry.get().strip()
        if address:
            detected = self._detect_chain(address)
            if detected and detected != self.chain_var.get():
                self.status_var.set(f"⚠ 注意：此地址格式為 {detected}，請確認選擇正確的鏈")

    # ── Hash 分析 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _is_tx_hash(text: str) -> bool:
        t = text.strip()
        if t.startswith("0x") and len(t) == 66:
            return True
        if len(t) == 64 and all(c in "0123456789abcdefABCDEF" for c in t):
            return True
        return False

    def _start_hash_analysis(self):
        tx_hash = self.hash_entry.get().strip()
        if not tx_hash:
            messagebox.showwarning("缺少輸入", "請輸入交易 Hash")
            return
        if not self._is_tx_hash(tx_hash):
            messagebox.showerror("格式錯誤",
                "交易 Hash 格式錯誤。\n"
                "ETH：0x 開頭，共 66 字元\n"
                "TRX / BTC：64 位十六進位字元")
            return
        chain = self.chain_var.get()
        self.hash_btn.configure(state="disabled")
        self.status_var.set("正在查詢交易 Hash...")
        self.progress.grid()
        self.progress.start()
        threading.Thread(target=self._run_hash_analysis,
                         args=(chain, tx_hash), daemon=True).start()

    def _run_hash_analysis(self, chain: str, tx_hash: str):
        try:
            if chain == "ETH":
                api = EtherscanAPI(self.config_data.get("etherscan_api_key", ""))
                raw = api.get_transaction(tx_hash)
                result = analyze_eth_tx(raw)
            elif chain == "TRX":
                api = TronScanAPI()
                raw = api.get_transaction(tx_hash)
                result = analyze_trx_tx(raw)
            else:
                api = BitcoinAPI()
                raw = api.get_transaction(tx_hash)
                result = analyze_btc_tx(raw)
            # 專案查詢才儲存至資料庫
            if self._query_mode.get() == "專案查詢":
                try:
                    case_id = self._active_case["id"] if self._active_case else None
                    _db.save_tx_lookup(result, case_id=case_id)
                except Exception:
                    pass
            self.after(0, self._update_hash_ui, result)
        except Exception as e:
            self.after(0, self._on_hash_error, str(e))

    def _on_hash_error(self, msg: str):
        self.progress.stop()
        self.progress.grid_remove()
        self.hash_btn.configure(state="normal")
        messagebox.showerror("查詢失敗", msg)

    def _update_hash_ui(self, result: dict):
        self.progress.stop()
        self.progress.grid_remove()
        self.hash_btn.configure(state="normal")

        # 切換到 Hash 分析分頁
        self.tabs.set("交易 Hash 分析")

        # 清除舊資料
        for w in self._hash_detail_frame.winfo_children():
            w.destroy()
        self._hash_detail_labels.clear()

        # 顯示欄位（排除 token_transfers 和 BTC 接收方明細）
        skip_keys = {"chain", "token_transfers", "接收方（明細）"}
        row = 0
        for key, val in result.items():
            if key in skip_keys:
                continue
            lbl = ctk.CTkLabel(self._hash_detail_frame, text=f"{key}：",
                               font=("Microsoft JhengHei", 11, "bold"),
                               anchor="e", width=130)
            lbl.grid(row=row, column=0, padx=(8, 4), pady=3, sticky="e")
            val_lbl = ctk.CTkLabel(self._hash_detail_frame, text=str(val),
                                   font=("Consolas", 11), anchor="w",
                                   wraplength=640)
            val_lbl.grid(row=row, column=1, padx=(4, 8), pady=3, sticky="w")
            self._bind_label_copy_menu(val_lbl)
            row += 1

        # BTC 接收方明細
        if "接收方（明細）" in result:
            lbl = ctk.CTkLabel(self._hash_detail_frame, text="接收方明細：",
                               font=("Microsoft JhengHei", 11, "bold"),
                               anchor="ne", width=130)
            lbl.grid(row=row, column=0, padx=(8, 4), pady=3, sticky="ne")
            lines = "\n".join(f"{r['地址']}  →  {r['BTC']}"
                              for r in result["接收方（明細）"])
            val_lbl = ctk.CTkLabel(self._hash_detail_frame, text=lines or "—",
                                   font=("Consolas", 10), anchor="w",
                                   justify="left", wraplength=640)
            val_lbl.grid(row=row, column=1, padx=(4, 8), pady=3, sticky="w")

        # Token 轉帳清單
        for row_id in self._hash_token_tree.get_children():
            self._hash_token_tree.delete(row_id)
        for t in result.get("token_transfers", []):
            self._hash_token_tree.insert("", "end", values=(
                t.get("Token", ""), t.get("從", ""),
                t.get("至", ""), t.get("金額", ""), t.get("合約", ""),
            ))

        mode = self._query_mode.get()
        if mode == "一般查詢":
            saved_hint = "【一般查詢－未儲存，清除後消失】"
        else:
            case_hint = (f"已存入【{self._active_case['case_number']}】"
                         if self._active_case else "已儲存（未關聯案件）")
            saved_hint = f"【專案查詢－{case_hint}】"
        self.status_var.set(
            f"Hash 查詢完成｜{result.get('chain','')} {result.get('狀態','')}　{saved_hint}"
        )

    def _start_analysis(self):
        address = self.addr_entry.get().strip()
        if not address:
            messagebox.showwarning("缺少輸入", "請輸入錢包地址")
            return
        chain = self.chain_var.get()

        # 自動偵測：若選錯鏈就提醒並自動切換
        detected = self._detect_chain(address)
        if detected and detected != chain:
            ans = messagebox.askyesno(
                "地址格式不符",
                f"您選擇的是 {chain}，但輸入的地址格式像是 {detected} 地址。\n\n"
                f"是否自動切換為 {detected} 並繼續？"
            )
            if ans:
                self.chain_var.set(detected)
                chain = detected
            else:
                return

        err = self._validate_address(chain, address)
        if err:
            messagebox.showerror("地址格式錯誤", err)
            return

        if chain == "ETH" and not self.config_data.get("etherscan_api_key"):
            messagebox.showwarning("缺少 API Key", "請先在設定中填入 Etherscan API Key")
            return

        # 取得時間篩選設定（格式有誤時已顯示訊息，回傳 None 表示不篩選）
        tf = self._get_time_filter()

        self.analyze_btn.configure(state="disabled")
        self.status_var.set("分析中，請稍候...")
        self.progress.grid()
        self.progress.start()
        self._profile = None
        threading.Thread(target=self._run_analysis,
                         args=(chain, address, tf), daemon=True).start()

    def _run_analysis(self, chain: str, address: str, tf: dict | None):
        try:
            if chain == "ETH":
                api = EtherscanAPI(self.config_data["etherscan_api_key"])
                ver = "V2" if api._use_v2 else "V1"
                self._set_status(f"使用 Etherscan {ver} API，正在抓取 ETH 一般交易...")
                txs      = api.get_normal_transactions(address)
                self._set_status("正在抓取 Internal 交易...")
                int_txs  = api.get_internal_transactions(address)
                self._set_status("正在抓取 ERC-20 轉帳記錄...")
                erc20    = api.get_erc20_transfers(address)
                self._set_status("正在分析授權紀錄...")
                approvals = api.get_token_approvals(txs, address)
                profile  = profile_eth(address, txs, int_txs, erc20, approvals)

            elif chain == "TRX":
                api = TronScanAPI()
                self._set_status("正在抓取 TRX 交易資料...")
                # TronScan 支援直接傳遞時間戳
                s_ts = tf["start_ts"] if tf and tf["mode"] == "range" else None
                e_ts = tf["end_ts"]   if tf and tf["mode"] == "range" else None
                txs      = api.get_transactions(address, start_ts=s_ts, end_ts=e_ts)
                self._set_status("正在抓取 TRC-20 轉帳...")
                trc20    = api.get_trc20_transfers(address)
                self._set_status("正在分析授權紀錄...")
                approvals = api.get_token_approvals(txs, address)
                profile  = profile_trx(address, txs, trc20, approvals)

            else:  # BTC
                api = BitcoinAPI()
                self._set_status("正在抓取 BTC 交易資料...")
                txs     = api.get_transactions(address)
                profile = profile_btc(address, txs)

            # 套用時間篩選（ETH / BTC 採後端篩選；TRX range 已由 API 篩選）
            if tf and not (chain == "TRX" and tf["mode"] == "range"):
                self._set_status("正在套用時間篩選...")
                profile = self._apply_time_filter_sync(profile, tf)

            self._profile = profile
            # 專案查詢才儲存至資料庫
            if self._query_mode.get() == "專案查詢":
                try:
                    wallet_id = _db.save_wallet_profile(profile)
                    if self._active_case and wallet_id:
                        _db.link_wallet_to_case(wallet_id, self._active_case["id"])
                except Exception:
                    pass
            self.after(0, self._update_ui, profile)
        except Exception as e:
            self.after(0, self._on_error, str(e))

    def _apply_time_filter_sync(self, profile: dict, tf: dict) -> dict:
        """在分析執行緒中套用時間篩選（需要彈窗詢問時透過 after 排程）"""
        chain = profile.get("chain", "ETH")
        raw   = profile.get("raw_txs", [])
        erc20 = profile.get("raw_erc20", [])
        trc20 = profile.get("raw_trc20", [])
        all_txs = raw + erc20 + trc20

        if tf["mode"] == "range":
            filtered_raw  = filter_by_range(raw,   chain, tf["start_ts"], tf["end_ts"])
            filtered_erc  = filter_by_range(erc20, chain, tf["start_ts"], tf["end_ts"])
            filtered_trc  = filter_by_range(trc20, chain, tf["start_ts"], tf["end_ts"])
            total = len(filtered_raw) + len(filtered_erc) + len(filtered_trc)
            warn  = check_overflow(total, MAX_TOTAL)
            if warn:
                # 在主執行緒彈窗
                import queue
                q = queue.Queue()
                def ask():
                    q.put(messagebox.askyesno("交易筆數超過上限", warn))
                self.after(0, ask)
                ans = q.get(timeout=60)
                if not ans:
                    filtered_raw = filtered_raw[:MAX_TOTAL]
                    filtered_erc = []
                    filtered_trc = []
            profile["raw_txs"]  = filtered_raw
            if erc20: profile["raw_erc20"] = filtered_erc
            if trc20: profile["raw_trc20"] = filtered_trc
            profile["_time_filter_applied"] = (
                f"範圍篩選 {ts_to_str(tf['start_ts'])} ～ {ts_to_str(tf['end_ts'])}，"
                f"共 {len(filtered_raw)+len(filtered_erc)+len(filtered_trc)} 筆"
            )

        else:  # center
            res = filter_centered(all_txs, chain, tf["start_ts"], tf["each_side"])
            sug = suggest_increase(tf["each_side"],
                                   res["total_available_before"],
                                   res["total_available_after"])
            if sug:
                import queue
                q = queue.Queue()
                def ask_sug():
                    q.put(messagebox.askyesno("建議增加筆數", sug))
                self.after(0, ask_sug)
                ans = q.get(timeout=60)
                if ans:
                    new_each = min(
                        max(res["total_available_before"],
                            res["total_available_after"]),
                        MAX_TOTAL // 2)
                    res = filter_centered(all_txs, chain,
                                          tf["start_ts"], new_each)
                    tf["each_side"] = new_each

            pivot    = res.get("pivot_tx") or {}
            raw_ts   = (pivot.get("timeStamp") or pivot.get("timestamp") or
                        pivot.get("time") or 0)
            try:
                ms = int(raw_ts)
                if ms > 1e12: ms //= 1000
            except Exception:
                ms = 0
            pivot_time = ts_to_str(ms) if ms else "N/A"

            profile["raw_txs"] = res["result"]
            if "raw_erc20" in profile: profile["raw_erc20"] = []
            if "raw_trc20" in profile: profile["raw_trc20"] = []
            profile["_time_filter_applied"] = (
                f"置中篩選 軸心 {ts_to_str(tf['start_ts'])}（最近 {pivot_time}），"
                f"前 {res['before']} 筆＋後 {res['after']} 筆，共 {len(res['result'])} 筆"
            )

        return profile

    def _set_status(self, msg: str):
        self.after(0, self.status_var.set, msg)

    def _on_error(self, msg: str):
        self._stop_progress()
        messagebox.showerror("分析失敗", msg)

    def _stop_progress(self):
        self.progress.stop()
        self.progress.grid_remove()
        self.analyze_btn.configure(state="normal")

    # ── UI 更新 ────────────────────────────────────────────────────────────────

    def _update_ui(self, p: dict):
        self._stop_progress()
        chain = p.get("chain", "")
        unit  = {"ETH": "ETH", "TRX": "TRX", "BTC": "BTC"}.get(chain, "")
        amt_key = {"ETH": "out_total_eth", "TRX": "out_total_trx", "BTC": "out_total_btc"}.get(chain, "")
        in_key  = {"ETH": "in_total_eth",  "TRX": "in_total_trx",  "BTC": "in_total_btc"}.get(chain, "")
        fee_key = {"ETH": "total_fee_eth", "TRX": "total_fee_trx", "BTC": "total_fee_btc"}.get(chain, "")
        def _fmt_token_dict(d: dict) -> str:
            if not d:
                return "—"
            return "  |  ".join(f"{sym}: {amt:,.4f}" for sym, amt in sorted(d.items()))

        if chain == "ETH":
            values = [
                chain,
                p.get("address", ""),
                p.get("first_tx_time", "N/A"),
                p.get("last_tx_time", "N/A"),
                p.get("first_source", "N/A"),
                str(p.get("out_count", 0)),
                str(p.get("eth_out_count", 0)),
                str(p.get("erc20_out_count", 0)),
                f"{p.get('out_total_eth', 0):,.8f} ETH",
                _fmt_token_dict(p.get("erc20_out_by_token", {})),
                str(p.get("in_count", 0)),
                str(p.get("eth_in_count", 0)),
                str(p.get("erc20_in_count", 0)),
                f"{p.get('in_total_eth', 0):,.8f} ETH",
                _fmt_token_dict(p.get("erc20_in_by_token", {})),
                f"{p.get('total_fee_eth', 0):,.8f} ETH",
                p.get("top_fee_dest", "N/A"),
            ]
        else:
            # TRX / BTC：維持舊版簡單顯示，填滿所有欄位
            out_val = f"{p.get(amt_key, 0):,.8f} {unit}"
            in_val  = f"{p.get(in_key,  0):,.8f} {unit}"
            fee_val = f"{p.get(fee_key, 0):,.8f} {unit}"
            trc20_out = p.get("trc20_out_by_token", {})
            trc20_in  = p.get("trc20_in_by_token",  {})
            values = [
                chain,
                p.get("address", ""),
                p.get("first_tx_time", "N/A"),
                p.get("last_tx_time", "N/A"),
                p.get("first_source", "N/A"),
                str(p.get("out_count", 0)), "—", "—",
                out_val, _fmt_token_dict(trc20_out),
                str(p.get("in_count", 0)), "—", "—",
                in_val, _fmt_token_dict(trc20_in),
                fee_val, p.get("top_fee_dest", "N/A"),
            ]

        for (_, val_lbl), val in zip(self._summary_labels, values):
            val_lbl.configure(text=val)

        # 時間篩選說明（若有）
        tf_info = p.get("_time_filter_applied")
        if hasattr(self, "_tf_info_lbl"):
            self._tf_info_lbl.configure(
                text=f"⏱ {tf_info}" if tf_info else "",
                text_color="#ffcc55" if tf_info else "gray60")

        # 授權
        for row in self._approval_tree.get_children():
            self._approval_tree.delete(row)
        for a in p.get("approval_targets", []):
            self._approval_tree.insert("", "end", values=(
                a.get("contract", ""),
                a.get("spender", ""),
                a.get("tx_hash", a.get("amount", "")),
                a.get("time", ""),
            ))

        # 原始交易
        self._rebuild_tree("_tx_tree", p.get("raw_txs", []))

        # Token 轉帳
        token_data = p.get("raw_erc20", p.get("raw_trc20", []))
        self._rebuild_tree("_token_tree", token_data)

        total = p.get("out_count", 0) + p.get("in_count", 0)
        has_any_data = bool(p.get("raw_txs") or p.get("raw_erc20") or p.get("raw_trc20"))
        if total == 0 and not has_any_data:
            chain = p.get("chain", "")
            addr  = p.get("address", "")
            explorer = {
                "ETH": f"https://etherscan.io/address/{addr}",
                "TRX": f"https://tronscan.org/#/address/{addr}",
                "BTC": f"https://blockchain.com/btc/address/{addr}",
            }.get(chain, "")
            messagebox.showwarning(
                "查無交易記錄",
                f"此地址在 {chain} 鏈上沒有找到任何交易記錄。\n\n"
                f"可能原因：\n"
                f"• 這是一個全新、從未使用過的地址\n"
                f"• 地址輸入有誤\n"
                f"• 搜尋的鏈選擇錯誤\n\n"
                f"請至區塊鏈瀏覽器確認：\n{explorer}"
            )
            self.status_var.set(f"查無資料｜請確認地址是否正確")
        else:
            mode = self._query_mode.get()
            if mode == "一般查詢":
                saved_hint = "【一般查詢－未儲存至資料庫，清除結果後資料消失】"
            else:
                case_hint = (f"已存入案件【{self._active_case['case_number']}】"
                             if self._active_case else "已儲存（未關聯案件）")
                saved_hint = f"【專案查詢－{case_hint}】"
            self.status_var.set(
                f"分析完成｜共 {total} 筆交易｜授權 {len(p.get('approval_targets', []))} 筆　{saved_hint}"
            )

    def _rebuild_tree(self, attr: str, rows: list[dict]):
        old_tree = getattr(self, attr)
        if not rows:
            return
        parent = old_tree.master
        for w in parent.winfo_children():
            w.destroy()
        keys = list(rows[0].keys())
        new_tree = self._make_treeview(parent, tuple(keys))
        setattr(self, attr, new_tree)
        for row in rows[:5000]:
            vals = []
            for k in keys:
                v = row.get(k, "")
                if isinstance(v, (dict, list)):
                    v = str(v)[:80]
                vals.append(v)
            new_tree.insert("", "end", values=vals)

    # ── 幣流圖 ────────────────────────────────────────────────────────────────

    def _add_to_flow_graph(self):
        """將目前查詢結果加入幣流關聯圖。"""
        if not self._profile:
            messagebox.showinfo("尚無資料", "請先執行分析。")
            return
        panel: FlowGraphPanel = self._flow_panel
        if panel._state is None:
            panel.load_from_profile(self._profile)
        else:
            panel.add_profile_to_graph(self._profile)
        # 同步更新案件 ID 與生成模式
        if self._query_mode.get() == "專案查詢" and self._active_case:
            panel.set_case_id(self._active_case["id"])
            panel._gen_mode.set("evidence")
        else:
            panel._gen_mode.set("explore")
        panel._update_mode_label()
        self.tabs.set("幣流關聯圖")

    def _on_flow_node_clicked(self, address: str, chain: str):
        """幣流圖節點點擊 → 填入地址欄並執行查詢。"""
        self.addr_entry.delete(0, "end")
        self.addr_entry.insert(0, address)
        self.chain_var.set(chain)
        self.tabs.set("錢包摘要")
        self._start_smart_query()

    # ── 匯出 ──────────────────────────────────────────────────────────────────

    def _export_excel(self):
        if not self._profile:
            messagebox.showinfo("尚無資料", "請先執行分析")
            return
        d = filedialog.askdirectory(title="選擇儲存資料夾")
        if not d:
            return
        path = export_excel(self._profile, d)
        messagebox.showinfo("匯出成功", f"已儲存至：\n{path}")

    def _export_csv(self):
        if not self._profile:
            messagebox.showinfo("尚無資料", "請先執行分析")
            return
        d = filedialog.askdirectory(title="選擇儲存資料夾")
        if not d:
            return
        files = export_csv(self._profile, d)
        messagebox.showinfo("匯出成功", "已匯出：\n" + "\n".join(files))

    # ── 設定視窗 ───────────────────────────────────────────────────────────────

    def _open_settings(self):
        win = ctk.CTkToplevel(self)
        win.title("設定")
        win.geometry("520x280")
        win.grab_set()

        ctk.CTkLabel(win, text="Etherscan API Key：",
                     font=("Microsoft JhengHei", 12)).grid(
            row=0, column=0, padx=16, pady=(24, 6), sticky="e")
        eth_key_entry = ctk.CTkEntry(win, width=320, font=("Consolas", 11))
        eth_key_entry.insert(0, self.config_data.get("etherscan_api_key", ""))
        eth_key_entry.grid(row=0, column=1, padx=8, pady=(24, 6))
        self._bind_entry_context_menu(eth_key_entry)

        ctk.CTkLabel(win, text="TronGrid API Key（選填）：",
                     font=("Microsoft JhengHei", 12)).grid(
            row=1, column=0, padx=16, pady=6, sticky="e")
        trx_key_entry = ctk.CTkEntry(win, width=320, font=("Consolas", 11))
        trx_key_entry.insert(0, self.config_data.get("trongrid_api_key", ""))
        trx_key_entry.grid(row=1, column=1, padx=8, pady=6)
        self._bind_entry_context_menu(trx_key_entry)

        def save():
            self.config_data["etherscan_api_key"] = eth_key_entry.get().strip()
            self.config_data["trongrid_api_key"]  = trx_key_entry.get().strip()
            save_config(self.config_data)
            messagebox.showinfo("已儲存", "設定已儲存", parent=win)
            win.destroy()

        ctk.CTkButton(win, text="儲存", font=("Microsoft JhengHei", 13),
                      command=save).grid(row=3, column=0, columnspan=2, pady=24)
