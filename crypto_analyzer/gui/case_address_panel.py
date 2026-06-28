from __future__ import annotations
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk

from database import db as _db
from api.label_fetcher import get_label

_ADDR_TYPES   = ["加密錢包", "金融帳戶"]
_HOLDER_ROLES = ["不明", "被害人", "嫌疑人", "中間人"]
_CRYPTO_CHAINS = ["TRX", "ETH", "BTC", "SOL", "BNB", "OTHER"]

_COLS = [
    ("類型",       "addr_type",         80),
    ("鏈/機構",    "chain_institution", 120),
    ("地址 / 帳號", "address",          300),
    ("持有人角色", "holder_role",        80),
    ("標記說明",   "label",             160),
    ("來源文件",   "source_doc",        140),
    ("備註",       "notes",             160),
]

BG_PANEL  = "#1e2235"
BG_ROW_ODD  = "#252a3d"
BG_ROW_EVEN = "#1e2235"


# ── 單筆新增 / 編輯對話框 ──────────────────────────────────────────────────────

class AddressDialog(ctk.CTkToplevel):
    def __init__(self, parent, case_id: int, row: dict = None, on_save=None,
                 prefill: dict = None):
        super().__init__(parent)
        self.case_id = case_id
        self.row     = row or {}
        self.on_save = on_save
        self.title("編輯地址/帳戶" if row else "新增地址/帳戶")
        self.geometry("620x480")
        self.resizable(False, False)
        self.configure(fg_color=BG_PANEL)
        self.transient(parent.winfo_toplevel())
        self.lift()
        self.focus_force()
        self._build()
        if row:
            self._fill(row)
        elif prefill:
            self._fill(prefill)
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

    def _entry(self, parent, row, placeholder="", width=300) -> ctk.CTkEntry:
        e = ctk.CTkEntry(parent, font=("Consolas", 11),
                         placeholder_text=placeholder, width=width)
        e.grid(row=row, column=1, padx=(4, 12), pady=5, sticky="w")
        return e

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        f = ctk.CTkFrame(self, corner_radius=10)
        f.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        f.grid_columnconfigure(1, weight=1)

        # 類型
        self._lbl(f, "類型*：", 0, True)
        self._type_var = tk.StringVar(value=_ADDR_TYPES[0])
        ctk.CTkOptionMenu(f, values=_ADDR_TYPES,
                          variable=self._type_var,
                          font=("Microsoft JhengHei", 11), width=180,
                          command=self._on_type_change).grid(
            row=0, column=1, padx=(4, 12), pady=5, sticky="w")

        # 鏈 / 機構
        self._lbl(f, "鏈/機構*：", 1, True)
        self._chain_var = tk.StringVar(value="TRX")
        self._chain_menu = ctk.CTkOptionMenu(f, values=_CRYPTO_CHAINS,
                                              variable=self._chain_var,
                                              font=("Microsoft JhengHei", 11), width=180)
        self._chain_menu.grid(row=1, column=1, padx=(4, 12), pady=5, sticky="w")
        self._chain_entry = ctk.CTkEntry(f, font=("Consolas", 11),
                                          placeholder_text="銀行名稱（如 玉山銀行808）", width=260)

        # 地址 / 帳號
        self._lbl(f, "地址/帳號*：", 2, True)
        self.addr_e = ctk.CTkEntry(f, font=("Consolas", 10),
                                    placeholder_text="錢包地址或銀行帳號", width=380)
        self.addr_e.grid(row=2, column=1, padx=(4, 12), pady=5, sticky="w")

        # 持有人角色
        self._lbl(f, "持有人角色：", 3)
        self._role_var = tk.StringVar(value=_HOLDER_ROLES[0])
        ctk.CTkOptionMenu(f, values=_HOLDER_ROLES,
                          variable=self._role_var,
                          font=("Microsoft JhengHei", 11), width=180).grid(
            row=3, column=1, padx=(4, 12), pady=5, sticky="w")

        # 標記說明
        self._lbl(f, "標記說明：", 4)
        label_row = ctk.CTkFrame(f, fg_color="transparent")
        label_row.grid(row=4, column=1, padx=(4, 12), pady=5, sticky="w")
        self.label_e = ctk.CTkEntry(label_row, font=("Consolas", 11),
                                     placeholder_text="如：被害人OKX帳戶 / 詐騙收款錢包",
                                     width=240)
        self.label_e.pack(side="left")
        self._fetch_btn = ctk.CTkButton(label_row, text="🔍 查標籤", width=80,
                                         font=("Microsoft JhengHei", 10),
                                         fg_color="#2a4a6a",
                                         command=self._fetch_label)
        self._fetch_btn.pack(side="left", padx=(6, 0))

        # 備註
        self._lbl(f, "備註：", 5)
        self.notes_e = self._entry(f, 5)

        # 按鈕
        btn_f = ctk.CTkFrame(f, fg_color="transparent")
        btn_f.grid(row=6, column=0, columnspan=2, pady=(10, 4))
        ctk.CTkButton(btn_f, text="儲存", width=110,
                      font=("Microsoft JhengHei", 12, "bold"),
                      command=self._save).pack(side="left", padx=8)
        ctk.CTkButton(btn_f, text="取消", width=90,
                      fg_color="gray40",
                      font=("Microsoft JhengHei", 12),
                      command=self.destroy).pack(side="left", padx=4)

    def _on_type_change(self, val):
        if val == "加密錢包":
            self._chain_entry.grid_remove()
            self._chain_menu.grid(row=1, column=1, padx=(4, 12), pady=5, sticky="w")
        else:
            self._chain_menu.grid_remove()
            self._chain_entry.grid(row=1, column=1, padx=(4, 12), pady=5, sticky="w")

    def _fill(self, row: dict):
        self._type_var.set(row.get("addr_type", _ADDR_TYPES[0]))
        chain_inst = row.get("chain_institution", "")
        if row.get("addr_type") == "加密錢包":
            self._chain_var.set(chain_inst if chain_inst in _CRYPTO_CHAINS else "OTHER")
        else:
            self._on_type_change("金融帳戶")
            self._chain_entry.delete(0, "end")
            self._chain_entry.insert(0, chain_inst)
        self.addr_e.delete(0, "end")
        self.addr_e.insert(0, row.get("address", ""))
        self._role_var.set(row.get("holder_role", _HOLDER_ROLES[0]))
        self.label_e.delete(0, "end")
        self.label_e.insert(0, row.get("label", "") or "")
        self.notes_e.delete(0, "end")
        self.notes_e.insert(0, row.get("notes", "") or "")

    def _fetch_label(self):
        addr = self.addr_e.get().strip()
        chain = self._chain_var.get() if self._type_var.get() == "加密錢包" else ""
        if not addr:
            messagebox.showwarning("缺少資料", "請先輸入地址", parent=self)
            return
        if chain not in ("BTC", "TRX"):
            messagebox.showinfo("不支援", f"目前僅支援 BTC / TRX 自動查標籤\n（{chain} 尚未支援）",
                                parent=self)
            return
        self._fetch_btn.configure(text="查詢中…", state="disabled")

        def _query():
            label = get_label(addr, chain)
            self.after(0, lambda: self._apply_label(label))

        threading.Thread(target=_query, daemon=True).start()

    def _apply_label(self, label: str | None):
        self._fetch_btn.configure(text="🔍 查標籤", state="normal")
        if label:
            self.label_e.delete(0, "end")
            self.label_e.insert(0, label)
        else:
            messagebox.showinfo("查無標籤", "此地址在資料來源中沒有標籤記錄", parent=self)

    def _save(self):
        addr = self.addr_e.get().strip()
        if not addr:
            messagebox.showwarning("缺少資料", "地址/帳號為必填", parent=self)
            return

        # ── 重複地址檢查 ──
        current_id = self.row.get("id")
        for existing in _db.get_case_addresses(self.case_id):
            if (existing["address"].strip().lower() == addr.lower()
                    and existing["id"] != current_id):
                dup_chain = existing.get("chain_institution", "")
                dup_role  = existing.get("holder_role", "")
                dup_label = existing.get("label") or "（無標記）"
                messagebox.showwarning(
                    "地址重複",
                    f"此地址已存在於本案件，拒絕重複新增。\n\n"
                    f"地址：{addr}\n"
                    f"鏈／機構：{dup_chain}　角色：{dup_role}\n"
                    f"標記：{dup_label}",
                    parent=self,
                )
                return

        addr_type = self._type_var.get()
        if addr_type == "加密錢包":
            chain_inst = self._chain_var.get()
        else:
            chain_inst = self._chain_entry.get().strip()
        data = {
            "id":                self.row.get("id"),
            "addr_type":         addr_type,
            "chain_institution": chain_inst,
            "address":           addr,
            "holder_role":       self._role_var.get(),
            "label":             self.label_e.get().strip(),
            "notes":             self.notes_e.get().strip(),
            "source_doc":        self.row.get("source_doc", ""),
        }
        _db.upsert_case_address(self.case_id, data)
        if self.on_save:
            self.on_save()
        self.destroy()


# ── 主面板 ─────────────────────────────────────────────────────────────────────

class CaseAddressPanel(ctk.CTkFrame):
    def __init__(self, parent, case_id: int):
        super().__init__(parent, fg_color=BG_PANEL, corner_radius=0)
        self.case_id = case_id
        self._active_dialog: AddressDialog | None = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()
        self._load()

    def _build(self):
        # ── 工具列 ──
        bar = ctk.CTkFrame(self, fg_color="#252a3d", corner_radius=6)
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))

        for text, cmd, color in [
            ("＋ 新增", self._add,        "#2a4a8a"),
            ("✎ 編輯", self._edit,        "#2a5a3a"),
            ("✕ 刪除", self._delete,      "#7a1f1f"),
            ("📂 從文件提取", self._import_doc, "#4a3a7a"),
            ("⇒ 加入幣流圖", self._to_flow_graph, "#5a4a2a"),
            ("🔍 批次查標籤", self._batch_fetch_labels, "#2a5a5a"),
        ]:
            ctk.CTkButton(bar, text=text, width=110,
                          font=("Microsoft JhengHei", 10),
                          fg_color=color,
                          command=cmd).pack(side="left", padx=2, pady=4)

        self._count_lbl = ctk.CTkLabel(bar, text="共 0 筆",
                                        font=("Microsoft JhengHei", 10),
                                        text_color="gray60")
        self._count_lbl.pack(side="right", padx=12)

        # ── 表格 ──
        tree_f = ctk.CTkFrame(self, fg_color=BG_PANEL)
        tree_f.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        tree_f.grid_columnconfigure(0, weight=1)
        tree_f.grid_rowconfigure(0, weight=1)

        cols = tuple(k for _, k, _ in _COLS)
        self._tree = ttk.Treeview(tree_f, columns=cols,
                                   show="headings", selectmode="extended")
        for label, key, width in _COLS:
            self._tree.heading(key, text=label)
            self._tree.column(key, width=width, minwidth=40, anchor="w")

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview",
                         background=BG_ROW_ODD, foreground="white",
                         fieldbackground=BG_ROW_ODD, rowheight=22,
                         font=("Consolas", 10))
        style.configure("Treeview.Heading",
                         background="#2a3556", foreground="#aac4ff",
                         font=("Microsoft JhengHei", 10, "bold"))
        style.map("Treeview", background=[("selected", "#3a5a9a")])

        vsb = ttk.Scrollbar(tree_f, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_f, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._tree.tag_configure("even", background=BG_ROW_EVEN)
        self._tree.tag_configure("odd",  background=BG_ROW_ODD)
        self._tree.tag_configure("suspect", foreground="#ff9944")
        self._tree.tag_configure("victim",  foreground="#66dd66")

        self._tree.bind("<Double-1>", lambda e: self._edit())

    def _load(self):
        for row in self._tree.get_children():
            self._tree.delete(row)
        rows = _db.get_case_addresses(self.case_id)
        for i, r in enumerate(rows):
            vals = tuple(r.get(k, "") or "" for _, k, _ in _COLS)
            tags = ["even" if i % 2 == 0 else "odd"]
            role = r.get("holder_role", "")
            if role == "嫌疑人":
                tags.append("suspect")
            elif role == "被害人":
                tags.append("victim")
            self._tree.insert("", "end", iid=str(r["id"]), values=vals, tags=tags)
        self._count_lbl.configure(text=f"共 {len(rows)} 筆")

    def _selected_row(self) -> dict | None:
        sel = self._tree.selection()
        if not sel:
            return None
        iid = sel[0]
        rows = _db.get_case_addresses(self.case_id)
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
        self._active_dialog = AddressDialog(self, self.case_id, on_save=self._load)

    def _edit(self):
        if self._dialog_open():
            return
        row = self._selected_row()
        if not row:
            messagebox.showinfo("請先選取", "請先點選一筆記錄", parent=self)
            return
        self._active_dialog = AddressDialog(self, self.case_id, row=row, on_save=self._load)

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
            _db.delete_case_address(int(iid))
        self._load()

    def _import_doc(self):
        """從文件中提取涉案地址/帳戶"""
        paths = filedialog.askopenfilenames(
            title="選擇文件檔案",
            filetypes=[
                ("支援文件", "*.pdf *.docx *.xlsx *.odt *.txt"),
                ("全部", "*.*"),
            ],
            parent=self,
        )
        if not paths:
            return

        def do_import():
            from analyzer.doc_transaction_extractor import analyze_files
            result = analyze_files(list(paths))
            addrs = result.get("addresses", [])
            imported = 0
            for a in addrs:
                _db.upsert_case_address(self.case_id, a)
                imported += 1
            self.after(0, self._load)
            self.after(0, lambda: messagebox.showinfo(
                "提取完成",
                f"從 {len(paths)} 份文件提取到 {imported} 筆涉案地址/帳戶。\n"
                "請逐筆確認持有人角色與標記說明。",
                parent=self))

        threading.Thread(target=do_import, daemon=True).start()

    def _to_flow_graph(self):
        """將選取的加密錢包地址加入幣流圖"""
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("請先選取", "請先選取要加入幣流圖的錢包地址", parent=self)
            return
        rows = _db.get_case_addresses(self.case_id)
        row_map = {str(r["id"]): r for r in rows}
        added = []
        for iid in sel:
            r = row_map.get(iid)
            if r and r.get("addr_type") == "加密錢包":
                added.append(r)
        if not added:
            messagebox.showinfo("無可加入項目",
                                "選取的記錄中沒有「加密錢包」類型，\n"
                                "只有加密錢包地址可加入幣流圖。",
                                parent=self)
            return
        # 透過事件通知主視窗
        self.event_generate("<<AddToFlowGraph>>",
                            data=str([r["address"] for r in added]))
        messagebox.showinfo("已標記",
                            f"已標記 {len(added)} 個地址，\n"
                            "請切換至「幣流關聯圖」分頁確認。",
                            parent=self)

    def _batch_fetch_labels(self):
        """批次查詢所有 BTC/TRX 地址的標籤（僅更新標記說明為空的記錄）"""
        rows = _db.get_case_addresses(self.case_id)
        targets = [
            r for r in rows
            if r.get("addr_type") == "加密錢包"
            and r.get("chain_institution") in ("BTC", "TRX")
            and not r.get("label")
        ]
        if not targets:
            messagebox.showinfo("無需查詢",
                                "沒有待查標籤的 BTC/TRX 地址\n（已有標記說明的地址不會被覆蓋）",
                                parent=self)
            return

        if not messagebox.askyesno("批次查標籤",
                                    f"將查詢 {len(targets)} 個未標記的 BTC/TRX 地址，\n"
                                    "是否繼續？",
                                    parent=self):
            return

        def _run():
            updated = 0
            for r in targets:
                label = get_label(r["address"], r["chain_institution"])
                if label:
                    data = dict(r)
                    data["label"] = label
                    _db.upsert_case_address(self.case_id, data)
                    updated += 1
            self.after(0, self._load)
            self.after(0, lambda: messagebox.showinfo(
                "批次查標籤完成",
                f"查詢 {len(targets)} 筆，成功標記 {updated} 筆。",
                parent=self))

        threading.Thread(target=_run, daemon=True).start()
