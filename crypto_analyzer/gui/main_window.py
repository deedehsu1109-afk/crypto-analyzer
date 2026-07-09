from __future__ import annotations
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import datetime
import webbrowser

from config import load_config, save_config
from api.etherscan import EtherscanAPI
from api.tronscan import TronScanAPI
from api.bitcoin import BitcoinAPI
from api.errors import TooManyRecordsError
from analyzer.wallet_profiler import profile_eth, profile_trx, profile_btc
from analyzer.tx_analyzer import analyze_eth_tx, analyze_trx_tx, analyze_btc_tx
from analyzer.time_filter import (parse_datetime_str, ts_to_str,
                                   filter_by_range, filter_centered, get_tx_ts,
                                   check_overflow, suggest_increase, MAX_TOTAL)
from exporter.report import export_excel, export_csv
from database import db as _db
from gui.case_window import CaseDialog, LinkToCaseDialog
from gui.case_address_panel import CaseAddressPanel, AddressDialog
from gui.flow_graph_panel import FlowGraphPanel
from gui.cloudfail_panel import CloudFailPanel

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_STEPS = [
    ("1", "歡迎說明"),
    ("2", "使用者身分"),
    ("3", "案件選擇"),
    ("4", "案件資料"),
    ("5", "幣流圖"),
    ("6", "產製報告"),
]

_WELCOME_TEXT = """\
【系統簡介】

本系統（CryptoAnalyzer）為虛擬貨幣鑑識分析工具，專為司法調查人員設計，
用於分析區塊鏈交易紀錄、建立幣流關係圖，並產製符合法庭標準的鑑識報告。

支援鏈別：  以太坊（ETH）  ·  波場（TRX）  ·  比特幣（BTC）

──────────────────────────────────────────────────────────────

【操作流程】

  步驟 2  ▶  輸入使用者身分（每次開啟系統均須確認，納入操作稽核軌跡）
  步驟 3  ▶  建立新案件或載入既有案件
  步驟 4  ▶  輸入涉案錢包地址或交易 Hash 進行分析，管理涉案地址清單
  步驟 5  ▶  建立幣流關係圖，視覺化金流路徑與節點關係
  步驟 6  ▶  產製司法鑑識報告（Word 格式）

──────────────────────────────────────────────────────────────

【查證義務（依據中華民國《刑事訴訟法》第 165 條之 1）】

電磁紀錄作為數位證據時，使用者負有下列查證義務：

  ▪  原始性（Originality）
     本工具不修改任何原始區塊鏈資料，僅讀取公開資訊。

  ▪  完整性（Integrity）
     分析過程中應確保資料完整性；建議對產出報告計算雜湊值並留存備查。

  ▪  可驗證性（Verifiability）
     所有分析操作均可重現；API 來源與時間戳均記錄於原始資料欄位。

  ▪  稽核軌跡（Chain of Custody）
     請確實記錄分析人員身分、分析時間及目的，以備法庭審查。

──────────────────────────────────────────────────────────────

【ACPO 四大原則（英國電腦犯罪調查指引）】

  原則一  不應以任何行為改變電子設備上可能於法庭作為證據的資料。

  原則二  在必要情況下，必須由具備勝任能力的人員存取原始數位資料，
          並記錄存取過程。

  原則三  所有數位證據的蒐集、存取、儲存及傳輸均應建立完整稽核軌跡。

  原則四  本調查案件的負責人員應對上述原則的遵循負全責。

──────────────────────────────────────────────────────────────

【適用標準】

  ▪  ISO/IEC 27037:2012  數位證據識別、蒐集、獲取及保全指引
  ▪  ACPO Good Practice Guide for Digital Evidence（2012 版）
  ▪  Scientific Working Group on Digital Evidence（SWGDE）準則

──────────────────────────────────────────────────────────────

【資料來源與免責聲明】

本工具透過以下公開 API 取得區塊鏈資料：
  ▪  Etherscan V2 API（以太坊）          ▪  TronScan API（波場）
  ▪  Blockchain.com API（比特幣）        ▪  CoinGecko API（歷史幣價）

分析結果僅供調查參考。使用者應自行確認資料來源的可靠性，並遵守相關法律法規。
分析結果不構成法律意見，引用於法庭前請諮詢合格法律專業人士。

──────────────────────────────────────────────────────────────

請閱讀並理解以上內容後，點選下方「我已了解，開始使用」繼續。
"""


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("虛擬貨幣鑑識分析系統  CryptoAnalyzer v2.0")
        self.geometry("1400x900")
        self.resizable(True, True)
        self.config_data          = load_config()
        self._profile: dict | None = None
        self._active_case: dict | None = None
        self._current_step        = 0
        self._case_addr_panel: CaseAddressPanel | None = None
        self._cloudfail_panel: CloudFailPanel | None = None
        self._selected_case_id: int | None = None
        self._tx_rows_base:    list[dict] = []
        self._token_rows_base: list[dict] = []
        self._sort_state: dict = {
            "_tx_tree":    {"col": "", "asc": True},
            "_token_tree": {"col": "", "asc": True},
        }
        self._addr_highlight_iids: dict = {
            "_tx_tree": {}, "_token_tree": {}
        }
        self._build_ui()

    # ═══════════════════════════════════════════════════════════════════════════
    # UI 骨架
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_step_bar()

        self._content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self._content.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 6))
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        # 狀態列
        self.status_var = tk.StringVar(value="就緒")
        sbar = ctk.CTkFrame(self, corner_radius=0, fg_color="#080d14", height=24)
        sbar.grid(row=2, column=0, sticky="ew")
        sbar.grid_propagate(False)
        sbar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(sbar, textvariable=self.status_var,
                     anchor="w", font=("Microsoft JhengHei", 10),
                     text_color="gray60").grid(row=0, column=0, padx=12, sticky="w")

        self.progress = ctk.CTkProgressBar(self, mode="indeterminate", height=3)
        self.progress.grid(row=3, column=0, sticky="ew")
        self.progress.stop()
        self.progress.grid_remove()

        # 建立各頁面
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._build_welcome_page()
        self._build_identity_page()
        self._build_case_setup_page()
        self._build_case_data_page()
        self._build_flow_page()
        self._build_report_page()

        self._show_step(0)

    # ── 步驟條 ──────────────────────────────────────────────────────────────────

    def _build_step_bar(self):
        bar = ctk.CTkFrame(self, corner_radius=0, fg_color="#0a0f1a", height=58)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, padx=(16, 0), pady=10, sticky="w")
        ctk.CTkLabel(left, text="⛓  CryptoAnalyzer",
                     font=("Microsoft JhengHei", 13, "bold"),
                     text_color="#60a5fa").pack(side="left")
        self._case_header_lbl = ctk.CTkLabel(
            left, text="",
            font=("Microsoft JhengHei", 11), text_color="#f5a623")
        self._case_header_lbl.pack(side="left", padx=(14, 0))

        mid = ctk.CTkFrame(bar, fg_color="transparent")
        mid.grid(row=0, column=1, pady=10)

        self._step_btns: list[ctk.CTkButton] = []
        for i, (num, label) in enumerate(_STEPS):
            btn = ctk.CTkButton(
                mid,
                text=f"  {num}  {label}  ",
                height=34, width=110,
                font=("Microsoft JhengHei", 11),
                corner_radius=17,
                fg_color="transparent",
                border_width=1,
                border_color="#2d3748",
                text_color="#4a5568",
                hover_color="#1a2535",
                command=lambda idx=i: self._on_step_click(idx),
            )
            btn.pack(side="left", padx=2)
            self._step_btns.append(btn)
            if i < len(_STEPS) - 1:
                ctk.CTkLabel(mid, text="›", font=("Arial", 14),
                             text_color="#2d3748").pack(side="left", padx=1)

        ctk.CTkButton(bar, text="⚙", width=34, height=34,
                      font=("Arial", 15), corner_radius=17,
                      fg_color="transparent", hover_color="#1a2535",
                      text_color="gray50",
                      command=self._open_settings).grid(
            row=0, column=2, padx=(0, 14), pady=10)

    def _on_step_click(self, idx: int):
        if idx >= 3 and not self._active_case:
            messagebox.showinfo("請先選擇案件",
                                "請完成步驟 3（選擇或建立案件）後，才能進入後續步驟。")
            self._show_step(2)
            return
        self._show_step(idx)

    def _show_step(self, idx: int):
        self._current_step = idx
        _keys = ["welcome", "identity", "case_setup", "case_data", "flow", "report"]
        for p in self._pages.values():
            p.grid_remove()
        key = _keys[idx]
        if key in self._pages:
            self._pages[key].grid(row=0, column=0, sticky="nsew")
        # 進入報告頁時更新案件顯示
        if idx == 5 and hasattr(self, "_report_case_lbl"):
            if self._active_case:
                self._report_case_lbl.configure(
                    text=f"【{self._active_case['case_number']}】"
                         f"{self._active_case['case_name']}",
                    text_color="#7eb8f7")
            else:
                self._report_case_lbl.configure(
                    text="尚未選擇案件", text_color="#f5a623")
        for i, btn in enumerate(self._step_btns):
            if i == idx:
                btn.configure(fg_color="#1e3a8a", border_color="#3b82f6",
                              text_color="white")
            elif i < idx:
                btn.configure(fg_color="#064e3b", border_color="#059669",
                              text_color="#6ee7b7")
            else:
                btn.configure(fg_color="transparent", border_color="#2d3748",
                              text_color="#4a5568")

    def _go_next(self):
        self._on_step_click(min(self._current_step + 1, len(_STEPS) - 1))

    def _go_prev(self):
        self._show_step(max(self._current_step - 1, 0))

    def _show_case_data_tab(self, tab_name: str):
        self._show_step(3)
        if hasattr(self, "_data_tabs"):
            self._data_tabs.set(tab_name)

    # ── 設定對話框 ────────────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("⚙  系統設定")
        dlg.geometry("560x280")
        dlg.resizable(False, False)
        dlg.configure(fg_color="#1a1f2e")
        dlg.transient(self)
        dlg.lift()
        dlg.focus_force()
        dlg.after(100, dlg.grab_set)

        inner = ctk.CTkFrame(dlg, corner_radius=10, fg_color="#1e2235")
        inner.pack(fill="both", expand=True, padx=20, pady=20)
        inner.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(inner, text="API 金鑰設定",
                     font=("Microsoft JhengHei", 14, "bold")).grid(
            row=0, column=0, columnspan=2, padx=16, pady=(16, 12), sticky="w")

        eth_entry = trx_entry = None
        for row_i, (lbl_text, cfg_key) in enumerate([
            ("Etherscan API Key：",        "etherscan_api_key"),
            ("TronGrid API Key（選填）：", "trongrid_api_key"),
        ], start=1):
            ctk.CTkLabel(inner, text=lbl_text,
                         font=("Microsoft JhengHei", 12)).grid(
                row=row_i, column=0, padx=(16, 8), pady=8, sticky="e")
            e = ctk.CTkEntry(inner, font=("Consolas", 11))
            e.insert(0, self.config_data.get(cfg_key, ""))
            e.grid(row=row_i, column=1, padx=(0, 16), pady=8, sticky="ew")
            self._bind_entry_context_menu(e)
            if cfg_key == "etherscan_api_key":
                eth_entry = e
            else:
                trx_entry = e

        def save():
            self.config_data["etherscan_api_key"] = eth_entry.get().strip()
            self.config_data["trongrid_api_key"]  = trx_entry.get().strip()
            save_config(self.config_data)
            self.status_var.set("設定已儲存")
            dlg.destroy()

        ctk.CTkButton(inner, text="儲存設定",
                      font=("Microsoft JhengHei", 12, "bold"),
                      fg_color="#1d6b3e", width=120, command=save).grid(
            row=3, column=0, columnspan=2, pady=(4, 16))

    # ═══════════════════════════════════════════════════════════════════════════
    # 頁面 1：歡迎說明
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_welcome_page(self):
        page = ctk.CTkFrame(self._content, corner_radius=10)
        self._pages["welcome"] = page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)

        txt = ctk.CTkTextbox(page, font=("Microsoft JhengHei", 12),
                             fg_color="#0d1520", text_color="#c9d1e0",
                             corner_radius=8, wrap="word")
        txt.grid(row=0, column=0, sticky="nsew", padx=16, pady=(16, 8))
        txt.insert("0.0", _WELCOME_TEXT)
        txt.configure(state="disabled")

        bottom = ctk.CTkFrame(page, fg_color="transparent")
        bottom.grid(row=1, column=0, pady=(4, 20))
        ctk.CTkButton(
            bottom,
            text="我已閱讀並了解，開始使用  →",
            font=("Microsoft JhengHei", 13, "bold"),
            height=44, width=270,
            fg_color="#1d4ed8", hover_color="#1e3a8a",
            corner_radius=22,
            command=lambda: self._show_step(1),
        ).pack()

    # ═══════════════════════════════════════════════════════════════════════════
    # 頁面 2：使用者身分確認
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_identity_page(self):
        page = ctk.CTkFrame(self._content, corner_radius=10)
        self._pages["identity"] = page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        title_f = ctk.CTkFrame(page, fg_color="transparent")
        title_f.grid(row=0, column=0, pady=(32, 0))
        ctk.CTkLabel(title_f, text="使用者身分確認",
                     font=("Microsoft JhengHei", 20, "bold"),
                     text_color="#e2e8f0").pack()
        ctk.CTkLabel(title_f,
                     text="每次開啟系統均須確認，姓名與識別碼將納入操作稽核軌跡",
                     font=("Microsoft JhengHei", 11), text_color="gray50").pack(pady=(6, 0))

        card = ctk.CTkFrame(page, corner_radius=14, fg_color="#1a2035")
        card.grid(row=1, column=0, pady=20, ipadx=10, ipady=6)
        card.grid_columnconfigure(1, weight=1)
        card.grid_columnconfigure(3, weight=1)

        op = self.config_data.get("operator", {})
        self._identity_entries: dict[str, ctk.CTkEntry] = {}

        def _field(row: int, col_pair: int, label: str, key: str,
                   placeholder: str, required: bool = False):
            color = "#fca5a5" if required else "#94a3b8"
            lpad = (20, 8) if col_pair == 0 else (16, 8)
            rpad = (0, 16) if col_pair == 0 else (0, 20)
            ctk.CTkLabel(card, text=label,
                         font=("Microsoft JhengHei", 12, "bold"),
                         text_color=color, anchor="e", width=90).grid(
                row=row, column=col_pair * 2,
                padx=lpad, pady=10, sticky="e")
            e = ctk.CTkEntry(card, font=("Microsoft JhengHei", 12),
                             placeholder_text=placeholder, width=280)
            e.insert(0, op.get(key, ""))
            e.grid(row=row, column=col_pair * 2 + 1,
                   padx=rpad, pady=10, sticky="ew")
            self._bind_entry_context_menu(e)
            self._identity_entries[key] = e

        # 列 0：姓名（左）、識別碼（右）
        _field(0, 0, "姓名 *",   "identity_name", "請輸入真實姓名",       required=True)
        _field(0, 1, "識別碼 *", "identity_id",   "警員編號 / 調查員 ID", required=True)

        # 分隔線
        ctk.CTkFrame(card, height=1, fg_color="#2a3556").grid(
            row=1, column=0, columnspan=4, sticky="ew", padx=20, pady=(2, 4))

        # 列 2：機關（左）、機關地址（右）
        _field(2, 0, "機關",     "identity_agency",         "例：內政部警政署刑事警察局")
        _field(2, 1, "機關地址", "identity_agency_address", "例：台北市大安區…")

        # 列 3：單位（左）、職稱（右）
        _field(3, 0, "單位", "identity_unit",  "例：科技犯罪偵查隊")
        _field(3, 1, "職稱", "identity_title", "例：偵查員")

        # 列 4：電話（左）、Email（右）
        _field(4, 0, "電話",  "identity_phone", "例：+886-2-27697399")
        _field(4, 1, "Email", "identity_email", "例：xxx@cib.npa.gov.tw")

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ctk.CTkFrame(card, height=1, fg_color="#2a3556").grid(
            row=5, column=0, columnspan=4, sticky="ew", padx=20, pady=(4, 0))
        ctk.CTkLabel(card, text="確認時間",
                     font=("Microsoft JhengHei", 12, "bold"),
                     text_color="#94a3b8", anchor="e", width=90).grid(
            row=6, column=0, padx=(20, 8), pady=10, sticky="e")
        ctk.CTkLabel(card, text=now_str,
                     font=("Consolas", 12), text_color="#60a5fa").grid(
            row=6, column=1, columnspan=3, padx=(0, 20), pady=10, sticky="w")

        bottom = ctk.CTkFrame(page, fg_color="transparent")
        bottom.grid(row=2, column=0, pady=(4, 32))
        btn_row = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_row.pack()
        ctk.CTkButton(btn_row, text="← 返回說明",
                      font=("Microsoft JhengHei", 11),
                      width=110, height=38, fg_color="gray30",
                      command=self._go_prev).pack(side="left", padx=8)
        ctk.CTkButton(btn_row,
                      text="確認身分，進入案件選擇  →",
                      font=("Microsoft JhengHei", 13, "bold"),
                      height=44, width=250,
                      fg_color="#1d4ed8", hover_color="#1e3a8a",
                      corner_radius=22,
                      command=self._on_identity_confirm).pack(side="left", padx=8)

    def _on_identity_confirm(self):
        name = self._identity_entries["identity_name"].get().strip()
        iden = self._identity_entries["identity_id"].get().strip()
        if not name or not iden:
            messagebox.showwarning("必填欄位",
                                   "「姓名」與「識別碼」為必填欄位，請填寫後繼續。")
            return
        op = {k: e.get().strip() for k, e in self._identity_entries.items()}
        op["confirmed_at"] = datetime.datetime.now().isoformat()
        self.config_data["operator"] = op
        save_config(self.config_data)
        agency = op.get("identity_agency", "")
        unit   = op.get("identity_unit", "")
        title  = op.get("identity_title", "")
        suffix = "　".join(v for v in (agency, unit, title) if v)
        self.status_var.set(
            f"已確認：{name}（{iden}）"
            + (f"　{suffix}" if suffix else "")
            + f"　時間：{datetime.datetime.now().strftime('%H:%M:%S')}")
        self._show_step(2)

    # ═══════════════════════════════════════════════════════════════════════════
    # 頁面 3：案件選擇
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_case_setup_page(self):
        page = ctk.CTkFrame(self._content, corner_radius=10)
        self._pages["case_setup"] = page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)

        # ── 置中捲動容器 ──
        outer = ctk.CTkScrollableFrame(page, corner_radius=0,
                                       fg_color="transparent")
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(outer, corner_radius=12, fg_color="#0f1520")
        card.grid(row=0, column=0, sticky="ew", padx=60, pady=30)
        card.grid_columnconfigure(0, weight=1)

        # ── 標題列 ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 4))
        ctk.CTkLabel(hdr, text="案件管理",
                     font=("Microsoft JhengHei", 18, "bold"),
                     text_color="#e2e8f0").pack(side="left")
        ctk.CTkLabel(hdr, text="  選擇或建立案件後方可進入分析步驟",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray50").pack(side="left", pady=(4, 0))

        # 分隔線
        ctk.CTkFrame(card, height=1, fg_color="#2a3556").grid(
            row=1, column=0, sticky="ew", padx=24, pady=(0, 16))

        # ── 下拉選單區 ──
        sel_f = ctk.CTkFrame(card, fg_color="transparent")
        sel_f.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 8))
        sel_f.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sel_f, text="選擇案件：",
                     font=("Microsoft JhengHei", 13, "bold"),
                     text_color="#aac4ff", width=90, anchor="e").grid(
            row=0, column=0, padx=(0, 10), sticky="e")

        self._case_combo = ctk.CTkComboBox(
            sel_f,
            values=[],
            font=("Microsoft JhengHei", 12),
            dropdown_font=("Microsoft JhengHei", 11),
            state="readonly",
            width=580, height=36,
            command=self._on_case3_select)
        self._case_combo.grid(row=0, column=1, sticky="ew")
        self._case_combo_map: dict[str, dict] = {}

        # ── 操作按鈕列 ──
        btn_f = ctk.CTkFrame(card, fg_color="transparent")
        btn_f.grid(row=3, column=0, padx=24, pady=(8, 4), sticky="w")
        for txt, color, cmd in [
            ("＋ 新建案件",     "#1d6b3e", self._case3_new),
            ("✎ 編輯選取案件", "#2a4a8a", self._case3_edit),
            ("🗑 刪除選取案件", "#7a1f1f", self._case3_delete),
        ]:
            ctk.CTkButton(btn_f, text=txt, height=34, width=140,
                          font=("Microsoft JhengHei", 11),
                          fg_color=color, command=cmd).pack(
                side="left", padx=(0, 8))

        # 分隔線
        ctk.CTkFrame(card, height=1, fg_color="#2a3556").grid(
            row=4, column=0, sticky="ew", padx=24, pady=14)

        # ── 案件詳情 ──
        ctk.CTkLabel(card, text="案件詳情",
                     font=("Microsoft JhengHei", 13, "bold"),
                     text_color="#aac4ff", anchor="w").grid(
            row=5, column=0, padx=24, pady=(0, 6), sticky="w")

        info_card = ctk.CTkFrame(card, corner_radius=8, fg_color="#0a0f1a")
        info_card.grid(row=6, column=0, sticky="ew", padx=24, pady=(0, 14))
        info_card.grid_columnconfigure(0, weight=1)
        self._case3_info_lbl = ctk.CTkLabel(
            info_card,
            text="請從上方下拉選單選擇案件，或點「＋ 新建案件」建立新案件。",
            font=("Microsoft JhengHei", 11), text_color="gray50",
            anchor="nw", justify="left", wraplength=680)
        self._case3_info_lbl.grid(row=0, column=0, padx=16, pady=14, sticky="nw")

        # ── 目前作業案件 ──
        ctk.CTkLabel(card, text="目前作業案件",
                     font=("Microsoft JhengHei", 13, "bold"),
                     text_color="#aac4ff", anchor="w").grid(
            row=7, column=0, padx=24, pady=(0, 6), sticky="w")

        active_card = ctk.CTkFrame(card, corner_radius=8, fg_color="#1a2744")
        active_card.grid(row=8, column=0, sticky="ew", padx=24, pady=(0, 16))
        active_card.grid_columnconfigure(0, weight=1)
        self._case3_active_lbl = ctk.CTkLabel(
            active_card, text="尚未選擇案件",
            font=("Microsoft JhengHei", 13), text_color="#f5a623",
            anchor="w", wraplength=680)
        self._case3_active_lbl.grid(row=0, column=0, padx=16, pady=12, sticky="w")

        # ── 底部操作按鈕 ──
        bottom_f = ctk.CTkFrame(card, fg_color="transparent")
        bottom_f.grid(row=9, column=0, padx=24, pady=(0, 24), sticky="w")
        ctk.CTkButton(bottom_f, text="設為目前作業案件",
                      font=("Microsoft JhengHei", 12, "bold"),
                      height=40, width=170, fg_color="#1d5e8a",
                      command=self._case3_set_active).pack(side="left", padx=(0, 10))
        ctk.CTkButton(bottom_f, text="進入案件資料分析  →",
                      font=("Microsoft JhengHei", 13, "bold"),
                      height=40, width=200,
                      fg_color="#1d4ed8", hover_color="#1e3a8a",
                      corner_radius=20,
                      command=self._case3_proceed).pack(side="left")

        self._reload_case_list()

    def _reload_case_list(self):
        cases = _db.get_all_cases()
        self._case_combo_map = {
            f"{c['case_number']}　{c['case_name']}　（{c['case_type']} · {c['status']}）": c
            for c in cases
        }
        values = list(self._case_combo_map.keys())
        self._case_combo.configure(values=values)
        if values:
            cur = self._case_combo.get()
            if cur not in self._case_combo_map:
                self._case_combo.set(values[0])
                self._on_case3_select(values[0])
        else:
            self._case_combo.set("")
            self._case3_info_lbl.configure(
                text="目前尚無案件，請點「＋ 新建案件」建立第一個案件。",
                text_color="gray50")

    def _on_case3_select(self, value=None):
        if value is None:
            value = self._case_combo.get()
        case = self._case_combo_map.get(value)
        if not case:
            return
        self._selected_case_id = case["id"]
        case = _db.get_case(case["id"])
        if not case:
            return
        wallets = _db.get_case_wallets(case["id"])
        info = (
            f"案件編號：{case['case_number']}\n"
            f"案件名稱：{case['case_name']}\n"
            f"類型：{case['case_type']}　　狀態：{case['status']}\n"
            f"承辦人：{case.get('investigator') or '—'}\n"
            f"建立時間：{case.get('created_at','')}\n"
            f"說明：{(case.get('description') or '（無）')[:200]}\n"
            f"關聯錢包：{len(wallets)} 個"
        )
        self._case3_info_lbl.configure(text=info, text_color="#c8d8f0")

    def _case3_set_active(self):
        if not self._selected_case_id:
            messagebox.showinfo("提示", "請先從下拉選單選取一個案件")
            return
        case = _db.get_case(self._selected_case_id)
        if case:
            self._set_active_case(case)

    def _case3_proceed(self):
        if not self._active_case:
            if self._selected_case_id:
                case = _db.get_case(self._selected_case_id)
                if case:
                    self._set_active_case(case)
                else:
                    return
            else:
                messagebox.showinfo("請先選擇案件",
                                    "請先從下拉選單選取案件或建立新案件，再進入分析頁面。")
                return
        self._show_step(3)

    def _set_active_case(self, case: dict):
        self._active_case = case
        disp = (f"  【{case['case_number']}】{case['case_name']}"
                f"  （{case['case_type']} · {case['status']}）")
        self._case_header_lbl.configure(text=disp)
        self._case3_active_lbl.configure(
            text=f"【{case['case_number']}】{case['case_name']}"
                 f"  ({case['case_type']} · {case['status']})",
            text_color="#7eb8f7")
        self.status_var.set(
            f"作業案件：{case['case_number']} {case['case_name']}")
        if hasattr(self, "_flow_panel"):
            self._flow_panel.set_case_id(case["id"])
        if hasattr(self, "_report_case_lbl"):
            self._report_case_lbl.configure(
                text=f"【{case['case_number']}】{case['case_name']}",
                text_color="#7eb8f7")
        self._refresh_case_addr_tab()
        self._refresh_sidebar()

    def _refresh_case_addr_tab(self):
        if not self._active_case or not hasattr(self, "_addr_tab_frame"):
            return
        case_id = self._active_case["id"]
        for w in self._addr_tab_frame.winfo_children():
            w.destroy()
        self._case_addr_panel = CaseAddressPanel(self._addr_tab_frame, case_id)
        self._case_addr_panel.grid(row=0, column=0, sticky="nsew")

    def _case3_new(self):
        def on_save(c):
            self._reload_case_list()
            self._set_active_case(c)
            key = next((k for k, v in self._case_combo_map.items()
                        if v["id"] == c["id"]), None)
            if key:
                self._case_combo.set(key)
        CaseDialog(self, on_save=on_save)

    def _case3_edit(self):
        if not self._selected_case_id:
            messagebox.showinfo("提示", "請先從下拉選單選取要編輯的案件")
            return
        case = _db.get_case(self._selected_case_id)
        if case:
            def on_save(_):
                self._reload_case_list()
                self._on_case3_select()
            CaseDialog(self, case=case, on_save=on_save)

    def _case3_delete(self):
        if not self._selected_case_id:
            messagebox.showinfo("提示", "請先從下拉選單選取要刪除的案件")
            return
        case = _db.get_case(self._selected_case_id)
        if not case:
            return
        if not messagebox.askyesno(
                "確認刪除",
                f"確定刪除案件【{case['case_number']}】{case['case_name']}？\n"
                "（關聯錢包與 Hash 記錄不會被刪除，僅解除關聯）"):
            return
        del_id = self._selected_case_id
        _db.delete_case(del_id)
        if self._active_case and self._active_case["id"] == del_id:
            self._active_case = None
            self._case_header_lbl.configure(text="")
            self._case3_active_lbl.configure(
                text="尚未選擇案件", text_color="#f5a623")
        self._selected_case_id = None
        self._reload_case_list()
        self._case3_info_lbl.configure(
            text="案件已刪除。請從下拉選單選擇其他案件。",
            text_color="gray50")

    # ═══════════════════════════════════════════════════════════════════════════
    # 頁面 4：案件資料
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_case_data_page(self):
        page = ctk.CTkFrame(self._content, corner_radius=10)
        self._pages["case_data"] = page
        page.grid_columnconfigure(0, weight=0)  # 左側清單（固定寬度）
        page.grid_columnconfigure(1, weight=1)  # 右側分頁
        page.grid_rowconfigure(0, weight=1)

        # ── 左側：涉案地址 + 查詢紀錄清單 ──
        self._build_case_sidebar(page)

        # ── 右側：功能分頁 ──
        tabs = ctk.CTkTabview(page, corner_radius=8)
        tabs.grid(row=0, column=1, sticky="nsew", padx=(0, 8), pady=8)
        for name in ["🔍  地址側寫", "🔗  Hash 分析", "📁  涉案錢包/帳戶",
                      "📜  查詢歷史", "🌐  網站溯源"]:
            tabs.add(name)
        self._data_tabs = tabs

        self._build_profile_tab(tabs.tab("🔍  地址側寫"))
        self._build_hash_tab(tabs.tab("🔗  Hash 分析"))
        self._build_addr_tab(tabs.tab("📁  涉案錢包/帳戶"))
        self._build_history_tab(tabs.tab("📜  查詢歷史"))
        self._build_cloudfail_tab(tabs.tab("🌐  網站溯源"))

    def _build_case_sidebar(self, page):
        sidebar = ctk.CTkFrame(page, corner_radius=8,
                               fg_color="#090e1a", width=230)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(2, weight=3)  # 涉案地址清單
        sidebar.grid_rowconfigure(5, weight=2)  # 查詢紀錄清單

        # ── 標題列 ──
        hdr = ctk.CTkFrame(sidebar, fg_color="#141d30", corner_radius=6)
        hdr.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 0))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="案件資料清單",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#aac4ff", anchor="w").grid(
            row=0, column=0, padx=10, pady=(8, 6), sticky="w")
        ctk.CTkButton(hdr, text="↺", width=28, height=24,
                      font=("Microsoft JhengHei", 12),
                      fg_color="#2a3556", hover_color="#3a4a70",
                      command=self._refresh_sidebar).grid(
            row=0, column=1, padx=(2, 8), pady=(8, 6))

        # ── 涉案地址 ──
        ctk.CTkLabel(sidebar, text="涉案地址",
                     font=("Microsoft JhengHei", 10, "bold"),
                     text_color="#66aaff", anchor="w").grid(
            row=1, column=0, sticky="w", padx=10, pady=(6, 2))
        self._sidebar_addr_frame = ctk.CTkScrollableFrame(
            sidebar, fg_color="#0c1220", corner_radius=4)
        self._sidebar_addr_frame.grid(row=2, column=0,
                                      sticky="nsew", padx=6, pady=(0, 4))
        self._sidebar_addr_frame.grid_columnconfigure(0, weight=1)

        # ── 分隔線 ──
        ctk.CTkFrame(sidebar, height=1, fg_color="#2a3556").grid(
            row=3, column=0, sticky="ew", padx=8, pady=2)

        # ── 查詢紀錄 ──
        ctk.CTkLabel(sidebar, text="查詢紀錄",
                     font=("Microsoft JhengHei", 10, "bold"),
                     text_color="#66aaff", anchor="w").grid(
            row=4, column=0, sticky="w", padx=10, pady=(4, 2))
        self._sidebar_hist_frame = ctk.CTkScrollableFrame(
            sidebar, fg_color="#0c1220", corner_radius=4)
        self._sidebar_hist_frame.grid(row=5, column=0,
                                      sticky="nsew", padx=6, pady=(0, 6))
        self._sidebar_hist_frame.grid_columnconfigure(0, weight=1)

    def _refresh_sidebar(self):
        if not self._active_case or not hasattr(self, "_sidebar_addr_frame"):
            return
        case_id = self._active_case["id"]

        # ── 涉案地址 ──
        for w in self._sidebar_addr_frame.winfo_children():
            w.destroy()
        addrs = _db.get_case_addresses(case_id)
        if not addrs:
            ctk.CTkLabel(self._sidebar_addr_frame,
                         text="（尚無涉案地址）",
                         font=("Microsoft JhengHei", 9),
                         text_color="gray50").pack(anchor="w", padx=6, pady=6)
        else:
            for a in addrs:
                self._make_sidebar_addr_item(self._sidebar_addr_frame, a)

        # ── 查詢紀錄 ──
        for w in self._sidebar_hist_frame.winfo_children():
            w.destroy()
        wallets = _db.get_case_wallets(case_id)
        if not wallets:
            ctk.CTkLabel(self._sidebar_hist_frame,
                         text="（尚無查詢紀錄）",
                         font=("Microsoft JhengHei", 9),
                         text_color="gray50").pack(anchor="w", padx=6, pady=6)
        else:
            for wlt in wallets:
                self._make_sidebar_hist_item(self._sidebar_hist_frame, wlt)

    @staticmethod
    def _mask_address(address: str) -> str:
        if len(address) > 14:
            return address[:7] + "***" + address[-7:]
        return address

    def _make_sidebar_addr_item(self, parent, addr: dict):
        is_crypto = addr.get("addr_type") == "加密錢包"
        icon      = "🔵" if is_crypto else "🏦"
        chain     = addr.get("chain_institution", "")
        address   = addr.get("address", "")
        role      = addr.get("holder_role") or ""
        label     = addr.get("label") or ""
        notes     = addr.get("notes") or ""

        masked = self._mask_address(address)
        role_color = {"被害人": "#66dd66", "嫌疑人": "#ff9944",
                      "中間人": "#88aaff"}.get(role, "gray55")

        item = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=4)
        item.pack(fill="x", padx=2, pady=1)
        item.grid_columnconfigure(0, weight=1)

        line1 = f"{icon} {chain}  {masked}"

        if is_crypto:
            btn = ctk.CTkButton(
                item, text=line1,
                font=("Consolas", 11), anchor="w", height=26,
                fg_color="transparent", hover_color="#1a2540",
                text_color="#c0d4f0",
                command=lambda a=address, c=chain: self._sidebar_click_addr(a, c)
            )
            btn.grid(row=0, column=0, sticky="ew")

            # 右鍵選單（避免右鍵意外觸發左鍵 command）
            _ctx = tk.Menu(item, tearoff=0, bg="#2b2b2b", fg="white",
                           activebackground="#1f538d", activeforeground="white",
                           font=("Microsoft JhengHei", 10))
            _ctx.add_command(
                label="複製地址",
                command=lambda _a=address: (
                    self.clipboard_clear(),
                    self.clipboard_append(_a),
                    self.status_var.set(f"已複製：{_a}")))
            _ctx.add_command(
                label="查詢此地址",
                command=lambda _a=address, _c=chain: self._sidebar_click_addr(_a, _c))
            _ctx.add_separator()
            _ctx.add_command(
                label="✎ 編輯涉案記錄",
                command=lambda _r=addr: self._sidebar_edit_addr(_r))
            _ctx.add_command(
                label="✕ 從涉案清單移除",
                command=lambda _r=addr: self._sidebar_remove_addr(_r))
            btn.bind("<Button-3>", lambda e, m=_ctx: m.tk_popup(e.x_root, e.y_root))
            btn.bind("<Enter>", lambda e, a=address: self._highlight_addr_in_trees(a))
            btn.bind("<Leave>", lambda e: self._clear_highlight_in_trees())
        else:
            ctk.CTkLabel(
                item, text=line1,
                font=("Consolas", 11), anchor="w",
                text_color="#888888"
            ).grid(row=0, column=0, sticky="ew", padx=4)

        # 依序顯示：持有人角色、標記說明、備註（同一排，有資料才加入）
        info_parts = [v for v in (role, label, notes) if v]
        if info_parts:
            ctk.CTkLabel(item, text="  " + "　".join(info_parts),
                         font=("Microsoft JhengHei", 10),
                         text_color=role_color, anchor="w").grid(
                row=1, column=0, sticky="ew", padx=10)

    def _make_sidebar_hist_item(self, parent, wlt: dict):
        chain   = wlt.get("chain", "")
        address = wlt.get("address", "")
        ts      = (wlt.get("analyzed_at") or "")[:10]
        label   = wlt.get("label") or ""

        masked  = self._mask_address(address)
        display = f"{chain}  {masked}"
        sub     = label if label else ts

        item = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=4)
        item.pack(fill="x", padx=2, pady=1)
        item.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(
            item, text=display,
            font=("Consolas", 11), anchor="w", height=26,
            fg_color="transparent", hover_color="#1a2540",
            text_color="#c0d4f0",
            command=lambda a=address, c=chain: self._sidebar_click_addr(a, c)
        ).grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(item, text=f"  {sub}",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray55", anchor="w").grid(
            row=1, column=0, sticky="ew", padx=10)

    def _sidebar_click_addr(self, address: str, chain: str):
        if chain in ("ETH", "TRX", "BTC"):
            self.chain_var.set(chain)
        self.addr_entry.delete(0, "end")
        self.addr_entry.insert(0, address)
        self._data_tabs.set("🔍  地址側寫")

        # 有目前案件時自動切為專案查詢，確保查詢結果存入案件
        if self._active_case and self._query_mode.get() != "專案查詢":
            self._query_mode.set("專案查詢")
            self._on_mode_change("專案查詢")

        wallet_row = _db.get_wallet_by_address(chain, address)
        if wallet_row is None:
            return
        wallet_id = wallet_row["id"]
        self.status_var.set(f"從資料庫載入 {chain} 地址資料中…")

        def _load():
            try:
                profile = _db.load_profile_from_db(wallet_id, chain, address, wallet_row)
                self.after(0, self._update_ui, profile)
            except Exception as e:
                self.after(0, self.status_var.set, f"資料庫載入失敗：{e}")

        threading.Thread(target=_load, daemon=True).start()

    def _sidebar_edit_addr(self, addr: dict):
        """從側邊欄右鍵選單開啟涉案地址編輯對話框"""
        if not self._active_case:
            messagebox.showwarning("未選擇案件", "請先選擇目前作業案件", parent=self)
            return
        def on_save():
            self._refresh_sidebar()
            if self._case_addr_panel:
                self._case_addr_panel._load()
        AddressDialog(self, self._active_case["id"], row=addr, on_save=on_save)

    def _sidebar_remove_addr(self, addr: dict):
        """從側邊欄右鍵選單移除涉案地址記錄"""
        address = addr.get("address", "")
        if not messagebox.askyesno(
            "確認移除",
            f"確定要從涉案清單移除此地址？\n\n{address}",
            parent=self
        ):
            return
        _db.delete_case_address(addr["id"])
        self._refresh_sidebar()
        if self._case_addr_panel:
            self._case_addr_panel._load()

    # ── 地址側寫分頁 ──────────────────────────────────────────────────────────

    def _build_profile_tab(self, parent: ctk.CTkFrame):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=0)  # 上方固定區域
        parent.grid_rowconfigure(2, weight=0)  # 交易篩選列
        parent.grid_rowconfigure(3, weight=1)  # 下方交易分頁（展開）

        top = ctk.CTkFrame(parent, corner_radius=8)
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
        top.grid_columnconfigure(4, weight=1)

        self._query_mode = ctk.StringVar(value="一般查詢")
        ctk.CTkSegmentedButton(
            top, values=["一般查詢", "專案查詢"],
            variable=self._query_mode, width=185,
            font=("Microsoft JhengHei", 11),
            command=self._on_mode_change).grid(
            row=0, column=0, padx=(10, 6), pady=8)

        ctk.CTkLabel(top, text="鏈：",
                     font=("Microsoft JhengHei", 12)).grid(
            row=0, column=1, padx=(4, 2), pady=8)
        self.chain_var = ctk.StringVar(value="ETH")
        ctk.CTkOptionMenu(top, variable=self.chain_var,
                          values=["ETH", "TRX", "BTC"], width=80,
                          font=("Microsoft JhengHei", 11),
                          command=self._on_chain_change).grid(
            row=0, column=2, padx=2, pady=8)

        ctk.CTkLabel(top, text="錢包地址：",
                     font=("Microsoft JhengHei", 12)).grid(
            row=0, column=3, padx=(8, 4), pady=8)
        self.addr_entry = ctk.CTkEntry(
            top, placeholder_text="輸入錢包地址（或 Hash，自動導向）",
            font=("Consolas", 11))
        self.addr_entry.grid(row=0, column=4, padx=4, pady=8, sticky="ew")
        self.addr_entry.bind("<FocusOut>",   self._on_addr_focusout)
        self.addr_entry.bind("<KeyRelease>", self._on_addr_keyrelease)
        self.addr_entry.bind("<Return>",     lambda _: self._start_smart_query())
        self._bind_entry_context_menu(self.addr_entry)

        self.analyze_btn = ctk.CTkButton(
            top, text="開始查詢", width=90,
            font=("Microsoft JhengHei", 12, "bold"),
            command=self._start_smart_query)
        self.analyze_btn.grid(row=0, column=5, padx=(6, 2), pady=8)

        self._clear_btn = ctk.CTkButton(
            top, text="清除", width=60,
            font=("Microsoft JhengHei", 11), fg_color="#6b3a1f",
            command=self._clear_results)
        self._clear_btn.grid(row=0, column=6, padx=2, pady=8)

        self.export_excel_btn = ctk.CTkButton(
            top, text="Excel", width=68,
            font=("Microsoft JhengHei", 11), fg_color="#2d6a4f",
            command=self._export_excel)
        self.export_excel_btn.grid(row=0, column=7, padx=2, pady=8)

        self.export_csv_btn = ctk.CTkButton(
            top, text="CSV", width=58,
            font=("Microsoft JhengHei", 11), fg_color="#5e3a8a",
            command=self._export_csv)
        self.export_csv_btn.grid(row=0, column=8, padx=2, pady=8)

        self.flow_btn = ctk.CTkButton(
            top, text="加入幣流圖", width=90,
            font=("Microsoft JhengHei", 11), fg_color="#4a2d6a",
            command=self._add_to_flow_graph)
        self.flow_btn.grid(row=0, column=9, padx=(2, 8), pady=8)

        # 時間篩選列（row=1，常駐顯示於工具列下方）
        tf_row = ctk.CTkFrame(top, fg_color="#1a1a2e", corner_radius=4)
        tf_row.grid(row=1, column=0, columnspan=10, sticky="ew", padx=4, pady=(0, 4))
        self._build_time_bar(tf_row)

        # 查詢進度列（row=2）：進度條 + 步驟文字
        self._query_progress_bar = ctk.CTkProgressBar(
            top, mode="indeterminate", height=6, corner_radius=0)
        self._query_progress_bar.grid(
            row=2, column=0, columnspan=10, sticky="ew", padx=0, pady=0)
        self._query_progress_bar.set(0)
        self._query_progress_bar.grid_remove()

        self._query_step_lbl = ctk.CTkLabel(
            top, text="", font=("Microsoft JhengHei", 10),
            text_color="#5fa8d3", anchor="w")
        self._query_step_lbl.grid(
            row=3, column=0, columnspan=10, sticky="ew", padx=10, pady=(2, 4))
        self._query_step_lbl.grid_remove()

        # ── 上方：摘要 ＋ 授權對象（按鈕折疊，預設隱藏） ──
        top_info = ctk.CTkFrame(parent, corner_radius=8)
        top_info.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 2))
        top_info.grid_columnconfigure(0, weight=1)

        # 按鈕列（常駐顯示）
        _btn_bar = ctk.CTkFrame(top_info, fg_color="transparent")
        _btn_bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 6))

        self._summary_visible   = False
        self._approvals_visible = False

        self._summary_btn = ctk.CTkButton(
            _btn_bar, text="錢包摘要 ▼", width=130,
            font=("Microsoft JhengHei", 11),
            fg_color="#1e3a5f", hover_color="#2a4a7f",
            command=self._toggle_summary)
        self._summary_btn.pack(side="left", padx=(4, 8))

        self._approval_btn = ctk.CTkButton(
            _btn_bar, text="無授權對象", width=160,
            font=("Microsoft JhengHei", 11),
            fg_color="gray30", hover_color="gray40",
            command=self._toggle_approvals)
        self._approval_btn.pack(side="left", padx=4)

        # 錢包摘要內容（預設隱藏，固定高度 230px）
        self._summary_content = ctk.CTkFrame(top_info, fg_color="transparent")
        self._summary_content.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))
        self._summary_content.grid_remove()
        self._build_summary_section(self._summary_content)

        # 授權對象內容（預設隱藏，固定高度 150px）
        self._approvals_content = ctk.CTkFrame(top_info, fg_color="transparent", height=150)
        self._approvals_content.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))
        self._approvals_content.grid_propagate(False)
        self._approvals_content.grid_remove()
        self._build_approvals_section(self._approvals_content)

        # ── 交易篩選列（雙列） ──
        self._dust_filter_var  = tk.BooleanVar(value=False)
        self._search_var       = tk.StringVar()
        self._search_amt_min   = tk.StringVar()
        self._search_amt_max   = tk.StringVar()
        self._search_t_from    = tk.StringVar()
        self._search_t_to      = tk.StringVar()

        filter_bar = ctk.CTkFrame(parent, corner_radius=6, fg_color="#1a1a2e", height=72)
        filter_bar.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 2))
        filter_bar.grid_propagate(False)
        filter_bar.grid_columnconfigure(0, weight=1)

        # 第一列：釣魚過濾
        r0 = ctk.CTkFrame(filter_bar, fg_color="transparent")
        r0.grid(row=0, column=0, sticky="w", padx=0, pady=(4, 0))
        self._dust_cb = ctk.CTkCheckBox(
            r0, text="🚫 過濾釣魚交易（數量 ＜ 1）",
            font=("Microsoft JhengHei", 11),
            variable=self._dust_filter_var,
            command=self._on_dust_filter_change,
            fg_color="#7a1f1f", hover_color="#9a2f2f",
            checkmark_color="white")
        self._dust_cb.pack(side="left", padx=12)
        self._dust_count_lbl = ctk.CTkLabel(
            r0, text="", font=("Microsoft JhengHei", 10), text_color="#ff9944")
        self._dust_count_lbl.pack(side="left", padx=4)

        # 第二列：搜尋列
        r1 = ctk.CTkFrame(filter_bar, fg_color="transparent")
        r1.grid(row=1, column=0, sticky="ew", padx=0, pady=(2, 4))
        _lf = ("Microsoft JhengHei", 10)
        _ef = ("Consolas", 10)
        ctk.CTkLabel(r1, text="🔍", font=_lf).pack(side="left", padx=(12, 2))
        self._search_entry = ctk.CTkEntry(
            r1, textvariable=self._search_var, width=185,
            placeholder_text="地址 / 交易 Hash", font=_ef)
        self._search_entry.pack(side="left", padx=(0, 6))
        self._search_entry.bind("<Return>", lambda _: self._refresh_tx_display())
        ctk.CTkLabel(r1, text="數量", font=_lf).pack(side="left", padx=(0, 2))
        ctk.CTkEntry(r1, textvariable=self._search_amt_min,
                     width=72, placeholder_text="最小", font=_ef).pack(side="left", padx=1)
        ctk.CTkLabel(r1, text="～", font=_lf).pack(side="left")
        ctk.CTkEntry(r1, textvariable=self._search_amt_max,
                     width=72, placeholder_text="最大", font=_ef).pack(side="left", padx=(1, 6))
        ctk.CTkLabel(r1, text="時間", font=_lf).pack(side="left", padx=(0, 2))
        ctk.CTkEntry(r1, textvariable=self._search_t_from,
                     width=130, placeholder_text="YYYY-MM-DD", font=_ef).pack(side="left", padx=1)
        ctk.CTkLabel(r1, text="～", font=_lf).pack(side="left")
        ctk.CTkEntry(r1, textvariable=self._search_t_to,
                     width=130, placeholder_text="YYYY-MM-DD", font=_ef).pack(side="left", padx=(1, 6))
        ctk.CTkButton(r1, text="套用", width=55, font=_lf, fg_color="#2a4a8a",
                      command=self._refresh_tx_display).pack(side="left", padx=2)
        ctk.CTkButton(r1, text="清除", width=55, font=_lf, fg_color="gray35",
                      command=self._clear_tx_search).pack(side="left", padx=2)
        self._search_result_lbl = ctk.CTkLabel(
            r1, text="", font=_lf, text_color="#88aacc")
        self._search_result_lbl.pack(side="left", padx=8)

        # ── 下方交易分頁（展開） ──
        self._tx_tabs = ctk.CTkTabview(parent, corner_radius=8)
        self._tx_tabs.grid(row=3, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self._tx_tabs.add("原始交易")
        self._tx_tabs.add("Token 轉帳")
        self._build_tx_section(self._tx_tabs.tab("原始交易"),   "_tx_tree")
        self._build_tx_section(self._tx_tabs.tab("Token 轉帳"), "_token_tree")

    def _build_time_bar(self, parent: ctk.CTkFrame):
        parent.grid_columnconfigure(7, weight=1)
        ctk.CTkLabel(parent, text="起始時間：",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#aaffaa").grid(row=0, column=0, padx=(12, 4), pady=6)
        self._time_start = ctk.CTkEntry(
            parent, width=160, font=("Consolas", 11),
            placeholder_text="YYYY-MM-DD HH:MM:SS")
        self._time_start.grid(row=0, column=1, padx=4, pady=6)
        self._bind_entry_context_menu(self._time_start)

        ctk.CTkLabel(parent, text="迄止時間：",
                     font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#ffccaa").grid(row=0, column=2, padx=(16, 4), pady=6)
        self._time_end = ctk.CTkEntry(
            parent, width=160, font=("Consolas", 11),
            placeholder_text="YYYY-MM-DD HH:MM:SS（選填）")
        self._time_end.grid(row=0, column=3, padx=4, pady=6)
        self._bind_entry_context_menu(self._time_end)

        ctk.CTkLabel(parent, text="前後各：",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray70").grid(row=0, column=4, padx=(16, 2), pady=6)
        self._time_each = ctk.CTkEntry(parent, width=55, font=("Consolas", 11))
        self._time_each.insert(0, "50")
        self._time_each.grid(row=0, column=5, padx=2, pady=6)
        ctk.CTkLabel(parent, text="筆",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray70").grid(row=0, column=6, padx=(2, 8), pady=6)

        self._time_mode_lbl = ctk.CTkLabel(
            parent, text="尚未設定時間",
            font=("Microsoft JhengHei", 10), text_color="gray60")
        self._time_mode_lbl.grid(row=0, column=7, padx=8, pady=6, sticky="w")
        ctk.CTkButton(parent, text="清除時間", width=80,
                      font=("Microsoft JhengHei", 10), fg_color="gray35",
                      command=self._clear_time_filter).grid(
            row=0, column=8, padx=(4, 12), pady=6)

        self._time_start.bind("<KeyRelease>", self._on_time_change)
        self._time_end.bind("<KeyRelease>",   self._on_time_change)
        self._time_each.bind("<KeyRelease>",  self._on_time_change)

    def _toggle_time_bar(self):
        """時間篩選已常駐顯示；保留此方法相容既有呼叫，執行時 focus 起始時間欄位。"""
        self._time_start.focus_set()

    def _toggle_summary(self):
        self._summary_visible = not self._summary_visible
        if self._summary_visible:
            self._summary_content.grid()
            self._summary_btn.configure(text="錢包摘要 ▲")
        else:
            self._summary_content.grid_remove()
            self._summary_btn.configure(text="錢包摘要 ▼")

    def _toggle_approvals(self):
        self._approvals_visible = not self._approvals_visible
        if self._approvals_visible:
            self._approvals_content.grid()
        else:
            self._approvals_content.grid_remove()

    def _build_summary_section(self, parent: ctk.CTkFrame):
        parent.grid_columnconfigure(0, weight=1)

        f = ctk.CTkFrame(parent, corner_radius=0, fg_color="transparent")
        f.grid(row=0, column=0, sticky="ew", padx=0, pady=(2, 4))
        # 4-column: col0=key_L  col1=val_L  col2=key_R  col3=val_R
        f.grid_columnconfigure(0, weight=0, minsize=88)
        f.grid_columnconfigure(1, weight=1)
        f.grid_columnconfigure(2, weight=0, minsize=88)
        f.grid_columnconfigure(3, weight=1)

        self._summary_labels: list[tuple] = []

        def _k(text, sub=False, row=0, col=0, padx=(6, 2), pady=2, cspan=1):
            ctk.CTkLabel(f, text=text + "：",
                         font=("Microsoft JhengHei", 9 if sub else 10),
                         text_color="gray50" if sub else "gray65",
                         anchor="e").grid(
                row=row, column=col, columnspan=cspan,
                padx=padx, pady=pady, sticky="e")

        def _v(row, col, wrap=180, mono=True, cspan=1, pady=2):
            lbl = ctk.CTkLabel(f, text="—",
                               font=("Consolas", 10) if mono
                               else ("Microsoft JhengHei", 10),
                               anchor="w", wraplength=wrap)
            lbl.grid(row=row, column=col, columnspan=cspan,
                     padx=(2, 6), pady=pady, sticky="ew")
            self._bind_label_copy_menu(lbl)
            return lbl

        # Row 0: 區塊鏈 (val idx 0)
        _k("區塊鏈", row=0, col=0)
        v0 = _v(0, 1, wrap=120)

        # Row 1: 錢包地址 (val idx 1) — full width
        _k("錢包地址", row=1, col=0)
        v1 = _v(1, 1, wrap=460, cspan=3)

        # Row 2: 首次交易時間 (idx 2) | 最後交易時間 (idx 3)
        _k("首次交易", row=2, col=0)
        v2 = _v(2, 1, wrap=160)
        _k("最後交易", row=2, col=2, padx=(10, 2))
        v3 = _v(2, 3, wrap=160)

        # Row 3: 首次資金來源 (idx 4) — full width
        _k("首次資金來源", row=3, col=0)
        v4 = _v(3, 1, wrap=460, cspan=3)

        # Row 4: 欄位標題 header
        ctk.CTkLabel(f, text="  發  起",
                     font=("Microsoft JhengHei", 10, "bold"),
                     text_color="#88aadd", fg_color="#1a2a3a",
                     anchor="w", corner_radius=4).grid(
            row=4, column=0, columnspan=2,
            padx=(6, 2), pady=(5, 2), sticky="ew")
        ctk.CTkLabel(f, text="  接  受",
                     font=("Microsoft JhengHei", 10, "bold"),
                     text_color="#88ddaa", fg_color="#1a3a2a",
                     anchor="w", corner_radius=4).grid(
            row=4, column=2, columnspan=2,
            padx=(4, 6), pady=(5, 2), sticky="ew")

        # Row 5: 次數合計 (idx 5) | (idx 10)
        _k("次數（合計）", row=5, col=0)
        v5 = _v(5, 1, wrap=80)
        _k("次數（合計）", row=5, col=2, padx=(10, 2))
        v10 = _v(5, 3, wrap=80)

        # Row 6: ETH 次數 (idx 6) | (idx 11)
        _k("── ETH", sub=True, row=6, col=0, pady=1)
        v6 = _v(6, 1, wrap=80, pady=1)
        _k("── ETH", sub=True, row=6, col=2, padx=(10, 2), pady=1)
        v11 = _v(6, 3, wrap=80, pady=1)

        # Row 7: Token 次數 (idx 7) | (idx 12)
        _k("── Token", sub=True, row=7, col=0, pady=1)
        v7 = _v(7, 1, wrap=80, pady=1)
        _k("── Token", sub=True, row=7, col=2, padx=(10, 2), pady=1)
        v12 = _v(7, 3, wrap=80, pady=1)

        # Row 8: 總金額 (idx 8) | (idx 13)
        _k("總金額", row=8, col=0)
        v8 = _v(8, 1, wrap=160)
        _k("總金額", row=8, col=2, padx=(10, 2))
        v13 = _v(8, 3, wrap=160)

        # Row 9: Token 明細 (idx 9) | (idx 14)
        _k("Token 明細", row=9, col=0)
        v9 = _v(9, 1, wrap=160, mono=False)
        _k("Token 明細", row=9, col=2, padx=(10, 2))
        v14 = _v(9, 3, wrap=160, mono=False)

        # Row 10: 總手續費 (idx 15) | 費用流向 (idx 16)
        _k("總手續費", row=10, col=0, pady=(4, 4))
        v15 = _v(10, 1, wrap=160, pady=(4, 4))
        _k("費用主要流向", row=10, col=2, padx=(10, 2), pady=(4, 4))
        v16 = _v(10, 3, wrap=160, pady=(4, 4))

        # _summary_labels 必須依照 values 清單索引順序 (0-16)
        for v in (v0, v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14, v15, v16):
            self._summary_labels.append((None, v))

        self._tf_info_lbl = ctk.CTkLabel(
            f, text="", font=("Microsoft JhengHei", 9),
            anchor="w", text_color="#ffcc55", wraplength=480)
        self._tf_info_lbl.grid(row=11, column=0, columnspan=4,
                               padx=6, pady=(2, 4), sticky="w")

    def _build_approvals_section(self, parent: ctk.CTkFrame):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(parent, text="授權對象",
                     font=("Microsoft JhengHei", 10, "bold"),
                     text_color="#aac4ff", anchor="w").grid(
            row=0, column=0, padx=6, pady=(4, 2), sticky="w")

        frame = ctk.CTkFrame(parent, corner_radius=0, fg_color="transparent")
        frame.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 4))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        cols = ("合約地址", "授權對象 (Spender)", "交易 Hash / 金額", "時間")
        self._approval_tree = self._make_treeview(frame, cols)

    def _build_tx_section(self, parent: ctk.CTkFrame, attr: str):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        frame = ctk.CTkFrame(parent, corner_radius=8)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        tree = self._make_treeview(frame, ("請先執行分析",))
        setattr(self, attr, tree)

    # ── Hash 分析分頁 ─────────────────────────────────────────────────────────

    def _build_hash_tab(self, parent: ctk.CTkFrame):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        input_frame = ctk.CTkFrame(parent, corner_radius=8)
        input_frame.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
        input_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(input_frame, text="交易 Hash：",
                     font=("Microsoft JhengHei", 12, "bold")).grid(
            row=0, column=0, padx=(12, 6), pady=8)
        self.hash_entry = ctk.CTkEntry(
            input_frame,
            placeholder_text="輸入交易 Hash（0x... 或 64位十六進位）",
            font=("Consolas", 11))
        self.hash_entry.grid(row=0, column=1, padx=4, pady=8, sticky="ew")
        self.hash_entry.bind("<Return>", lambda _: self._start_hash_analysis())
        self._bind_entry_context_menu(self.hash_entry)

        ctk.CTkLabel(input_frame, text="鏈：",
                     font=("Microsoft JhengHei", 12)).grid(
            row=0, column=2, padx=(8, 2), pady=8)
        ctk.CTkOptionMenu(input_frame, variable=self.chain_var,
                          values=["ETH", "TRX", "BTC"], width=80,
                          font=("Microsoft JhengHei", 11)).grid(
            row=0, column=3, padx=2, pady=8)
        self.hash_btn = ctk.CTkButton(
            input_frame, text="查詢", width=90,
            font=("Microsoft JhengHei", 12, "bold"),
            command=self._start_hash_analysis)
        self.hash_btn.grid(row=0, column=4, padx=(4, 4), pady=8)

        ctk.CTkButton(
            input_frame, text="加入幣流圖", width=90,
            font=("Microsoft JhengHei", 11), fg_color="#4a2d6a",
            command=self._add_hash_to_flow_graph).grid(
            row=0, column=5, padx=(4, 12), pady=8)

        result_frame = ctk.CTkFrame(parent, corner_radius=8)
        result_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        result_frame.grid_columnconfigure(0, weight=1)
        result_frame.grid_rowconfigure(1, weight=1)

        self._hash_detail_frame = ctk.CTkScrollableFrame(
            result_frame, corner_radius=0, height=260)
        self._hash_detail_frame.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        self._hash_detail_frame.grid_columnconfigure(1, weight=1)
        self._hash_detail_labels: list[tuple] = []

        token_frame = ctk.CTkFrame(result_frame, corner_radius=0)
        token_frame.grid(row=1, column=0, sticky="nsew", padx=2, pady=(0, 2))
        token_frame.grid_columnconfigure(0, weight=1)
        token_frame.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(token_frame, text="Token 轉帳明細",
                     font=("Microsoft JhengHei", 11, "bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        self._hash_token_tree = self._make_treeview(
            token_frame, ("Token", "從", "至", "金額", "合約"))
        self._hash_token_tree.grid(row=1, column=0, sticky="nsew")

    # ── 涉案錢包/帳戶分頁 ────────────────────────────────────────────────────

    def _build_addr_tab(self, parent: ctk.CTkFrame):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        self._addr_tab_frame = parent
        ctk.CTkLabel(
            parent,
            text="請先完成步驟 3 選擇案件，此分頁將顯示涉案錢包 / 帳戶管理。",
            font=("Microsoft JhengHei", 12), text_color="gray50").grid(
            row=0, column=0)

    # ── 查詢歷史分頁 ──────────────────────────────────────────────────────────

    def _build_history_tab(self, parent: ctk.CTkFrame):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(parent, corner_radius=8)
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))

        ctk.CTkLabel(bar, text="顯示：",
                     font=("Microsoft JhengHei", 12)).pack(
            side="left", padx=(12, 4), pady=8)
        self._hist_mode = ctk.StringVar(value="錢包分析")
        ctk.CTkSegmentedButton(bar, values=["錢包分析", "Hash 查詢"],
                               variable=self._hist_mode,
                               command=self._load_history).pack(
            side="left", padx=4, pady=8)
        ctk.CTkButton(bar, text="重新整理", width=90,
                      font=("Microsoft JhengHei", 11),
                      command=self._load_history).pack(side="left", padx=8)
        self._del_hist_btn = ctk.CTkButton(
            bar, text="刪除選取", width=90,
            font=("Microsoft JhengHei", 11), fg_color="#8b1a1a",
            command=self._delete_history_row)
        self._del_hist_btn.pack(side="left", padx=4)
        ctk.CTkLabel(bar, text="搜尋地址/Hash：",
                     font=("Microsoft JhengHei", 11)).pack(side="left", padx=(20, 4))
        self._hist_search = ctk.CTkEntry(bar, width=220, font=("Consolas", 11))
        self._hist_search.pack(side="left", padx=4)
        self._bind_entry_context_menu(self._hist_search)
        self._hist_search.bind("<Return>", lambda _: self._load_history())
        ctk.CTkButton(bar, text="搜尋", width=60,
                      font=("Microsoft JhengHei", 11),
                      command=self._load_history).pack(side="left", padx=4)

        frame = ctk.CTkFrame(parent, corner_radius=8)
        frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        wallet_cols = ("鏈", "地址", "發起次數", "接受次數",
                       "Token 轉帳", "首次交易", "最後交易", "分析時間")
        self._hist_wallet_tree = self._make_treeview(frame, wallet_cols)

        hash_cols = ("鏈", "交易 Hash", "狀態", "發送方",
                     "接收方", "金額", "手續費", "時間", "查詢時間")
        self._hist_hash_tree = self._make_treeview(frame, hash_cols)
        self._hist_hash_tree.grid_remove()

        self._load_history()

    # ── 網站溯源分頁 ──────────────────────────────────────────────────────────

    def _build_cloudfail_tab(self, parent: ctk.CTkFrame):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        def _get_case_id() -> int | None:
            return self._active_case["id"] if self._active_case else None

        def _get_case_name() -> str:
            if self._active_case:
                return f"{self._active_case['case_number']} {self._active_case['case_name']}"
            return "（未選擇案件）"

        def _on_add_addr(data: dict) -> None:
            case_id = _get_case_id()
            if not case_id:
                return
            _db.upsert_case_address(case_id, data)
            if self._case_addr_panel:
                self._case_addr_panel._load()

        self._cloudfail_panel = CloudFailPanel(
            parent,
            get_case_id=_get_case_id,
            get_case_name=_get_case_name,
            on_add_address=_on_add_addr,
            corner_radius=0,
            fg_color="transparent",
        )
        self._cloudfail_panel.grid(row=0, column=0, sticky="nsew")

    # ═══════════════════════════════════════════════════════════════════════════
    # 頁面 5：幣流圖
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_flow_page(self):
        page = ctk.CTkFrame(self._content, corner_radius=10)
        self._pages["flow"] = page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self._flow_panel = FlowGraphPanel(page)
        self._flow_panel.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._flow_panel.set_node_click_callback(self._on_flow_node_clicked)

    # ═══════════════════════════════════════════════════════════════════════════
    # 頁面 6：產製報告
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_report_page(self):
        page = ctk.CTkFrame(self._content, corner_radius=10)
        self._pages["report"] = page
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(1, weight=1)

        # 頂部資訊列
        top = ctk.CTkFrame(page, corner_radius=8, fg_color="#12192a")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        top.grid_columnconfigure(1, weight=1)
        top.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(top, text="目前案件：",
                     font=("Microsoft JhengHei", 12, "bold"),
                     text_color="#94a3b8").grid(row=0, column=0, padx=(16, 6), pady=12)
        self._report_case_lbl = ctk.CTkLabel(
            top, text="尚未選擇案件",
            font=("Microsoft JhengHei", 12), text_color="#f5a623", anchor="w")
        self._report_case_lbl.grid(row=0, column=1, pady=12, sticky="w")

        ctk.CTkLabel(top, text="輸出目錄：",
                     font=("Microsoft JhengHei", 12, "bold"),
                     text_color="#94a3b8").grid(row=0, column=2, padx=(20, 6), pady=12)
        out_row = ctk.CTkFrame(top, fg_color="transparent")
        out_row.grid(row=0, column=3, padx=(0, 16), pady=12, sticky="ew")
        out_row.grid_columnconfigure(0, weight=1)
        self._report_outdir_entry = ctk.CTkEntry(
            out_row, font=("Consolas", 11),
            placeholder_text="點擊「瀏覽」選擇輸出資料夾…")
        self._report_outdir_entry.grid(row=0, column=0, sticky="ew")
        self._bind_entry_context_menu(self._report_outdir_entry)
        ctk.CTkButton(out_row, text="瀏覽", width=60,
                      font=("Microsoft JhengHei", 11),
                      command=self._report_browse_dir).grid(
            row=0, column=1, padx=(6, 0))

        # 左：幣流分析報告
        left = ctk.CTkFrame(page, corner_radius=10, fg_color="#0f1520")
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 4), pady=(4, 10))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(left, text="📊  幣流分析報告",
                     font=("Microsoft JhengHei", 15, "bold"),
                     text_color="#60a5fa").grid(
            row=0, column=0, padx=16, pady=(16, 4), sticky="w")
        ctk.CTkLabel(left,
                     text="依案件幣流資料產製「幣流分析專家意見書」Word 文件\n"
                          "壹案件背景 → 貳資料來源 → 參幣流事實 → … → 玖附錄",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray50", justify="left").grid(
            row=1, column=0, padx=16, pady=(0, 8), sticky="w")
        self._flow_report_log = ctk.CTkTextbox(
            left, font=("Consolas", 10), fg_color="#080d14",
            text_color="#6ee7b7", corner_radius=6, state="disabled")
        self._flow_report_log.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        ctk.CTkButton(left, text="▶  產製幣流分析報告",
                      font=("Microsoft JhengHei", 12, "bold"),
                      fg_color="#1d4ed8", hover_color="#1e3a8a",
                      height=42, command=self._generate_flow_report).grid(
            row=3, column=0, padx=12, pady=(0, 16), sticky="ew")

        # 右：案件分析報告
        right = ctk.CTkFrame(page, corner_radius=10, fg_color="#0f1520")
        right.grid(row=1, column=1, sticky="nsew", padx=(4, 10), pady=(4, 4))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(right, text="📋  案件分析報告",
                     font=("Microsoft JhengHei", 15, "bold"),
                     text_color="#a78bfa").grid(
            row=0, column=0, padx=16, pady=(16, 4), sticky="w")
        ctk.CTkLabel(right,
                     text="依案件基本資料產製「虛擬貨幣詐欺案件分析範本」Word 文件\n"
                          "九大章節架構，適用於偵查報告與法庭提呈",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray50", justify="left").grid(
            row=1, column=0, padx=16, pady=(0, 8), sticky="w")
        self._case_report_log = ctk.CTkTextbox(
            right, font=("Consolas", 10), fg_color="#080d14",
            text_color="#c4b5fd", corner_radius=6, state="disabled")
        self._case_report_log.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        ctk.CTkButton(right, text="▶  產製案件分析報告",
                      font=("Microsoft JhengHei", 12, "bold"),
                      fg_color="#4c1d95", hover_color="#3b1a7a",
                      height=42, command=self._generate_case_report).grid(
            row=3, column=0, padx=12, pady=(0, 16), sticky="ew")

        # ── 底部：交易所調閱申請書（橫跨兩欄） ──
        inquiry = ctk.CTkFrame(page, corner_radius=10, fg_color="#0f1a1f")
        inquiry.grid(row=2, column=0, columnspan=2, sticky="ew",
                     padx=10, pady=(0, 10))
        inquiry.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(inquiry, text="📨",
                     font=("Arial", 26)).grid(
            row=0, column=0, rowspan=2, padx=(18, 4), pady=16, sticky="w")

        ctk.CTkLabel(inquiry, text="交易所調閱申請書",
                     font=("Microsoft JhengHei", 15, "bold"),
                     text_color="#34d399").grid(
            row=0, column=1, padx=4, pady=(14, 0), sticky="w")
        ctk.CTkLabel(inquiry,
                     text="依案件資料填寫並產製寄往交易所（OKX / Binance / Bybit…）的正式調閱申請書"
                          "　　支援格式：.docx　.odt　.pdf",
                     font=("Microsoft JhengHei", 10),
                     text_color="gray50", justify="left").grid(
            row=1, column=1, padx=4, pady=(0, 14), sticky="w")

        info_bar = ctk.CTkFrame(inquiry, fg_color="transparent")
        info_bar.grid(row=0, column=2, rowspan=2, padx=(0, 16), pady=12, sticky="e")

        for tag, desc, color in [
            ("中英雙語", "符合交易所格式", "#6ee7b7"),
            ("自動帶入", "機關/聯絡資訊", "#93c5fd"),
            ("批次選址", "勾選涉案地址", "#fcd34d"),
        ]:
            badge = ctk.CTkFrame(info_bar, fg_color="#1a2a1f", corner_radius=10)
            badge.pack(side="left", padx=4)
            ctk.CTkLabel(badge, text=tag,
                         font=("Microsoft JhengHei", 10, "bold"),
                         text_color=color).pack(padx=10, pady=(4, 0))
            ctk.CTkLabel(badge, text=desc,
                         font=("Microsoft JhengHei", 9),
                         text_color="gray60").pack(padx=10, pady=(0, 4))

        ctk.CTkButton(inquiry,
                      text="📨  開啟申請書填表",
                      font=("Microsoft JhengHei", 12, "bold"),
                      height=40, width=180,
                      fg_color="#065f46", hover_color="#064e3b",
                      corner_radius=20,
                      command=self._open_inquiry_dialog).grid(
            row=0, column=3, rowspan=2, padx=(8, 20), pady=12)

    def _open_inquiry_dialog(self):
        from gui.exchange_inquiry_dialog import ExchangeInquiryDialog
        from database import db as _db2
        operator  = self.config_data.get("operator", {})
        case      = self._active_case or {}
        addresses = _db2.get_case_addresses(case["id"]) if case else []
        out_dir   = self._report_outdir_entry.get().strip()
        ExchangeInquiryDialog(self, operator=operator, case=case,
                              addresses=addresses, out_dir=out_dir)

    def _report_browse_dir(self):
        d = filedialog.askdirectory(title="選擇報告輸出目錄")
        if d:
            self._report_outdir_entry.delete(0, "end")
            self._report_outdir_entry.insert(0, d)

    def _append_report_log(self, textbox: ctk.CTkTextbox, msg: str):
        textbox.configure(state="normal")
        textbox.insert("end", msg + "\n")
        textbox.see("end")
        textbox.configure(state="disabled")

    def _generate_flow_report(self):
        if not self._active_case:
            messagebox.showinfo("請先選擇案件", "請先在步驟 3 選擇案件後再產製報告。")
            return
        out_dir = self._report_outdir_entry.get().strip()
        if not out_dir:
            messagebox.showwarning("缺少輸出目錄", "請先選擇報告輸出目錄。")
            return
        import os
        case = self._active_case
        op   = self.config_data.get("operator", {})
        ts   = datetime.datetime.now().strftime("%Y%m%d%H%M")
        fname = f"幣流分析報告_{case['case_number']}_{ts}.docx"
        out_path = os.path.join(out_dir, fname)

        self._append_report_log(self._flow_report_log,
            f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 開始產製幣流分析報告…")
        self._append_report_log(self._flow_report_log,
            f"  案件：{case['case_number']} {case['case_name']}")
        self._append_report_log(self._flow_report_log,
            f"  分析人員：{op.get('identity_name','—')} ({op.get('identity_id','—')})")

        def do_gen():
            try:
                from exporter.flow_report_builder import build_flow_report
                data = {
                    "case_number":  case.get("case_number", ""),
                    "case_name":    case.get("case_name", ""),
                    "investigator": op.get("identity_name", ""),
                    "unit":         op.get("identity_unit", ""),
                    "report_date":  datetime.datetime.now().strftime("%Y年%m月%d日"),
                }
                build_flow_report(data, out_path)
                self.after(0, self._append_report_log, self._flow_report_log,
                           f"  ✔ 完成：{out_path}")
                self.after(0, self.status_var.set, f"幣流報告已產製：{fname}")
            except Exception as e:
                self.after(0, self._append_report_log, self._flow_report_log,
                           f"  ✘ 錯誤：{e}")
        threading.Thread(target=do_gen, daemon=True).start()

    def _generate_case_report(self):
        if not self._active_case:
            messagebox.showinfo("請先選擇案件", "請先在步驟 3 選擇案件後再產製報告。")
            return
        out_dir = self._report_outdir_entry.get().strip()
        if not out_dir:
            messagebox.showwarning("缺少輸出目錄", "請先選擇報告輸出目錄。")
            return
        import os
        case = self._active_case
        op   = self.config_data.get("operator", {})
        ts   = datetime.datetime.now().strftime("%Y%m%d%H%M")
        fname = f"案件分析報告_{case['case_number']}_{ts}.docx"
        out_path = os.path.join(out_dir, fname)

        self._append_report_log(self._case_report_log,
            f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 開始產製案件分析報告…")
        self._append_report_log(self._case_report_log,
            f"  案件：{case['case_number']} {case['case_name']}")

        def do_gen():
            try:
                from exporter.case_template_builder import build_case_doc
                data = {
                    "case_number":  case.get("case_number", ""),
                    "case_name":    case.get("case_name", ""),
                    "case_type":    case.get("case_type", ""),
                    "investigator": op.get("identity_name", ""),
                    "unit":         op.get("identity_unit", ""),
                    "description":  case.get("description", ""),
                    "report_date":  datetime.datetime.now().strftime("%Y年%m月%d日"),
                }
                build_case_doc(data, out_path)
                self.after(0, self._append_report_log, self._case_report_log,
                           f"  ✔ 完成：{out_path}")
                self.after(0, self.status_var.set, f"案件報告已產製：{fname}")
            except Exception as e:
                self.after(0, self._append_report_log, self._case_report_log,
                           f"  ✘ 錯誤：{e}")
        threading.Thread(target=do_gen, daemon=True).start()

    # ═══════════════════════════════════════════════════════════════════════════
    # Treeview 與右鍵選單
    # ═══════════════════════════════════════════════════════════════════════════

    def _make_treeview(self, parent, columns: tuple,
                       add_addr_menu: bool = False) -> ttk.Treeview:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#2b2b2b", foreground="white",
                        fieldbackground="#2b2b2b", rowheight=22,
                        font=("Consolas", 10))
        style.configure("Treeview.Heading", background="#1f1f2e",
                        foreground="white", font=("Microsoft JhengHei", 10, "bold"))
        style.map("Treeview", background=[("selected", "#1f538d")])

        vsb  = ttk.Scrollbar(parent, orient="vertical")
        hsb  = ttk.Scrollbar(parent, orient="horizontal")
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
        tree.tag_configure("addr_highlight", background="#1a3a5a", foreground="#aaddff")
        self._bind_tree_context_menu(tree, add_addr_menu=add_addr_menu)
        return tree

    def _bind_entry_context_menu(self, widget):
        inner = widget._entry if hasattr(widget, "_entry") else widget
        menu  = tk.Menu(inner, tearoff=0, bg="#2b2b2b", fg="white",
                        activebackground="#1f538d", activeforeground="white",
                        font=("Microsoft JhengHei", 11))
        menu.add_command(label="複製", command=lambda: inner.event_generate("<<Copy>>"))
        menu.add_command(label="貼上", command=lambda: inner.event_generate("<<Paste>>"))
        menu.add_command(label="剪下", command=lambda: inner.event_generate("<<Cut>>"))
        menu.add_separator()
        menu.add_command(label="全選",
                         command=lambda: (inner.select_range(0, "end"),
                                          inner.icursor("end")))
        inner.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def _highlight_addr_in_trees(self, address: str):
        """側邊欄地址 hover：在符合的發送方/接收方格位前加 ► 標記（僅格位，不影響整列）。"""
        self._clear_highlight_in_trees()
        if not address:
            return
        addr_lower = address.lower()
        for attr in ("_tx_tree", "_token_tree"):
            tree = getattr(self, attr, None)
            if tree is None:
                continue
            cols = list(tree.cget("columns"))
            addr_col_map: dict = {}
            for c in ("發送方", "接收方"):
                if c in cols:
                    addr_col_map[c] = cols.index(c)
            if not addr_col_map:
                continue
            saved: dict = {}  # {iid: {col_idx: original_str}}
            for iid in tree.get_children():
                vals = list(tree.item(iid, "values"))
                cell_changes: dict = {}
                for col_name, col_idx in addr_col_map.items():
                    if col_idx < len(vals) and addr_lower in str(vals[col_idx]).lower():
                        original = str(vals[col_idx])
                        cell_changes[col_idx] = original
                        vals[col_idx] = "► " + original
                if cell_changes:
                    tree.item(iid, values=vals)
                    saved[iid] = cell_changes
            self._addr_highlight_iids[attr] = saved

    def _clear_highlight_in_trees(self):
        """清除交易列表中格位的 ► 標記，還原原始值。"""
        for attr in ("_tx_tree", "_token_tree"):
            tree = getattr(self, attr, None)
            if tree is None:
                continue
            saved = self._addr_highlight_iids.get(attr, {})
            for iid, cell_changes in saved.items():
                try:
                    vals = list(tree.item(iid, "values"))
                    for col_idx, original in cell_changes.items():
                        if col_idx < len(vals):
                            vals[col_idx] = original
                    tree.item(iid, values=vals)
                except Exception:
                    pass
            self._addr_highlight_iids[attr] = {}

    def _bind_tree_context_menu(self, tree: ttk.Treeview,
                                add_addr_menu: bool = False):
        menu = tk.Menu(tree, tearoff=0, bg="#2b2b2b", fg="white",
                       activebackground="#1f538d", activeforeground="white",
                       font=("Microsoft JhengHei", 11))
        self._tree_ctx_col: str = ""

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
            text = "\t".join(str(v) for v in tree.item(sel[0])["values"])
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_var.set("已複製整列資料")

        def copy_all():
            cols = tree["columns"]
            rows = ["\t".join(cols)]
            for iid in tree.get_children():
                rows.append("\t".join(str(v) for v in tree.item(iid)["values"]))
            self.clipboard_clear()
            self.clipboard_append("\n".join(rows))
            self.status_var.set(f"已複製全部 {len(rows)-1} 列資料")

        menu.add_command(label="複製此格", command=copy_cell)
        menu.add_command(label="複製整列", command=copy_row)
        menu.add_separator()
        menu.add_command(label="複製全部（含標題）", command=copy_all)

        if add_addr_menu:
            def _add_addr(col_name: str):
                sel = tree.selection()
                if not sel:
                    return
                vals = tree.item(sel[0])["values"]
                cols = list(tree["columns"])
                try:
                    raw = str(vals[cols.index(col_name)])
                except (ValueError, IndexError):
                    messagebox.showinfo("提示", f"目前分頁無「{col_name}」欄位", parent=self)
                    return
                addr = raw.split("…")[0].strip()
                if not addr or addr == "—":
                    messagebox.showinfo("無地址", "此欄位無有效地址", parent=self)
                    return
                if not self._active_case:
                    messagebox.showwarning("未選擇案件",
                        "請先在步驟 3 選取或建立案件，才能加入涉案地址。",
                        parent=self)
                    return
                chain = self._profile.get("chain", "TRX") if self._profile else "TRX"
                def on_save():
                    self._refresh_case_addr_tab()
                    self._refresh_sidebar()
                AddressDialog(
                    self,
                    case_id=self._active_case["id"],
                    prefill={"addr_type": "加密錢包",
                             "chain_institution": chain,
                             "address": addr},
                    on_save=on_save,
                )

            menu.add_separator()
            menu.add_command(label="＋ 加入發送方至涉案地址",
                             command=lambda: _add_addr("發送方"))
            menu.add_command(label="＋ 加入接收方至涉案地址",
                             command=lambda: _add_addr("接收方"))

            def _add_tx_to_flow():
                sel = tree.selection()
                if not sel:
                    return
                vals = tree.item(sel[0])["values"]
                cols = list(tree["columns"])

                def _get(col: str) -> str:
                    try:
                        return str(vals[cols.index(col)])
                    except (ValueError, IndexError):
                        return ""

                tx_hash   = _get("交易哈希")
                time_str  = _get("時間 (UTC+8)")
                from_addr = _get("發送方")
                to_addr   = _get("接收方")
                token     = _get("代幣")
                amt_str   = _get("數量")

                if not from_addr or not to_addr or from_addr == "—" or to_addr == "—":
                    messagebox.showinfo("無法加入", "此列缺少發送方或接收方地址", parent=self)
                    return

                try:
                    amount = float(amt_str.replace(",", "") or 0)
                except ValueError:
                    amount = 0.0

                if token in ("ETH", "TRX", "BTC"):
                    value_native = amount
                    token_symbol = ""
                    token_amount = 0.0
                else:
                    value_native = 0.0
                    token_symbol = token if token not in ("", "—") else ""
                    token_amount = amount

                chain = self._profile.get("chain", "ETH") if self._profile else "ETH"
                panel: FlowGraphPanel = self._flow_panel
                panel.add_row_edge(from_addr, to_addr, tx_hash, time_str,
                                   value_native, token_symbol, token_amount, chain)
                if self._query_mode.get() == "專案查詢" and self._active_case:
                    panel.set_case_id(self._active_case["id"])
                    panel._gen_mode.set("evidence")
                else:
                    panel._gen_mode.set("explore")
                panel._update_mode_label()
                self._show_step(4)

            menu.add_separator()
            menu.add_command(label="📊 此交易加入幣流圖",
                             command=_add_tx_to_flow)

        def show(event):
            region = tree.identify_region(event.x, event.y)
            if region in ("cell", "heading"):
                self._tree_ctx_col = tree.identify_column(event.x)
                try:
                    col_idx = int(self._tree_ctx_col.replace("#", "")) - 1
                    self._tree_ctx_col = tree["columns"][col_idx]
                except (ValueError, IndexError):
                    self._tree_ctx_col = ""
                iid = tree.identify_row(event.y)
                if iid:
                    tree.selection_set(iid)
            menu.tk_popup(event.x_root, event.y_root)

        tree.bind("<Button-3>", show)

    def _bind_label_copy_menu(self, label: ctk.CTkLabel):
        menu = tk.Menu(label, tearoff=0, bg="#2b2b2b", fg="white",
                       activebackground="#1f538d", activeforeground="white",
                       font=("Microsoft JhengHei", 11))
        menu.add_command(label="複製", command=lambda: (
            self.clipboard_clear(),
            self.clipboard_append(label.cget("text")),
            self.status_var.set(f"已複製：{label.cget('text')[:60]}")
        ))
        label.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    # ═══════════════════════════════════════════════════════════════════════════
    # 查詢歷史
    # ═══════════════════════════════════════════════════════════════════════════

    def _load_history(self, _=None):
        mode = self._hist_mode.get()
        kw   = self._hist_search.get().strip() if hasattr(self, "_hist_search") else ""
        if mode == "錢包分析":
            self._hist_hash_tree.grid_remove()
            self._hist_wallet_tree.grid(row=0, column=0, sticky="nsew")
            for iid in self._hist_wallet_tree.get_children():
                self._hist_wallet_tree.delete(iid)
            wallets = _db.get_all_wallets()
            if kw:
                wallets = [w for w in wallets
                           if kw.lower() in w.get("address","").lower()]
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
        if not messagebox.askyesno("確認刪除",
                                   f"確定刪除選取的 {len(sel)} 筆記錄？"):
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

    # ═══════════════════════════════════════════════════════════════════════════
    # 查詢模式 / 地址偵測
    # ═══════════════════════════════════════════════════════════════════════════

    def _on_mode_change(self, mode: str):
        if mode == "專案查詢" and not self._active_case:
            self.status_var.set("⚠ 專案查詢需先選擇案件（步驟 3）")
        else:
            self.status_var.set(
                f"已切換為【{mode}】模式" + (
                    "（結果不儲存至資料庫）" if mode == "一般查詢"
                    else "（結果將儲存並關聯案件）"))

    def _start_smart_query(self):
        text = self.addr_entry.get().strip()
        if not text:
            messagebox.showwarning("缺少輸入", "請輸入錢包地址或交易 Hash")
            return
        if self._query_mode.get() == "專案查詢" and not self._active_case:
            if not messagebox.askyesno(
                    "尚未選擇案件",
                    "專案查詢需要先選擇案件。\n\n"
                    "是否現在前往步驟 3 選擇案件？\n"
                    "（選「否」將改以一般查詢執行，結果不儲存）"):
                self._query_mode.set("一般查詢")
                self._on_mode_change("一般查詢")
            else:
                self._show_step(2)
                return
        if self._is_tx_hash(text):
            self.hash_entry.delete(0, "end")
            self.hash_entry.insert(0, text)
            self._show_case_data_tab("🔗  Hash 分析")
            self._start_hash_analysis()
        else:
            self._start_analysis()

    def _clear_results(self):
        self._profile = None
        self._last_hash_result = None
        self._tx_rows_base    = []
        self._token_rows_base = []
        for _, val_lbl in self._summary_labels:
            val_lbl.configure(text="—")
        for iid in self._approval_tree.get_children():
            self._approval_tree.delete(iid)
        for attr in ("_tx_tree", "_token_tree"):
            tree = getattr(self, attr, None)
            if tree:
                for iid in tree.get_children():
                    tree.delete(iid)
        for w in self._hash_detail_frame.winfo_children():
            w.destroy()
        for iid in self._hash_token_tree.get_children():
            self._hash_token_tree.delete(iid)
        self.addr_entry.delete(0, "end")
        if hasattr(self, "hash_entry"):
            self.hash_entry.delete(0, "end")
        if hasattr(self, "_search_var"):
            self._search_var.set("")
            self._search_amt_min.set("")
            self._search_amt_max.set("")
            self._search_t_from.set("")
            self._search_t_to.set("")
        if hasattr(self, "_dust_count_lbl"):
            self._dust_count_lbl.configure(text="")
        if hasattr(self, "_search_result_lbl"):
            self._search_result_lbl.configure(text="")
        self.status_var.set("查詢結果已清除")

    def _on_dust_filter_change(self):
        if self._profile is not None:
            self._refresh_tx_display()

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
        address = self.addr_entry.get().strip()
        if address:
            detected = self._detect_chain(address)
            if detected and detected != self.chain_var.get():
                self.status_var.set(
                    f"⚠ 注意：此地址格式為 {detected}，請確認選擇正確的鏈")

    # ═══════════════════════════════════════════════════════════════════════════
    # Hash 分析
    # ═══════════════════════════════════════════════════════════════════════════

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
                api    = EtherscanAPI(self.config_data.get("etherscan_api_key", ""))
                raw    = api.get_transaction(tx_hash)
                result = analyze_eth_tx(raw)
            elif chain == "TRX":
                api    = TronScanAPI(self.config_data.get("trongrid_api_key", ""))
                raw    = api.get_transaction(tx_hash)
                result = analyze_trx_tx(raw)
            else:
                api    = BitcoinAPI()
                raw    = api.get_transaction(tx_hash)
                result = analyze_btc_tx(raw)
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
        self._last_hash_result = result
        self.progress.stop()
        self.progress.grid_remove()
        self.hash_btn.configure(state="normal")
        self._show_case_data_tab("🔗  Hash 分析")

        for w in self._hash_detail_frame.winfo_children():
            w.destroy()
        self._hash_detail_labels.clear()

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

        for row_id in self._hash_token_tree.get_children():
            self._hash_token_tree.delete(row_id)
        for t in result.get("token_transfers", []):
            self._hash_token_tree.insert("", "end", values=(
                t.get("Token",""), t.get("從",""),
                t.get("至",""), t.get("金額",""), t.get("合約",""),
            ))

        mode = self._query_mode.get()
        if mode == "一般查詢":
            saved_hint = "【一般查詢－未儲存】"
        else:
            case_hint = (f"已存入【{self._active_case['case_number']}】"
                         if self._active_case else "已儲存（未關聯案件）")
            saved_hint = f"【專案查詢－{case_hint}】"
        self.status_var.set(
            f"Hash 查詢完成｜{result.get('chain','')} {result.get('狀態','')}　{saved_hint}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 錢包分析
    # ═══════════════════════════════════════════════════════════════════════════

    def _start_analysis(self):
        address = self.addr_entry.get().strip()
        if not address:
            messagebox.showwarning("缺少輸入", "請輸入錢包地址")
            return
        chain = self.chain_var.get()
        detected = self._detect_chain(address)
        if detected and detected != chain:
            ans = messagebox.askyesno(
                "地址格式不符",
                f"您選擇的是 {chain}，但輸入地址格式像是 {detected}。\n\n"
                f"是否自動切換為 {detected} 並繼續？")
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
            messagebox.showwarning("缺少 API Key",
                                   "請先在右上角「⚙ 設定」中填入 Etherscan API Key")
            return
        tf = self._get_time_filter()
        no_limit = getattr(self, '_no_limit_next', False)
        self._no_limit_next = False
        self.analyze_btn.configure(state="disabled", text="查詢中…")
        self.status_var.set("分析中，請稍候...")
        self.progress.grid()
        self.progress.start()
        self._query_progress_bar.grid()
        self._query_progress_bar.start()
        self._query_step_lbl.configure(text="⏳ 正在連接 API…")
        self._query_step_lbl.grid()
        self._profile = None
        threading.Thread(target=self._run_analysis,
                         args=(chain, address, tf, no_limit), daemon=True).start()

    def _run_analysis(self, chain: str, address: str, tf: dict | None,
                      no_limit: bool = False):
        # 無時間篩選且未被使用者強制取消上限時，啟用早期中止保護
        max_r = MAX_TOTAL if not (tf or no_limit) else None
        try:
            if chain == "ETH":
                api = EtherscanAPI(self.config_data["etherscan_api_key"])
                ver = "V2" if api._use_v2 else "V1"
                self._set_query_step(f"使用 Etherscan {ver} API，正在抓取 ETH 一般交易...")
                txs       = api.get_normal_transactions(address, max_records=max_r)
                self._set_query_step("正在抓取 Internal 交易...")
                int_txs   = api.get_internal_transactions(address)
                remaining = (max_r - len(txs)) if max_r is not None else None
                self._set_query_step("正在抓取 ERC-20 轉帳記錄...")
                erc20     = api.get_erc20_transfers(address, max_records=remaining)
                self._set_query_step("正在分析授權紀錄...")
                approvals = api.get_token_approvals(txs, address)
                profile   = profile_eth(address, txs, int_txs, erc20, approvals)
                total_raw = len(txs) + len(erc20)
                detail    = f"原生交易 {len(txs)} 筆、ERC-20 轉帳 {len(erc20)} 筆"
            elif chain == "TRX":
                api = TronScanAPI(self.config_data.get("trongrid_api_key", ""))
                is_center = tf and tf["mode"] == "center"

                if tf and tf["mode"] == "range":
                    self._set_query_step("正在抓取 TRX 交易資料（範圍篩選）...")
                    s_ts, e_ts = tf["start_ts"], tf["end_ts"]
                    txs = api.get_transactions(address, start_ts=s_ts, end_ts=e_ts,
                                               max_records=max_r)
                elif is_center:
                    # 置中模式：TronScan 預設降序
                    # 軸心前：max_timestamp=pivot → 降序第一頁即最接近 pivot 的 N 筆
                    # 軸心後：漸進擴大時間窗口，找到「窗口內所有記錄可全量取回且 >= each」的最小窗口；
                    #         因為可全量取回（< 10000 筆），排序後取最舊 N 筆即為 pivot 後最早的交易。
                    #         若窗口超過 10001 筆（TooManyRecordsError），用上一個窗口的最佳結果。
                    s_ts = e_ts = None
                    center_ts = tf["start_ts"]
                    each = tf["each_side"]

                    self._set_query_step("正在抓取 TRX 軸心前交易...")
                    before_list = api.get_transactions(address, end_ts=center_ts, limit=each)

                    self._set_query_step("正在搜尋軸心後最適查詢窗口...")
                    after_list = []
                    _best_raw: list = []  # 最近一次未飽和的全量結果
                    for _w in [600, 1800, 3600, 21600, 86400, 259200, 604800, 2592000]:
                        try:
                            raw_w = api.get_transactions(
                                address, start_ts=center_ts, end_ts=center_ts + _w,
                                limit=99999, max_records=10001)
                            _best_raw = raw_w  # 未飽和，存為最佳候選
                            if len(raw_w) >= each:
                                # 已有足夠筆數，取最舊 each 筆即為軸心後最早的交易
                                self._set_query_step("正在整理 TRX 軸心後交易...")
                                raw_w.sort(key=lambda t: get_tx_ts(t, "TRX"))
                                after_list = raw_w[:each]
                                break
                            # 不足 each 筆，嘗試更大窗口
                        except TooManyRecordsError:
                            # 此窗口已飽和，使用上一個未飽和窗口的最佳結果
                            if _best_raw:
                                _best_raw.sort(key=lambda t: get_tx_ts(t, "TRX"))
                                after_list = _best_raw[:each]
                            break  # 更大窗口必然也飽和，不繼續嘗試

                    if not after_list and _best_raw:
                        # 遍歷完所有窗口仍不足 each 筆（低頻地址），取現有全部
                        _best_raw.sort(key=lambda t: get_tx_ts(t, "TRX"))
                        after_list = _best_raw[:each]

                    txs = before_list + after_list
                else:
                    self._set_query_step("正在抓取 TRX 交易資料...")
                    s_ts = e_ts = None
                    txs = api.get_transactions(address, max_records=max_r)

                remaining = (max_r - len(txs)) if max_r is not None else None
                self._set_query_step("正在抓取 TRC-20 轉帳...")
                if is_center:
                    # 置中模式 TRC-20：分側策略（與 TRX 原生交易相同）
                    # 軸心前：TronScan 預設降序，limit=each 即可取最近 each 筆
                    try:
                        _trc20_before = api.get_trc20_transfers(
                            address, end_ts=center_ts, limit=each)
                    except Exception:
                        _trc20_before = []
                    # 軸心後：漸進擴大窗口，取最舊 each 筆（排序後截取）
                    self._set_query_step("正在搜尋 TRC-20 軸心後轉帳...")
                    _trc20_after: list = []
                    _trc20_best_a: list = []
                    for _wt in [3600, 21600, 86400, 259200, 604800]:
                        try:
                            _chunk = api.get_trc20_transfers(
                                address, start_ts=center_ts, end_ts=center_ts + _wt,
                                limit=99999, max_records=10001)
                            _trc20_best_a = _chunk
                            if len(_chunk) >= each:
                                _chunk.sort(key=lambda t: get_tx_ts(t, "TRX"))
                                _trc20_after = _chunk[:each]
                                break
                        except TooManyRecordsError:
                            if _trc20_best_a:
                                _trc20_best_a.sort(key=lambda t: get_tx_ts(t, "TRX"))
                                _trc20_after = _trc20_best_a[:each]
                            break
                    if not _trc20_after and _trc20_best_a:
                        _trc20_best_a.sort(key=lambda t: get_tx_ts(t, "TRX"))
                        _trc20_after = _trc20_best_a[:each]
                    trc20 = _trc20_before + _trc20_after
                else:
                    trc20_start, trc20_end = s_ts, e_ts
                    if trc20_start is None and tf:
                        trc20_start = tf["start_ts"] - 86400 * 90
                        trc20_end   = tf["start_ts"] + 86400 * 90
                    trc20 = api.get_trc20_transfers(address, start_ts=trc20_start,
                                                    end_ts=trc20_end,
                                                    max_records=remaining)
                self._set_query_step("正在分析授權紀錄...")
                approvals = api.get_token_approvals(txs, address)
                profile   = profile_trx(address, txs, trc20, approvals)
                total_raw = len(txs) + len(trc20)
                detail    = f"原生交易 {len(txs)} 筆、TRC-20 轉帳 {len(trc20)} 筆"
            else:
                api = BitcoinAPI()
                self._set_query_step("正在抓取 BTC 交易資料...")
                txs     = api.get_transactions(address, max_records=max_r)
                profile = profile_btc(address, txs)
                total_raw = len(txs)
                detail    = f"交易 {len(txs)} 筆"

            # ── 原始資料超量警告（no_limit 模式：使用者確認後仍全量抓取） ──
            if total_raw > MAX_TOTAL and not tf and no_limit:
                import queue as _queue
                q = _queue.Queue()
                self.after(0, self._show_overflow_dialog,
                           total_raw, detail, address, chain, q)
                if not q.get(timeout=120):
                    self.after(0, self._stop_progress)
                    self._set_status(
                        f"已取消｜共 {total_raw:,} 筆，請設定時間篩選後重新查詢")
                    return

            if tf and not (chain == "TRX" and tf["mode"] == "range"):
                self._set_query_step("正在套用時間篩選...")
                profile = self._apply_time_filter_sync(profile, tf)

            self._profile = profile
            if self._query_mode.get() == "專案查詢":
                try:
                    wallet_id = _db.save_wallet_profile(profile)
                    if self._active_case and wallet_id:
                        _db.link_wallet_to_case(wallet_id, self._active_case["id"])
                except Exception:
                    pass
            self.after(0, self._update_ui, profile)
        except TooManyRecordsError as exc:
            self.after(0, self._on_too_many_records, exc.count, chain, address)
        except Exception as e:
            self.after(0, self._on_error, str(e))

    def _apply_time_filter_sync(self, profile: dict, tf: dict) -> dict:
        chain = profile.get("chain", "ETH")
        raw   = profile.get("raw_txs", [])
        erc20 = profile.get("raw_erc20", [])
        trc20 = profile.get("raw_trc20", [])
        all_txs = raw + erc20 + trc20

        if tf["mode"] == "range":
            filtered_raw = filter_by_range(raw,   chain, tf["start_ts"], tf["end_ts"])
            filtered_erc = filter_by_range(erc20, chain, tf["start_ts"], tf["end_ts"])
            filtered_trc = filter_by_range(trc20, chain, tf["start_ts"], tf["end_ts"])
            total = len(filtered_raw) + len(filtered_erc) + len(filtered_trc)
            warn  = check_overflow(total, MAX_TOTAL)
            if warn:
                import queue
                q = queue.Queue()
                def ask():
                    q.put(messagebox.askyesno("交易筆數超過上限", warn))
                self.after(0, ask)
                try:
                    ans = q.get(timeout=300)
                except Exception:
                    ans = False
                if not ans:
                    filtered_raw = filtered_raw[:MAX_TOTAL]
                    filtered_erc = []
                    filtered_trc = []
            profile["raw_txs"] = filtered_raw
            if erc20: profile["raw_erc20"] = filtered_erc
            if trc20: profile["raw_trc20"] = filtered_trc
            profile["_time_filter_applied"] = (
                f"範圍篩選 {ts_to_str(tf['start_ts'])} ～ {ts_to_str(tf['end_ts'])}，"
                f"共 {len(filtered_raw)+len(filtered_erc)+len(filtered_trc)} 筆"
            )
        else:
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
                try:
                    ans = q.get(timeout=300)
                except Exception:
                    ans = False
                if ans:
                    new_each = min(
                        max(res["total_available_before"],
                            res["total_available_after"]),
                        MAX_TOTAL // 2)
                    res = filter_centered(all_txs, chain, tf["start_ts"], new_each)
                    tf["each_side"] = new_each
            pivot  = res.get("pivot_tx") or {}
            raw_ts = (pivot.get("timeStamp") or pivot.get("timestamp") or
                      pivot.get("time") or 0)
            try:
                ms = int(raw_ts)
                if ms > 1e12: ms //= 1000
            except Exception:
                ms = 0
            pivot_time = ts_to_str(ms) if ms else "N/A"
            # 用物件 id 將混合結果拆回各自的分類，保留各欄位正確格式
            raw_ids   = {id(tx) for tx in raw}
            erc20_ids = {id(tx) for tx in erc20}
            trc20_ids = {id(tx) for tx in trc20}
            profile["raw_txs"] = [tx for tx in res["result"] if id(tx) in raw_ids]
            if "raw_erc20" in profile:
                profile["raw_erc20"] = [tx for tx in res["result"] if id(tx) in erc20_ids]
            if "raw_trc20" in profile:
                profile["raw_trc20"] = [tx for tx in res["result"] if id(tx) in trc20_ids]
            n_raw   = len(profile["raw_txs"])
            n_token = len(profile.get("raw_erc20", [])) + len(profile.get("raw_trc20", []))
            profile["_time_filter_applied"] = (
                f"置中篩選 軸心 {ts_to_str(tf['start_ts'])}（最近 {pivot_time}），"
                f"前 {res['before']} 筆＋後 {res['after']} 筆，"
                f"共 {n_raw} 筆原生交易＋{n_token} 筆 Token 轉帳"
            )
        return profile

    def _set_status(self, msg: str):
        self.after(0, self.status_var.set, msg)

    def _set_query_step(self, msg: str):
        self.after(0, self.status_var.set, msg)
        self.after(0, self._query_step_lbl.configure, {"text": "⏳ " + msg})

    def _on_error(self, msg: str):
        self._stop_progress()
        messagebox.showerror("分析失敗", msg)

    def _stop_progress(self):
        self.progress.stop()
        self.progress.grid_remove()
        self._query_progress_bar.stop()
        self._query_progress_bar.grid_remove()
        self._query_step_lbl.configure(text="")
        self._query_step_lbl.grid_remove()
        self.analyze_btn.configure(state="normal", text="開始查詢")

    def _on_too_many_records(self, count: int, chain: str, address: str):
        """查詢中途超過 MAX_TOTAL 筆時立即中止，主執行緒呼叫此對話框。"""
        self._stop_progress()
        self._set_status(f"查詢中止｜抓取到 {count:,} 筆時已超過 {MAX_TOTAL:,} 筆上限")

        _CHAIN_PATH = {"ETH": "eth", "TRX": "tron", "BTC": "btc"}
        chain_path = _CHAIN_PATH.get(chain, chain.lower())
        oklink_url = f"https://www.oklink.com/zh-hant/{chain_path}/address/{address}"

        dlg = ctk.CTkToplevel(self)
        dlg.title("查詢中止 — 超過 1,000 筆")
        dlg.geometry("560x360")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus_force()
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(body,
            text=f"🛑  抓取到 {count:,} 筆時已超過上限，查詢已立即停止",
            font=("Microsoft JhengHei", 14, "bold"),
            text_color="#ff6b6b").pack(anchor="w")

        ctk.CTkLabel(body,
            text=f"此地址交易量龐大（已知至少 {count:,} 筆），繼續全量抓取將耗費大量時間。",
            font=("Microsoft JhengHei", 11),
            text_color="gray70").pack(anchor="w", pady=(4, 10))

        ctk.CTkLabel(body,
            text="建議做法：\n"
                 "  1. 點擊「⏱ 設定時間篩選」，縮小查詢日期區間後重新查詢\n"
                 "  2. 於 OKLink 確認此地址是否為交易所錢包（具水庫標籤），\n"
                 "     再決定是否需要全量資料\n\n"
                 "若確認需要全部資料，可選擇「繼續查詢全部」（可能需數分鐘）。",
            font=("Microsoft JhengHei", 11),
            justify="left",
            text_color="gray85").pack(anchor="w")

        link_frame = ctk.CTkFrame(body, fg_color="#1a2a3a", corner_radius=6)
        link_frame.pack(fill="x", pady=(10, 4))
        ctk.CTkLabel(link_frame,
            text="🔗  OKLink 地址查詢：",
            font=("Microsoft JhengHei", 10),
            text_color="gray60").pack(side="left", padx=(10, 4), pady=6)
        url_lbl = ctk.CTkLabel(link_frame,
            text=oklink_url,
            font=("Consolas", 10),
            text_color="#4da6ff",
            cursor="hand2")
        url_lbl.pack(side="left", pady=6)
        url_lbl.bind("<Button-1>", lambda _: webbrowser.open(oklink_url))
        ctk.CTkButton(link_frame,
            text="開啟", width=55, height=24,
            font=("Microsoft JhengHei", 10),
            fg_color="#1f538d",
            command=lambda: webbrowser.open(oklink_url)).pack(
            side="right", padx=8, pady=4)

        btn_frame = ctk.CTkFrame(body, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(12, 0))

        def _use_filter():
            dlg.destroy()
            self._show_case_data_tab("🔍  地址側寫")
            self._toggle_time_bar()

        def _continue_all():
            dlg.destroy()
            self._no_limit_next = True
            self._start_analysis()

        ctk.CTkButton(btn_frame,
            text="⏱  設定時間篩選（建議）",
            width=210, font=("Microsoft JhengHei", 11, "bold"),
            fg_color="#1f538d", hover_color="#2a6aad",
            command=_use_filter).pack(side="left")
        ctk.CTkButton(btn_frame,
            text="繼續查詢全部資料",
            width=170, font=("Microsoft JhengHei", 11),
            fg_color="gray30", hover_color="gray40",
            command=_continue_all).pack(side="right")

    def _show_overflow_dialog(self, total_raw: int, detail: str,
                               address: str, chain: str, result_queue):
        """超量警告自訂對話框（主執行緒呼叫，結果透過 queue 回傳）。"""
        _CHAIN_PATH = {"ETH": "eth", "TRX": "tron", "BTC": "btc"}
        chain_path  = _CHAIN_PATH.get(chain, chain.lower())
        oklink_url  = f"https://www.oklink.com/zh-hant/{chain_path}/address/{address}"

        dlg = ctk.CTkToplevel(self)
        dlg.title("交易筆數過多")
        dlg.geometry("540x340")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus_force()
        dlg.protocol("WM_DELETE_WINDOW", lambda: (result_queue.put(False), dlg.destroy()))

        result = [False]

        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(body,
            text=f"⚠  共抓取到 {total_raw:,} 筆交易記錄",
            font=("Microsoft JhengHei", 14, "bold"),
            text_color="#ffcc44").pack(anchor="w")

        ctk.CTkLabel(body,
            text=detail,
            font=("Microsoft JhengHei", 11),
            text_color="gray70").pack(anchor="w", pady=(2, 10))

        ctk.CTkLabel(body,
            text="資料量過大可能導致：\n"
                 "  • 顯示與捲動較慢\n"
                 "  • 幣流圖節點過多難以辨識\n\n"
                 "建議：先至 OKLink 確認該地址是否為交易所錢包\n"
                 "（具有「水庫」等標籤），再決定是否套用時間篩選。",
            font=("Microsoft JhengHei", 11),
            justify="left",
            text_color="gray85").pack(anchor="w")

        link_frame = ctk.CTkFrame(body, fg_color="#1a2a3a", corner_radius=6)
        link_frame.pack(fill="x", pady=(10, 4))
        ctk.CTkLabel(link_frame,
            text="🔗  OKLink 地址查詢：",
            font=("Microsoft JhengHei", 10),
            text_color="gray60").pack(side="left", padx=(10, 4), pady=6)
        url_lbl = ctk.CTkLabel(link_frame,
            text=oklink_url,
            font=("Consolas", 10),
            text_color="#4da6ff",
            cursor="hand2")
        url_lbl.pack(side="left", pady=6)
        url_lbl.bind("<Button-1>", lambda _: webbrowser.open(oklink_url))
        ctk.CTkButton(link_frame,
            text="開啟", width=55, height=24,
            font=("Microsoft JhengHei", 10),
            fg_color="#1f538d",
            command=lambda: webbrowser.open(oklink_url)).pack(
            side="right", padx=8, pady=4)

        btn_frame = ctk.CTkFrame(body, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(12, 0))

        def _yes():
            result[0] = True
            result_queue.put(True)
            dlg.destroy()

        def _no():
            result[0] = False
            result_queue.put(False)
            dlg.destroy()

        ctk.CTkButton(btn_frame,
            text=f"繼續顯示全部 {total_raw:,} 筆",
            width=200, font=("Microsoft JhengHei", 11),
            fg_color="#2a5a2a", hover_color="#3a7a3a",
            command=_yes).pack(side="left")
        ctk.CTkButton(btn_frame,
            text="取消，改用時間篩選",
            width=180, font=("Microsoft JhengHei", 11),
            fg_color="gray30", hover_color="gray40",
            command=_no).pack(side="right")

    # ═══════════════════════════════════════════════════════════════════════════
    # UI 更新
    # ═══════════════════════════════════════════════════════════════════════════

    def _update_ui(self, p: dict):
        self._stop_progress()
        chain   = p.get("chain", "")
        unit    = {"ETH": "ETH", "TRX": "TRX", "BTC": "BTC"}.get(chain, "")
        amt_key = {"ETH": "out_total_eth", "TRX": "out_total_trx",
                   "BTC": "out_total_btc"}.get(chain, "")
        in_key  = {"ETH": "in_total_eth",  "TRX": "in_total_trx",
                   "BTC": "in_total_btc"}.get(chain, "")
        fee_key = {"ETH": "total_fee_eth", "TRX": "total_fee_trx",
                   "BTC": "total_fee_btc"}.get(chain, "")

        def _fmt_token_dict(d: dict) -> str:
            if not d:
                return "—"
            return "  |  ".join(f"{sym}: {amt:,.4f}"
                                 for sym, amt in sorted(d.items()))

        if chain == "ETH":
            values = [
                chain, p.get("address",""),
                p.get("first_tx_time","N/A"), p.get("last_tx_time","N/A"),
                p.get("first_source","N/A"),
                str(p.get("out_count",0)),
                str(p.get("eth_out_count",0)), str(p.get("erc20_out_count",0)),
                f"{p.get('out_total_eth',0):,.8f} ETH",
                _fmt_token_dict(p.get("erc20_out_by_token",{})),
                str(p.get("in_count",0)),
                str(p.get("eth_in_count",0)), str(p.get("erc20_in_count",0)),
                f"{p.get('in_total_eth',0):,.8f} ETH",
                _fmt_token_dict(p.get("erc20_in_by_token",{})),
                f"{p.get('total_fee_eth',0):,.8f} ETH",
                p.get("top_fee_dest","N/A"),
            ]
        elif chain == "TRX":
            trc20_out = p.get("trc20_out_by_token", {})
            trc20_in  = p.get("trc20_in_by_token",  {})
            values = [
                chain, p.get("address",""),
                p.get("first_tx_time","N/A"), p.get("last_tx_time","N/A"),
                p.get("first_source","N/A"),
                str(p.get("out_count",0)),
                str(p.get("trx_out_count",0)), str(p.get("trc20_out_count",0)),
                f"{p.get(amt_key,0):,.6f} TRX", _fmt_token_dict(trc20_out),
                str(p.get("in_count",0)),
                str(p.get("trx_in_count",0)), str(p.get("trc20_in_count",0)),
                f"{p.get(in_key,0):,.6f} TRX", _fmt_token_dict(trc20_in),
                f"{p.get(fee_key,0):,.6f} TRX", p.get("top_fee_dest","N/A"),
            ]
        else:
            values = [
                chain, p.get("address",""),
                p.get("first_tx_time","N/A"), p.get("last_tx_time","N/A"),
                p.get("first_source","N/A"),
                str(p.get("out_count",0)), "—", "—",
                f"{p.get(amt_key,0):,.8f} {unit}", "—",
                str(p.get("in_count",0)), "—", "—",
                f"{p.get(in_key,0):,.8f} {unit}", "—",
                f"{p.get(fee_key,0):,.8f} {unit}", p.get("top_fee_dest","N/A"),
            ]

        for (_, val_lbl), val in zip(self._summary_labels, values):
            val_lbl.configure(text=val)

        tf_info = p.get("_time_filter_applied")
        if hasattr(self, "_tf_info_lbl"):
            self._tf_info_lbl.configure(
                text=f"⏱ {tf_info}" if tf_info else "",
                text_color="#ffcc55" if tf_info else "gray60")

        for row in self._approval_tree.get_children():
            self._approval_tree.delete(row)
        for a in p.get("approval_targets", []):
            self._approval_tree.insert("", "end", values=(
                a.get("contract",""), a.get("spender",""),
                a.get("tx_hash", a.get("amount","")), a.get("time",""),
            ))

        # 更新授權按鈕文字與顏色
        approval_count = len(p.get("approval_targets", []))
        if hasattr(self, "_approval_btn"):
            if approval_count > 0:
                self._approval_btn.configure(
                    text=f"⚠ 有授權對象（{approval_count} 筆）",
                    fg_color="#7a1f1f",
                    hover_color="#9a2f2f"
                )
            else:
                self._approval_btn.configure(
                    text="無授權對象",
                    fg_color="gray30",
                    hover_color="gray40"
                )

        tx_rows, token_rows = self._normalize_for_display(p)
        self._tx_rows_base    = tx_rows
        self._token_rows_base = token_rows
        self._sort_state["_tx_tree"]    = {"col": "", "asc": True}
        self._sort_state["_token_tree"] = {"col": "", "asc": True}
        if hasattr(self, "_search_var"):
            self._search_var.set("")
            self._search_amt_min.set("")
            self._search_amt_max.set("")
            self._search_t_from.set("")
            self._search_t_to.set("")
        if hasattr(self, "_search_result_lbl"):
            self._search_result_lbl.configure(text="")
        self._rebuild_tree("_tx_tree",    tx_rows)
        self._rebuild_tree("_token_tree", token_rows)
        self._refresh_tx_display()

        total    = p.get("out_count",0) + p.get("in_count",0)
        has_data = bool(p.get("raw_txs") or p.get("raw_erc20") or p.get("raw_trc20"))
        if total == 0 and not has_data:
            addr = p.get("address","")
            explorer = {
                "ETH": f"https://etherscan.io/address/{addr}",
                "TRX": f"https://tronscan.org/#/address/{addr}",
                "BTC": f"https://blockchain.com/btc/address/{addr}",
            }.get(chain, "")
            messagebox.showwarning("查無交易記錄",
                f"此地址在 {chain} 鏈上沒有找到任何交易記錄。\n\n"
                f"可能原因：\n• 全新未使用的地址\n• 地址輸入有誤\n• 鏈選擇錯誤\n\n"
                f"請至區塊鏈瀏覽器確認：\n{explorer}")
            self.status_var.set("查無資料｜請確認地址是否正確")
        else:
            if p.get("_from_db"):
                saved_hint = "【資料庫快取－無需重新查詢】"
            else:
                mode = self._query_mode.get()
                if mode == "一般查詢":
                    saved_hint = "【一般查詢－未儲存至資料庫】"
                else:
                    case_hint = (f"已存入案件【{self._active_case['case_number']}】"
                                 if self._active_case else "已儲存（未關聯案件）")
                    saved_hint = f"【專案查詢－{case_hint}】"
            self.status_var.set(
                f"分析完成｜共 {total} 筆交易｜"
                f"授權 {len(p.get('approval_targets',[]))} 筆　{saved_hint}")
        self._refresh_sidebar()

    def _normalize_for_display(self, p: dict) -> tuple[list[dict], list[dict]]:
        """將各鏈原始 API 資料轉換為固定 6 欄顯示格式，時間統一轉 UTC+8。"""
        _TZ8 = datetime.timezone(datetime.timedelta(hours=8))

        def _fmt_ts(ts_val, ms=False) -> str:
            try:
                ts = int(ts_val)
                if ms:
                    ts //= 1000
                if ts <= 0:
                    return "—"
                return datetime.datetime.fromtimestamp(ts, tz=_TZ8).strftime(
                    "%Y-%m-%d %H:%M:%S")
            except Exception:
                return "—"

        chain     = p.get("chain", "")
        raw_txs   = p.get("raw_txs",   [])
        raw_erc20 = p.get("raw_erc20", [])
        raw_trc20 = p.get("raw_trc20", [])
        tx_rows: list[dict]    = []
        token_rows: list[dict] = []

        if chain == "ETH":
            for t in raw_txs:
                try:
                    amt = f"{int(t.get('value', 0)) / 1e18:.8f}"
                except Exception:
                    amt = t.get("value", "—")
                tx_rows.append({
                    "交易哈希":      t.get("hash", ""),
                    "時間 (UTC+8)": _fmt_ts(t.get("timeStamp", 0)),
                    "發送方":        t.get("from", ""),
                    "接收方":        t.get("to",   ""),
                    "代幣":          "ETH",
                    "數量":          amt,
                })
            for t in raw_erc20:
                try:
                    dec = int(t.get("tokenDecimal", 18) or 18)
                    amt = f"{int(t.get('value', 0)) / (10 ** dec):.6f}"
                except Exception:
                    amt = t.get("value", "—")
                token_rows.append({
                    "交易哈希":      t.get("hash", ""),
                    "時間 (UTC+8)": _fmt_ts(t.get("timeStamp", 0)),
                    "發送方":        t.get("from", ""),
                    "接收方":        t.get("to",   ""),
                    "代幣":          t.get("tokenSymbol", "—"),
                    "數量":          amt,
                })

        elif chain == "TRX":
            for t in raw_txs:
                try:
                    sun = t.get("amount", 0) or t.get("contractData", {}).get("amount", 0)
                    amt = f"{int(sun) / 1_000_000:.6f}"
                except Exception:
                    amt = "—"
                tx_rows.append({
                    "交易哈希":      t.get("hash", ""),
                    "時間 (UTC+8)": _fmt_ts(t.get("timestamp", 0), ms=True),
                    "發送方":        t.get("ownerAddress", ""),
                    "接收方":        t.get("toAddress",    ""),
                    "代幣":          "TRX",
                    "數量":          amt,
                })
            for t in raw_trc20:
                ti = t.get("tokenInfo") or {}
                try:
                    dec = int(ti.get("tokenDecimal", 6) or 6)
                    amt = f"{int(t.get('quant', 0)) / (10 ** dec):.6f}"
                except Exception:
                    amt = "—"
                token_rows.append({
                    "交易哈希":      t.get("transaction_id", ""),
                    "時間 (UTC+8)": _fmt_ts(t.get("block_ts", 0), ms=True),
                    "發送方":        t.get("from_address", ""),
                    "接收方":        t.get("to_address",   ""),
                    "代幣":          ti.get("tokenAbbr", "—"),
                    "數量":          amt,
                })

        elif chain == "BTC":
            for t in raw_txs:
                inputs  = t.get("inputs", [])
                outputs = t.get("out",    [])
                from_addrs = list(dict.fromkeys(
                    i.get("prev_out", {}).get("addr", "")
                    for i in inputs if i.get("prev_out", {}).get("addr")
                ))
                to_addrs = list(dict.fromkeys(
                    o.get("addr", "") for o in outputs if o.get("addr")
                ))
                if len(from_addrs) == 1:
                    from_str = from_addrs[0]
                elif from_addrs:
                    from_str = f"{from_addrs[0]}…（共{len(from_addrs)}方）"
                else:
                    from_str = "—"
                if len(to_addrs) == 1:
                    to_str = to_addrs[0]
                elif to_addrs:
                    to_str = f"{to_addrs[0]}…（共{len(to_addrs)}方）"
                else:
                    to_str = "—"
                try:
                    total_sat = sum(o.get("value", 0) for o in outputs)
                    amt = f"{total_sat / 1e8:.8f}"
                except Exception:
                    amt = "—"
                tx_rows.append({
                    "交易哈希":      t.get("hash", ""),
                    "時間 (UTC+8)": _fmt_ts(t.get("time", 0)),
                    "發送方":        from_str,
                    "接收方":        to_str,
                    "代幣":          "BTC",
                    "數量":          amt,
                })

        return tx_rows, token_rows

    # ── dust / search / sort helpers ─────────────────────────────────────────

    def _dust_filter_rows(self, tx_rows: list[dict], token_rows: list[dict]):
        """套用釣魚交易過濾，回傳 (tx_rows, token_rows, filtered_count)。"""
        if not (getattr(self, "_dust_filter_var", None) and self._dust_filter_var.get()):
            return tx_rows, token_rows, 0

        def _keep(row: dict) -> bool:
            try:
                amt = float(row.get("數量", "0") or 0)
            except (ValueError, TypeError):
                return True
            token = row.get("代幣", "")
            if token == "ETH":
                threshold = 0.001
            elif token == "BTC":
                threshold = 0.00001
            else:
                threshold = 1.0
            return amt >= threshold

        orig       = len(tx_rows) + len(token_rows)
        tx_rows    = [r for r in tx_rows    if _keep(r)]
        token_rows = [r for r in token_rows if _keep(r)]
        return tx_rows, token_rows, orig - len(tx_rows) - len(token_rows)

    def _apply_search_filter(self, rows: list[dict]) -> list[dict]:
        """依搜尋列條件篩選，全空時直接回傳原 list。"""
        text    = self._search_var.get().strip().lower()
        amt_min = self._search_amt_min.get().strip()
        amt_max = self._search_amt_max.get().strip()
        t_from  = self._search_t_from.get().strip()
        t_to    = self._search_t_to.get().strip()

        if not any([text, amt_min, amt_max, t_from, t_to]):
            return rows

        result = []
        for r in rows:
            if text:
                haystack = (
                    r.get("交易哈希", "") + " " +
                    r.get("發送方",   "") + " " +
                    r.get("接收方",   "")
                ).lower()
                if text not in haystack:
                    continue
            if amt_min or amt_max:
                try:
                    amt = float(r.get("數量", "0") or 0)
                except (ValueError, TypeError):
                    amt = 0.0
                if amt_min:
                    try:
                        if amt < float(amt_min):
                            continue
                    except ValueError:
                        pass
                if amt_max:
                    try:
                        if amt > float(amt_max):
                            continue
                    except ValueError:
                        pass
            if t_from or t_to:
                ts = r.get("時間 (UTC+8)", "")
                date_str = ts[:10] if len(ts) >= 10 else ts
                if t_from and date_str < t_from:
                    continue
                if t_to and date_str > t_to:
                    continue
            result.append(r)
        return result

    def _apply_sort(self, rows: list[dict], attr: str) -> list[dict]:
        state = self._sort_state.get(attr, {})
        col   = state.get("col", "")
        asc   = state.get("asc", True)
        if not col:
            return rows

        def _key(r: dict):
            v = r.get(col, "")
            if col == "數量":
                try:
                    return float(v or 0)
                except (ValueError, TypeError):
                    return 0.0
            return str(v)

        return sorted(rows, key=_key, reverse=not asc)

    def _fill_tree(self, attr: str, rows: list[dict]):
        tree = getattr(self, attr, None)
        if tree is None:
            return
        cols = tree.cget("columns")
        for iid in tree.get_children():
            tree.delete(iid)
        for row in rows[:5000]:
            tree.insert("", "end", values=[str(row.get(c, "")) for c in cols])

    def _on_sort_click(self, col: str, attr: str):
        state = self._sort_state[attr]
        if state["col"] == col:
            state["asc"] = not state["asc"]
        else:
            state["col"] = col
            state["asc"] = True
        self._update_sort_headings(attr)
        self._refresh_tx_display()

    def _update_sort_headings(self, attr: str):
        tree  = getattr(self, attr, None)
        if tree is None:
            return
        state = self._sort_state.get(attr, {})
        active_col = state.get("col", "")
        asc        = state.get("asc", True)
        labels = {
            "時間 (UTC+8)": "時間 (UTC+8)",
            "數量":         "數量",
        }
        for col, base in labels.items():
            if col == active_col:
                indicator = " ▲" if asc else " ▼"
            else:
                indicator = ""
            try:
                tree.heading(col, text=base + indicator)
            except Exception:
                pass

    def _bind_sort_headings(self, attr: str):
        tree = getattr(self, attr, None)
        if tree is None:
            return
        for col in ("時間 (UTC+8)", "數量"):
            try:
                tree.heading(col, command=lambda c=col, a=attr: self._on_sort_click(c, a))
            except Exception:
                pass
        self._update_sort_headings(attr)

    def _refresh_tx_display(self):
        tx_rows    = list(self._tx_rows_base)
        token_rows = list(self._token_rows_base)

        # 釣魚過濾
        tx_rows, token_rows, dust_cnt = self._dust_filter_rows(tx_rows, token_rows)
        if hasattr(self, "_dust_count_lbl"):
            if getattr(self, "_dust_filter_var", None) and self._dust_filter_var.get():
                self._dust_count_lbl.configure(
                    text=f"已過濾 {dust_cnt} 筆釣魚交易" if dust_cnt else "未發現釣魚交易")
            else:
                self._dust_count_lbl.configure(text="")

        # 搜尋過濾
        tx_rows_f    = self._apply_search_filter(tx_rows)
        token_rows_f = self._apply_search_filter(token_rows)

        # 排序
        tx_rows_f    = self._apply_sort(tx_rows_f,    "_tx_tree")
        token_rows_f = self._apply_sort(token_rows_f, "_token_tree")

        # 填入 treeview
        self._fill_tree("_tx_tree",    tx_rows_f)
        self._fill_tree("_token_tree", token_rows_f)

        # 搜尋結果標籤
        if hasattr(self, "_search_result_lbl"):
            total_base = len(tx_rows) + len(token_rows)
            total_show = len(tx_rows_f) + len(token_rows_f)
            has_filter = any([
                self._search_var.get().strip(),
                self._search_amt_min.get().strip(),
                self._search_amt_max.get().strip(),
                self._search_t_from.get().strip(),
                self._search_t_to.get().strip(),
            ])
            if has_filter:
                self._search_result_lbl.configure(
                    text=f"顯示 {total_show} / {total_base} 筆")
            else:
                self._search_result_lbl.configure(text="")

    def _clear_tx_search(self):
        self._search_var.set("")
        self._search_amt_min.set("")
        self._search_amt_max.set("")
        self._search_t_from.set("")
        self._search_t_to.set("")
        if hasattr(self, "_search_result_lbl"):
            self._search_result_lbl.configure(text="")
        self._refresh_tx_display()

    def _rebuild_tree(self, attr: str, rows: list[dict]):
        old_tree = getattr(self, attr)
        if not rows:
            return
        parent = old_tree.master
        for w in parent.winfo_children():
            w.destroy()
        keys     = list(rows[0].keys())
        new_tree = self._make_treeview(
            parent, tuple(keys),
            add_addr_menu=(attr in ("_tx_tree", "_token_tree")),
        )
        setattr(self, attr, new_tree)
        for row in rows[:5000]:
            vals = []
            for k in keys:
                v = row.get(k, "")
                if isinstance(v, (dict, list)):
                    v = str(v)[:80]
                vals.append(v)
            new_tree.insert("", "end", values=vals)
        if attr in ("_tx_tree", "_token_tree"):
            self._bind_sort_headings(attr)

    # ═══════════════════════════════════════════════════════════════════════════
    # 幣流圖
    # ═══════════════════════════════════════════════════════════════════════════

    def _add_to_flow_graph(self):
        """地址側寫 → 加入單一節點（不加邊）。"""
        if not self._profile:
            messagebox.showinfo("尚無資料", "請先執行分析。")
            return
        panel: FlowGraphPanel = self._flow_panel
        address = self._profile.get("address", "")
        chain   = self._profile.get("chain", "ETH")
        panel.add_address_node(address, chain)
        if self._query_mode.get() == "專案查詢" and self._active_case:
            panel.set_case_id(self._active_case["id"])
            panel._gen_mode.set("evidence")
        else:
            panel._gen_mode.set("explore")
        panel._update_mode_label()
        self._show_step(4)

    def _add_hash_to_flow_graph(self):
        """Hash 分析 → 加入發送方→接收方的交易邊。"""
        if not getattr(self, "_last_hash_result", None):
            messagebox.showinfo("尚無資料", "請先執行交易 Hash 查詢。")
            return
        panel: FlowGraphPanel = self._flow_panel
        panel.add_hash_edge(self._last_hash_result)
        if self._query_mode.get() == "專案查詢" and self._active_case:
            panel.set_case_id(self._active_case["id"])
            panel._gen_mode.set("evidence")
        else:
            panel._gen_mode.set("explore")
        panel._update_mode_label()
        self._show_step(4)

    def _on_flow_node_clicked(self, address: str, chain: str):
        self.addr_entry.delete(0, "end")
        self.addr_entry.insert(0, address)
        self.chain_var.set(chain)
        self._show_case_data_tab("🔍  地址側寫")
        self._start_smart_query()

    # ═══════════════════════════════════════════════════════════════════════════
    # 匯出
    # ═══════════════════════════════════════════════════════════════════════════

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

    # ═══════════════════════════════════════════════════════════════════════════
    # 時間篩選
    # ═══════════════════════════════════════════════════════════════════════════

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
        s_str = self._time_start.get().strip()
        e_str = self._time_end.get().strip()
        if not s_str:
            return None
        s_ts = parse_datetime_str(s_str)
        if not s_ts:
            messagebox.showerror("時間格式錯誤",
                                 f"起始時間格式錯誤：{s_str}\n正確格式：YYYY-MM-DD HH:MM:SS")
            return None
        e_ts = parse_datetime_str(e_str) if e_str else None
        if e_str and not e_ts:
            messagebox.showerror("時間格式錯誤",
                                 f"迄止時間格式錯誤：{e_str}\n正確格式：YYYY-MM-DD HH:MM:SS")
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
            return {"mode": "range", "start_ts": s_ts, "end_ts": e_ts, "each_side": each}
        return {"mode": "center", "start_ts": s_ts, "end_ts": None, "each_side": each}

    def _clear_time_filter(self):
        self._time_start.delete(0, "end")
        self._time_end.delete(0, "end")
        self._time_each.delete(0, "end")
        self._time_each.insert(0, "50")
        self._time_mode_lbl.configure(text="時間篩選已清除", text_color="gray60")
