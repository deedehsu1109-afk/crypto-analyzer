from __future__ import annotations
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
from database import db as _db

_CASE_TYPES    = ["一般", "詐欺", "洗錢", "資恐", "勒索軟體", "非法交易所", "其他"]
_CASE_STATUSES = ["進行中", "已結案", "暫停", "移送"]


# ══════════════════════════════════════════════════════════════════════════════
# 新增 / 編輯案件對話框（多分頁）
# ══════════════════════════════════════════════════════════════════════════════

class CaseDialog(ctk.CTkToplevel):
    def __init__(self, parent, case: dict = None, on_save=None):
        super().__init__(parent)
        self.case      = case          # None = 新建，dict = 編輯
        self.on_save   = on_save
        self._case_id  = case["id"] if case else None  # 已存入 DB 的案件 ID
        self._pending_addrs: list[dict] = []             # 案件尚未儲存時暫存的提取地址
        self.title("編輯案件" if case else "新建案件")
        self.geometry("960x680")
        self.resizable(True, True)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()
        if case:
            self._fill(case)

    # ── 頂部：匯入案件編號 ─────────────────────────────────────────────────────

    def _build(self):
        # ── 頂部工具列：匯入案件編號 ──
        top = ctk.CTkFrame(self, corner_radius=8, fg_color="#1a2744")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(top, text="匯入案件編號：",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#aac4ff").grid(
            row=0, column=0, padx=(12, 4), pady=8)
        self._import_entry = ctk.CTkEntry(
            top, font=("Consolas", 11), width=240,
            placeholder_text="輸入案件編號（如 CASE-20260603-001）載入既有案件")
        self._import_entry.grid(row=0, column=1, padx=4, pady=8)
        ctk.CTkButton(top, text="載入", width=70,
                      font=("Microsoft JhengHei", 11),
                      fg_color="#2a4a8a",
                      command=self._import_by_number).grid(
            row=0, column=2, padx=(4, 4), pady=8, sticky="w")
        ctk.CTkLabel(top,
                     text="← 輸入既有案件編號可載入資料繼續編輯；或直接填寫下方欄位新建案件",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray60").grid(
            row=0, column=3, padx=(8, 12), pady=8, sticky="w")
        top.grid_columnconfigure(3, weight=1)

        # ── 分頁內容 ──
        self._tabs = ctk.CTkTabview(self, corner_radius=10)
        self._tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 4))
        for name in ["案件基本資料", "涉案錢包 / 帳戶"]:
            self._tabs.add(name)
        self._tabs.set("案件基本資料")

        self._build_basic_tab()
        self._build_address_tab()

        # ── 底部按鈕 ──
        btn_f = ctk.CTkFrame(self, fg_color="transparent")
        btn_f.grid(row=2, column=0, pady=(0, 10))
        ctk.CTkButton(btn_f, text="儲存案件", width=130,
                      font=("Microsoft JhengHei", 13, "bold"),
                      command=self._save).pack(side="left", padx=8)
        ctk.CTkButton(btn_f, text="取消", width=90,
                      fg_color="gray40",
                      font=("Microsoft JhengHei", 12),
                      command=self.destroy).pack(side="left", padx=4)

    # ── 分頁一：案件基本資料 ───────────────────────────────────────────────────

    def _build_basic_tab(self):
        tab = self._tabs.tab("案件基本資料")
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(6, weight=1)  # description
        tab.grid_rowconfigure(7, weight=0)

        def lbl(text, row, required=False):
            color = "#ff9999" if required else None
            ctk.CTkLabel(tab, text=text,
                         font=("Microsoft JhengHei", 12, "bold"),
                         text_color=color, anchor="e", width=110).grid(
                row=row, column=0, padx=(8, 4), pady=5, sticky="e")

        # 案件編號
        lbl("案件編號*：", 0, True)
        num_f = ctk.CTkFrame(tab, fg_color="transparent")
        num_f.grid(row=0, column=1, sticky="ew", padx=(4, 8), pady=5)
        self._num_e = ctk.CTkEntry(num_f, font=("Consolas", 12), width=220)
        self._num_e.insert(0, _db.next_case_number())
        self._num_e.pack(side="left")
        ctk.CTkLabel(num_f, text="（可自行修改）",
                     font=("Microsoft JhengHei", 9),
                     text_color="gray60").pack(side="left", padx=6)

        # 案件名稱
        lbl("案件名稱*：", 1, True)
        self._name_e = ctk.CTkEntry(tab, font=("Microsoft JhengHei", 12),
                                    placeholder_text="請輸入案件名稱")
        self._name_e.grid(row=1, column=1, sticky="ew", padx=(4, 8), pady=5)

        # 案件類型 + 狀態（同列）
        lbl("案件類型：", 2)
        ts_f = ctk.CTkFrame(tab, fg_color="transparent")
        ts_f.grid(row=2, column=1, sticky="w", padx=(4, 8), pady=5)
        self._type_var = ctk.StringVar(value=_CASE_TYPES[0])
        ctk.CTkOptionMenu(ts_f, variable=self._type_var,
                          values=_CASE_TYPES, width=130,
                          font=("Microsoft JhengHei", 11)).pack(side="left")
        ctk.CTkLabel(ts_f, text="   狀態：",
                     font=("Microsoft JhengHei", 12, "bold")).pack(side="left")
        self._status_var = ctk.StringVar(value=_CASE_STATUSES[0])
        ctk.CTkOptionMenu(ts_f, variable=self._status_var,
                          values=_CASE_STATUSES, width=110,
                          font=("Microsoft JhengHei", 11)).pack(side="left")

        # 承辦人
        lbl("承辦人：", 3)
        self._inv_e = ctk.CTkEntry(tab, font=("Microsoft JhengHei", 12),
                                   placeholder_text="承辦人姓名")
        self._inv_e.grid(row=3, column=1, sticky="ew", padx=(4, 8), pady=5)

        # 案件描述（多行 + 匯入按鈕）
        lbl("案件描述：", 5)
        desc_ctrl = ctk.CTkFrame(tab, fg_color="transparent")
        desc_ctrl.grid(row=4, column=1, sticky="w", padx=(4, 8), pady=(4, 0))
        ctk.CTkButton(desc_ctrl, text="📄 從文件匯入摘要", width=160,
                      font=("Microsoft JhengHei", 10),
                      fg_color="#4a3a7a",
                      command=self._import_folder_to_desc).pack(side="left")
        ctk.CTkLabel(desc_ctrl,
                     text="（可多選 PDF/DOCX/XLSX/ODT 文件，自動摘要）",
                     font=("Microsoft JhengHei", 9),
                     text_color="gray60").pack(side="left", padx=6)

        self._desc_t = ctk.CTkTextbox(tab, font=("Microsoft JhengHei", 11),
                                       height=130)
        self._desc_t.grid(row=5, column=0, columnspan=2,
                          sticky="nsew", padx=8, pady=(0, 4))

        # 備註
        lbl("備註：", 6)
        self._notes_t = ctk.CTkTextbox(tab, font=("Microsoft JhengHei", 11),
                                        height=70)
        self._notes_t.grid(row=6, column=0, columnspan=2,
                           sticky="nsew", padx=8, pady=(0, 8))

    # ── 分頁二：涉案錢包 / 帳戶 ──────────────────────────────────────────────

    def _build_address_tab(self):
        tab = self._tabs.tab("涉案錢包 / 帳戶")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # 提示列
        hint = ctk.CTkFrame(tab, fg_color="#1a2744", corner_radius=6)
        hint.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
        hint.grid_columnconfigure(0, weight=1)
        self._tx_hint_lbl = ctk.CTkLabel(
            hint,
            text="請先在「案件基本資料」分頁填寫案件名稱並點「儲存案件」，即可新增涉案地址/帳戶。",
            font=("Microsoft JhengHei", 11), text_color="#f5a623")
        self._tx_hint_lbl.grid(row=0, column=0, padx=12, pady=8, sticky="w")

        # 面板容器（儲存後才填入）
        self._victim_panel_container = ctk.CTkFrame(tab, corner_radius=8)
        self._victim_panel_container.grid(row=1, column=0,
                                           sticky="nsew", padx=4, pady=(0, 4))
        self._victim_panel_container.grid_columnconfigure(0, weight=1)
        self._victim_panel_container.grid_rowconfigure(0, weight=1)
        self._victim_tx_panel = None   # 保留屬性名稱供其他方法使用

        if self._case_id:
            self._init_victim_panel()

    def _init_victim_panel(self):
        """初始化涉案地址/帳戶面板（需有 case_id）"""
        from gui.case_address_panel import CaseAddressPanel
        for w in self._victim_panel_container.winfo_children():
            w.destroy()
        self._victim_tx_panel = CaseAddressPanel(
            self._victim_panel_container, self._case_id)
        self._victim_tx_panel.grid(row=0, column=0, sticky="nsew")
        self._tx_hint_lbl.configure(
            text=f"案件 ID：{self._case_id}　可新增、編輯、從文件提取涉案錢包地址或金融帳戶。",
            text_color="#aaffaa")

    # ── 匯入案件編號 ──────────────────────────────────────────────────────────

    def _import_by_number(self):
        num = self._import_entry.get().strip()
        if not num:
            messagebox.showwarning("缺少輸入", "請輸入案件編號", parent=self)
            return
        found = _db.get_case_by_number(num)
        if not found:
            messagebox.showerror("找不到案件",
                                 f"找不到案件編號：{num}\n請確認編號是否正確。",
                                 parent=self)
            return
        # 確認是否覆蓋目前填寫的資料
        if (self._name_e.get().strip() and
                not messagebox.askyesno("確認載入",
                                        f"載入「{found['case_number']} {found['case_name']}」\n"
                                        "將覆蓋目前已填入的資料，是否繼續？",
                                        parent=self)):
            return
        self._case_id = found["id"]
        self.case     = found
        self._fill(found)
        self._init_victim_panel()
        self._import_entry.delete(0, "end")
        self.title(f"編輯案件：{found['case_number']}")

    # ── 從資料夾匯入摘要 ──────────────────────────────────────────────────────

    def _import_folder_to_desc(self):
        paths = filedialog.askopenfilenames(
            title="選擇案件文件（可多選）",
            filetypes=[
                ("支援文件", "*.pdf *.docx *.doc *.xlsx *.odt *.txt"),
                ("PDF", "*.pdf"),
                ("Word", "*.docx *.doc"),
                ("Excel", "*.xlsx"),
                ("ODT", "*.odt"),
                ("文字", "*.txt"),
                ("全部", "*.*"),
            ],
            parent=self)
        if not paths:
            return

        self._desc_t.insert("end", "\n\n【分析中，請稍候…】")
        self.update_idletasks()

        # 在主執行緒先取得目前描述內容（Tkinter 不允許從背景執行緒存取 widget）
        cur_desc_snapshot = self._desc_t.get("1.0", "end").strip()

        def do_import():
            try:
                from analyzer.doc_transaction_extractor import (
                    analyze_files, summarize_for_case)
                result  = analyze_files(list(paths))
                summary = summarize_for_case(result["raw_text"])
                cur_desc = cur_desc_snapshot.replace("【分析中，請稍候…】", "").strip()
                new_desc = ((cur_desc + "\n\n") if cur_desc else "") + \
                           "【文件分析摘要】\n" + summary
                self.after(0, self._fill_desc, new_desc, result)
            except Exception as e:
                self.after(0, lambda err=e: messagebox.showerror(
                    "分析失敗", f"文件分析時發生錯誤：\n{err}", parent=self))

        threading.Thread(target=do_import, daemon=True).start()

    def _fill_desc(self, new_desc: str, result: dict):
        self._desc_t.delete("1.0", "end")
        self._desc_t.insert("1.0", new_desc)
        # 同步更新資料庫
        if self._case_id:
            _db.update_case(self._case_id, description=new_desc)

        addrs = result.get("addresses", [])
        proc  = len(result["processed_files"])
        err   = len(result["error_files"])

        if not addrs:
            messagebox.showinfo(
                "文件分析完成",
                f"已處理 {proc} 份文件（{err} 份失敗），摘要已填入案件描述。\n"
                "未從文件中提取到錢包地址或金融帳號。",
                parent=self)
            return

        # ── 有提取到地址/帳戶 ──
        if self._case_id:
            imported = 0
            for a in addrs:
                _db.upsert_case_address(self._case_id, a)
                imported += 1
            if self._victim_tx_panel:
                self._victim_tx_panel._load()
            else:
                self._init_victim_panel()
            self._tabs.set("涉案錢包 / 帳戶")
            messagebox.showinfo(
                "文件匯入完成",
                f"已處理 {proc} 份文件（{err} 份失敗）\n"
                f"摘要已填入「案件描述」\n"
                f"提取 {imported} 筆涉案地址/帳戶，目前顯示於「涉案錢包 / 帳戶」分頁。",
                parent=self)
        else:
            if self._auto_save():
                imported = 0
                for a in addrs:
                    _db.upsert_case_address(self._case_id, a)
                    imported += 1
                self._init_victim_panel()
                self._tabs.set("涉案錢包 / 帳戶")
                messagebox.showinfo(
                    "文件匯入完成",
                    f"已處理 {proc} 份文件（{err} 份失敗）\n"
                    f"案件已自動儲存\n"
                    f"提取 {imported} 筆涉案地址/帳戶，目前顯示於「涉案錢包 / 帳戶」分頁。",
                    parent=self)
            else:
                self._pending_addrs = addrs
                self._update_pending_hint()
                messagebox.showinfo(
                    "文件分析完成（暫存）",
                    f"已處理 {proc} 份文件（{err} 份失敗）\n"
                    f"摘要已填入「案件描述」\n"
                    f"提取到 {len(addrs)} 筆涉案地址/帳戶。\n\n"
                    "⚠ 案件尚未儲存（請填寫案件名稱）\n"
                    "儲存案件後地址/帳戶將自動匯入。",
                    parent=self)

    def _update_pending_hint(self):
        """在涉案錢包/帳戶分頁提示列顯示暫存數量"""
        if self._pending_addrs:
            self._tx_hint_lbl.configure(
                text=f"⚠ 有 {len(self._pending_addrs)} 筆來自文件分析的地址/帳戶待匯入，請先儲存案件。",
                text_color="#f5a623")

    # ── 填入既有資料 ──────────────────────────────────────────────────────────

    def _fill(self, case: dict):
        def _set(e, key):
            v = str(case.get(key) or "")
            if hasattr(e, "delete"):
                e.delete(0, "end")
                e.insert(0, v)
        _set(self._num_e,  "case_number")
        _set(self._name_e, "case_name")
        _set(self._inv_e,  "investigator")
        self._type_var.set(case.get("case_type", _CASE_TYPES[0]))
        self._status_var.set(case.get("status", _CASE_STATUSES[0]))
        self._desc_t.delete("1.0", "end")
        self._desc_t.insert("1.0", case.get("description", "") or "")
        self._notes_t.delete("1.0", "end")
        self._notes_t.insert("1.0", case.get("notes", "") or "")

    # ── 自動儲存（切換分頁前確保有 case_id）────────────────────────────────────

    def _auto_save(self) -> bool:
        """儲存基本資料，回傳是否成功"""
        num  = self._num_e.get().strip()
        name = self._name_e.get().strip()
        if not num or not name:
            return False
        data = self._collect_data()
        try:
            if self._case_id:
                _db.update_case(self._case_id, **{
                    k: v for k, v in data.items() if k != "case_number"})
            else:
                self._case_id = _db.create_case(**data)
                self.case     = {**data, "id": self._case_id}
            return True
        except Exception:
            return False

    def _collect_data(self) -> dict:
        return {
            "case_number":  self._num_e.get().strip(),
            "case_name":    self._name_e.get().strip(),
            "case_type":    self._type_var.get(),
            "status":       self._status_var.get(),
            "investigator": self._inv_e.get().strip(),
            "description":  self._desc_t.get("1.0", "end").strip(),
            "notes":        self._notes_t.get("1.0", "end").strip(),
        }

    # ── 儲存 ─────────────────────────────────────────────────────────────────

    def _save(self):
        data = self._collect_data()
        if not data["case_number"]:
            messagebox.showwarning("缺少資料", "請填寫案件編號", parent=self)
            return
        if not data["case_name"]:
            messagebox.showwarning("缺少資料", "請填寫案件名稱", parent=self)
            return
        try:
            if self._case_id:
                _db.update_case(self._case_id,
                                **{k: v for k, v in data.items()
                                   if k != "case_number"})
                result = {**data, "id": self._case_id}
            else:
                self._case_id = _db.create_case(**data)
                result        = {**data, "id": self._case_id}
            # 初始化被害人交易面板（若尚未建立）
            if self._victim_tx_panel is None:
                self._init_victim_panel()
            # 補匯入暫存的文件分析地址/帳戶
            pending_msg = ""
            if self._pending_addrs:
                imported = 0
                for a in self._pending_addrs:
                    _db.upsert_case_address(self._case_id, a)
                    imported += 1
                self._pending_addrs = []
                self._victim_tx_panel._load()
                pending_msg = f"\n已自動匯入 {imported} 筆文件分析地址/帳戶。"
            messagebox.showinfo("已儲存",
                                f"案件【{data['case_number']}】已儲存。"
                                f"{pending_msg}\n"
                                "可繼續在「涉案錢包 / 帳戶」分頁新增記錄。",
                                parent=self)
            if pending_msg:
                self._tabs.set("涉案錢包 / 帳戶")
        except Exception as e:
            messagebox.showerror("儲存失敗", str(e), parent=self)
            return
        if self.on_save:
            self.on_save(result)


# ── 選擇案件對話框（LinkToCaseDialog，不變）────────────────────────────────────

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
