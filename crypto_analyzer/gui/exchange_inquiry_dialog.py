"""
exchange_inquiry_dialog.py
加密貨幣交易所調閱案件申請書填表對話框
"""
from __future__ import annotations
import threading
import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
import datetime
import os

from exporter.exchange_inquiry_builder import (
    KNOWN_EXCHANGES, CASE_TYPES, PROVIDE_ITEMS, REQUEST_ITEMS,
    ATTACHMENT_OPTIONS, build_inquiry, _today_str,
    auto_translate_agency, auto_translate_address, translate_long,
)


class _ScrollableDropdown(ctk.CTkToplevel):
    """自訂下拉選單：項目依字母排序，固定顯示 visible_rows 筆，超出部分以
    滑鼠滾輪上下捲動瀏覽選取。本身不含輸入框、也不搶焦點——搜尋文字由外部
    觸發欄位（例如主表單的交易所名稱欄）打字時即時呼叫 set_filter() 篩選，
    這樣輸入焦點全程留在主表單欄位上，點選清單項目才不會被 FocusOut 卡到。"""

    def __init__(self, attach: ctk.CTkBaseClass, values: list[str],
                 command, width: int = 220, visible_rows: int = 10,
                 row_height: int = 30):
        # 注意：master 刻意指向所在視窗（Toplevel），而非 attach 本身。
        # CTkScrollableFrame 的滑鼠滾輪事件是用 bind_all 綁定、並沿著
        # widget.master 逐層往上找自己的 canvas 來判斷要不要捲動；若 master
        # 設為 attach（位在主對話框的 CTkScrollableFrame 內部），這條鏈會
        # 一路往上連到主對話框的捲動區，導致在下拉選單內滾動滑鼠時，主對話框
        # 也會跟著捲動。改用視窗本身當 master 可切斷這條鏈。
        super().__init__(attach.winfo_toplevel())
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color="#1b2338")
        self._command = command
        self._row_height = row_height
        self._all_values = sorted(values, key=lambda s: s.lower())

        x = attach.winfo_rootx()
        y = attach.winfo_rooty() + attach.winfo_height() + 2
        visible = max(1, min(visible_rows, len(self._all_values)))
        height = visible * row_height + 10
        self.geometry(f"{width}x{height}+{int(x)}+{int(y)}")

        frame = ctk.CTkScrollableFrame(
            self, fg_color="#1b2338", width=width - 20, height=height - 16,
            scrollbar_button_color="#3b4766", scrollbar_button_hover_color="#4b5a86")
        frame.pack(fill="both", expand=True, padx=2, pady=2)
        self._frame = frame

        self._buttons: dict[str, ctk.CTkButton] = {}
        for v in self._all_values:
            btn = ctk.CTkButton(
                frame, text=v, anchor="w", corner_radius=4,
                fg_color="transparent", hover_color="#2a3556",
                text_color="#e2e8f0", font=("Microsoft JhengHei", 11),
                height=row_height - 4,
                command=lambda val=v: self._select(val),
            )
            btn.pack(fill="x", padx=2, pady=1)
            self._buttons[v] = btn

    def set_filter(self, query: str):
        """依輸入的開頭字元（前綴、不分大小寫）即時篩選顯示項目——例如輸入第一個
        字元「b」時，比對的也是各交易所名稱的第一個字元，只顯示 B 開頭的項目，
        而不是名稱中任何位置含有 b 的項目"""
        query = query.strip().lower()
        for v in self._all_values:
            self._buttons[v].pack_forget()
        for v in self._all_values:
            if v.lower().startswith(query):
                self._buttons[v].pack(fill="x", padx=2, pady=1)

    def has_match(self, query: str) -> bool:
        query = query.strip().lower()
        return any(v.lower().startswith(query) for v in self._all_values)

    def _select(self, value: str):
        self._command(value)
        self.destroy()


class ExchangeInquiryDialog(ctk.CTkToplevel):
    """交易所調閱申請書填表視窗"""

    def __init__(self, parent, operator: dict, case: dict | None,
                 addresses: list[dict], out_dir: str = ""):
        super().__init__(parent)
        self.title("📨  產製交易所調閱申請書")
        self.geometry("940x820")
        self.resizable(True, True)
        self.configure(fg_color="#12192a")
        self.transient(parent)
        self.lift()
        self.focus_force()
        self.after(100, self.grab_set)

        self._operator  = operator
        self._case      = case or {}
        self._addresses = addresses
        self._out_dir   = out_dir

        self._case_type_vars:  dict[str, tk.BooleanVar] = {}
        self._provide_vars:    dict[str, tk.BooleanVar] = {}
        self._request_vars:    dict[str, tk.BooleanVar] = {}
        self._attach_vars:     dict[str, tk.BooleanVar] = {}
        self._wallet_vars:     list[tuple[dict, tk.BooleanVar]] = []
        self._manual_rows:     list[dict] = []

        self._build_ui()
        self._prefill()

    # ─────────────────────────────────────────────────────────────────────────
    # UI 建構
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── 頂部標題 ──
        top = ctk.CTkFrame(self, corner_radius=0, fg_color="#0a1020", height=54)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="加密貨幣交易所調閱案件申請書",
                     font=("Microsoft JhengHei", 14, "bold"),
                     text_color="#60a5fa").grid(
            row=0, column=0, padx=20, pady=14, sticky="w")
        ctk.CTkLabel(top,
                     text="填妥各欄位後點選「產製申請書」，系統將依所選格式輸出檔案",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray50").grid(
            row=0, column=1, padx=20, pady=14, sticky="e")

        # ── 主要捲動區 ──
        scroll = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="#12192a")
        scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        scroll.grid_columnconfigure(0, weight=1)

        row_idx = 0

        # ── Section 壹：受文者資訊 ──────────────────────────────────────────
        row_idx = self._section(scroll, row_idx, "壹、受文者資訊 / Recipient")

        ex_row = self._field_row(scroll, row_idx, "交易所名稱")
        self._exchange_var = ctk.StringVar(value="OKX")
        self._exchange_dropdown: _ScrollableDropdown | None = None
        self._exchange_list = list(KNOWN_EXCHANGES.keys())
        self._exchange_entry = ctk.CTkEntry(
            ex_row, textvariable=self._exchange_var, width=180,
            font=("Microsoft JhengHei", 11))
        self._exchange_entry.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(ex_row, text="▾ 輸入即可搜尋", font=("Microsoft JhengHei", 10),
                     text_color="#94a3b8").pack(side="left", padx=(0, 10))
        self._exchange_entry.bind("<KeyRelease>", self._on_exchange_typed)
        self._exchange_entry.bind("<Button-1>", self._on_exchange_entry_click)
        self._exchange_entry.bind("<Escape>", lambda e: self._close_exchange_dropdown())
        self._exchange_entry.bind("<FocusOut>", self._on_exchange_entry_focus_out)
        self._custom_exchange_entry = ctk.CTkEntry(
            ex_row, width=200, font=("Microsoft JhengHei", 11),
            placeholder_text="自訂名稱（選「其他」時填寫）")
        self._custom_exchange_entry.pack(side="left")
        row_idx += 1

        email_row = self._field_row(scroll, row_idx, "受文 Email")
        self._recipient_email_entry = ctk.CTkEntry(
            email_row, width=420, font=("Consolas", 11))
        self._recipient_email_entry.pack(side="left")
        row_idx += 1

        # ── Section 貳：發文資訊 ──────────────────────────────────────────
        row_idx = self._section(scroll, row_idx, "貳、發文資訊")

        doc_row = self._field_row(scroll, row_idx, "發文日期")
        self._doc_date_entry = ctk.CTkEntry(doc_row, width=160, font=("Consolas", 11))
        self._doc_date_entry.pack(side="left", padx=(0, 20))
        ctk.CTkLabel(doc_row, text="文號（選填）",
                     font=("Microsoft JhengHei", 11),
                     text_color="#94a3b8").pack(side="left", padx=(0, 6))
        self._doc_number_entry = ctk.CTkEntry(doc_row, width=260, font=("Consolas", 11),
                                               placeholder_text="例：刑偵字第1140001234號")
        self._doc_number_entry.pack(side="left")
        row_idx += 1

        case_row = self._field_row(scroll, row_idx, "關聯案件")
        case_name = (f"{self._case.get('case_number','—')}　"
                     f"{self._case.get('case_name','（未選案件）')}")
        ctk.CTkLabel(case_row, text=case_name,
                     font=("Microsoft JhengHei", 11),
                     text_color="#f5a623").pack(side="left")
        row_idx += 1

        # ── Section 參：發文機關資訊（中英文）──────────────────────────────
        row_idx = self._section(scroll, row_idx, "參、發文機關資訊（自動帶入，可修改）")

        # 機關中文 + 「翻譯全部」主按鈕
        ag_row = self._field_row(scroll, row_idx, "機關（中文）")
        self._agency_entry = ctk.CTkEntry(ag_row, width=280, font=("Microsoft JhengHei", 11))
        self._agency_entry.pack(side="left", padx=(0, 8))
        self._translate_all_btn = ctk.CTkButton(
            ag_row, text="🔄 翻譯英文欄位", width=120, height=28,
            font=("Microsoft JhengHei", 10), fg_color="#1d4b2e", hover_color="#2a6e42",
            corner_radius=14,
            command=self._translate_all)
        self._translate_all_btn.pack(side="left")
        row_idx += 1

        # 機關英文 + 單獨翻譯按鈕
        agen_row = self._field_row(scroll, row_idx, "機關（English）")
        self._agency_en_entry = ctk.CTkEntry(agen_row, width=340, font=("Consolas", 11),
                                              placeholder_text="e.g. Criminal Investigation Bureau")
        self._agency_en_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            agen_row, text="🔄", width=36, height=28,
            font=("Arial", 12), fg_color="#1a3a2a", hover_color="#265c3e",
            corner_radius=14,
            command=self._translate_agency).pack(side="left")
        self._agency_src_lbl = ctk.CTkLabel(
            agen_row, text="", font=("Microsoft JhengHei", 9),
            text_color="#6b7280", width=60)
        self._agency_src_lbl.pack(side="left", padx=(4, 0))
        row_idx += 1

        # 地址中文
        addr_row = self._field_row(scroll, row_idx, "地址（中文）")
        self._address_entry = ctk.CTkEntry(addr_row, width=380, font=("Microsoft JhengHei", 11))
        self._address_entry.pack(side="left")
        row_idx += 1

        # 地址英文 + 單獨翻譯按鈕
        addren_row = self._field_row(scroll, row_idx, "Address（EN）")
        self._address_en_entry = ctk.CTkEntry(addren_row, width=340, font=("Consolas", 11),
                                               placeholder_text="e.g. No.1, Sec.1, Jinan Rd., Zhongzheng Dist., Taipei")
        self._address_en_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            addren_row, text="🔄", width=36, height=28,
            font=("Arial", 12), fg_color="#1a3a2a", hover_color="#265c3e",
            corner_radius=14,
            command=self._translate_address).pack(side="left")
        self._address_src_lbl = ctk.CTkLabel(
            addren_row, text="", font=("Microsoft JhengHei", 9),
            text_color="#6b7280", width=60)
        self._address_src_lbl.pack(side="left", padx=(4, 0))
        row_idx += 1

        unit_row = self._field_row(scroll, row_idx, "單位 / 姓名")
        self._unit_entry = ctk.CTkEntry(unit_row, width=160, font=("Microsoft JhengHei", 11),
                                         placeholder_text="單位")
        self._unit_entry.pack(side="left", padx=(0, 8))
        self._title_entry = ctk.CTkEntry(unit_row, width=130, font=("Microsoft JhengHei", 11),
                                          placeholder_text="職稱")
        self._title_entry.pack(side="left", padx=(0, 8))
        self._name_entry = ctk.CTkEntry(unit_row, width=130, font=("Microsoft JhengHei", 11),
                                         placeholder_text="姓名")
        self._name_entry.pack(side="left", padx=(0, 8))
        self._name_en_entry = ctk.CTkEntry(unit_row, width=180, font=("Consolas", 11),
                                            placeholder_text="Name in English")
        self._name_en_entry.pack(side="left")
        row_idx += 1

        cont_row = self._field_row(scroll, row_idx, "電話 / 電郵")
        self._phone_entry = ctk.CTkEntry(cont_row, width=180, font=("Consolas", 11),
                                          placeholder_text="電話")
        self._phone_entry.pack(side="left", padx=(0, 8))
        self._email_entry = ctk.CTkEntry(cont_row, width=260, font=("Consolas", 11),
                                          placeholder_text="Email")
        self._email_entry.pack(side="left")
        row_idx += 1

        # ── Section 肆：案件性質 ──────────────────────────────────────────
        row_idx = self._section(scroll, row_idx, "肆、案件性質 / Type of Crime")

        types_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        types_frame.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=4)
        types_frame.grid_columnconfigure((0, 1, 2), weight=1)
        for i, (cn_name, en_name) in enumerate(CASE_TYPES):
            var = tk.BooleanVar(value=False)
            self._case_type_vars[cn_name] = var
            ctk.CTkCheckBox(
                types_frame,
                text=f"{cn_name} / {en_name}",
                variable=var,
                font=("Microsoft JhengHei", 10),
                checkbox_width=16, checkbox_height=16,
            ).grid(row=i // 3, column=i % 3, sticky="w", padx=8, pady=3)
        row_idx += 1

        # ── Section 伍：案情描述 ──────────────────────────────────────────
        row_idx = self._section(scroll, row_idx, "伍、案情摘要 / Brief Description")

        ctk.CTkLabel(scroll, text="中文說明：",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#94a3b8", anchor="w").grid(
            row=row_idx, column=0, sticky="w", padx=24, pady=(4, 0))
        row_idx += 1
        self._desc_cn_text = ctk.CTkTextbox(
            scroll, height=90, font=("Microsoft JhengHei", 11),
            fg_color="#0d1520", text_color="#c9d1e0", corner_radius=6)
        self._desc_cn_text.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=(0, 6))
        row_idx += 1

        en_hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        en_hdr.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=(4, 0))
        ctk.CTkLabel(en_hdr, text="English Description：",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#94a3b8").pack(side="left")
        self._desc_translate_btn = ctk.CTkButton(
            en_hdr, text="🔄 翻譯中文說明", width=120, height=26,
            font=("Microsoft JhengHei", 10), fg_color="#1d4b2e", hover_color="#2a6e42",
            corner_radius=13, command=self._translate_desc)
        self._desc_translate_btn.pack(side="left", padx=(10, 6))
        self._desc_translate_lbl = ctk.CTkLabel(
            en_hdr, text="", font=("Microsoft JhengHei", 9), text_color="#6b7280")
        self._desc_translate_lbl.pack(side="left")
        row_idx += 1
        self._desc_en_text = ctk.CTkTextbox(
            scroll, height=90, font=("Consolas", 11),
            fg_color="#0d1520", text_color="#c9d1e0", corner_radius=6)
        self._desc_en_text.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=(0, 6))
        row_idx += 1

        # ── Section 陸：警方提供資訊 ──────────────────────────────────────
        row_idx = self._section(scroll, row_idx,
                                "陸、警方提供資訊 / Information Provided to Exchange")

        ctk.CTkLabel(scroll,
                     text="（警方已掌握並提供給交易所的資訊，請勾選）",
                     font=("Microsoft JhengHei", 10), text_color="gray50",
                     anchor="w").grid(row=row_idx, column=0, sticky="w", padx=24, pady=(0, 2))
        row_idx += 1

        pi_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        pi_frame.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=4)
        pi_frame.grid_columnconfigure((0, 1), weight=1)
        for i, (cn_name, en_name) in enumerate(PROVIDE_ITEMS):
            var = tk.BooleanVar(value=(cn_name == "錢包位址"))
            self._provide_vars[cn_name] = var
            ctk.CTkCheckBox(
                pi_frame, text=f"{cn_name}  {en_name}",
                variable=var,
                font=("Microsoft JhengHei", 10),
                checkbox_width=16, checkbox_height=16,
            ).grid(row=i, column=0, sticky="w", padx=8, pady=3)
        row_idx += 1

        # ── Section 柒：要求交易所提供 ────────────────────────────────────
        row_idx = self._section(scroll, row_idx,
                                "柒、要求交易所提供 / Information Requested from Exchange")

        ctk.CTkLabel(scroll,
                     text="（請交易所提供的資訊，請勾選）",
                     font=("Microsoft JhengHei", 10), text_color="gray50",
                     anchor="w").grid(row=row_idx, column=0, sticky="w", padx=24, pady=(0, 2))
        row_idx += 1

        ri_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        ri_frame.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=4)
        ri_frame.grid_columnconfigure((0, 1), weight=1)
        for i, (cn_name, en_name) in enumerate(REQUEST_ITEMS):
            var = tk.BooleanVar(value=(cn_name in ("電話號碼", "英文姓名", "交易哈希")))
            self._request_vars[cn_name] = var
            ctk.CTkCheckBox(
                ri_frame, text=f"{cn_name}  {en_name}",
                variable=var,
                font=("Microsoft JhengHei", 10),
                checkbox_width=16, checkbox_height=16,
            ).grid(row=i, column=0, sticky="w", padx=8, pady=3)
        row_idx += 1

        # ── Section 捌：涉案錢包地址 ──────────────────────────────────────
        row_idx = self._section(scroll, row_idx, "捌、涉案錢包地址 / Wallet Addresses")

        crypto_addrs = [a for a in self._addresses
                        if a.get("addr_type") == "加密錢包"]
        if crypto_addrs:
            ctk.CTkLabel(scroll,
                         text="（勾選要列入申請書的地址，可手動填寫交易雜湊）",
                         font=("Microsoft JhengHei", 10),
                         text_color="gray50", anchor="w").grid(
                row=row_idx, column=0, sticky="w", padx=24)
            row_idx += 1
            for addr in crypto_addrs:
                var = tk.BooleanVar(value=True)
                addr_copy = dict(addr)
                self._wallet_vars.append((addr_copy, var))
                w_row = ctk.CTkFrame(scroll, fg_color="#0d1520", corner_radius=6)
                w_row.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=2)
                w_row.grid_columnconfigure(2, weight=1)
                ctk.CTkCheckBox(w_row, text="", variable=var,
                                checkbox_width=16, checkbox_height=16).grid(
                    row=0, column=0, padx=8, pady=6)
                ctk.CTkLabel(w_row,
                             text=f"{addr.get('chain_institution','?')}",
                             font=("Microsoft JhengHei", 10, "bold"),
                             text_color="#60a5fa", width=44).grid(
                    row=0, column=1, padx=(0, 6), pady=6)
                ctk.CTkLabel(w_row,
                             text=addr.get("address", ""),
                             font=("Consolas", 10),
                             text_color="#c0d4f0", anchor="w").grid(
                    row=0, column=2, sticky="ew", padx=(0, 8), pady=6)
                hash_e = ctk.CTkEntry(w_row, width=220, font=("Consolas", 10),
                                      placeholder_text="交易雜湊（選填）",
                                      fg_color="#1a2035")
                hash_e.grid(row=0, column=3, padx=(0, 8), pady=6)
                addr_copy["_hash_entry"] = hash_e
                row_idx += 1
        else:
            ctk.CTkLabel(scroll,
                         text="（目前案件無涉案加密錢包，請選擇案件後再操作，或手動新增）",
                         font=("Microsoft JhengHei", 11),
                         text_color="gray50").grid(
                row=row_idx, column=0, padx=24, pady=6)
            row_idx += 1

        # 手動新增地址
        add_row = ctk.CTkFrame(scroll, fg_color="transparent")
        add_row.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=(4, 0))
        ctk.CTkLabel(add_row, text="手動新增：",
                     font=("Microsoft JhengHei", 10),
                     text_color="#94a3b8").pack(side="left", padx=(0, 6))
        self._manual_chain = ctk.CTkOptionMenu(
            add_row, values=["BTC", "ETH", "TRX", "USDT(TRC-20)", "USDT(ERC-20)"],
            width=130, font=("Microsoft JhengHei", 10))
        self._manual_chain.pack(side="left", padx=(0, 6))
        self._manual_addr  = ctk.CTkEntry(add_row, width=280, font=("Consolas", 10),
                                           placeholder_text="地址")
        self._manual_addr.pack(side="left", padx=(0, 6))
        self._manual_hash  = ctk.CTkEntry(add_row, width=220, font=("Consolas", 10),
                                           placeholder_text="交易雜湊（選填）")
        self._manual_hash.pack(side="left", padx=(0, 6))
        ctk.CTkButton(add_row, text="＋ 新增", width=70,
                      font=("Microsoft JhengHei", 10), fg_color="#1d6b3e",
                      command=self._add_manual_wallet).pack(side="left")
        row_idx += 1

        self._manual_list_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._manual_list_frame.grid(row=row_idx, column=0, sticky="ew", padx=24)
        row_idx += 1

        # ── Section 玖：調閱期間 ──────────────────────────────────────────
        row_idx = self._section(scroll, row_idx, "玖、調閱時間區間 / Time Period")

        period_row = self._field_row(scroll, row_idx, "起訖日期")
        ctk.CTkLabel(period_row, text="自",
                     font=("Microsoft JhengHei", 11)).pack(side="left", padx=(0, 4))
        self._date_from_entry = ctk.CTkEntry(period_row, width=130, font=("Consolas", 11),
                                              placeholder_text="YYYY-MM-DD")
        self._date_from_entry.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(period_row, text="至",
                     font=("Microsoft JhengHei", 11)).pack(side="left", padx=(0, 4))
        self._date_to_entry = ctk.CTkEntry(period_row, width=130, font=("Consolas", 11),
                                            placeholder_text="YYYY-MM-DD")
        self._date_to_entry.pack(side="left")
        row_idx += 1

        # ── Section 拾：附件 ──────────────────────────────────────────────
        row_idx = self._section(scroll, row_idx, "拾、附件 / Attachments")

        att_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        att_frame.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=4)
        att_frame.grid_columnconfigure((0, 1, 2), weight=1)
        for i, att in enumerate(ATTACHMENT_OPTIONS):
            var = tk.BooleanVar(value=False)
            self._attach_vars[att] = var
            ctk.CTkCheckBox(att_frame, text=att, variable=var,
                            font=("Microsoft JhengHei", 10),
                            checkbox_width=16, checkbox_height=16).grid(
                row=i // 3, column=i % 3, sticky="w", padx=8, pady=3)
        row_idx += 1

        # ── Section 拾壹：不披露與特殊請求 ──────────────────────────────
        row_idx = self._section(scroll, row_idx, "拾壹、不披露 & 特殊請求")

        nd_row = self._field_row(scroll, row_idx, "不披露至")
        self._nd_date_entry = ctk.CTkEntry(nd_row, width=160, font=("Consolas", 11),
                                            placeholder_text="YYYY-MM-DD（留空則不填）")
        self._nd_date_entry.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(nd_row, text="（建議：發文後 5 天內）",
                     font=("Microsoft JhengHei", 10), text_color="gray50").pack(side="left")
        row_idx += 1

        keep_row = self._field_row(scroll, row_idx, "特殊請求")
        self._keep_open_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(keep_row, text="保持帳號開啟（OKX 等：最多 7 天）",
                        variable=self._keep_open_var,
                        font=("Microsoft JhengHei", 10),
                        checkbox_width=16, checkbox_height=16).pack(side="left")
        row_idx += 1

        ctk.CTkLabel(scroll, text="其他備註：",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#94a3b8", anchor="w").grid(
            row=row_idx, column=0, sticky="w", padx=24, pady=(4, 0))
        row_idx += 1
        self._special_notes_text = ctk.CTkTextbox(
            scroll, height=60, font=("Microsoft JhengHei", 11),
            fg_color="#0d1520", text_color="#c9d1e0", corner_radius=6)
        self._special_notes_text.grid(row=row_idx, column=0, sticky="ew", padx=24, pady=(0, 8))
        row_idx += 1

        # ── 底部操作列 ──
        bottom = ctk.CTkFrame(self, corner_radius=0, fg_color="#0a1020", height=68)
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.grid_propagate(False)
        bottom.grid_columnconfigure(0, weight=1)

        btn_inner = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(btn_inner, text="輸出格式：",
                     font=("Microsoft JhengHei", 11),
                     text_color="#94a3b8").pack(side="left", padx=(0, 6))
        self._fmt_var = ctk.StringVar(value="DOCX")
        for fmt in ["DOCX", "ODT", "PDF"]:
            ctk.CTkRadioButton(
                btn_inner, text=fmt, variable=self._fmt_var, value=fmt,
                font=("Microsoft JhengHei", 11)).pack(side="left", padx=8)

        ctk.CTkFrame(btn_inner, width=1, height=30, fg_color="gray30").pack(
            side="left", padx=12)

        ctk.CTkLabel(btn_inner, text="輸出至：",
                     font=("Microsoft JhengHei", 11),
                     text_color="#94a3b8").pack(side="left", padx=(0, 4))
        self._outdir_entry = ctk.CTkEntry(btn_inner, width=230, font=("Consolas", 10))
        if self._out_dir:
            self._outdir_entry.insert(0, self._out_dir)
        self._outdir_entry.pack(side="left", padx=(0, 4))
        ctk.CTkButton(btn_inner, text="瀏覽", width=54,
                      font=("Microsoft JhengHei", 10),
                      command=self._browse_dir).pack(side="left", padx=(0, 14))

        self._gen_btn = ctk.CTkButton(
            btn_inner, text="▶  產製申請書", width=140, height=40,
            font=("Microsoft JhengHei", 12, "bold"),
            fg_color="#1d4ed8", hover_color="#1e3a8a",
            corner_radius=20,
            command=self._generate)
        self._gen_btn.pack(side="left", padx=(0, 8))

        ctk.CTkButton(btn_inner, text="關閉", width=80, height=40,
                      font=("Microsoft JhengHei", 11),
                      fg_color="gray30", hover_color="gray40",
                      corner_radius=20,
                      command=self.destroy).pack(side="left")

    # ─────────────────────────────────────────────────────────────────────────
    # Helper：section 標頭 & field row
    # ─────────────────────────────────────────────────────────────────────────

    def _section(self, parent, row: int, title: str) -> int:
        sep = ctk.CTkFrame(parent, height=1, fg_color="#2a3556")
        sep.grid(row=row, column=0, sticky="ew", padx=16, pady=(10, 0))
        row += 1
        ctk.CTkLabel(parent, text=title,
                     font=("Microsoft JhengHei", 12, "bold"),
                     text_color="#aac4ff", anchor="w").grid(
            row=row, column=0, sticky="w", padx=16, pady=(4, 2))
        return row + 1

    def _field_row(self, parent, row: int, label: str) -> ctk.CTkFrame:
        ctk.CTkLabel(parent, text=label + "：",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#94a3b8", anchor="e", width=100).grid(
            row=row, column=0, padx=(16, 0), pady=6, sticky="w")
        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.grid(row=row, column=0, sticky="ew", padx=(130, 16), pady=6)
        return inner

    # ─────────────────────────────────────────────────────────────────────────
    # 預填邏輯
    # ─────────────────────────────────────────────────────────────────────────

    def _prefill(self):
        # 受文者 Email
        self._on_exchange_change("OKX")

        # 發文日期（今天）
        self._doc_date_entry.insert(0, _today_str())

        # 不披露日期（今天 + 5 天）
        nd = (datetime.date.today() + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        self._nd_date_entry.insert(0, nd)

        # 調閱時間（預設：一年前至今）
        today = datetime.date.today()
        year_ago = (today - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        self._date_from_entry.insert(0, year_ago)
        self._date_to_entry.insert(0, today.strftime("%Y-%m-%d"))

        # 案情中文描述
        desc = self._case.get("description", "")
        if desc:
            self._desc_cn_text.insert("0.0", desc[:500])

        # 案件類型預勾選
        case_type = self._case.get("case_type", "")
        if case_type in self._case_type_vars:
            self._case_type_vars[case_type].set(True)

        # 發文機關資訊（從 operator 預填）
        op = self._operator
        self._agency_entry.insert(0,   op.get("identity_agency",         ""))
        self._agency_en_entry.insert(0, op.get("identity_agency_en",     ""))
        self._address_entry.insert(0,  op.get("identity_agency_address", ""))
        self._address_en_entry.insert(0, op.get("identity_address_en",   ""))
        self._unit_entry.insert(0,     op.get("identity_unit",           ""))
        self._title_entry.insert(0,    op.get("identity_title",          ""))
        self._name_entry.insert(0,     op.get("identity_name",           ""))
        self._name_en_entry.insert(0,  op.get("identity_name_en",        ""))
        self._phone_entry.insert(0,    op.get("identity_phone",          ""))
        self._email_entry.insert(0,    op.get("identity_email",          ""))

    def _on_exchange_change(self, value: str):
        email = KNOWN_EXCHANGES.get(value, "")
        self._recipient_email_entry.delete(0, "end")
        self._recipient_email_entry.insert(0, email)

    # ── 交易所名稱：輸入即搜尋的自動完成欄位 ──────────────────────────────

    _EXCHANGE_IGNORE_KEYS = {
        "Escape", "Up", "Down", "Left", "Right", "Return", "KP_Enter",
        "Tab", "Shift_L", "Shift_R", "Control_L", "Control_R",
        "Alt_L", "Alt_R", "Caps_Lock",
    }

    def _open_exchange_dropdown(self):
        if self._exchange_dropdown is not None and self._exchange_dropdown.winfo_exists():
            return
        self._exchange_dropdown = _ScrollableDropdown(
            self._exchange_entry, self._exchange_list,
            command=self._on_exchange_selected,
            width=self._exchange_entry.winfo_width(), visible_rows=10)
        self._exchange_dropdown.set_filter(self._exchange_var.get())
        # 建立一個 topmost 的新視窗（下拉選單）在 Windows 上會把鍵盤焦點從
        # 欄位搶走（即使選單本身從未呼叫 focus_set），導致選單開啟後打字/
        # 刪除文字都沒有反應（使用者回報「OKX 無法刪除」正是這個原因）。
        # 這裡在下一個事件循環把焦點搶回欄位，讓輸入不中斷。
        self.after(10, self._reclaim_exchange_focus)

    def _reclaim_exchange_focus(self):
        if self._exchange_entry.winfo_exists():
            self._exchange_entry.focus_set()

    def _close_exchange_dropdown(self):
        if self._exchange_dropdown is not None and self._exchange_dropdown.winfo_exists():
            self._exchange_dropdown.destroy()
        self._exchange_dropdown = None

    def _on_exchange_entry_click(self, event=None):
        # 點擊欄位時若選單尚未開啟，立即開啟並依目前文字篩選
        self._open_exchange_dropdown()

    def _on_exchange_typed(self, event=None):
        if event is not None and event.keysym in self._EXCHANGE_IGNORE_KEYS:
            return
        self._open_exchange_dropdown()
        self._exchange_dropdown.set_filter(self._exchange_var.get())

    def _on_exchange_entry_focus_out(self, event=None):
        # 開啟選單本身就會觸發一次欄位失焦（見 _open_exchange_dropdown 的說
        # 明），所以這裡不能無條件關閉選單，而是延遲一小段時間後「重新檢查」
        # 當時焦點是否已經回到欄位（代表只是選單開啟造成的暫時性失焦，應保持
        # 選單開啟）；真正點擊選單外部或選取項目時，焦點不會回到欄位，屆時才
        # 真正關閉。
        self.after(150, self._maybe_close_exchange_dropdown)

    def _maybe_close_exchange_dropdown(self):
        focused = self.focus_get()
        entry_inner = getattr(self._exchange_entry, "_entry", self._exchange_entry)
        if focused is self._exchange_entry or focused is entry_inner:
            return
        self._close_exchange_dropdown()

    def _on_exchange_selected(self, value: str):
        self._exchange_var.set(value)
        self._exchange_dropdown = None
        self._on_exchange_change(value)
        self._exchange_entry.focus_set()

    # ─────────────────────────────────────────────────────────────────────────
    # 手動新增錢包
    # ─────────────────────────────────────────────────────────────────────────

    def _add_manual_wallet(self):
        chain   = self._manual_chain.get()
        address = self._manual_addr.get().strip()
        tx_hash = self._manual_hash.get().strip()
        if not address:
            messagebox.showwarning("缺少地址", "請輸入錢包地址", parent=self)
            return
        row_data = {"chain": chain, "address": address, "tx_hash": tx_hash}
        self._manual_rows.append(row_data)

        row_frame = ctk.CTkFrame(self._manual_list_frame, fg_color="#0d1520",
                                  corner_radius=4)
        row_frame.pack(fill="x", padx=0, pady=2)
        ctk.CTkLabel(row_frame, text=f"  {chain}",
                     font=("Microsoft JhengHei", 10, "bold"),
                     text_color="#60a5fa", width=70).pack(side="left")
        ctk.CTkLabel(row_frame, text=address,
                     font=("Consolas", 9), text_color="#c0d4f0").pack(side="left", padx=4)
        if tx_hash:
            ctk.CTkLabel(row_frame, text=f"  hash: {tx_hash[:20]}…",
                         font=("Consolas", 9), text_color="gray60").pack(side="left")

        def _remove(_rd=row_data, _rf=row_frame):
            self._manual_rows.remove(_rd)
            _rf.destroy()
        ctk.CTkButton(row_frame, text="✕", width=28, height=22,
                      font=("Arial", 10), fg_color="#7a1f1f",
                      command=_remove).pack(side="right", padx=4)

        self._manual_addr.delete(0, "end")
        self._manual_hash.delete(0, "end")

    # ─────────────────────────────────────────────────────────────────────────
    # 自動翻譯（機關名稱 / 地址）
    # ─────────────────────────────────────────────────────────────────────────

    def _translate_desc(self):
        """將中文說明自動翻譯並填入 English Description"""
        cn = self._desc_cn_text.get("0.0", "end").strip()
        if not cn:
            messagebox.showinfo("提示", "請先填入中文說明", parent=self)
            return
        self._desc_translate_btn.configure(text="翻譯中…", state="disabled")
        self._desc_translate_lbl.configure(text="")

        # MyMemory 單次上限約 500 字元，超過分段翻譯
        def _do():
            try:
                result = translate_long(cn)
                self.after(0, self._on_desc_translated, result)
            except Exception:
                self.after(0, self._on_desc_translated, None)

        threading.Thread(target=_do, daemon=True).start()

    def _on_desc_translated(self, en: str | None):
        self._desc_translate_btn.configure(text="🔄 翻譯中文說明", state="normal")
        if not en:
            self._desc_translate_lbl.configure(text="翻譯失敗")
            messagebox.showwarning(
                "翻譯失敗",
                "案情描述翻譯失敗（網路翻譯 API 無回應）。\n請手動填入英文說明。",
                parent=self)
            return
        self._desc_en_text.delete("0.0", "end")
        self._desc_en_text.insert("0.0", en)
        self._desc_translate_lbl.configure(text="（線上翻譯，請校閱）")

    def _translate_agency(self):
        """翻譯機關中文名稱 → 英文（靜態表優先，否則呼叫 MyMemory API）"""
        cn = self._agency_entry.get().strip()
        if not cn:
            messagebox.showinfo("提示", "請先填入機關中文名稱", parent=self)
            return
        self._agency_src_lbl.configure(text="翻譯中…")
        self._translate_all_btn.configure(state="disabled")

        def _do():
            en, src = auto_translate_agency(cn)
            self.after(0, self._on_agency_translated, en, src)

        threading.Thread(target=_do, daemon=True).start()

    def _on_agency_translated(self, en: str, src: str):
        self._translate_all_btn.configure(state="normal")
        if not en:
            self._agency_src_lbl.configure(text="翻譯失敗")
            messagebox.showwarning(
                "翻譯失敗",
                "無法取得英文名稱。\n"
                "可能原因：機關名稱未在對照表中，且網路翻譯 API 無回應。\n"
                "請手動填入英文名稱。",
                parent=self)
            return
        self._agency_en_entry.delete(0, "end")
        self._agency_en_entry.insert(0, en)
        src_text = {
            "table":         "（對照表）",
            "table+api":     "（對照表+翻譯）",
            "table+partial": "（⚠ 請確認）",
            "api":           "（線上翻譯）",
        }.get(src, "")
        self._agency_src_lbl.configure(text=src_text)

    def _translate_address(self):
        """翻譯機關地址（呼叫 MyMemory API）"""
        cn = self._address_entry.get().strip()
        if not cn:
            messagebox.showinfo("提示", "請先填入中文地址", parent=self)
            return
        self._address_src_lbl.configure(text="翻譯中…")
        self._translate_all_btn.configure(state="disabled")

        def _do():
            en, src = auto_translate_address(cn)
            self.after(0, self._on_address_translated, en, src)

        threading.Thread(target=_do, daemon=True).start()

    def _on_address_translated(self, en: str, src: str):
        self._translate_all_btn.configure(state="normal")
        if not en:
            self._address_src_lbl.configure(text="翻譯失敗")
            messagebox.showwarning(
                "翻譯失敗",
                "地址翻譯失敗（網路翻譯 API 無回應）。\n請手動填入英文地址。",
                parent=self)
            return
        self._address_en_entry.delete(0, "end")
        self._address_en_entry.insert(0, en)
        self._address_src_lbl.configure(text="（線上翻譯）")

    def _translate_all(self):
        """依序翻譯機關英文名稱 + 地址（共用按鈕，背景執行）"""
        cn_agency = self._agency_entry.get().strip()
        cn_addr   = self._address_entry.get().strip()
        if not cn_agency and not cn_addr:
            messagebox.showinfo("提示", "請先填入機關中文名稱或地址", parent=self)
            return
        self._translate_all_btn.configure(text="翻譯中…", state="disabled")
        self._agency_src_lbl.configure(text="")
        self._address_src_lbl.configure(text="")

        def _do():
            results: dict[str, tuple[str, str]] = {}
            if cn_agency:
                results["agency"] = auto_translate_agency(cn_agency)
            if cn_addr:
                results["address"] = auto_translate_address(cn_addr)
            self.after(0, self._on_translate_all_done, results)

        threading.Thread(target=_do, daemon=True).start()

    def _on_translate_all_done(self, results: dict):
        self._translate_all_btn.configure(text="🔄 翻譯英文欄位", state="normal")
        failed = []
        if "agency" in results:
            en, src = results["agency"]
            if en:
                self._agency_en_entry.delete(0, "end")
                self._agency_en_entry.insert(0, en)
                self._agency_src_lbl.configure(text={
                    "table":         "（對照表）",
                    "table+api":     "（對照表+翻譯）",
                    "table+partial": "（⚠ 請確認）",
                    "api":           "（線上翻譯）",
                }.get(src, ""))
            else:
                self._agency_src_lbl.configure(text="失敗")
                failed.append("機關英文名稱")
        if "address" in results:
            en, src = results["address"]
            if en:
                self._address_en_entry.delete(0, "end")
                self._address_en_entry.insert(0, en)
                self._address_src_lbl.configure(text="（線上翻譯）")
            else:
                self._address_src_lbl.configure(text="失敗")
                failed.append("英文地址")
        if failed:
            messagebox.showwarning(
                "部分翻譯失敗",
                f"以下欄位翻譯失敗，請手動填入：\n" + "\n".join(f"• {f}" for f in failed),
                parent=self)

    # ─────────────────────────────────────────────────────────────────────────
    # 輸出目錄
    # ─────────────────────────────────────────────────────────────────────────

    def _browse_dir(self):
        d = filedialog.askdirectory(title="選擇輸出目錄", parent=self)
        if d:
            self._outdir_entry.delete(0, "end")
            self._outdir_entry.insert(0, d)

    # ─────────────────────────────────────────────────────────────────────────
    # 收集資料 & 產製
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_data(self) -> dict:
        exchange = self._exchange_var.get()
        if exchange == "其他":
            exchange = self._custom_exchange_entry.get().strip() or "（交易所）"

        # 收集勾選的錢包
        wallets: list[dict] = []
        for addr_data, var in self._wallet_vars:
            if var.get():
                h_entry = addr_data.get("_hash_entry")
                tx_hash = h_entry.get().strip() if h_entry else ""
                wallets.append({
                    "chain":   addr_data.get("chain_institution", ""),
                    "address": addr_data.get("address", ""),
                    "tx_hash": tx_hash,
                })
        for row_data in self._manual_rows:
            wallets.append(row_data)

        return {
            # 發文方（從欄位讀取，已預填 operator）
            "sender_agency":         self._agency_entry.get().strip(),
            "sender_agency_en":      self._agency_en_entry.get().strip(),
            "sender_address":        self._address_entry.get().strip(),
            "sender_address_en":     self._address_en_entry.get().strip(),
            "sender_unit":           self._unit_entry.get().strip(),
            "sender_title":          self._title_entry.get().strip(),
            "sender_name":           self._name_entry.get().strip(),
            "sender_name_en":        self._name_en_entry.get().strip(),
            "sender_phone":          self._phone_entry.get().strip(),
            "sender_email":          self._email_entry.get().strip(),
            # 發文資訊
            "doc_date":              self._doc_date_entry.get().strip(),
            "doc_number":            self._doc_number_entry.get().strip(),
            # 受文者
            "recipient_name":        exchange,
            "recipient_email":       self._recipient_email_entry.get().strip(),
            # 案件
            "case_number":           self._case.get("case_number", ""),
            "case_types":            [k for k, v in self._case_type_vars.items() if v.get()],
            # 案情
            "desc_cn":               self._desc_cn_text.get("0.0", "end").strip(),
            "desc_en":               self._desc_en_text.get("0.0", "end").strip(),
            # 提供 / 要求
            "provided_items":        [k for k, v in self._provide_vars.items()  if v.get()],
            "requested_items":       [k for k, v in self._request_vars.items()  if v.get()],
            # 錢包
            "wallets":               wallets,
            # 期間
            "date_from":             self._date_from_entry.get().strip(),
            "date_to":               self._date_to_entry.get().strip(),
            # 附件
            "attachments":           [k for k, v in self._attach_vars.items() if v.get()],
            # 不披露
            "nondisclosure_date":    self._nd_date_entry.get().strip(),
            # 特殊請求
            "keep_account_open":     self._keep_open_var.get(),
            "special_notes":         self._special_notes_text.get("0.0", "end").strip(),
        }

    def _generate(self):
        out_dir = self._outdir_entry.get().strip()
        if not out_dir:
            messagebox.showwarning("缺少輸出目錄", "請先選擇輸出目錄", parent=self)
            return

        fmt  = self._fmt_var.get().lower()
        data = self._collect_data()

        case_num     = self._case.get("case_number", "inquiry")
        ts           = datetime.datetime.now().strftime("%Y%m%d%H%M")
        exch_safe    = data["recipient_name"].replace("/", "-").replace("\\", "-")
        fname        = f"調閱申請書_{exch_safe}_{case_num}_{ts}.{fmt}"
        out_path     = os.path.join(out_dir, fname)

        self._gen_btn.configure(text="產製中…", state="disabled")

        def _do():
            try:
                build_inquiry(data, out_path)
                self.after(0, self._on_done, out_path, None)
            except Exception as e:
                self.after(0, self._on_done, None, str(e))

        threading.Thread(target=_do, daemon=True).start()

    def _on_done(self, out_path: str | None, error: str | None):
        self._gen_btn.configure(text="▶  產製申請書", state="normal")
        if error:
            messagebox.showerror("產製失敗", f"產製申請書時發生錯誤：\n\n{error}", parent=self)
        else:
            if messagebox.askyesno(
                "產製完成",
                f"申請書已產製完成：\n{out_path}\n\n是否開啟所在資料夾？",
                parent=self
            ):
                import subprocess
                subprocess.Popen(f'explorer /select,"{out_path}"')
