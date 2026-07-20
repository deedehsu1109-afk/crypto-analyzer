"""
網站溯源鑑識 PDF 報告產生器

依 ReportLab Platypus 架構，產生 A4 中文鑑識報告。
入口函式：generate_website_scan_pdf(result, case_name, output_path)
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── 色彩 ──────────────────────────────────────────────────────────────────────
_CN   = HexColor('#1C2340')   # 深藍（標題）
_CA   = HexColor('#1A5CAD')   # 重點藍
_CG   = HexColor('#146038')   # 確認綠
_CGB  = HexColor('#D4EFDF')   # 綠底色
_CR   = HexColor('#9B2020')   # 警示紅
_CRB  = HexColor('#F9E0E0')   # 紅底色
_CB   = HexColor('#CBD3E8')   # 邊框
_CT2  = HexColor('#4A567A')   # 次要文字
_CT3  = HexColor('#8090B0')   # 輔助文字
_CALT = HexColor('#F5F8FF')   # 交替行底色

PAGE_W, PAGE_H = A4
_M = 2.0 * cm
_UW = PAGE_W - 2 * _M   # 可用寬度 ≈ 481pt (17cm)

# ── 字型 ──────────────────────────────────────────────────────────────────────
_BODY: str | None = None
_MONO: str | None = None


def _get_fonts() -> tuple[str, str]:
    """注冊中文 + 等寬字型，回傳 (body, mono) 字型名稱。"""
    global _BODY, _MONO
    if _BODY is not None:
        return _BODY, _MONO

    registered = set(pdfmetrics.getRegisteredFontNames())
    body = 'Helvetica'
    mono = 'Courier'

    for name, path in [
        ('KaiTi',    'C:/Windows/Fonts/kaiu.ttf'),
        ('JhengHei', 'C:/Windows/Fonts/msjh.ttf'),
        ('SimHei',   'C:/Windows/Fonts/simhei.ttf'),
    ]:
        if name not in registered and os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                body = name
                break
            except Exception:
                pass

    if 'Consolas' not in registered:
        for p in ('C:/Windows/Fonts/consola.ttf',):
            if os.path.exists(p):
                try:
                    pdfmetrics.registerFont(TTFont('Consolas', p))
                    mono = 'Consolas'
                    break
                except Exception:
                    pass

    _BODY, _MONO = body, mono
    return body, mono


# ── 頁面裝飾（頁眉/頁腳）─────────────────────────────────────────────────────

def _draw_page(canvas, doc, *, target: str, case_label: str,
               body: str, mono: str) -> None:
    canvas.saveState()

    # 頁眉深藍列
    bar = 22 * mm
    canvas.setFillColor(_CN)
    canvas.rect(0, PAGE_H - bar, PAGE_W, bar, fill=1, stroke=0)

    canvas.setFillColor(white)
    canvas.setFont(body, 13)
    canvas.drawString(_M, PAGE_H - 13 * mm, '網站溯源鑑識報告')

    canvas.setFont(mono, 8.5)
    canvas.setFillColor(HexColor('#8FA0C5'))
    canvas.drawString(_M, PAGE_H - 19.5 * mm, f'TARGET  {target}')

    canvas.setFillColor(HexColor('#9ABAE8'))
    canvas.setFont(body, 8)
    canvas.drawRightString(PAGE_W - _M, PAGE_H - 13 * mm, case_label)

    canvas.setFillColor(HexColor('#E06060'))
    canvas.setFont(body, 7)
    canvas.drawRightString(PAGE_W - _M, PAGE_H - 20 * mm,
                           '⚠  司法鑑識文件 · OSINT')

    # 頁腳淺藍列
    foot = 11 * mm
    canvas.setFillColor(HexColor('#EEF2FA'))
    canvas.rect(0, 0, PAGE_W, foot, fill=1, stroke=0)

    canvas.setFillColor(_CT3)
    canvas.setFont(body, 7)
    canvas.drawString(_M, 6.5 * mm,
                      '本報告僅供合法授權之司法鑑識調查使用，未經授權不得轉閱。')
    canvas.setFont(mono, 8)
    canvas.drawRightString(PAGE_W - _M, 6.5 * mm, f'第 {doc.page} 頁')

    canvas.restoreState()


# ── 樣式工廠 ──────────────────────────────────────────────────────────────────

def _S(body: str, mono: str) -> dict:
    kw = dict(wordWrap='CJK', leading=12)
    return {
        'h2':     ParagraphStyle('h2',     fontName=body, fontSize=10,
                                 textColor=_CA, spaceBefore=7, spaceAfter=3,
                                 leading=14, wordWrap='CJK'),
        'body':   ParagraphStyle('body',   fontName=body, fontSize=8.5,
                                 textColor=_CN, **kw),
        'caption':ParagraphStyle('caption',fontName=body, fontSize=7,
                                 textColor=_CT3, leading=10, wordWrap='CJK'),
    }


# ── 表格共用設定 ──────────────────────────────────────────────────────────────

def _ts(*cmds) -> TableStyle:
    base = [
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 7),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ('BOX',           (0, 0), (-1, -1), 0.5, _CB),
        ('LINEABOVE',     (0, 1), (-1, -1), 0.3, _CB),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]
    return TableStyle(base + list(cmds))


def _hdr(body: str, *extra) -> TableStyle:
    """深藍表頭 + 交替行 + 共用設定（body 為中文字型名稱）。"""
    return _ts(
        ('BACKGROUND', (0, 0), (-1,  0), _CN),
        ('TEXTCOLOR',  (0, 0), (-1,  0), white),
        ('FONTNAME',   (0, 0), (-1, -1), body),   # 所有格預設中文字型
        ('FONTSIZE',   (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, _CALT]),
        *extra,
    )


# ── 報告內容 ──────────────────────────────────────────────────────────────────

def _build_story(result: Dict, case_name: str, now: str,
                 body: str, mono: str) -> list:
    s = _S(body, mono)
    story: list = []

    target      = result.get('target', '')
    resolved    = result.get('resolved_ip', '—')
    in_cf       = result.get('is_cloudflare', False)
    wildcard    = result.get('has_wildcard', False)
    non_cf      = result.get('non_cf_ips', [])
    sub_hits    = result.get('subdomain_hits', [])
    ct_names    = sorted(result.get('ct_names', []))
    passive     = sorted(result.get('passive_ips', []))
    page_hits   = result.get('page_hits', [])
    cms_detected = result.get('cms_detected', '')

    # ── 案件資訊列 ─────────────────────────────────────────────────────────
    story.append(Spacer(1, 3 * mm))
    info_rows = [
        ['案件名稱', case_name or '（未指定）', '調查時間', now],
        ['目標網域', target,
         'CF 保護', '是' if in_cf else '否'],
    ]
    story.append(Table(
        info_rows,
        colWidths=[2.5*cm, 6.5*cm, 2.5*cm, 5.5*cm],
        style=_ts(
            ('BACKGROUND',    (0, 0), (-1, -1), _CALT),
            ('ROWBACKGROUNDS',(0, 0), (-1, -1), [_CALT, white]),
            ('FONTNAME',  (0, 0), (-1, -1), body),
            ('FONTNAME',  (1, 1), (1, 1),   mono),   # 目標網域列用等寬；案件名稱列保持中文字型
            ('FONTNAME',  (3, 0), (3, -1), body),
            ('FONTSIZE',  (0, 0), (-1, -1), 8),
            ('TEXTCOLOR', (0, 0), (0, -1), _CT3),
            ('TEXTCOLOR', (2, 0), (2, -1), _CT3),
            ('TEXTCOLOR', (3, 1), (3, 1), _CR if in_cf else _CG),
        ),
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=_CB))
    story.append(Spacer(1, 3 * mm))

    # ── 執行摘要 ───────────────────────────────────────────────────────────
    story.append(Paragraph('■  執行摘要', s['h2']))

    cf_status = ('是 — 受 Cloudflare 保護（主站真實 IP 被遮蔽）' if in_cf
                 else '否 — 直接對外 IP')
    wc_status = ('是 — 萬用字元 DNS 偵測到（暴力列舉結果可信度降低）' if wildcard
                 else '否 — 結果可信')

    ssl_conf_count = sum(1 for e in non_cf if e.get('ssl_confirmed') is True)
    summ_rows = [
        ['項目', '狀態'],
        ['DNS 解析結果',     resolved],
        ['Cloudflare 保護',  cf_status],
        ['萬用字元 DNS',     wc_status],
        ['偵測 CMS',         cms_detected or '（未偵測到 / 未知）'],
        ['非 CF 候選 IP 數量', f'{len(non_cf)} 個'],
        ['SSL 憑證確認 IP',  f'{ssl_conf_count} 個' if ssl_conf_count else '（無）'],
        ['頁面參照域名命中', f'{len(page_hits)} 個'],
        ['CT 日誌子網域名稱', f'{len(ct_names)} 個'],
        ['被動 DNS 歷史 IP',  f'{len(passive)} 個'],
        ['子網域解析命中',    f'{len(sub_hits)} 個'],
    ]
    story.append(Table(
        summ_rows,
        colWidths=[5 * cm, _UW - 5 * cm],
        style=_hdr(body,
            ('TEXTCOLOR', (0, 1), (0, -1), _CT3),
            ('TEXTCOLOR', (1, 2), (1, 2), _CR if in_cf else _CG),
            ('TEXTCOLOR', (1, 3), (1, 3), _CR if wildcard else _CG),
            ('TEXTCOLOR', (1, 4), (1, 4), _CA if cms_detected else _CT3),
            ('TEXTCOLOR', (1, 5), (1, 5), _CG if non_cf else _CT3),
            ('TEXTCOLOR', (1, 6), (1, 6), _CG if ssl_conf_count else _CT3),
        ),
    ))
    story.append(Spacer(1, 5 * mm))

    # ── 非 CF IP 清單 ─────────────────────────────────────────────────────
    story.append(Paragraph('■  非 Cloudflare IP 清單（潛在來源主機）', s['h2']))
    if non_cf:
        ip_rows = [['IP 位址', 'ASN 資訊', 'IP 業者', '信心度', 'SSL']]
        for e in non_cf:
            ssl_ok = e.get('ssl_confirmed')
            ssl_txt = '✔ 確認' if ssl_ok is True else ('✘' if ssl_ok is False else '—')
            ip_rows.append([
                e.get('ip', ''),
                e.get('asn', '—'),
                e.get('isp', '—'),
                f"{float(e.get('confidence', 0)):.0f}%",
                ssl_txt,
            ])
        ssl_cell_cmds = []
        for i, e in enumerate(non_cf, start=1):
            if e.get('ssl_confirmed') is True:
                ssl_cell_cmds.append(('TEXTCOLOR', (4, i), (4, i), _CG))
            elif e.get('ssl_confirmed') is False:
                ssl_cell_cmds.append(('TEXTCOLOR', (4, i), (4, i), _CT3))
        story.append(Table(
            ip_rows,
            colWidths=[3.5 * cm, 5 * cm, 4.5 * cm, 2 * cm, 2 * cm],
            style=_hdr(body,
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [_CGB, white]),
                ('TEXTCOLOR', (0, 1), (0, -1), _CG),
                ('FONTNAME',  (0, 1), (0, -1), mono),
                ('FONTNAME',  (3, 1), (3, -1), mono),
                ('BOX', (0, 0), (-1, -1), 1.0, _CG),
                ('ALIGN', (3, 0), (3, -1), 'CENTER'),
                ('ALIGN', (4, 0), (4, -1), 'CENTER'),
                *ssl_cell_cmds,
            ),
        ))
    else:
        story.append(Paragraph(
            '（未發現非 Cloudflare IP — 目標可能完全由 Cloudflare 或 CDN 保護）',
            s['caption']))
    story.append(Spacer(1, 5 * mm))

    # ── 子網域解析結果 ────────────────────────────────────────────────────
    if sub_hits:
        story.append(Paragraph('■  子網域解析結果', s['h2']))
        show = sub_hits[:100]
        sub_rows = [['子網域名稱', '解析 IP', 'CF 保護']]
        per_cell = []
        for i, h in enumerate(show, start=1):
            behind = h.get('behind_cloudflare', True)
            sub_rows.append([h.get('host', ''), h.get('ip', ''),
                              '是' if behind else '否'])
            col = _CR if behind else _CG
            per_cell += [
                ('TEXTCOLOR', (1, i), (1, i), col),
                ('TEXTCOLOR', (2, i), (2, i), col),
            ]
        story.append(Table(
            sub_rows,
            colWidths=[7 * cm, 5.5 * cm, 4.5 * cm],
            style=_hdr(body,
                ('FONTNAME', (0, 1), (1, -1), mono),   # 子網域/IP 欄用等寬；CF欄保持中文字型
                ('FONTSIZE', (0, 0), (-1, -1), 7.5),
                ('ALIGN',    (2, 0), (2, -1), 'CENTER'),
                *per_cell,
            ),
        ))
        if len(sub_hits) > 100:
            story.append(Paragraph(
                f'（共 {len(sub_hits)} 筆，僅顯示前 100 筆）', s['caption']))
        story.append(Spacer(1, 5 * mm))

    # ── 頁面參照來源（主動偵察） ──────────────────────────────────────────
    if page_hits:
        story.append(Paragraph('■  頁面參照域名（主動偵察）', s['h2']))
        show_pg = page_hits[:100]
        pg_rows = [['域名', '解析 IP', 'CF 保護', '來源']]
        pg_cell_cmds = []
        for i, h in enumerate(show_pg, start=1):
            behind = h.get('behind_cloudflare', True)
            pg_rows.append([
                h.get('host', ''),
                h.get('ip', ''),
                '是' if behind else '否',
                h.get('source', 'page'),
            ])
            col = _CR if behind else _CG
            pg_cell_cmds += [
                ('TEXTCOLOR', (1, i), (1, i), col),
                ('TEXTCOLOR', (2, i), (2, i), col),
            ]
        story.append(Table(
            pg_rows,
            colWidths=[6 * cm, 4.5 * cm, 3 * cm, 3.5 * cm],
            style=_hdr(body,
                ('FONTNAME', (0, 1), (1, -1), mono),
                ('FONTSIZE', (0, 0), (-1, -1), 7.5),
                ('ALIGN',    (2, 0), (2, -1), 'CENTER'),
                ('ALIGN',    (3, 0), (3, -1), 'CENTER'),
                *pg_cell_cmds,
            ),
        ))
        if len(page_hits) > 100:
            story.append(Paragraph(
                f'（共 {len(page_hits)} 筆，僅顯示前 100 筆）', s['caption']))
        story.append(Spacer(1, 5 * mm))

    # ── CT 日誌子網域 ─────────────────────────────────────────────────────
    if ct_names:
        story.append(Paragraph('■  憑證透明度日誌（CT Log）發現的子網域', s['h2']))
        show_ct = ct_names[:120]
        ct_rows = []
        for i in range(0, len(show_ct), 2):
            ct_rows.append([
                show_ct[i],
                show_ct[i + 1] if i + 1 < len(show_ct) else '',
            ])
        story.append(Table(
            ct_rows,
            colWidths=[_UW / 2, _UW / 2],
            style=TableStyle([
                ('FONTNAME',  (0, 0), (-1, -1), mono),
                ('FONTSIZE',  (0, 0), (-1, -1), 7.5),
                ('TEXTCOLOR', (0, 0), (-1, -1), _CT2),
                ('ROWBACKGROUNDS', (0, 0), (-1, -1), [white, _CALT]),
                ('TOPPADDING',    (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING',   (0, 0), (-1, -1), 6),
                ('BOX',      (0, 0), (-1, -1), 0.3, _CB),
                ('LINEBEFORE',(1, 0), (1, -1), 0.3, _CB),
                ('VALIGN',   (0, 0), (-1, -1), 'MIDDLE'),
            ]),
        ))
        if len(ct_names) > 120:
            story.append(Paragraph(
                f'（共 {len(ct_names)} 個，僅顯示前 120 個）', s['caption']))
        story.append(Spacer(1, 5 * mm))

    # ── 被動 DNS 歷史 IP ──────────────────────────────────────────────────
    if passive:
        story.append(Paragraph('■  被動 DNS 歷史 IP', s['h2']))
        show_p = passive[:90]
        p_rows = []
        for i in range(0, len(show_p), 3):
            p_rows.append([
                show_p[i],
                show_p[i + 1] if i + 1 < len(show_p) else '',
                show_p[i + 2] if i + 2 < len(show_p) else '',
            ])
        col3 = _UW / 3
        story.append(Table(
            p_rows,
            colWidths=[col3, col3, col3],
            style=TableStyle([
                ('FONTNAME',  (0, 0), (-1, -1), mono),
                ('FONTSIZE',  (0, 0), (-1, -1), 8),
                ('TEXTCOLOR', (0, 0), (-1, -1), _CT2),
                ('ROWBACKGROUNDS', (0, 0), (-1, -1), [white, _CALT]),
                ('TOPPADDING',    (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING',   (0, 0), (-1, -1), 6),
                ('BOX',      (0, 0), (-1, -1), 0.3, _CB),
                ('LINEABOVE',(0, 0), (-1, -1), 0.2, _CB),
                ('VALIGN',   (0, 0), (-1, -1), 'MIDDLE'),
            ]),
        ))
        if len(passive) > 90:
            story.append(Paragraph(
                f'（共 {len(passive)} 個，僅顯示前 90 個）', s['caption']))
        story.append(Spacer(1, 5 * mm))

    # ── 免責聲明 ──────────────────────────────────────────────────────────
    story.append(HRFlowable(width='100%', thickness=0.5, color=_CB))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        '本報告由幣流分析鑑識系統之網站溯源功能自動產生，僅供合法授權之司法鑑識調查使用。'
        '報告中各項 IP 位址及域名資訊均取自公開來源（CT 日誌、被動 DNS、ASN 資料庫），'
        '調查人員應自行驗證資料正確性。',
        s['caption'],
    ))

    return story


# ── 公開 API ──────────────────────────────────────────────────────────────────

def generate_website_scan_pdf(
    result: Dict,
    case_name: str,
    output_path: str,
) -> None:
    """
    從 scan_domain() 的結果產生 A4 PDF 鑑識報告。

    Args:
        result:      scan_domain() 回傳的結果字典
        case_name:   案件名稱，顯示於頁眉右側
        output_path: 輸出 PDF 檔案的完整路徑
    """
    body, mono = _get_fonts()
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    target = result.get('target', '')
    case_label = case_name if case_name and '未選擇' not in case_name else now

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=_M, rightMargin=_M,
        topMargin=27 * mm, bottomMargin=16 * mm,
        title=f'網站溯源報告 — {target}',
        author='幣流分析鑑識系統',
        subject='司法鑑識網站溯源分析',
    )

    story = _build_story(result, case_name, now, body, mono)

    def _page(canvas, doc_obj):
        _draw_page(canvas, doc_obj, target=target,
                   case_label=case_label, body=body, mono=mono)

    doc.build(story, onFirstPage=_page, onLaterPages=_page)
