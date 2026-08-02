"""
跨鏈交易追蹤面板 — 「🌉 跨鏈追蹤」分頁

流程（詳見 analyzer/crosschain_tracer.py 的設計說明）：
  1. 輸入目的鏈交易 Hash →「開始偵測」：用免費公開資料判斷發送方/接收方
     是否疑似為橋接資金池，給出信心等級（HIGH/MEDIUM/NONE）與具體證據。
  2. 偵測到 MEDIUM 以上信心時，畫面列出跨鏈瀏覽器清單，供調查員人工查詢
     來源鏈交易 Hash 並回填。
  3. 回填後按「獨立驗證」：工具用既有 API 重新查一次來源鏈交易，比對地址／
     時序／金額，結果供調查員參考，不代替調查員下結論。
  4. 全程步驟記錄於方法紀錄區，可連同結果一併儲存至案件。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from database import db as _db
from analyzer.crosschain_tracer import (
    detect_pool_candidate, verify_origin_claim, effective_transfer,
)
from analyzer.bridge_registry import BRIDGE_EXPLORERS
from analyzer.tx_analyzer import analyze_eth_tx, analyze_trx_tx, analyze_btc_tx
from api.etherscan import EtherscanAPI
from api.tronscan import TronScanAPI
from api.bitcoin import BitcoinAPI

_CONF_COLOR = {"HIGH": "#c0392b", "MEDIUM": "#e67e22", "NONE": "#666666"}
_CONF_TEXT = {"HIGH": "🔴 HIGH（命中橋接標籤）",
              "MEDIUM": "🟠 MEDIUM（疑似資金池，無公開標籤）",
              "NONE": "⚪ NONE（未達疑似資金池門檻）"}


def _is_tx_hash(text: str) -> bool:
    t = text.strip()
    if t.startswith("0x") and len(t) == 66:
        return True
    if len(t) == 64 and all(c in "0123456789abcdefABCDEF" for c in t):
        return True
    return False


class CrossChainTracePanel(ctk.CTkFrame):
    """跨鏈交易偵測 + 引導式人工查證 + 獨立覆核面板。"""

    def __init__(
        self,
        master,
        get_case_id: Callable[[], Optional[int]],
        get_case_name: Callable[[], str],
        get_api_keys: Callable[[], Dict],
        get_last_hash_result: Callable[[], Optional[Dict]],
        **kwargs,
    ):
        """
        參數：
            get_case_id           — 回呼函式，回傳目前開啟案件的 ID（無案件時 None）
            get_case_name         — 回呼函式，回傳案件名稱
            get_api_keys          — 回呼函式，回傳 {"etherscan_api_key":.., "trongrid_api_key":..}
            get_last_hash_result  — 回呼函式，回傳「🔗 Hash 分析」分頁最近一次查詢結果（無則 None）
        """
        super().__init__(master, **kwargs)
        self._get_case_id = get_case_id
        self._get_case_name = get_case_name
        self._get_api_keys = get_api_keys
        self._get_last_hash_result = get_last_hash_result

        self._dest_result: Optional[Dict] = None
        self._dest_xfer: Optional[Dict] = None
        self._detect_result: Optional[Dict] = None
        self._verify_result: Optional[Dict] = None
        self._method_log: List[dict] = []
        self._running = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._build_input_area()
        self._build_notice()
        self._build_scroll_body()
        self._build_action_bar()
        self._build_history()

    # ── 輸入區 ────────────────────────────────────────────────────────────────

    def _build_input_area(self) -> None:
        frame = ctk.CTkFrame(self, corner_radius=6)
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="目的鏈交易 Hash：",
                     font=("Microsoft JhengHei", 13)).grid(
            row=0, column=0, padx=(12, 6), pady=10, sticky="w")
        self._hash_var = tk.StringVar()
        self._hash_entry = ctk.CTkEntry(
            frame, textvariable=self._hash_var,
            placeholder_text="輸入交易 Hash（0x... 或 64 位十六進位）",
            font=("Consolas", 12), height=32)
        self._hash_entry.grid(row=0, column=1, padx=4, pady=10, sticky="ew")
        self._hash_entry.bind("<Return>", lambda _: self._start_detect())

        ctk.CTkLabel(frame, text="鏈：", font=("Microsoft JhengHei", 13)).grid(
            row=0, column=2, padx=(8, 2), pady=10)
        self._chain_var = tk.StringVar(value="TRX")
        ctk.CTkOptionMenu(frame, variable=self._chain_var,
                          values=["ETH", "TRX", "BTC"], width=80).grid(
            row=0, column=3, padx=2, pady=10)

        ctk.CTkButton(frame, text="帶入本次 Hash", width=110,
                      fg_color="#4a2d6a",
                      command=self._fill_from_last_hash).grid(
            row=0, column=4, padx=(4, 4), pady=10)

        self._detect_btn = ctk.CTkButton(
            frame, text="▶  開始偵測", width=110, height=32,
            font=("Microsoft JhengHei", 13, "bold"),
            command=self._start_detect)
        self._detect_btn.grid(row=0, column=5, padx=(4, 12), pady=10)

    def _build_notice(self) -> None:
        notice = ctk.CTkLabel(
            self,
            text=("⚠ 本功能只用免費公開資料，偵測結果為「信心等級」而非確定結論；"
                  "找不到公開標籤是常態，需調查員至橋接瀏覽器人工查證來源鏈交易後，"
                  "再用「獨立驗證」覆核。"),
            font=("Microsoft JhengHei", 11), text_color="#e67e22",
            wraplength=980, justify="left")
        notice.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 4))

    # ── 主體（可捲動）────────────────────────────────────────────────────────

    def _build_scroll_body(self) -> None:
        body = ctk.CTkScrollableFrame(self, corner_radius=6)
        body.grid(row=2, column=0, sticky="nsew", padx=10, pady=4)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        self._body = body

        # ── 偵測結果：發送方/接收方 卡片 ──
        self._side_frames = {}
        for col, side in enumerate(("發送方", "接收方")):
            card = ctk.CTkFrame(body, corner_radius=6)
            card.grid(row=0, column=col, sticky="nsew", padx=6, pady=6)
            card.columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=side, font=("Microsoft JhengHei", 13, "bold")).grid(
                row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 2))
            labels = {}
            for i, key in enumerate(("address", "label", "tx_count", "confidence", "matched_bridge")):
                lbl = ctk.CTkLabel(card, text="", font=("Consolas", 11), anchor="w",
                                   wraplength=380, justify="left")
                lbl.grid(row=i + 1, column=0, columnspan=2, sticky="w", padx=10, pady=1)
                labels[key] = lbl
            self._side_frames[side] = labels

        # ── 橋接瀏覽器建議清單 ──
        bridge_card = ctk.CTkFrame(body, corner_radius=6)
        bridge_card.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        bridge_card.columnconfigure(0, weight=1)
        top = ctk.CTkFrame(bridge_card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        ctk.CTkLabel(top, text="建議查詢的跨鏈瀏覽器（人工查詢來源鏈交易用）",
                     font=("Microsoft JhengHei", 13, "bold")).pack(side="left")
        ctk.CTkButton(top, text="複製本次 Hash", width=110,
                      command=self._copy_hash).pack(side="right")
        self._bridge_list_frame = ctk.CTkFrame(bridge_card, fg_color="transparent")
        self._bridge_list_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        # ── 人工回填 + 獨立驗證 ──
        verify_card = ctk.CTkFrame(body, corner_radius=6)
        verify_card.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        verify_card.columnconfigure(1, weight=1)
        ctk.CTkLabel(verify_card, text="人工查證來源鏈交易（依上方瀏覽器查得後回填）",
                     font=("Microsoft JhengHei", 13, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 4))

        ctk.CTkLabel(verify_card, text="來源鏈：").grid(row=1, column=0, padx=(10, 2), pady=6)
        self._origin_chain_var = tk.StringVar(value="ETH")
        ctk.CTkOptionMenu(verify_card, variable=self._origin_chain_var,
                          values=["ETH", "TRX", "BTC"], width=80).grid(
            row=1, column=1, padx=2, pady=6, sticky="w")

        ctk.CTkLabel(verify_card, text="來源交易 Hash：").grid(row=1, column=2, padx=(12, 2), pady=6)
        self._origin_hash_var = tk.StringVar()
        self._origin_hash_entry = ctk.CTkEntry(
            verify_card, textvariable=self._origin_hash_var,
            font=("Consolas", 12), width=420)
        self._origin_hash_entry.grid(row=1, column=3, padx=2, pady=6, sticky="ew")
        verify_card.columnconfigure(3, weight=1)

        self._verify_btn = ctk.CTkButton(
            verify_card, text="🔍  獨立驗證", width=120,
            state="disabled", command=self._start_verify)
        self._verify_btn.grid(row=1, column=4, padx=(8, 10), pady=6)

        self._verify_result_lbl = ctk.CTkLabel(
            verify_card, text="", font=("Consolas", 11), anchor="w",
            justify="left", wraplength=960)
        self._verify_result_lbl.grid(row=2, column=0, columnspan=5, sticky="w",
                                     padx=10, pady=(0, 10))

        # ── 方法紀錄 ──
        log_card = ctk.CTkFrame(body, corner_radius=6)
        log_card.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        log_card.columnconfigure(0, weight=1)
        ctk.CTkLabel(log_card, text="方法紀錄（使用工具與查詢步驟）",
                     font=("Microsoft JhengHei", 13, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        self._log_box = ctk.CTkTextbox(log_card, height=140, font=("Consolas", 11),
                                       wrap="word", state="disabled")
        self._log_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

    # ── 操作列 ────────────────────────────────────────────────────────────────

    def _build_action_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=10, pady=(4, 4))
        self._save_btn = ctk.CTkButton(
            bar, text="💾  儲存至案件", width=140, state="disabled",
            fg_color="#1a7a3c", hover_color="#145e2e",
            command=self._save_trace)
        self._save_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text="重新整理歷史", width=110,
                      command=self._reload_history).pack(side="left")
        self._status_lbl = ctk.CTkLabel(bar, text="", font=("Microsoft JhengHei", 11),
                                        text_color="#888")
        self._status_lbl.pack(side="right", padx=4)

    # ── 歷史清單 ──────────────────────────────────────────────────────────────

    def _build_history(self) -> None:
        frame = ctk.CTkFrame(self, corner_radius=6)
        frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 10))
        frame.columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text="本案件跨鏈追蹤歷史", font=("Microsoft JhengHei", 12, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(6, 2))
        cols = ("dest_chain", "dest_tx_hash", "confidence", "matched_bridge",
                "origin_chain", "origin_tx_hash", "created_at")
        self._hist_tree = ttk.Treeview(frame, columns=cols, show="headings", height=5)
        headers = {"dest_chain": "目的鏈", "dest_tx_hash": "目的 Hash",
                  "confidence": "信心", "matched_bridge": "橋名稱",
                  "origin_chain": "來源鏈", "origin_tx_hash": "來源 Hash",
                  "created_at": "建立時間"}
        widths = {"dest_chain": 60, "dest_tx_hash": 220, "confidence": 70,
                 "matched_bridge": 140, "origin_chain": 60, "origin_tx_hash": 220,
                 "created_at": 140}
        for c in cols:
            self._hist_tree.heading(c, text=headers[c])
            self._hist_tree.column(c, width=widths[c], anchor="w")
        self._hist_tree.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._reload_history()

    def _reload_history(self) -> None:
        for item in self._hist_tree.get_children():
            self._hist_tree.delete(item)
        case_id = self._get_case_id()
        rows = _db.get_crosschain_traces(case_id) if case_id else []
        for r in rows:
            self._hist_tree.insert("", "end", values=(
                r.get("dest_chain", ""), r.get("dest_tx_hash", ""),
                r.get("confidence", ""), r.get("matched_bridge", "") or "",
                r.get("origin_chain", "") or "", r.get("origin_tx_hash", "") or "",
                r.get("created_at", ""),
            ))

    # ── 日誌工具 ──────────────────────────────────────────────────────────────

    def _append_log(self, steps: List[dict]) -> None:
        self._method_log.extend(steps)
        self._log_box.configure(state="normal")
        for s in steps:
            self._log_box.insert(
                "end", f"[{s['time']}] ({s['by']}) {s['tool']} — {s['action']} → {s['result']}\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self._method_log = []
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ── 帶入本次 Hash ────────────────────────────────────────────────────────

    def _fill_from_last_hash(self) -> None:
        last = self._get_last_hash_result()
        if not last or not last.get("hash"):
            messagebox.showinfo("無資料", "「🔗 Hash 分析」分頁尚未查過任何交易。")
            return
        self._hash_var.set(last["hash"])
        self._chain_var.set(last.get("chain", "TRX"))

    def _copy_hash(self) -> None:
        h = self._hash_var.get().strip()
        if not h:
            return
        self.clipboard_clear()
        self.clipboard_append(h)
        self._status_lbl.configure(text="已複製 Hash 至剪貼簿")

    # ── 偵測 ──────────────────────────────────────────────────────────────────

    def _start_detect(self) -> None:
        tx_hash = self._hash_var.get().strip()
        if not tx_hash or not _is_tx_hash(tx_hash):
            messagebox.showerror("格式錯誤", "交易 Hash 格式錯誤。\nETH：0x 開頭，共 66 字元\nTRX / BTC：64 位十六進位字元")
            return
        chain = self._chain_var.get()
        self._clear_log()
        self._detect_btn.configure(state="disabled")
        self._verify_btn.configure(state="disabled")
        self._save_btn.configure(state="disabled")
        self._status_lbl.configure(text="偵測中…")
        self._running = True
        threading.Thread(target=self._run_detect, args=(chain, tx_hash), daemon=True).start()

    def _run_detect(self, chain: str, tx_hash: str) -> None:
        try:
            api_keys = self._get_api_keys()
            if chain == "ETH":
                api = EtherscanAPI(api_keys.get("etherscan_api_key", ""))
                dest_result = analyze_eth_tx(api.get_transaction(tx_hash))
            elif chain == "TRX":
                api = TronScanAPI(api_keys.get("trongrid_api_key", ""))
                dest_result = analyze_trx_tx(api.get_transaction(tx_hash))
            else:
                api = BitcoinAPI()
                dest_result = analyze_btc_tx(api.get_transaction(tx_hash))

            dest_xfer = effective_transfer(dest_result)
            detect_result = detect_pool_candidate(
                chain, dest_xfer["from"], dest_xfer["to"],
                api_keys.get("trongrid_api_key", ""))
            self.after(0, self._on_detect_done, chain, tx_hash, dest_result,
                      dest_xfer, detect_result, None)
        except Exception as exc:
            self.after(0, self._on_detect_done, chain, tx_hash, None, None, None, exc)

    def _on_detect_done(self, chain, tx_hash, dest_result, dest_xfer,
                        detect_result, error) -> None:
        self._running = False
        self._detect_btn.configure(state="normal")

        if error is not None:
            self._status_lbl.configure(text="偵測失敗")
            messagebox.showerror("查詢失敗", str(error))
            return

        self._dest_chain = chain
        self._dest_tx_hash = tx_hash
        self._dest_result = dest_result
        self._dest_xfer = dest_xfer
        self._detect_result = detect_result
        self._verify_result = None
        self._verify_result_lbl.configure(text="")

        self._append_log(detect_result["method_log"])
        self._populate_sides(detect_result)
        self._populate_bridge_list()

        overall = detect_result["overall_confidence"]
        self._status_lbl.configure(
            text=f"偵測完成｜整體信心：{overall}"
            + (f"｜疑似資金池側：{detect_result['pool_side']}" if detect_result['pool_side'] else ""))

        if overall != "NONE":
            self._verify_btn.configure(state="normal")
        self._save_btn.configure(state="normal")

    def _populate_sides(self, detect_result: Dict) -> None:
        for side in ("發送方", "接收方"):
            data = detect_result[side]
            labels = self._side_frames[side]
            conf = data["confidence"]
            labels["address"].configure(text=f"地址：{data['address']}")
            labels["label"].configure(text=f"公開標籤：{data['label'] or '無'}")
            labels["tx_count"].configure(text=f"交易總數：{data['tx_count']:,}")
            labels["confidence"].configure(
                text=f"信心等級：{_CONF_TEXT.get(conf, conf)}",
                text_color=_CONF_COLOR.get(conf, "#888"))
            labels["matched_bridge"].configure(
                text=f"命中橋接：{data['matched_bridge'] or '—'}")

    def _populate_bridge_list(self) -> None:
        for w in self._bridge_list_frame.winfo_children():
            w.destroy()
        for i, (name, url) in enumerate(BRIDGE_EXPLORERS.items()):
            row = ctk.CTkFrame(self._bridge_list_frame, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", pady=1)
            ctk.CTkLabel(row, text=f"• {name}", font=("Microsoft JhengHei", 11),
                        width=170, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=url, font=("Consolas", 10), text_color="#7fa8d9",
                        anchor="w").pack(side="left", padx=(4, 8))
            ctk.CTkButton(row, text="開啟", width=50, height=22,
                         command=lambda u=url: self._open_url(u)).pack(side="left")

    @staticmethod
    def _open_url(url: str) -> None:
        import webbrowser
        webbrowser.open(url)

    # ── 獨立驗證 ──────────────────────────────────────────────────────────────

    def _start_verify(self) -> None:
        origin_hash = self._origin_hash_var.get().strip()
        if not origin_hash or not _is_tx_hash(origin_hash):
            messagebox.showerror("格式錯誤", "來源交易 Hash 格式錯誤。")
            return
        if not self._dest_xfer or not self._detect_result:
            return
        origin_chain = self._origin_chain_var.get()
        self._verify_btn.configure(state="disabled")
        self._status_lbl.configure(text="獨立驗證中…")
        threading.Thread(target=self._run_verify,
                         args=(origin_chain, origin_hash), daemon=True).start()

    def _run_verify(self, origin_chain: str, origin_hash: str) -> None:
        try:
            pool_side = self._detect_result["pool_side"] or "發送方"
            pool_addr = self._dest_xfer["from"] if pool_side == "發送方" else self._dest_xfer["to"]
            api_keys = self._get_api_keys()
            result = verify_origin_claim(
                dest_chain=self._dest_chain,
                dest_pool_addr=pool_addr,
                dest_time_str=self._dest_result.get("時間", ""),
                dest_amount_str=self._dest_xfer["amount_str"],
                origin_chain=origin_chain,
                origin_tx_hash=origin_hash,
                api_keys=api_keys,
                trx_api_key=api_keys.get("trongrid_api_key", ""),
            )
            self.after(0, self._on_verify_done, origin_chain, origin_hash, result, None)
        except Exception as exc:
            self.after(0, self._on_verify_done, origin_chain, origin_hash, None, exc)

    def _on_verify_done(self, origin_chain, origin_hash, result, error) -> None:
        self._verify_btn.configure(state="normal")
        if error is not None:
            self._status_lbl.configure(text="驗證失敗")
            messagebox.showerror("驗證失敗", str(error))
            return

        self._origin_chain = origin_chain
        self._origin_tx_hash = origin_hash
        self._verify_result = result
        self._append_log(result["method_log"])

        checks = result["checks"]
        lines = [
            f"地址比對：{'✔' if checks['address_match']['pass'] else '⚠'} {checks['address_match']['detail']}",
            f"時序比對：{'✔' if checks['time_order'].get('pass') else '⚠'} {checks['time_order']['detail']}",
            f"金額比對：{checks['amount_note']['detail']}",
            "",
            f"結論（僅供參考，最終判斷請由調查員確認）：{result['verdict']}",
        ]
        self._verify_result_lbl.configure(text="\n".join(lines))
        self._status_lbl.configure(text="獨立驗證完成")

    # ── 儲存 ──────────────────────────────────────────────────────────────────

    def _save_trace(self) -> None:
        if not self._detect_result:
            return
        case_id = self._get_case_id()
        pool_side = self._detect_result["pool_side"]
        pool_addr = None
        if pool_side and self._dest_xfer:
            pool_addr = self._dest_xfer["from"] if pool_side == "發送方" else self._dest_xfer["to"]
        matched_bridge = self._detect_result[pool_side]["matched_bridge"] if pool_side else None

        investigator = None
        if case_id:
            case = _db.get_case(case_id)
            investigator = case.get("investigator") if case else None

        data = {
            "dest_chain": self._dest_chain,
            "dest_tx_hash": self._dest_tx_hash,
            "confidence": self._detect_result["overall_confidence"],
            "pool_side": pool_side,
            "pool_address": pool_addr,
            "matched_bridge": matched_bridge,
            "detection_evidence": self._detect_result,
            "origin_chain": getattr(self, "_origin_chain", None),
            "origin_tx_hash": getattr(self, "_origin_tx_hash", None),
            "origin_sender": (self._verify_result["origin_result"].get("發送方")
                             if self._verify_result else None),
            "verify_result": self._verify_result,
            "method_log": self._method_log,
            "investigator": investigator,
        }
        _db.save_crosschain_trace(data, case_id=case_id)
        hint = f"（已關聯案件：{self._get_case_name()}）" if case_id else "（未關聯案件）"
        self._status_lbl.configure(text=f"已儲存 {hint}")
        self._reload_history()
