"""
manual_dialog.py
系統內建操作手冊檢視器：讀取 docs/操作手冊.md，以輕量 Markdown 渲染顯示，
左側提供二級標題（##）目錄，點擊可跳轉至對應段落。
"""
from __future__ import annotations
import os
import re
import customtkinter as ctk

_MANUAL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "操作手冊.md",
)

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


class ManualDialog(ctk.CTkToplevel):
    """系統內建操作手冊視窗（唯讀渲染 + 目錄跳轉）。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("📖  操作手冊")
        self.geometry("980x720")
        self.configure(fg_color="#12192a")
        self.transient(parent)
        self.lift()
        self.focus_force()
        self.after(100, self.grab_set)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._toc_frame = ctk.CTkScrollableFrame(
            self, width=210, corner_radius=0, fg_color="#0d1420",
            label_text="目錄", label_font=("Microsoft JhengHei", 12, "bold"),
            label_text_color="#94a3b8")
        self._toc_frame.grid(row=0, column=0, sticky="nsw")

        self._textbox = ctk.CTkTextbox(
            self, corner_radius=0, fg_color="#12192a",
            font=("Microsoft JhengHei", 12), wrap="word")
        self._textbox.grid(row=0, column=1, sticky="nsew", padx=(1, 0))

        self._configure_tags()
        self._load_and_render()

    # ── 標籤樣式 ─────────────────────────────────────────────────────────────

    def _configure_tags(self):
        t = self._textbox._textbox  # 取得底層 tkinter.Text 元件
        t.tag_config("h1", font=("Microsoft JhengHei", 20, "bold"),
                     foreground="#60a5fa", spacing1=14, spacing3=8)
        t.tag_config("h2", font=("Microsoft JhengHei", 16, "bold"),
                     foreground="#7eb8f7", spacing1=16, spacing3=6)
        t.tag_config("h3", font=("Microsoft JhengHei", 13, "bold"),
                     foreground="#aac4ff", spacing1=10, spacing3=4)
        t.tag_config("body", font=("Microsoft JhengHei", 12),
                     foreground="#e2e8f0", spacing3=2)
        t.tag_config("bullet", font=("Microsoft JhengHei", 12),
                     foreground="#e2e8f0", lmargin1=20, lmargin2=20, spacing3=2)
        t.tag_config("quote", font=("Microsoft JhengHei", 11, "italic"),
                     foreground="#94a3b8", lmargin1=16, lmargin2=16, spacing3=2)
        t.tag_config("table", font=("Consolas", 11),
                     foreground="#cbd5e1", spacing3=1)
        t.tag_config("hr", font=("Microsoft JhengHei", 6),
                     foreground="#2a3556")
        t.tag_config("bold", font=("Microsoft JhengHei", 12, "bold"),
                     foreground="#f5c26b")
        t.tag_raise("bold")
        self._textbox.configure(state="disabled")

    # ── 讀取與渲染 ────────────────────────────────────────────────────────────

    def _load_and_render(self):
        try:
            with open(_MANUAL_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            content = f"# 無法讀取操作手冊\n\n找不到或無法開啟檔案：\n{_MANUAL_PATH}\n\n錯誤：{e}"
        self._render_markdown(content)

    def _render_markdown(self, text: str):
        t = self._textbox._textbox
        self._textbox.configure(state="normal")
        t.delete("1.0", "end")

        toc_entries: list[tuple[str, str]] = []  # (標題文字, tk index)

        for raw_line in text.splitlines():
            line = raw_line.rstrip()

            if not line.strip():
                t.insert("end", "\n")
                continue

            if line.startswith("---") and set(line.strip()) <= {"-"}:
                t.insert("end", "─" * 60 + "\n", ("hr",))
                continue

            if line.startswith("> "):
                self._insert_line(line[2:], "quote")
                continue

            if line.startswith("### "):
                self._insert_line(line[4:], "h3")
                continue

            if line.startswith("## "):
                anchor = t.index("end")
                title = line[3:]
                self._insert_line(title, "h2")
                toc_entries.append((title, anchor))
                continue

            if line.startswith("# "):
                self._insert_line(line[2:], "h1")
                continue

            if line.lstrip().startswith(("- ", "* ")):
                stripped = line.lstrip()
                self._insert_line("•  " + stripped[2:], "bullet")
                continue

            if re.match(r"^\d+\.\s", line.lstrip()):
                self._insert_line("　" + line.lstrip(), "bullet")
                continue

            if line.strip().startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if set("".join(cells)) <= {"-", ":", " ", ""}:
                    continue  # 表格分隔線（|---|---|）不渲染
                t.insert("end", "  ｜  ".join(cells) + "\n", ("table",))
                continue

            self._insert_line(line, "body")

        self._textbox.configure(state="disabled")
        t.see("1.0")
        self._build_toc(toc_entries)

    def _insert_line(self, text: str, base_tag: str):
        """插入一行文字，支援 **粗體** 行內標記。"""
        t = self._textbox._textbox
        pos = 0
        for m in _BOLD_RE.finditer(text):
            if m.start() > pos:
                t.insert("end", text[pos:m.start()], (base_tag,))
            t.insert("end", m.group(1), (base_tag, "bold"))
            pos = m.end()
        t.insert("end", text[pos:] + "\n", (base_tag,))

    # ── 目錄 ─────────────────────────────────────────────────────────────────

    def _build_toc(self, entries: list[tuple[str, str]]):
        for w in self._toc_frame.winfo_children():
            w.destroy()
        for title, idx in entries:
            ctk.CTkButton(
                self._toc_frame, text=title, anchor="w",
                font=("Microsoft JhengHei", 11),
                fg_color="transparent", hover_color="#1a2540",
                text_color="#94a3b8", height=28,
                command=lambda i=idx: self._jump_to(i),
            ).pack(fill="x", padx=4, pady=1)

    def _jump_to(self, index: str):
        self._textbox.configure(state="normal")
        self._textbox._textbox.see(index)
        self._textbox.configure(state="disabled")
