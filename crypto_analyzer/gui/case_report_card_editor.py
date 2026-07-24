"""
case_report_card_editor.py
案件分析報告卡片編排介面：把各類查詢紀錄轉成的卡片，讓使用者自由選取、
排序，並在明確按下「編輯」後修改文字內容，需再按「確認修正」才會真正
套用，避免搬移/排序其他卡片時不小心誤動或遺失尚未確認的文字。
"""
from __future__ import annotations
from tkinter import messagebox
import customtkinter as ctk

_SOURCE_LABELS = {
    "transcript":   ("筆錄內容", "#fb923c"),
    "case_summary": ("案件摘要", "#facc15"),
    "wallet":       ("錢包分析", "#60a5fa"),
    "tx_lookup":    ("Hash查詢", "#34d399"),
    "case_address": ("涉案地址", "#fcd34d"),
    "stated_tx":    ("陳述交易", "#f472b6"),
    "domain_scan":  ("網站溯源", "#a78bfa"),
    "image":        ("圖片卡片", "#38bdf8"),
}

_THUMB_MAX_SIZE = (300, 200)


class CardReportEditor(ctk.CTkToplevel):
    """
    on_confirm(ordered_cards) 會在使用者按下「確認編排，產製 DOCX」時呼叫，
    ordered_cards 為 [{"title": str, "text": str, "kind": str, "image_path": str|None}, ...]
    （已套用所有已確認的文字修改；kind=="image" 的卡片才會有 image_path）。
    """

    def __init__(self, parent, case_info: dict, available_cards: list[dict], on_confirm):
        super().__init__(parent)
        self.case_info = case_info
        self.on_confirm = on_confirm
        self._available: list[dict] = list(available_cards)
        self._arranged:  list[dict] = []
        self._widgets: dict[str, ctk.CTkTextbox] = {}
        self._editing:  set[str] = set()   # 目前處於「編輯中」的卡片 id
        self._drafts:   dict[str, str] = {}  # 編輯中、尚未確認的暫存文字（重繪畫面時保留用）
        self._thumb_cache: dict[str, ctk.CTkImage] = {}  # 圖片卡片縮圖快取

        self.title("📋  案件分析報告 — 卡片編排")
        self.geometry("1320x820")
        self.configure(fg_color="#0f1520")
        self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        self.lift()
        self.focus_force()
        self.after(100, self._safe_grab)

        self._build_ui()
        self._render_available()
        self._render_arranged()

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            self.after(50, self._safe_grab)

    # ── 版面 ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="#0a0f1a", corner_radius=0, height=56)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)
        ctk.CTkLabel(
            header,
            text=f"📋 {self.case_info.get('case_number', '')} "
                 f"{self.case_info.get('case_name', '')}",
            font=("Microsoft JhengHei", 13, "bold"),
            text_color="#a78bfa").pack(side="left", padx=16, pady=14)
        ctk.CTkLabel(
            header,
            text="卡片預設唯讀；按「✎ 編輯」才能修改文字，改完要按「✔ 確認修正」才會套用",
            font=("Microsoft JhengHei", 10), text_color="gray50").pack(side="left", padx=8)

        # 左：可用卡片
        left = ctk.CTkFrame(self, corner_radius=10, fg_color="#12192a")
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 4), pady=8)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text="可用卡片（查詢紀錄）",
                     font=("Microsoft JhengHei", 12, "bold"),
                     text_color="#94a3b8").grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")
        self._avail_frame = ctk.CTkScrollableFrame(left, fg_color="#12192a", corner_radius=0)
        self._avail_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 8))

        # 右：已編排卡片
        right = ctk.CTkFrame(self, corner_radius=10, fg_color="#12192a")
        right.grid(row=1, column=1, sticky="nsew", padx=(4, 10), pady=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        top_r = ctk.CTkFrame(right, fg_color="transparent")
        top_r.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top_r.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top_r, text="已編排卡片（報告內容，由上到下依序輸出）",
                     font=("Microsoft JhengHei", 12, "bold"),
                     text_color="#a78bfa").grid(row=0, column=0, sticky="w")
        self._count_lbl = ctk.CTkLabel(top_r, text="已編排 0 張卡片",
                                       font=("Microsoft JhengHei", 10), text_color="gray50")
        self._count_lbl.grid(row=0, column=1, sticky="e")
        self._arr_frame = ctk.CTkScrollableFrame(right, fg_color="#12192a", corner_radius=0)
        self._arr_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 8))

        bottom = ctk.CTkFrame(self, fg_color="#0a0f1a", corner_radius=0, height=56)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew")
        bottom.grid_propagate(False)
        ctk.CTkButton(bottom, text="取消", width=100, fg_color="gray30",
                      font=("Microsoft JhengHei", 12),
                      command=self.destroy).pack(side="right", padx=(4, 16), pady=10)
        ctk.CTkButton(bottom, text="✔ 確認編排，產製 DOCX", width=200, height=38,
                      font=("Microsoft JhengHei", 12, "bold"),
                      fg_color="#4c1d95", hover_color="#3b1a7a",
                      command=self._confirm).pack(side="right", padx=4, pady=10)

    # ── 卡片渲染 ─────────────────────────────────────────────────────────────

    def _render_available(self):
        self._capture_drafts()
        for w in self._avail_frame.winfo_children():
            w.destroy()
        if not self._available:
            ctk.CTkLabel(self._avail_frame, text="（沒有更多可用卡片）",
                         font=("Microsoft JhengHei", 11), text_color="gray50").pack(pady=20)
            return
        for card in self._available:
            self._build_card_widget(self._avail_frame, card, arranged=False)

    def _render_arranged(self):
        self._capture_drafts()
        for w in self._arr_frame.winfo_children():
            w.destroy()
        self._count_lbl.configure(text=f"已編排 {len(self._arranged)} 張卡片")
        if not self._arranged:
            ctk.CTkLabel(self._arr_frame, text="（尚未加入任何卡片，報告內容會是空的）",
                         font=("Microsoft JhengHei", 11), text_color="#f5a623").pack(pady=20)
            return
        for idx, card in enumerate(self._arranged):
            self._build_card_widget(self._arr_frame, card, arranged=True, index=idx)

    def _build_card_widget(self, parent, card: dict, arranged: bool, index: int = -1):
        editing = card["id"] in self._editing

        frame = ctk.CTkFrame(parent, corner_radius=8,
                             fg_color="#20263a" if editing else "#1a2035",
                             border_width=1 if editing else 0,
                             border_color="#7c5cff")
        frame.pack(fill="x", padx=6, pady=5)

        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))

        label_text, color = _SOURCE_LABELS.get(card["source"], (card["source"], "#94a3b8"))
        ctk.CTkLabel(top, text=label_text, font=("Microsoft JhengHei", 9, "bold"),
                     text_color=color, fg_color="#12192a", corner_radius=6,
                     width=70, height=20).pack(side="left")
        ctk.CTkLabel(top, text=card["title"], font=("Microsoft JhengHei", 11, "bold"),
                     text_color="#e2e8f0", anchor="w").pack(
            side="left", padx=(8, 0), fill="x", expand=True)

        btns = ctk.CTkFrame(top, fg_color="transparent")
        btns.pack(side="right")

        if editing:
            ctk.CTkButton(btns, text="✔ 確認修正", width=88, height=24,
                          font=("Microsoft JhengHei", 10, "bold"), fg_color="#1e4620",
                          command=lambda c=card: self._confirm_edit(c)).pack(side="left", padx=2)
            ctk.CTkButton(btns, text="✕ 取消", width=60, height=24,
                          font=("Microsoft JhengHei", 10), fg_color="gray30",
                          command=lambda c=card: self._cancel_edit(c)).pack(side="left", padx=2)
        else:
            ctk.CTkButton(btns, text="✎ 編輯", width=60, height=24,
                          font=("Microsoft JhengHei", 10), fg_color="#2a3556",
                          command=lambda c=card: self._start_edit(c)).pack(side="left", padx=2)
            if arranged:
                if index > 0:
                    ctk.CTkButton(btns, text="↑", width=28, height=24,
                                  font=("Arial", 11), fg_color="#2a3556",
                                  command=lambda i=index: self._move_up(i)).pack(side="left", padx=2)
                if index < len(self._arranged) - 1:
                    ctk.CTkButton(btns, text="↓", width=28, height=24,
                                  font=("Arial", 11), fg_color="#2a3556",
                                  command=lambda i=index: self._move_down(i)).pack(side="left", padx=2)
                ctk.CTkButton(btns, text="← 移除", width=60, height=24,
                              font=("Microsoft JhengHei", 10), fg_color="#7a1f1f",
                              command=lambda c=card: self._move_to_available(c)).pack(side="left", padx=2)
            else:
                ctk.CTkButton(btns, text="加入 →", width=70, height=24,
                              font=("Microsoft JhengHei", 10), fg_color="#1e4620",
                              command=lambda c=card: self._move_to_arranged(c)).pack(side="left", padx=2)

        if card.get("kind") == "image":
            thumb = self._get_thumbnail(card.get("image_path"))
            if thumb is not None:
                ctk.CTkLabel(frame, image=thumb, text="").pack(padx=10, pady=(2, 4))
            else:
                ctk.CTkLabel(frame, text="（圖片讀取失敗，檔案可能已移動或刪除）",
                             font=("Microsoft JhengHei", 10), text_color="#f87171").pack(
                    padx=10, pady=(2, 4))

        initial_text = self._drafts.get(card["id"], card.get("text", ""))
        tb_height = 44 if card.get("kind") == "image" else 70
        tb = ctk.CTkTextbox(frame, font=("Microsoft JhengHei", 11), height=tb_height,
                            fg_color="#0d1420" if editing else "#11182a",
                            text_color="#f1f5f9" if editing else "#8b95ab",
                            corner_radius=6, wrap="word")
        tb.pack(fill="x", padx=10, pady=(2, 10))
        tb.insert("1.0", initial_text)
        if not editing:
            tb.configure(state="disabled")
        self._widgets[card["id"]] = tb

    def _get_thumbnail(self, image_path: str | None):
        if not image_path:
            return None
        if image_path in self._thumb_cache:
            return self._thumb_cache[image_path]
        try:
            from PIL import Image
            img = Image.open(image_path)
            img.thumbnail(_THUMB_MAX_SIZE)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        except Exception:
            return None
        self._thumb_cache[image_path] = ctk_img
        return ctk_img

    # ── 編輯中文字暫存（防止搬移/排序其他卡片時遺失尚未確認的編輯） ─────────

    def _capture_drafts(self):
        for card_id in list(self._editing):
            tb = self._widgets.get(card_id)
            if tb is None:
                continue
            try:
                if tb.winfo_exists():
                    self._drafts[card_id] = tb.get("1.0", "end-1c")
            except Exception:
                pass

    def _find_card(self, card_id: str) -> dict | None:
        for c in self._available:
            if c["id"] == card_id:
                return c
        for c in self._arranged:
            if c["id"] == card_id:
                return c
        return None

    # ── 編輯 / 確認修正 / 取消 ───────────────────────────────────────────────

    def _start_edit(self, card: dict):
        self._editing.add(card["id"])
        self._render_available()
        self._render_arranged()

    def _confirm_edit(self, card: dict):
        tb = self._widgets.get(card["id"])
        if tb is not None and tb.winfo_exists():
            card["text"] = tb.get("1.0", "end-1c")
        self._editing.discard(card["id"])
        self._drafts.pop(card["id"], None)
        self._render_available()
        self._render_arranged()

    def _cancel_edit(self, card: dict):
        """放棄尚未確認的修改，卡片內容還原成上次確認過的版本。"""
        self._editing.discard(card["id"])
        self._drafts.pop(card["id"], None)
        self._render_available()
        self._render_arranged()

    # ── 搬移 / 排序 ──────────────────────────────────────────────────────────

    def _move_to_arranged(self, card: dict):
        self._available = [c for c in self._available if c["id"] != card["id"]]
        self._arranged.append(card)
        self._render_available()
        self._render_arranged()

    def _move_to_available(self, card: dict):
        self._arranged = [c for c in self._arranged if c["id"] != card["id"]]
        self._available.append(card)
        self._render_available()
        self._render_arranged()

    def _move_up(self, index: int):
        if index > 0:
            self._arranged[index - 1], self._arranged[index] = \
                self._arranged[index], self._arranged[index - 1]
        self._render_arranged()

    def _move_down(self, index: int):
        if index < len(self._arranged) - 1:
            self._arranged[index + 1], self._arranged[index] = \
                self._arranged[index], self._arranged[index + 1]
        self._render_arranged()

    # ── 確認 ─────────────────────────────────────────────────────────────────

    def _confirm(self):
        if self._editing:
            messagebox.showwarning(
                "尚有卡片正在編輯",
                f"有 {len(self._editing)} 張卡片還在編輯中，請先按「✔ 確認修正」"
                "或「✕ 取消」處理完再產製報告，避免遺失或誤植尚未確認的內容。",
                parent=self)
            return
        if not self._arranged:
            if not messagebox.askyesno(
                    "確認", "尚未加入任何卡片，報告內容會是空的，仍要繼續產製嗎？",
                    parent=self):
                return
        ordered = [
            {"title": c["title"], "text": c["text"],
             "kind": c.get("kind", "text"), "image_path": c.get("image_path")}
            for c in self._arranged
        ]
        callback = self.on_confirm
        self.destroy()
        callback(ordered)
