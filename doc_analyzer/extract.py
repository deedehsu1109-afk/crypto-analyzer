#!/usr/bin/env python3
"""
文件內容提取工具
支援：PDF、DOCX、XLSX、PPTX、ODT、ODS、ODP、圖片（PNG/JPG/BMP/TIFF/WEBP）
用法：python extract.py <檔案路徑> [--meta] [--pages X-Y]
"""
from __future__ import annotations
import sys
import os
import json

# 強制 stdout 使用 UTF-8，避免 CP950/Big5 無法編碼特殊字元
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import argparse
import datetime

def _check_dep(pkg: str) -> bool:
    import importlib
    try:
        importlib.import_module(pkg)
        return True
    except ImportError:
        return False


# ── PDF ──────────────────────────────────────────────────────────────────────

def extract_pdf(path: str, pages: str = None) -> dict:
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        total  = len(reader.pages)
        meta   = reader.metadata or {}

        # 解析頁碼範圍
        if pages:
            parts = pages.split("-")
            start = int(parts[0]) - 1
            end   = int(parts[1]) if len(parts) > 1 else start + 1
        else:
            start, end = 0, total

        texts = []
        for i in range(max(0, start), min(end, total)):
            txt = reader.pages[i].extract_text() or ""
            texts.append({"page": i + 1, "text": txt.strip()})

        return {
            "type": "PDF",
            "total_pages": total,
            "extracted_pages": f"{start+1}-{min(end, total)}",
            "metadata": {
                "title":    meta.get("/Title", ""),
                "author":   meta.get("/Author", ""),
                "creator":  meta.get("/Creator", ""),
                "created":  str(meta.get("/CreationDate", "")),
                "modified": str(meta.get("/ModDate", "")),
            },
            "pages": texts,
        }
    except ImportError:
        return {"error": "需要安裝 pypdf：pip install pypdf"}
    except Exception as e:
        return {"error": str(e)}


# ── DOCX ──────────────────────────────────────────────────────────────────────

def extract_docx(path: str) -> dict:
    try:
        from docx import Document
        from docx.oxml.ns import qn
        doc  = Document(path)
        core = doc.core_properties

        # 段落
        paragraphs = []
        for p in doc.paragraphs:
            txt = p.text.strip()
            if txt:
                paragraphs.append({
                    "style": p.style.name if p.style else "",
                    "text":  txt,
                })

        # 表格
        tables = []
        for ti, tbl in enumerate(doc.tables):
            rows = []
            for row in tbl.rows:
                rows.append([c.text.strip() for c in row.cells])
            tables.append({"table_index": ti + 1, "rows": rows})

        return {
            "type": "DOCX",
            "metadata": {
                "title":    core.title or "",
                "author":   core.author or "",
                "created":  str(core.created or ""),
                "modified": str(core.modified or ""),
                "revision": core.revision or 0,
            },
            "paragraph_count": len(paragraphs),
            "table_count": len(tables),
            "paragraphs": paragraphs,
            "tables": tables,
        }
    except ImportError:
        return {"error": "需要安裝 python-docx：pip install python-docx"}
    except Exception as e:
        return {"error": str(e)}


# ── XLSX ──────────────────────────────────────────────────────────────────────

def extract_xlsx(path: str) -> dict:
    try:
        import openpyxl
        wb   = openpyxl.load_workbook(path, data_only=True)
        prop = wb.properties

        sheets = []
        for name in wb.sheetnames:
            ws   = wb[name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                row_data = [str(c) if c is not None else "" for c in row]
                if any(c for c in row_data):
                    rows.append(row_data)
            sheets.append({
                "name":      name,
                "row_count": ws.max_row,
                "col_count": ws.max_column,
                "data":      rows[:500],  # 最多 500 列
            })

        return {
            "type": "XLSX",
            "metadata": {
                "title":    prop.title or "",
                "creator":  prop.creator or "",
                "created":  str(prop.created or ""),
                "modified": str(prop.modified or ""),
            },
            "sheet_count": len(sheets),
            "sheets": sheets,
        }
    except ImportError:
        return {"error": "需要安裝 openpyxl：pip install openpyxl"}
    except Exception as e:
        return {"error": str(e)}


# ── PPTX ──────────────────────────────────────────────────────────────────────

def extract_pptx(path: str) -> dict:
    try:
        from pptx import Presentation
        prs  = Presentation(path)
        core = prs.core_properties

        slides = []
        for si, slide in enumerate(prs.slides):
            texts  = []
            images = 0
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        txt = para.text.strip()
                        if txt:
                            texts.append(txt)
                if shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                    images += 1
            slides.append({
                "slide": si + 1,
                "texts": texts,
                "image_count": images,
            })

        return {
            "type": "PPTX",
            "metadata": {
                "title":    core.title or "",
                "author":   core.author or "",
                "created":  str(core.created or ""),
                "modified": str(core.modified or ""),
            },
            "slide_count": len(slides),
            "slides": slides,
        }
    except ImportError:
        return {"error": "需要安裝 python-pptx：pip install python-pptx"}
    except Exception as e:
        return {"error": str(e)}


# ── ODT / ODS / ODP ──────────────────────────────────────────────────────────

def extract_odf(path: str) -> dict:
    try:
        from odf.opendocument import load
        from odf import text as odftext, table as odftable
        from odf.element import Element

        doc  = load(path)
        ext  = os.path.splitext(path)[1].lower()
        ftype = {"odt": "ODT", "ods": "ODS", "odp": "ODP"}.get(ext[1:], "ODF")

        # 提取所有文字節點
        def get_text(node) -> str:
            result = []
            if hasattr(node, "childNodes"):
                for child in node.childNodes:
                    if hasattr(child, "data"):
                        result.append(child.data)
                    else:
                        result.append(get_text(child))
            return "".join(result)

        body  = doc.body
        texts = []
        for elem in body.childNodes:
            t = get_text(elem).strip()
            if t:
                texts.append(t)

        meta = doc.meta
        def _meta(tag):
            try:
                return str(meta.getElementsByType(tag)[0]) if meta else ""
            except Exception:
                return ""

        return {
            "type": ftype,
            "metadata": {
                "title":    _meta("title"),
                "creator":  _meta("initial-creator"),
                "created":  _meta("creation-date"),
            },
            "text_blocks": len(texts),
            "content": texts,
        }
    except ImportError:
        return {"error": "需要安裝 odfpy：pip install odfpy"}
    except Exception as e:
        return {"error": str(e)}


# ── 圖片 ──────────────────────────────────────────────────────────────────────

def extract_image(path: str) -> dict:
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
        img  = Image.open(path)
        info: dict = {
            "type":   "IMAGE",
            "format": img.format or os.path.splitext(path)[1].upper()[1:],
            "mode":   img.mode,
            "width":  img.width,
            "height": img.height,
            "size_bytes": os.path.getsize(path),
        }

        # EXIF
        exif_data = {}
        raw_exif  = img._getexif() if hasattr(img, "_getexif") else None
        if raw_exif:
            for tag_id, value in raw_exif.items():
                tag = TAGS.get(tag_id, str(tag_id))
                if tag == "GPSInfo" and isinstance(value, dict):
                    gps = {}
                    for gk, gv in value.items():
                        gps[GPSTAGS.get(gk, gk)] = str(gv)
                    exif_data["GPSInfo"] = gps
                elif isinstance(value, bytes):
                    exif_data[tag] = value.hex()[:64]
                else:
                    try:
                        exif_data[tag] = str(value)[:256]
                    except Exception:
                        pass
        if exif_data:
            info["exif"] = exif_data

        # 檔案時間
        stat = os.stat(path)
        info["file_created"]  = datetime.datetime.fromtimestamp(
            stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
        info["file_modified"] = datetime.datetime.fromtimestamp(
            stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

        # GPS 座標換算
        if "GPSInfo" in exif_data:
            try:
                gps = exif_data["GPSInfo"]
                def dms_to_deg(dms_str):
                    import re
                    nums = re.findall(r"[\d.]+", dms_str)
                    return float(nums[0]) + float(nums[1])/60 + float(nums[2])/3600
                lat = dms_to_deg(str(gps.get("GPSLatitude", "0 0 0")))
                lon = dms_to_deg(str(gps.get("GPSLongitude", "0 0 0")))
                if gps.get("GPSLatitudeRef") == "S":  lat = -lat
                if gps.get("GPSLongitudeRef") == "W": lon = -lon
                info["gps_decimal"] = {"lat": round(lat, 7), "lon": round(lon, 7)}
            except Exception:
                pass

        return info
    except ImportError:
        return {"error": "需要安裝 Pillow：pip install Pillow"}
    except Exception as e:
        return {"error": str(e)}


# ── 主程式 ────────────────────────────────────────────────────────────────────

EXT_MAP = {
    ".pdf":  extract_pdf,
    ".docx": extract_docx,
    ".xlsx": extract_xlsx,
    ".pptx": extract_pptx,
    ".odt":  extract_odf,
    ".ods":  extract_odf,
    ".odp":  extract_odf,
    ".png":  extract_image,
    ".jpg":  extract_image,
    ".jpeg": extract_image,
    ".bmp":  extract_image,
    ".tiff": extract_image,
    ".tif":  extract_image,
    ".webp": extract_image,
    ".gif":  extract_image,
    ".heic": extract_image,
}

def main():
    parser = argparse.ArgumentParser(description="文件內容提取工具")
    parser.add_argument("file", help="檔案路徑")
    parser.add_argument("--pages", default=None, help="PDF 頁碼範圍（如 1-5）")
    parser.add_argument("--pretty", action="store_true", help="格式化 JSON 輸出")
    args = parser.parse_args()

    path = args.file
    if not os.path.exists(path):
        print(json.dumps({"error": f"檔案不存在：{path}"}))
        sys.exit(1)

    ext = os.path.splitext(path)[1].lower()
    fn  = EXT_MAP.get(ext)
    if not fn:
        print(json.dumps({
            "error": f"不支援的檔案類型：{ext}",
            "supported": list(EXT_MAP.keys())
        }))
        sys.exit(1)

    if ext == ".pdf" and args.pages:
        result = fn(path, pages=args.pages)
    else:
        result = fn(path)

    result["file_path"] = os.path.abspath(path)
    result["file_name"] = os.path.basename(path)
    result["file_size"] = os.path.getsize(path)

    indent = 2 if args.pretty else None
    output = json.dumps(result, ensure_ascii=False, indent=indent,
                        default=str)
    # 直接寫入 bytes，避免 stdout 編碼問題（CP950/Big5 環境）
    sys.stdout.buffer.write(output.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()

if __name__ == "__main__":
    main()
