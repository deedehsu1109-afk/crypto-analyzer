"""
CloudFail 整合面板 — 網站來源 IP 溯源

此模組為 Step 4「案件資料」分頁中「🌐 網站溯源」標籤的 UI 元件。
掃描在背景執行緒執行，結果可直接加入涉案地址或儲存至案件。
"""
from __future__ import annotations

import json
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional

import customtkinter as ctk


class CloudFailPanel(ctk.CTkFrame):
    """Cloudflare 源頭 IP 溯源面板。"""

    def __init__(
        self,
        master,
        get_case_id: Callable[[], Optional[int]],
        get_case_name: Callable[[], str],
        on_add_address: Callable[[Dict], None],
        **kwargs,
    ):
        """
        參數：
            get_case_id    — 回呼函式，回傳目前開啟案件的 ID（無案件時 None）
            get_case_name  — 回呼函式，回傳案件名稱（用於提示訊息）
            on_add_address — 回呼函式，傳入 addr_data dict 以新增涉案地址
        """
        super().__init__(master, **kwargs)
        self._get_case_id = get_case_id
        self._get_case_name = get_case_name
        self._on_add_address = on_add_address
        self._scan_thread: Optional[threading.Thread] = None
        self._scan_result: Optional[Dict] = None
        self._running = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)  # 進度區自動伸縮
        self.rowconfigure(3, weight=2)  # 結果區佔更多空間

        self._build_input_area()
        self._build_notice()
        self._build_progress_area()
        self._build_results_area()
        self._build_action_bar()

    # ── 輸入區 ────────────────────────────────────────────────────────────────

    def _build_input_area(self) -> None:
        frame = ctk.CTkFrame(self, corner_radius=6)
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="目標網域：", font=("標楷體", 14)).grid(
            row=0, column=0, padx=(12, 6), pady=10, sticky="w"
        )
        self._domain_var = tk.StringVar()
        self._domain_entry = ctk.CTkEntry(
            frame, textvariable=self._domain_var,
            placeholder_text="例：example.com（不含 http://）",
            font=("Consolas", 13), height=34,
        )
        self._domain_entry.grid(row=0, column=1, padx=4, pady=10, sticky="ew")
        self._domain_entry.bind("<Return>", lambda _: self._start_scan())

        self._scan_btn = ctk.CTkButton(
            frame, text="▶  開始溯源", width=120, height=34,
            font=("標楷體", 13, "bold"), command=self._start_scan,
        )
        self._scan_btn.grid(row=0, column=2, padx=(4, 6), pady=10)

        self._stop_btn = ctk.CTkButton(
            frame, text="■  停止", width=80, height=34,
            fg_color="#c0392b", hover_color="#a93226",
            font=("標楷體", 13), command=self._stop_scan,
            state="disabled",
        )
        self._stop_btn.grid(row=0, column=3, padx=(0, 12), pady=10)

        # 選項列
        opt = ctk.CTkFrame(frame, fg_color="transparent")
        opt.grid(row=1, column=0, columnspan=4, padx=12, pady=(0, 8), sticky="w")

        self._passive_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            opt, text="僅被動偵察（不進行 DNS 解析）",
            variable=self._passive_var, font=("標楷體", 12),
        ).pack(side="left", padx=(0, 20))

        ctk.CTkLabel(opt, text="執行緒：", font=("標楷體", 12)).pack(side="left")
        self._threads_var = tk.StringVar(value="10")
        ctk.CTkEntry(
            opt, textvariable=self._threads_var, width=56,
            font=("Consolas", 12),
        ).pack(side="left", padx=(4, 0))

    # ── 授權聲明 ──────────────────────────────────────────────────────────────

    def _build_notice(self) -> None:
        notice = ctk.CTkLabel(
            self,
            text=(
                "⚠  本功能僅供合法授權之司法鑑識調查使用。"
                "對任何網域執行溯源前，須確保已取得合法授權。"
            ),
            font=("標楷體", 12),
            text_color="#e67e22",
            wraplength=900,
            justify="left",
        )
        notice.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 4))

    # ── 進度日誌 ──────────────────────────────────────────────────────────────

    def _build_progress_area(self) -> None:
        frame = ctk.CTkFrame(self, corner_radius=6)
        frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="掃描進度", font=("標楷體", 13, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(6, 2)
        )
        self._log_box = ctk.CTkTextbox(
            frame, height=130, font=("Consolas", 12),
            wrap="word", state="disabled",
        )
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))

    # ── 結果表格區 ────────────────────────────────────────────────────────────

    def _build_results_area(self) -> None:
        frame = ctk.CTkFrame(self, corner_radius=6)
        frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

        # — 非 CF IP 表格 ——————————————————————————————————————
        left = ctk.CTkFrame(frame, fg_color="transparent")
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(8, 4), pady=8)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        ctk.CTkLabel(
            left, text="■  非 Cloudflare IP（潛在來源主機）",
            font=("標楷體", 13, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        cols_ip = ("ip", "asn", "isp", "confidence")
        self._ip_tree = ttk.Treeview(
            left, columns=cols_ip, show="headings", height=8,
            selectmode="browse",
        )
        self._ip_tree.heading("ip",         text="IP 位址")
        self._ip_tree.heading("asn",        text="ASN 資訊")
        self._ip_tree.heading("isp",        text="IP 業者")
        self._ip_tree.heading("confidence", text="信心度 (%)")
        self._ip_tree.column("ip",         width=140, anchor="w")
        self._ip_tree.column("asn",        width=180, anchor="w")
        self._ip_tree.column("isp",        width=200, anchor="w")
        self._ip_tree.column("confidence", width=90,  anchor="center")
        self._ip_tree.grid(row=1, column=0, sticky="nsew")

        ip_sb = ttk.Scrollbar(left, orient="vertical", command=self._ip_tree.yview)
        self._ip_tree.configure(yscrollcommand=ip_sb.set)
        ip_sb.grid(row=1, column=1, sticky="ns")
        left.columnconfigure(1, minsize=0)

        self._ip_tree.bind("<<TreeviewSelect>>", self._on_ip_select)

        # — 子網域命中表格 ——————————————————————————————————————
        right = ctk.CTkFrame(frame, fg_color="transparent")
        right.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(4, 8), pady=8)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        ctk.CTkLabel(
            right, text="■  域名解析（子網域 + 頁面參照）",
            font=("標楷體", 13, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        cols_sub = ("host", "ip", "source")
        self._sub_tree = ttk.Treeview(
            right, columns=cols_sub, show="headings", height=8,
            selectmode="browse",
        )
        self._sub_tree.heading("host",   text="域名")
        self._sub_tree.heading("ip",     text="解析 IP")
        self._sub_tree.heading("source", text="來源")
        self._sub_tree.column("host",   width=210, anchor="w")
        self._sub_tree.column("ip",     width=140, anchor="w")
        self._sub_tree.column("source", width=70,  anchor="center")
        self._sub_tree.grid(row=1, column=0, sticky="nsew")

        sub_sb = ttk.Scrollbar(right, orient="vertical", command=self._sub_tree.yview)
        self._sub_tree.configure(yscrollcommand=sub_sb.set)
        sub_sb.grid(row=1, column=1, sticky="ns")
        right.columnconfigure(1, minsize=0)

        self._sub_tree.bind("<<TreeviewSelect>>", self._on_sub_select)

    # ── 操作列 ────────────────────────────────────────────────────────────────

    def _build_action_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 10))

        self._add_selected_btn = ctk.CTkButton(
            bar, text="＋  加入選取 IP 至涉案地址",
            font=("標楷體", 13), width=200, state="disabled",
            command=self._add_selected_to_case,
        )
        self._add_selected_btn.pack(side="left", padx=(0, 8))

        self._add_all_btn = ctk.CTkButton(
            bar, text="＋  加入全部非 CF IP",
            font=("標楷體", 13), width=160, state="disabled",
            fg_color="#1a7a3c", hover_color="#145e2e",
            command=self._add_all_to_case,
        )
        self._add_all_btn.pack(side="left", padx=(0, 8))

        self._export_btn = ctk.CTkButton(
            bar, text="匯出 JSON",
            font=("標楷體", 13), width=100, state="disabled",
            fg_color="#555", hover_color="#444",
            command=self._export_json,
        )
        self._export_btn.pack(side="left", padx=(0, 8))

        self._pdf_btn = ctk.CTkButton(
            bar, text="📄  匯出 PDF 報告",
            font=("標楷體", 13), width=140, state="disabled",
            fg_color="#1a4a8c", hover_color="#133570",
            command=self._export_pdf,
        )
        self._pdf_btn.pack(side="left")

        self._status_lbl = ctk.CTkLabel(
            bar, text="", font=("標楷體", 12), text_color="#888"
        )
        self._status_lbl.pack(side="right", padx=4)

    # ── 日誌工具 ──────────────────────────────────────────────────────────────

    def _log(self, phase: str, msg: str) -> None:
        """在進度日誌中追加一行，必須在主執行緒呼叫（或透過 after）。"""
        tag = {
            "Phase1": "[初始化]", "Phase2": "[偵察]",
            "Phase3": "[解析]",   "Phase4": "[頁面]",
            "Phase5": "[分析]",   "Phase6": "[SSL]",
        }.get(phase, f"[{phase}]")
        line = f"{tag} {msg}\n"
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _log_from_thread(self, phase: str, msg: str) -> None:
        """從背景執行緒安全地更新日誌（透過 after 排程）。"""
        self.after(0, self._log, phase, msg)

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ── 掃描控制 ──────────────────────────────────────────────────────────────

    def _start_scan(self) -> None:
        domain = self._domain_var.get().strip().lower()
        if not domain:
            messagebox.showwarning("輸入錯誤", "請輸入目標網域。")
            return
        # 去除 http(s):// 前綴
        for prefix in ("https://", "http://"):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
        domain = domain.rstrip("/")

        try:
            threads = int(self._threads_var.get())
            if threads < 1 or threads > 50:
                raise ValueError
        except ValueError:
            messagebox.showwarning("輸入錯誤", "執行緒數須為 1～50 的整數。")
            return

        passive_only = self._passive_var.get()

        self._clear_log()
        self._clear_tables()
        self._scan_result = None
        self._running = True
        self._scan_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._add_selected_btn.configure(state="disabled")
        self._add_all_btn.configure(state="disabled")
        self._export_btn.configure(state="disabled")
        self._pdf_btn.configure(state="disabled")
        self._status_lbl.configure(text="掃描進行中…")

        self._log("Phase1", f"目標：{domain}｜{"僅被動" if passive_only else "完整"}模式｜執行緒：{threads}")

        self._scan_thread = threading.Thread(
            target=self._run_scan,
            args=(domain, passive_only, threads),
            daemon=True,
        )
        self._scan_thread.start()

    def _run_scan(self, domain: str, passive_only: bool, threads: int) -> None:
        """在背景執行緒中執行掃描。"""
        from api.website_tracer import scan_domain
        try:
            result = scan_domain(
                domain,
                passive_only=passive_only,
                threads=threads,
                progress=self._log_from_thread,
            )
        except Exception as exc:
            result = {"error": str(exc)}
        self.after(0, self._on_scan_done, result)

    def _stop_scan(self) -> None:
        self._running = False
        self._scan_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._status_lbl.configure(text="已停止")

    def _on_scan_done(self, result: Dict) -> None:
        self._running = False
        self._scan_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")

        if "error" in result:
            self._log("Phase1", f"❌ 錯誤：{result['error']}")
            self._status_lbl.configure(text="掃描失敗")
            return

        self._scan_result = result
        self._populate_tables(result)

        non_cf_count = len(result.get("non_cf_ips", []))
        cms = result.get("cms_detected", "")
        cms_tag = f"｜CMS：{cms}" if cms else ""
        self._status_lbl.configure(
            text=f"完成｜找到 {non_cf_count} 個非 CF IP{cms_tag}"
        )
        if result.get("non_cf_ips"):
            self._add_all_btn.configure(state="normal")
        self._export_btn.configure(state="normal")
        self._pdf_btn.configure(state="normal")

    # ── 表格填充 ──────────────────────────────────────────────────────────────

    def _clear_tables(self) -> None:
        for item in self._ip_tree.get_children():
            self._ip_tree.delete(item)
        for item in self._sub_tree.get_children():
            self._sub_tree.delete(item)

    def _populate_tables(self, result: Dict) -> None:
        self._clear_tables()

        for entry in result.get("non_cf_ips", []):
            ssl_ok = entry.get("ssl_confirmed")
            conf = entry.get("confidence", "")
            if ssl_ok is True:
                conf_display = f"{conf} ✔SSL"
            elif ssl_ok is False:
                conf_display = str(conf)
            else:
                conf_display = str(conf)
            self._ip_tree.insert(
                "", "end",
                iid=entry["ip"],
                values=(entry["ip"], entry.get("asn", ""), entry.get("isp", ""), conf_display),
            )

        # 子網域（被動 DNS / CT 解析）
        for hit in result.get("subdomain_hits", []):
            if not hit.get("behind_cloudflare"):
                self._sub_tree.insert(
                    "", "end",
                    values=(hit["host"], hit["ip"], "子網域"),
                )

        # 頁面參照（主動抓取）
        for hit in result.get("page_hits", []):
            if not hit.get("behind_cloudflare"):
                self._sub_tree.insert(
                    "", "end",
                    values=(hit["host"], hit["ip"], "頁面"),
                )

    # ── 選取事件 ──────────────────────────────────────────────────────────────

    def _on_ip_select(self, _event=None) -> None:
        sel = self._ip_tree.selection()
        self._add_selected_btn.configure(
            state="normal" if sel else "disabled"
        )

    def _on_sub_select(self, _event=None) -> None:
        sel = self._sub_tree.selection()
        self._add_selected_btn.configure(
            state="normal" if sel else "disabled"
        )

    # ── 加入涉案地址 ──────────────────────────────────────────────────────────

    def _make_addr_data(self, ip: str, label_suffix: str = "") -> Dict:
        case_id = self._get_case_id()
        if not self._scan_result:
            return {}
        target = self._scan_result.get("target", "")
        asn = ""
        isp = ""
        for entry in self._scan_result.get("non_cf_ips", []):
            if entry["ip"] == ip:
                asn = entry.get("asn", "")
                isp = entry.get("isp", "")
                break
        notes = f"由網站溯源功能發現（目標網域：{target}）"
        if asn:
            notes += f"；ASN：{asn}"
        return {
            "case_id":          case_id,
            "addr_type":        "主機IP",
            "chain_institution": isp or asn or "未知",
            "address":          ip,
            "holder_role":      "不明",
            "label":            f"{target}{label_suffix}",
            "source_doc":       "",
            "notes":            notes,
        }

    def _add_selected_to_case(self) -> None:
        case_id = self._get_case_id()
        if not case_id:
            messagebox.showwarning("未選擇案件", "請先在左側選擇一個案件。")
            return

        # 優先從 IP 表格取選取
        ip_sel = self._ip_tree.selection()
        sub_sel = self._sub_tree.selection()

        if ip_sel:
            ip = self._ip_tree.item(ip_sel[0])["values"][0]
            data = self._make_addr_data(str(ip))
        elif sub_sel:
            vals = self._sub_tree.item(sub_sel[0])["values"]
            host = str(vals[0])
            ip   = str(vals[1])
            data = self._make_addr_data(ip, f" / {host}")
        else:
            return

        if not data:
            return

        self._on_add_address(data)
        self._log("Phase4", f"✔ 已加入涉案地址：{ip}")

    def _add_all_to_case(self) -> None:
        case_id = self._get_case_id()
        if not case_id:
            messagebox.showwarning("未選擇案件", "請先在左側選擇一個案件。")
            return

        ips = self._scan_result.get("non_cf_ips", []) if self._scan_result else []
        if not ips:
            return

        if not messagebox.askyesno(
            "確認", f"確定要將全部 {len(ips)} 個非 CF IP 加入案件「{self._get_case_name()}」？"
        ):
            return

        for entry in ips:
            data = self._make_addr_data(entry["ip"])
            if data:
                self._on_add_address(data)

        self._log("Phase4", f"✔ 已加入 {len(ips)} 個非 CF IP 至案件地址")

    # ── 匯出 ──────────────────────────────────────────────────────────────────

    def _export_json(self) -> None:
        if not self._scan_result:
            return
        from tkinter import filedialog
        target = self._scan_result.get("target", "scan")
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"webscan_{target}.json",
            title="匯出掃描結果",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._scan_result, f, ensure_ascii=False, indent=2)
        self._log("Phase4", f"✔ 已匯出至：{path}")

    def _export_pdf(self) -> None:
        if not self._scan_result:
            return
        from tkinter import filedialog
        import threading
        target = self._scan_result.get("target", "scan")
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"webscan_{target}.pdf",
            title="匯出 PDF 鑑識報告",
        )
        if not path:
            return

        self._pdf_btn.configure(state="disabled", text="產生中…")
        self._log("Phase4", "正在產生 PDF 報告，請稍候…")

        result = self._scan_result
        case_name = self._get_case_name()

        def _gen():
            try:
                from api.pdf_report import generate_website_scan_pdf
                generate_website_scan_pdf(result, case_name, path)
                self.after(0, self._on_pdf_done, path, None)
            except Exception as exc:
                self.after(0, self._on_pdf_done, path, exc)

        threading.Thread(target=_gen, daemon=True).start()

    def _on_pdf_done(self, path: str, error) -> None:
        self._pdf_btn.configure(state="normal", text="📄  匯出 PDF 報告")
        if error:
            self._log("Phase4", f"❌ PDF 產生失敗：{error}")
            messagebox.showerror("PDF 錯誤", f"報告產生失敗：\n{error}")
        else:
            self._log("Phase4", f"✔ PDF 報告已產生：{path}")
            if messagebox.askyesno("完成", f"PDF 報告已儲存至：\n{path}\n\n是否立即開啟？"):
                import os, subprocess
                try:
                    os.startfile(path)
                except Exception:
                    subprocess.Popen(["explorer", path])
