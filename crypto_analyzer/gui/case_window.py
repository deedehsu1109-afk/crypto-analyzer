from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
from database import db as _db

_CASE_TYPES   = ["一般", "詐欺", "洗錢", "資恐", "勒索軟體", "非法交易所", "其他"]
_CASE_STATUSES = ["進行中", "已結案", "暫停", "移送"]


# ── 新增 / 編輯案件對話框 ──────────────────────────────────────────────────────

class CaseDialog(ctk.CTkToplevel):
    def __init__(self, parent, case: dict = None, on_save=None):
        super().__init__(parent)
        self.case     = case
        self.on_save  = on_save
        self.result   = None
        self.title("編輯案件" if case else "新建案件")
        self.geometry("560x520")
        self.resizable(False, False)
        self.grab_set()
        self._build()
        if case:
            self._fill(case)

    def _lbl(self, parent, text, row):
        ctk.CTkLabel(parent, text=text,
                     font=("Microsoft JhengHei", 12, "bold"),
                     anchor="e", width=100).grid(
            row=row, column=0, padx=(16, 6), pady=6, sticky="e")

    def _build(self):
        self.grid_columnconfigure(0, weight=1)

        f = ctk.CTkFrame(self, corner_radius=10)
        f.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        f.grid_columnconfigure(1, weight=1)

        # 案件編號
        self._lbl(f, "案件編號：", 0)
        self.num_entry = ctk.CTkEntry(f, font=("Consolas", 12))
        self.num_entry.insert(0, _db.next_case_number())
        self.num_entry.grid(row=0, column=1, padx=(0, 16), pady=6, sticky="ew")

        # 案件名稱
        self._lbl(f, "案件名稱：", 1)
        self.name_entry = ctk.CTkEntry(f, font=("Microsoft JhengHei", 12),
                                       placeholder_text="請輸入案件名稱")
        self.name_entry.grid(row=1, column=1, padx=(0, 16), pady=6, sticky="ew")

        # 案件類型
        self._lbl(f, "案件類型：", 2)
        self.type_var = ctk.StringVar(value=_CASE_TYPES[0])
        ctk.CTkOptionMenu(f, variable=self.type_var,
                          values=_CASE_TYPES,
                          font=("Microsoft JhengHei", 12)).grid(
            row=2, column=1, padx=(0, 16), pady=6, sticky="w")

        # 狀態
        self._lbl(f, "狀態：", 3)
        self.status_var = ctk.StringVar(value=_CASE_STATUSES[0])
        ctk.CTkOptionMenu(f, variable=self.status_var,
                          values=_CASE_STATUSES,
                          font=("Microsoft JhengHei", 12)).grid(
            row=3, column=1, padx=(0, 16), pady=6, sticky="w")

        # 承辦人
        self._lbl(f, "承辦人：", 4)
        self.inv_entry = ctk.CTkEntry(f, font=("Microsoft JhengHei", 12),
                                      placeholder_text="承辦人姓名")
        self.inv_entry.grid(row=4, column=1, padx=(0, 16), pady=6, sticky="ew")

        # 案件描述
        self._lbl(f, "案件描述：", 5)
        self.desc_text = ctk.CTkTextbox(f, height=80,
                                        font=("Microsoft JhengHei", 11))
        self.desc_text.grid(row=5, column=1, padx=(0, 16), pady=6, sticky="ew")

        # 備註
        self._lbl(f, "備註：", 6)
        self.notes_text = ctk.CTkTextbox(f, height=60,
                                         font=("Microsoft JhengHei", 11))
        self.notes_text.grid(row=6, column=1, padx=(0, 16), pady=6, sticky="ew")

        # 按鈕
        btn_f = ctk.CTkFrame(self, fg_color="transparent")
        btn_f.grid(row=1, column=0, pady=(0, 16))
        ctk.CTkButton(btn_f, text="儲存", width=120,
                      font=("Microsoft JhengHei", 13, "bold"),
                      command=self._save).pack(side="left", padx=8)
        ctk.CTkButton(btn_f, text="取消", width=90,
                      font=("Microsoft JhengHei", 12),
                      fg_color="gray40",
                      command=self.destroy).pack(side="left", padx=8)

    def _fill(self, case: dict):
        self.num_entry.delete(0, "end")
        self.num_entry.insert(0, case.get("case_number", ""))
        self.name_entry.insert(0, case.get("case_name", ""))
        self.type_var.set(case.get("case_type", _CASE_TYPES[0]))
        self.status_var.set(case.get("status", _CASE_STATUSES[0]))
        self.inv_entry.insert(0, case.get("investigator", ""))
        self.desc_text.insert("1.0", case.get("description", ""))
        self.notes_text.insert("1.0", case.get("notes", ""))

    def _save(self):
        num  = self.num_entry.get().strip()
        name = self.name_entry.get().strip()
        if not num:
            messagebox.showwarning("缺少資料", "請填寫案件編號", parent=self)
            return
        if not name:
            messagebox.showwarning("缺少資料", "請填寫案件名稱", parent=self)
            return
        data = {
            "case_number":  num,
            "case_name":    name,
            "case_type":    self.type_var.get(),
            "status":       self.status_var.get(),
            "investigator": self.inv_entry.get().strip(),
            "description":  self.desc_text.get("1.0", "end").strip(),
            "notes":        self.notes_text.get("1.0", "end").strip(),
        }
        try:
            if self.case:
                _db.update_case(self.case["id"], **{k: v for k, v in data.items()
                                                    if k != "case_number"})
                self.result = {**self.case, **data}
            else:
                new_id = _db.create_case(**data)
                self.result = {**data, "id": new_id}
        except Exception as e:
            messagebox.showerror("儲存失敗", str(e), parent=self)
            return
        if self.on_save:
            self.on_save(self.result)
        self.destroy()


# ── 連結查詢記錄到案件的對話框 ────────────────────────────────────────────────

class LinkToCaseDialog(ctk.CTkToplevel):
    """選擇要連結的案件"""
    def __init__(self, parent, title="選擇案件", on_select=None):
        super().__init__(parent)
        self.on_select = on_select
        self.title(title)
        self.geometry("520x420")
        self.grab_set()
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text="請選擇要關聯的案件：",
                     font=("Microsoft JhengHei", 13, "bold")).grid(
            row=0, column=0, padx=16, pady=(16, 4), sticky="w")

        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        style.configure("Treeview", background="#2b2b2b", foreground="white",
                        fieldbackground="#2b2b2b", rowheight=24,
                        font=("Microsoft JhengHei", 10))
        style.configure("Treeview.Heading", background="#1f1f2e",
                        foreground="white", font=("Microsoft JhengHei", 10, "bold"))
        style.map("Treeview", background=[("selected", "#1f538d")])

        cols = ("案件編號", "案件名稱", "類型", "狀態", "承辦人", "建立時間")
        vsb  = ttk.Scrollbar(frame, orient="vertical")
        self._tree = ttk.Treeview(frame, columns=cols, show="headings",
                                  yscrollcommand=vsb.set, height=12)
        vsb.config(command=self._tree.yview)
        widths = [130, 160, 70, 70, 80, 130]
        for col, w in zip(cols, widths):
            self._tree.heading(col, text=col)
            self._tree.column(col, width=w, minwidth=60)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.grid_rowconfigure(0, weight=1)

        self._cases: list[dict] = []
        self._load()
        self._tree.bind("<Double-1>", lambda _: self._select())

        btn_f = ctk.CTkFrame(self, fg_color="transparent")
        btn_f.grid(row=2, column=0, pady=(4, 16))
        ctk.CTkButton(btn_f, text="選擇", width=100,
                      font=("Microsoft JhengHei", 12, "bold"),
                      command=self._select).pack(side="left", padx=8)
        ctk.CTkButton(btn_f, text="取消", width=80,
                      fg_color="gray40",
                      font=("Microsoft JhengHei", 12),
                      command=self.destroy).pack(side="left", padx=8)

    def _load(self):
        self._cases = _db.get_all_cases()
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        for c in self._cases:
            self._tree.insert("", "end", iid=str(c["id"]), values=(
                c.get("case_number",""), c.get("case_name",""),
                c.get("case_type",""), c.get("status",""),
                c.get("investigator",""), c.get("created_at",""),
            ))

    def _select(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("提示", "請先選取一個案件", parent=self)
            return
        case_id = int(sel[0])
        case    = next((c for c in self._cases if c["id"] == case_id), None)
        if case and self.on_select:
            self.on_select(case)
        self.destroy()
