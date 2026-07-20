"""
case_stated_tx_panel.py
被害人陳述交易紀錄面板：整合原本分開的「一般帳戶交易」與「區塊鏈交易」。

案件資料來源多為被害人口述，非原生帳本或交易所提供的精確明細，因此：
- 時間欄位額外提供「時間精確度」與「概略時間描述」，不強制要求精確日期時間
- 新增「交易方式」欄位涵蓋銀行轉帳／場外交易(OTC)／場內交易(交易所)／
  透過交易所轉帳／私人幣商現金交易／其他，反映實務上常見的多種交易型態
- 銀行資訊與區塊鏈資訊兩組欄位並存、皆可留白，因為單一被害人陳述常同時
  涉及兩種型態（例如轉帳給幣商銀行帳戶、幣商再匯出虛擬貨幣至被害人錢包）
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk

from database import db as _db

_METHODS = ["不明", "銀行轉帳", "場外交易(OTC)", "場內交易(交易所)",
            "透過交易所轉帳", "私人幣商現金交易", "其他"]
_DIRECTIONS = ["不明", "支出", "收入"]
_TIME_PRECISIONS = ["不確定", "精確時間", "大約時間", "僅知先後順序"]
_CURRENCIES = ["TWD", "USD", "USDT", "TRX", "ETH", "BTC", "OTHER"]
_CHAINS = ["", "TRX", "ETH", "BTC", "SOL", "BNB", "OTHER"]

_COLS = [
    ("交易方式",   "method",           110),
    ("方向",       "direction",         60),
    ("日期",       "tx_date",           90),
    ("時間",       "tx_time",           60),
    ("時間精確度", "time_precision",    80),
    ("金額",       "amount",            90),
    ("幣別/幣種",  "currency",          70),
    ("對象描述",   "counterpart_desc", 160),
    ("銀行",       "bank_name",        100),
    ("鏈別",       "chain",             60),
    ("交易Hash",   "tx_hash",          140),
    ("備註",       "notes",            160),
    ("來源文件",   "source_doc",       120),
]

BG_PANEL    = "#1e2235"
BG_ROW_ODD  = "#252a3d"
BG_ROW_EVEN = "#1e2235"


class StatedTxDialog(ctk.CTkToplevel):
    def __init__(self, parent, case_id: int, row: dict = None, on_save=None):
        super().__init__(parent)
        self.case_id = case_id
        self.row     = row or {}
        self.on_save = on_save
        self.title("編輯被害人陳述交易紀錄" if row else "新增被害人陳述交易紀錄")
        self.geometry("640x760")
        self.configure(fg_color=BG_PANEL)
        self.transient(parent.winfo_toplevel())
        self.lift()
        self.focus_force()
        self._build()
        if row:
            self._fill(row)
        self.after(50, self._safe_grab)

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            self.after(50, self._safe_grab)

    def _section(self, parent, text):
        ctk.CTkLabel(parent, text=text,
                     font=("Microsoft JhengHei", 12, "bold"),
                     text_color="#aac4ff", anchor="w").pack(
            fill="x", padx=8, pady=(14, 4))
        ctk.CTkFrame(parent, height=1, fg_color="#3a4568").pack(
            fill="x", padx=8, pady=(0, 6))

    def _row(self, parent, label, widget_factory, hint=""):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(f, text=label, font=("Microsoft JhengHei", 11, "bold"),
                     anchor="e", width=100).pack(side="left", padx=(0, 8))
        widget = widget_factory(f)
        widget.pack(side="left")
        if hint:
            ctk.CTkLabel(f, text=hint, font=("Microsoft JhengHei", 9),
                         text_color="gray50").pack(side="left", padx=(8, 0))
        return widget

    def _entry(self, parent, placeholder="", width=280):
        return lambda f: ctk.CTkEntry(f, font=("Consolas", 11),
                                      placeholder_text=placeholder, width=width)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG_PANEL, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 0))

        # ── 交易方式與時間 ──────────────────────────────────────────────────
        self._section(scroll, "交易方式與時間（口述資料可能不精確，皆可留白或概略填寫）")

        self._method_var = tk.StringVar(value=_METHODS[0])
        self._row(scroll, "交易方式", lambda f: ctk.CTkOptionMenu(
            f, values=_METHODS, variable=self._method_var,
            font=("Microsoft JhengHei", 11), width=180))

        self._dir_var = tk.StringVar(value=_DIRECTIONS[0])
        self._row(scroll, "方向", lambda f: ctk.CTkOptionMenu(
            f, values=_DIRECTIONS, variable=self._dir_var,
            font=("Microsoft JhengHei", 11), width=120),
            hint="支出＝被害人付出、收入＝被害人收到")

        self._precision_var = tk.StringVar(value=_TIME_PRECISIONS[0])
        self._row(scroll, "時間精確度", lambda f: ctk.CTkOptionMenu(
            f, values=_TIME_PRECISIONS, variable=self._precision_var,
            font=("Microsoft JhengHei", 11), width=140))

        date_f = ctk.CTkFrame(scroll, fg_color="transparent")
        date_f.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(date_f, text="日期／時間", font=("Microsoft JhengHei", 11, "bold"),
                     anchor="e", width=100).pack(side="left", padx=(0, 8))
        self._date_e = ctk.CTkEntry(date_f, font=("Consolas", 11),
                                    placeholder_text="YYYY-MM-DD（可留白）", width=150)
        self._date_e.pack(side="left")
        self._time_e = ctk.CTkEntry(date_f, font=("Consolas", 11),
                                    placeholder_text="HH:MM（可留白）", width=100)
        self._time_e.pack(side="left", padx=(6, 0))

        self._timedesc_e = self._row(
            scroll, "概略時間描述",
            self._entry(scroll, "如：約下午、詐騙初期第二筆、三天內"))

        # ── 金額與對象 ──────────────────────────────────────────────────────
        self._section(scroll, "金額與對象")

        amt_f = ctk.CTkFrame(scroll, fg_color="transparent")
        amt_f.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(amt_f, text="金額", font=("Microsoft JhengHei", 11, "bold"),
                     anchor="e", width=100).pack(side="left", padx=(0, 8))
        self._amt_e = ctk.CTkEntry(amt_f, font=("Consolas", 11),
                                   placeholder_text="0.00", width=130)
        self._amt_e.pack(side="left")
        self._cur_var = tk.StringVar(value=_CURRENCIES[0])
        ctk.CTkComboBox(amt_f, values=_CURRENCIES, variable=self._cur_var,
                        font=("Microsoft JhengHei", 11), width=110).pack(
            side="left", padx=(6, 0))

        self._cpdesc_e = self._row(
            scroll, "對象描述",
            self._entry(scroll, "如：自稱OKX幣商、LINE暱稱阿凱", width=320))

        # ── 銀行資訊（可留白） ──────────────────────────────────────────────
        self._section(scroll, "銀行資訊（若涉及銀行轉帳/現金交易，可留白）")
        self._bank_e  = self._row(scroll, "銀行名稱", self._entry(scroll, "如：玉山銀行（808）"))
        self._acc_e   = self._row(scroll, "我方帳號", self._entry(scroll, "", 300))
        self._cacc_e  = self._row(scroll, "對方帳號", self._entry(scroll, "", 300))

        # ── 區塊鏈資訊（可留白） ────────────────────────────────────────────
        self._section(scroll, "區塊鏈資訊（若涉及虛擬貨幣轉帳，可留白）")
        self._chain_var = tk.StringVar(value="")
        self._row(scroll, "鏈別", lambda f: ctk.CTkComboBox(
            f, values=_CHAINS, variable=self._chain_var,
            font=("Microsoft JhengHei", 11), width=140))
        self._hash_e = self._row(scroll, "交易Hash", self._entry(scroll, "", 320))
        self._from_e = self._row(scroll, "發送地址", self._entry(scroll, "", 320))
        self._to_e   = self._row(scroll, "接收地址", self._entry(scroll, "", 320))

        # ── 其他 ────────────────────────────────────────────────────────────
        self._section(scroll, "其他")
        self._notes_e = self._row(scroll, "備註", self._entry(scroll, "", 320))
        self._src_e   = self._row(scroll, "來源文件", self._entry(scroll, "文件路徑或說明", 320))

        # 按鈕
        btn_f = ctk.CTkFrame(self, fg_color="transparent")
        btn_f.grid(row=1, column=0, pady=10)
        ctk.CTkButton(btn_f, text="儲存", width=110,
                      font=("Microsoft JhengHei", 12, "bold"),
                      command=self._save).pack(side="left", padx=8)
        ctk.CTkButton(btn_f, text="取消", width=90,
                      fg_color="gray40",
                      font=("Microsoft JhengHei", 12),
                      command=self.destroy).pack(side="left", padx=4)

    def _fill(self, row: dict):
        def _set(e, key):
            e.delete(0, "end")
            e.insert(0, str(row.get(key) or ""))
        self._method_var.set(row.get("method") or _METHODS[0])
        self._dir_var.set(row.get("direction") or _DIRECTIONS[0])
        self._precision_var.set(row.get("time_precision") or _TIME_PRECISIONS[0])
        _set(self._date_e, "tx_date")
        _set(self._time_e, "tx_time")
        _set(self._timedesc_e, "time_desc")
        amt = row.get("amount")
        self._amt_e.delete(0, "end")
        if amt is not None:
            self._amt_e.insert(0, str(amt))
        self._cur_var.set(row.get("currency") or _CURRENCIES[0])
        _set(self._cpdesc_e, "counterpart_desc")
        _set(self._bank_e, "bank_name")
        _set(self._acc_e, "account_no")
        _set(self._cacc_e, "counterpart_account")
        self._chain_var.set(row.get("chain") or "")
        _set(self._hash_e, "tx_hash")
        _set(self._from_e, "from_addr")
        _set(self._to_e, "to_addr")
        _set(self._notes_e, "notes")
        _set(self._src_e, "source_doc")

    def _save(self):
        amt_str = self._amt_e.get().strip()
        try:
            amount = float(amt_str) if amt_str else None
        except ValueError:
            messagebox.showwarning("格式錯誤", "金額請輸入數字", parent=self)
            return
        data = {
            "id":                  self.row.get("id"),
            "method":              self._method_var.get(),
            "direction":           self._dir_var.get(),
            "time_precision":      self._precision_var.get(),
            "tx_date":             self._date_e.get().strip(),
            "tx_time":             self._time_e.get().strip(),
            "time_desc":           self._timedesc_e.get().strip(),
            "amount":              amount,
            "currency":            self._cur_var.get(),
            "counterpart_desc":    self._cpdesc_e.get().strip(),
            "bank_name":           self._bank_e.get().strip(),
            "account_no":          self._acc_e.get().strip(),
            "counterpart_account": self._cacc_e.get().strip(),
            "chain":               self._chain_var.get().strip(),
            "tx_hash":             self._hash_e.get().strip(),
            "from_addr":           self._from_e.get().strip(),
            "to_addr":             self._to_e.get().strip(),
            "notes":               self._notes_e.get().strip(),
            "source_doc":          self._src_e.get().strip(),
        }
        _db.upsert_stated_transaction(self.case_id, data)
        if self.on_save:
            self.on_save()
        self.destroy()


class CaseStatedTxPanel(ctk.CTkFrame):
    def __init__(self, parent, case_id: int):
        super().__init__(parent, fg_color=BG_PANEL, corner_radius=0)
        self.case_id = case_id
        self._active_dialog: StatedTxDialog | None = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()
        self._load()

    def _build(self):
        bar = ctk.CTkFrame(self, fg_color="#252a3d", corner_radius=6)
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
        for text, cmd, color in [
            ("＋ 新增", self._add,   "#2a4a8a"),
            ("✎ 編輯", self._edit,   "#2a5a3a"),
            ("✕ 刪除", self._delete, "#7a1f1f"),
        ]:
            ctk.CTkButton(bar, text=text, width=100,
                          font=("Microsoft JhengHei", 10),
                          fg_color=color, command=cmd).pack(
                side="left", padx=2, pady=4)
        self._count_lbl = ctk.CTkLabel(bar, text="共 0 筆",
                                       font=("Microsoft JhengHei", 10),
                                       text_color="gray60")
        self._count_lbl.pack(side="right", padx=12)

        tree_f = ctk.CTkFrame(self, fg_color=BG_PANEL)
        tree_f.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        tree_f.grid_columnconfigure(0, weight=1)
        tree_f.grid_rowconfigure(0, weight=1)

        cols = tuple(k for _, k, _ in _COLS)
        self._tree = ttk.Treeview(tree_f, columns=cols,
                                  show="headings", selectmode="extended")
        for label, key, width in _COLS:
            self._tree.heading(key, text=label)
            self._tree.column(key, width=width, minwidth=30, anchor="w")

        style = ttk.Style()
        style.configure("StatedTx.Treeview",
                        background=BG_ROW_ODD, foreground="white",
                        fieldbackground=BG_ROW_ODD, rowheight=22,
                        font=("Consolas", 10))
        style.configure("StatedTx.Treeview.Heading",
                        background="#2a3556", foreground="#aac4ff",
                        font=("Microsoft JhengHei", 10, "bold"))
        style.map("StatedTx.Treeview", background=[("selected", "#3a5a9a")])
        self._tree.configure(style="StatedTx.Treeview")

        vsb = ttk.Scrollbar(tree_f, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_f, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._tree.tag_configure("even",   background=BG_ROW_EVEN)
        self._tree.tag_configure("odd",    background=BG_ROW_ODD)
        self._tree.tag_configure("income", foreground="#66dd66")
        self._tree.tag_configure("outgo",  foreground="#ff9944")
        self._tree.bind("<Double-1>", lambda e: self._edit())

    def _load(self):
        for r in self._tree.get_children():
            self._tree.delete(r)
        rows = _db.get_stated_transactions(self.case_id)
        for i, r in enumerate(rows):
            vals = tuple(r.get(k, "") or "" for _, k, _ in _COLS)
            tags = ["even" if i % 2 == 0 else "odd"]
            d = r.get("direction", "")
            if d == "收入":
                tags.append("income")
            elif d == "支出":
                tags.append("outgo")
            self._tree.insert("", "end", iid=str(r["id"]), values=vals, tags=tags)
        self._count_lbl.configure(text=f"共 {len(rows)} 筆")

    def _selected_row(self) -> dict | None:
        sel = self._tree.selection()
        if not sel:
            return None
        iid = sel[0]
        rows = _db.get_stated_transactions(self.case_id)
        return next((r for r in rows if str(r["id"]) == iid), None)

    def _dialog_open(self) -> bool:
        if self._active_dialog and self._active_dialog.winfo_exists():
            self._active_dialog.focus_force()
            return True
        self._active_dialog = None
        return False

    def _add(self):
        if self._dialog_open():
            return
        self._active_dialog = StatedTxDialog(self, self.case_id, on_save=self._load)

    def _edit(self):
        if self._dialog_open():
            return
        row = self._selected_row()
        if not row:
            messagebox.showinfo("請先選取", "請先點選一筆記錄", parent=self)
            return
        self._active_dialog = StatedTxDialog(self, self.case_id, row=row, on_save=self._load)

    def _delete(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("請先選取", "請先選取要刪除的記錄", parent=self)
            return
        if not messagebox.askyesno("確認刪除",
                                   f"確定刪除選取的 {len(sel)} 筆記錄？",
                                   parent=self):
            return
        for iid in sel:
            _db.delete_stated_transaction(int(iid))
        self._load()
