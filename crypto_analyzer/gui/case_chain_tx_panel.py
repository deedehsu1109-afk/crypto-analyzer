from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk

from database import db as _db

_CHAINS     = ["TRX", "ETH", "BTC", "SOL", "BNB", "OTHER"]
_DIRECTIONS = ["不明", "入帳", "出帳"]
_TOKENS     = ["USDT", "TRX", "ETH", "BTC", "BNB", "USDC", "OTHER"]

_COLS = [
    ("日期時間",   "tx_datetime",  150),
    ("鏈別",       "chain",         60),
    ("方向",       "direction",     60),
    ("發送地址",   "from_addr",    260),
    ("接收地址",   "to_addr",      260),
    ("金額",       "amount",        90),
    ("幣種",       "token_symbol",  70),
    ("交易Hash",   "tx_hash",      200),
    ("備註",       "notes",        160),
    ("來源文件",   "source_doc",   140),
]

BG_PANEL    = "#1e2235"
BG_ROW_ODD  = "#252a3d"
BG_ROW_EVEN = "#1e2235"


class ChainTxDialog(ctk.CTkToplevel):
    def __init__(self, parent, case_id: int, row: dict = None, on_save=None):
        super().__init__(parent)
        self.case_id = case_id
        self.row     = row or {}
        self.on_save = on_save
        self.title("編輯區塊鏈交易" if row else "新增區塊鏈交易")
        self.geometry("720x480")
        self.resizable(False, False)
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

    def _lbl(self, parent, text, row, required=False):
        color = "#ff9999" if required else None
        ctk.CTkLabel(parent, text=text,
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color=color, anchor="e", width=110).grid(
            row=row, column=0, padx=(12, 4), pady=5, sticky="e")

    def _entry(self, parent, row, placeholder="", width=320) -> ctk.CTkEntry:
        e = ctk.CTkEntry(parent, font=("Consolas", 10),
                         placeholder_text=placeholder, width=width)
        e.grid(row=row, column=1, padx=(4, 12), pady=5, sticky="w")
        return e

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        f = ctk.CTkFrame(self, corner_radius=10)
        f.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        f.grid_columnconfigure(1, weight=1)

        # 鏈別 + 方向
        self._lbl(f, "鏈別*：", 0, True)
        chain_f = ctk.CTkFrame(f, fg_color="transparent")
        chain_f.grid(row=0, column=1, padx=(4, 12), pady=5, sticky="w")
        self._chain_var = tk.StringVar(value=_CHAINS[0])
        ctk.CTkOptionMenu(chain_f, values=_CHAINS, variable=self._chain_var,
                          font=("Microsoft JhengHei", 11), width=100).pack(side="left")
        ctk.CTkLabel(chain_f, text="  方向：",
                     font=("Microsoft JhengHei", 11, "bold")).pack(side="left")
        self._dir_var = tk.StringVar(value=_DIRECTIONS[0])
        ctk.CTkOptionMenu(chain_f, values=_DIRECTIONS, variable=self._dir_var,
                          font=("Microsoft JhengHei", 11), width=100).pack(side="left")

        # 日期時間
        self._lbl(f, "日期時間：", 1)
        self._dt_e = self._entry(f, 1, "YYYY-MM-DD HH:MM:SS", width=200)

        # 交易Hash
        self._lbl(f, "交易Hash：", 2)
        self._hash_e = self._entry(f, 2, "0x… 或完整 tx hash", width=440)

        # 發送地址
        self._lbl(f, "發送地址：", 3)
        self._from_e = self._entry(f, 3, "", width=440)

        # 接收地址
        self._lbl(f, "接收地址：", 4)
        self._to_e = self._entry(f, 4, "", width=440)

        # 金額 + 幣種
        self._lbl(f, "金額：", 5)
        amt_f = ctk.CTkFrame(f, fg_color="transparent")
        amt_f.grid(row=5, column=1, padx=(4, 12), pady=5, sticky="w")
        self._amt_e = ctk.CTkEntry(amt_f, font=("Consolas", 11),
                                   placeholder_text="0.000000", width=130)
        self._amt_e.pack(side="left")
        ctk.CTkLabel(amt_f, text="  幣種：",
                     font=("Microsoft JhengHei", 11, "bold")).pack(side="left")
        self._tok_var = tk.StringVar(value=_TOKENS[0])
        ctk.CTkOptionMenu(amt_f, values=_TOKENS, variable=self._tok_var,
                          font=("Microsoft JhengHei", 11), width=90).pack(side="left")

        # 備註
        self._lbl(f, "備註：", 6)
        self._notes_e = self._entry(f, 6)

        # 來源文件
        self._lbl(f, "來源文件：", 7)
        self._src_e = self._entry(f, 7, "文件路徑或說明", width=360)

        # 按鈕
        btn_f = ctk.CTkFrame(f, fg_color="transparent")
        btn_f.grid(row=8, column=0, columnspan=2, pady=(10, 4))
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
        self._chain_var.set(row.get("chain", _CHAINS[0]) or _CHAINS[0])
        self._dir_var.set(row.get("direction", _DIRECTIONS[0]) or _DIRECTIONS[0])
        _set(self._dt_e,    "tx_datetime")
        _set(self._hash_e,  "tx_hash")
        _set(self._from_e,  "from_addr")
        _set(self._to_e,    "to_addr")
        amt = row.get("amount")
        self._amt_e.delete(0, "end")
        if amt is not None:
            self._amt_e.insert(0, str(amt))
        tok = row.get("token_symbol") or ""
        self._tok_var.set(tok if tok in _TOKENS else "OTHER")
        _set(self._notes_e, "notes")
        _set(self._src_e,   "source_doc")

    def _save(self):
        if not self._chain_var.get():
            messagebox.showwarning("缺少資料", "請選擇鏈別", parent=self)
            return
        amt_str = self._amt_e.get().strip()
        try:
            amount = float(amt_str) if amt_str else None
        except ValueError:
            messagebox.showwarning("格式錯誤", "金額請輸入數字", parent=self)
            return
        data = {
            "id":           self.row.get("id"),
            "chain":        self._chain_var.get(),
            "direction":    self._dir_var.get(),
            "tx_datetime":  self._dt_e.get().strip(),
            "tx_hash":      self._hash_e.get().strip(),
            "from_addr":    self._from_e.get().strip(),
            "to_addr":      self._to_e.get().strip(),
            "amount":       amount,
            "token_symbol": self._tok_var.get(),
            "notes":        self._notes_e.get().strip(),
            "source_doc":   self._src_e.get().strip(),
        }
        _db.upsert_chain_transaction(self.case_id, data)
        if self.on_save:
            self.on_save()
        self.destroy()


class CaseChainTxPanel(ctk.CTkFrame):
    def __init__(self, parent, case_id: int):
        super().__init__(parent, fg_color=BG_PANEL, corner_radius=0)
        self.case_id = case_id
        self._active_dialog: ChainTxDialog | None = None
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
        style.configure("ChainTx.Treeview",
                        background=BG_ROW_ODD, foreground="white",
                        fieldbackground=BG_ROW_ODD, rowheight=22,
                        font=("Consolas", 10))
        style.configure("ChainTx.Treeview.Heading",
                        background="#2a3556", foreground="#aac4ff",
                        font=("Microsoft JhengHei", 10, "bold"))
        style.map("ChainTx.Treeview", background=[("selected", "#3a5a9a")])
        self._tree.configure(style="ChainTx.Treeview")

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
        rows = _db.get_chain_transactions(self.case_id)
        for i, r in enumerate(rows):
            vals = tuple(r.get(k, "") or "" for _, k, _ in _COLS)
            tags = ["even" if i % 2 == 0 else "odd"]
            d = r.get("direction", "")
            if d == "入帳":
                tags.append("income")
            elif d == "出帳":
                tags.append("outgo")
            self._tree.insert("", "end", iid=str(r["id"]), values=vals, tags=tags)
        self._count_lbl.configure(text=f"共 {len(rows)} 筆")

    def _selected_row(self) -> dict | None:
        sel = self._tree.selection()
        if not sel:
            return None
        iid = sel[0]
        rows = _db.get_chain_transactions(self.case_id)
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
        self._active_dialog = ChainTxDialog(self, self.case_id, on_save=self._load)

    def _edit(self):
        if self._dialog_open():
            return
        row = self._selected_row()
        if not row:
            messagebox.showinfo("請先選取", "請先點選一筆記錄", parent=self)
            return
        self._active_dialog = ChainTxDialog(self, self.case_id, row=row, on_save=self._load)

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
            _db.delete_chain_transaction(int(iid))
        self._load()
