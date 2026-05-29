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
from exporter.report import export_excel, export_csv
from database import db as _db

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("虛擬貨幣錢包分析工具 v1.0")
        self.geometry("1100x740")
        self.resizable(True, True)
        self.config_data = load_config()
        self._profile: dict | None = None
        self._build_ui()

    # ── UI 建構 ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 頂部輸入區
        top = ctk.CTkFrame(self, corner_radius=10)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="區塊鏈：", font=("Microsoft JhengHei", 13)).grid(
            row=0, column=0, padx=(12, 4), pady=10)
        self.chain_var = ctk.StringVar(value="ETH")
        chain_menu = ctk.CTkOptionMenu(top, variable=self.chain_var,
                                       values=["ETH", "TRX", "BTC"], width=90,
                                       command=self._on_chain_change)
        chain_menu.grid(row=0, column=1, padx=4, pady=10, sticky="w")

        ctk.CTkLabel(top, text="錢包地址：", font=("Microsoft JhengHei", 13)).grid(
            row=0, column=2, padx=(12, 4), pady=10)
        self.addr_entry = ctk.CTkEntry(top, placeholder_text="輸入錢包地址（貼上後自動偵測鏈）...",
                                       width=440, font=("Consolas", 12))
        self.addr_entry.grid(row=0, column=3, padx=4, pady=10, sticky="ew")
        self.addr_entry.bind("<FocusOut>", self._on_addr_focusout)
        self.addr_entry.bind("<KeyRelease>", self._on_addr_keyrelease)
        self._bind_entry_context_menu(self.addr_entry)
        top.grid_columnconfigure(3, weight=1)

        self.analyze_btn = ctk.CTkButton(top, text="開始分析", width=110,
                                         font=("Microsoft JhengHei", 13, "bold"),
                                         command=self._start_analysis)
        self.analyze_btn.grid(row=0, column=4, padx=(8, 4), pady=10)

        self.export_excel_btn = ctk.CTkButton(top, text="匯出 Excel", width=110,
                                              font=("Microsoft JhengHei", 12),
                                              fg_color="#2d6a4f",
                                              command=self._export_excel)
        self.export_excel_btn.grid(row=0, column=5, padx=4, pady=10)

        self.export_csv_btn = ctk.CTkButton(top, text="匯出 CSV", width=110,
                                            font=("Microsoft JhengHei", 12),
                                            fg_color="#5e3a8a",
                                            command=self._export_csv)
        self.export_csv_btn.grid(row=0, column=6, padx=4, pady=10)

        self.settings_btn = ctk.CTkButton(top, text="⚙ 設定", width=80,
                                          font=("Microsoft JhengHei", 12),
                                          fg_color="gray35",
                                          command=self._open_settings)
        self.settings_btn.grid(row=0, column=7, padx=(4, 12), pady=10)

        # 主內容（分頁）
        self.tabs = ctk.CTkTabview(self, corner_radius=10)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 4))
        for name in ["錢包摘要", "授權對象", "原始交易", "Token 轉帳", "交易 Hash 分析", "查詢歷史"]:
            self.tabs.add(name)

        self._build_summary_tab()
        self._build_approvals_tab()
        self._build_tx_tab("原始交易", "_tx_tree")
        self._build_tx_tab("Token 轉帳", "_token_tree")
        self._build_hash_tab()
        self._build_history_tab()

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
            try:
                _db.save_tx_lookup(result)
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

        self.status_var.set(f"Hash 查詢完成｜{result.get('chain','')} - {result.get('狀態','')}")

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

        self.analyze_btn.configure(state="disabled")
        self.status_var.set("分析中，請稍候...")
        self.progress.grid()
        self.progress.start()
        self._profile = None
        threading.Thread(target=self._run_analysis, args=(chain, address), daemon=True).start()

    def _run_analysis(self, chain: str, address: str):
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
                approvals = api.get_token_approvals(txs, address)  # 不重複呼叫 API
                profile  = profile_eth(address, txs, int_txs, erc20, approvals)

            elif chain == "TRX":
                api = TronScanAPI()
                self._set_status("正在抓取 TRX 交易資料...")
                txs      = api.get_transactions(address)
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

            self._profile = profile
            # 背景儲存至資料庫
            try:
                _db.save_wallet_profile(profile)
            except Exception:
                pass
            self.after(0, self._update_ui, profile)
        except Exception as e:
            self.after(0, self._on_error, str(e))

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
            self.status_var.set(f"分析完成｜共 {total} 筆交易｜授權 {len(p.get('approval_targets', []))} 筆")

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
