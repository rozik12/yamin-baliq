"""Конвертация документов между PDF, Word, Excel, CSV, TXT и картинками.

Работает на чистом Python. Если на машине есть LibreOffice (`soffice`), он подключается сам:
даёт качественнее Word/Excel → PDF и старые форматы (.doc, .ppt, .odt, .rtf, .ods ...).
"""
import csv
import os
import shutil
import subprocess
import zipfile
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_ROWS = 20000
MAX_PDF_PAGES = 60
MAX_PIXELS = 40_000_000
IMAGES = {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff"}
LO_IN = {"doc", "docx", "odt", "rtf", "ppt", "pptx", "odp", "xls", "xlsx", "ods"}


class ConvertError(Exception):
    """Ожидаемая ошибка: код причины в .code (bad_file, too_big, empty, unsupported)."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


def soffice():
    return shutil.which("soffice") or shutil.which("libreoffice")


# ---------- общие помощники

def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime):
        return v.strftime("%d.%m.%Y %H:%M") if (v.hour or v.minute) else v.strftime("%d.%m.%Y")
    if isinstance(v, date):
        return v.strftime("%d.%m.%Y")
    return str(v)


def load_sheets(path, ext):
    """[(имя листа, [[ячейки-строки]])] для xlsx / xls / csv."""
    try:
        if ext == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            out = []
            for ws in wb.worksheets:
                rows = []
                for i, r in enumerate(ws.iter_rows(values_only=True)):
                    if i >= MAX_ROWS:
                        break
                    rows.append([_cell(c) for c in r])
                out.append((ws.title, _trim(rows)))
            wb.close()
        elif ext == "xls":
            import xlrd
            wb = xlrd.open_workbook(path)
            out = []
            for sh in wb.sheets():
                rows = []
                for i in range(min(sh.nrows, MAX_ROWS)):
                    row = []
                    for j in range(sh.ncols):
                        c = sh.cell(i, j)
                        if c.ctype == xlrd.XL_CELL_DATE:
                            try:
                                row.append(_cell(xlrd.xldate.xldate_as_datetime(c.value, wb.datemode)))
                            except Exception:
                                row.append(str(c.value))
                        else:
                            row.append(_cell(c.value))
                    rows.append(row)
                out.append((sh.name, _trim(rows)))
        elif ext == "csv":
            raw = Path(path).read_bytes()
            for enc in ("utf-8-sig", "cp1251", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            rows = [r for _, r in zip(range(MAX_ROWS), csv.reader(text.splitlines(), dialect))]
            out = [("Sheet1", _trim(rows))]
        else:
            raise ConvertError("unsupported")
    except ConvertError:
        raise
    except Exception as e:
        raise ConvertError("bad_file", str(e)) from e
    if not any(rows for _, rows in out):
        raise ConvertError("empty")
    return out


def _trim(rows):
    """Убирает пустые строки в конце и пустые столбцы справа."""
    while rows and not any(rows[-1]):
        rows.pop()
    width = max((max((i + 1 for i, c in enumerate(r) if c != ""), default=0) for r in rows), default=0)
    return [r[:width] + [""] * (width - len(r)) for r in rows]


def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    names = pdfmetrics.getRegisteredFontNames()
    if "DJ" not in names:
        pdfmetrics.registerFont(TTFont("DJ", str(HERE / "fonts" / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DJ-B", str(HERE / "fonts" / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("DJ", normal="DJ", bold="DJ-B", italic="DJ", boldItalic="DJ-B")
    return "DJ"


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- в PDF

def sheets_to_pdf(sheets, out):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle
    _font()
    wide = max((len(r) for _, rows in sheets for r in rows), default=0) > 6
    page = landscape(A4) if wide else A4
    doc = SimpleDocTemplate(str(out), pagesize=page, leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    width = page[0] - 48
    story = []
    for si, (name, rows) in enumerate(sheets):
        if not rows:
            continue
        if si:
            story.append(PageBreak())
        if len(sheets) > 1:
            story.append(Paragraph(_esc(name), ParagraphStyle("h", fontName="DJ-B", fontSize=12, spaceAfter=6)))
        ncol = max(len(r) for r in rows)
        fs = 8 if ncol <= 8 else 6.5 if ncol <= 14 else 5.5
        st = ParagraphStyle("c", fontName="DJ", fontSize=fs, leading=fs + 2)
        data = [[Paragraph(_esc(c), st) for c in r] + [""] * (ncol - len(r)) for r in rows]
        t = Table(data, colWidths=[width / ncol] * ncol, repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE"))]))
        story.append(t)
    doc.build(story)


def docx_to_pdf(src, out):
    import docx
    from docx.table import Table as DTable
    from docx.text.paragraph import Paragraph as DPara
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    _font()
    d = docx.Document(str(src))
    base = ParagraphStyle("b", fontName="DJ", fontSize=10, leading=13, spaceAfter=4)
    story = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = DPara(el, d)
            html = ""
            for r in p.runs:
                t = _esc(r.text)
                if not t:
                    continue
                if r.bold:
                    t = f"<b>{t}</b>"
                if r.italic:
                    t = f"<i>{t}</i>"
                if r.underline:
                    t = f"<u>{t}</u>"
                html += t
            sname = (p.style.name or "") if p.style is not None else ""
            if sname.startswith("Heading") or sname == "Title":
                lvl = int(sname[-1]) if sname[-1:].isdigit() else 0
                size = {0: 18, 1: 16, 2: 14, 3: 12}.get(lvl, 11)
                story.append(Paragraph(f"<b>{html or ' '}</b>", ParagraphStyle("h", parent=base, fontSize=size, leading=size + 3, spaceBefore=8)))
            elif html.strip():
                story.append(Paragraph(html, base))
            else:
                story.append(Spacer(1, 6))
        elif tag == "tbl":
            t = DTable(el, d)
            rows = [[_cell_text(c) for c in r.cells] for r in t.rows]
            if not rows:
                continue
            ncol = max(len(r) for r in rows)
            st = ParagraphStyle("c", parent=base, fontSize=8, leading=10, spaceAfter=0)
            data = [[Paragraph(_esc(c), st) for c in r] + [""] * (ncol - len(r)) for r in rows]
            tb = Table(data, colWidths=[(A4[0] - 80) / ncol] * ncol, repeatRows=1)
            tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.extend([tb, Spacer(1, 8)])
    if not story:
        raise ConvertError("empty")
    SimpleDocTemplate(str(out), pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40).build(story)


def _cell_text(c):
    return c.text.strip()


def text_to_pdf(src, out):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    _font()
    text = _read_text(src)
    st = ParagraphStyle("t", fontName="DJ", fontSize=10, leading=13)
    story = [Paragraph(_esc(line), st) if line.strip() else Spacer(1, 8) for line in text.splitlines()]
    if not story:
        raise ConvertError("empty")
    SimpleDocTemplate(str(out), pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40).build(story)


def _read_text(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def image_to_pdf(src, out):
    img = _open_image(src)
    img.convert("RGB").save(str(out), "PDF", resolution=150)


def _open_image(src):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        img = Image.open(str(src))
        img.load()
    except Exception as e:
        raise ConvertError("bad_file", str(e)) from e
    return img


def image_to_image(src, out, fmt):
    img = _open_image(src)
    if fmt == "JPEG" and img.mode in ("RGBA", "LA", "P"):
        bg = img.convert("RGBA")
        from PIL import Image
        base = Image.new("RGB", bg.size, "white")
        base.paste(bg, mask=bg.split()[-1])
        img = base
    img.convert("RGB" if fmt == "JPEG" else img.mode if img.mode != "P" else "RGBA").save(str(out), fmt)


# ---------- в Word / Excel / текст

def sheets_to_docx(sheets, out):
    import docx
    d = docx.Document()
    for name, rows in sheets:
        if not rows:
            continue
        if len(sheets) > 1:
            d.add_heading(name, level=2)
        ncol = max(len(r) for r in rows)
        t = d.add_table(rows=len(rows), cols=ncol)
        t.style = "Table Grid"
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                t.cell(i, j).text = v
        d.add_paragraph()
    d.save(str(out))


def text_to_docx(src, out):
    import docx
    d = docx.Document()
    for line in _read_text(src).splitlines():
        d.add_paragraph(line)
    d.save(str(out))


def sheets_to_xlsx(sheets, out):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used = set()
    for name, rows in sheets:
        title = "".join(ch for ch in name if ch not in '[]:*?/\\')[:31] or "Sheet"
        n, base = 1, title
        while title in used:
            n += 1
            title = f"{base[:28]}_{n}"
        used.add(title)
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append([_num(c) for c in r])
        for col in ws.columns:
            w = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max(w + 2, 8), 60)
    if not wb.worksheets:
        raise ConvertError("empty")
    wb.save(str(out))


def _num(v):
    """«1234» и «12,5» становятся числами, остальное остаётся текстом."""
    s = v.strip().replace(" ", "").replace(" ", "")
    if not s or len(s) > 15 or (s[0] == "0" and len(s) > 1 and s[1].isdigit()):
        return v
    try:
        return int(s)
    except ValueError:
        pass
    try:
        if s.count(",") + s.count(".") == 1:
            return float(s.replace(",", "."))
    except ValueError:
        pass
    return v


def sheets_to_csv(sheets, out):
    """Один лист — csv; несколько листов — zip с csv на каждый."""
    if len(sheets) == 1:
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";").writerows(sheets[0][1])
        return
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (name, rows) in enumerate(sheets, 1):
            import io
            buf = io.StringIO()
            csv.writer(buf, delimiter=";").writerows(rows)
            z.writestr(f"{i}_{name[:25] or 'sheet'}.csv", "﻿" + buf.getvalue())


def docx_tables(src):
    import docx
    d = docx.Document(str(src))
    sheets = []
    for i, t in enumerate(d.tables, 1):
        rows = _trim([[_cell_text(c) for c in r.cells] for r in t.rows])
        if rows:
            sheets.append((f"Table {i}", rows))
    if not sheets:
        lines = [[p.text] for p in d.paragraphs if p.text.strip()]
        if lines:
            sheets = [("Text", lines)]
    if not sheets:
        raise ConvertError("empty")
    return sheets


def docx_to_txt(src, out):
    import docx
    d = docx.Document(str(src))
    lines = [p.text for p in d.paragraphs]
    for t in d.tables:
        for r in t.rows:
            lines.append("\t".join(_cell_text(c) for c in r.cells))
    Path(out).write_text("\n".join(lines), encoding="utf-8")


# ---------- из PDF

def _open_pdf(src):
    import pymupdf
    try:
        doc = pymupdf.open(str(src))
        if doc.needs_pass:
            raise ConvertError("bad_file", "password")
    except ConvertError:
        raise
    except Exception as e:
        raise ConvertError("bad_file", str(e)) from e
    if doc.page_count == 0:
        raise ConvertError("empty")
    return doc


def pdf_to_docx(src, out):
    doc = _open_pdf(src)
    pages = doc.page_count
    doc.close()
    if pages > MAX_PDF_PAGES:
        raise ConvertError("too_big", f"{pages} pages")
    from pdf2docx import Converter
    cv = Converter(str(src))
    try:
        cv.convert(str(out))
    except Exception as e:
        raise ConvertError("bad_file", str(e)) from e
    finally:
        cv.close()


def pdf_to_txt(src, out):
    doc = _open_pdf(src)
    text = "\n\n".join(p.get_text() for p in doc)
    if not text.strip():
        raise ConvertError("empty", "no text layer (scan?)")
    Path(out).write_text(text, encoding="utf-8")


def pdf_to_sheets(src):
    doc = _open_pdf(src)
    sheets = []
    for pi, page in enumerate(doc, 1):
        if pi > MAX_PDF_PAGES:
            break
        try:
            tabs = page.find_tables().tables
        except Exception:
            tabs = []
        for ti, t in enumerate(tabs, 1):
            rows = _trim([[(c or "").replace("\n", " ").strip() for c in r] for r in t.extract()])
            if rows:
                sheets.append((f"p{pi}_t{ti}", rows))
    if not sheets:  # таблиц нет — каждая строка текста в отдельную строку листа
        lines = []
        for page in doc:
            lines += [[ln] for ln in page.get_text().splitlines() if ln.strip()]
        if not lines:
            raise ConvertError("empty", "no text layer (scan?)")
        sheets = [("Text", lines[:MAX_ROWS])]
    return sheets


def pdf_to_png(src, out):
    doc = _open_pdf(src)
    n = min(doc.page_count, MAX_PDF_PAGES)
    if n == 1:
        doc[0].get_pixmap(dpi=140).save(str(out))
        return
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for i in range(n):
            z.writestr(f"page_{i + 1:02d}.png", doc[i].get_pixmap(dpi=120).tobytes("png"))


# ---------- LibreOffice (необязательный)

def lo_convert(src, dst_ext, outdir, timeout=120):
    exe = soffice()
    if not exe:
        raise ConvertError("unsupported")
    profile = Path(outdir) / "lo_profile"
    cmd = [exe, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", dst_ext, "--outdir", str(outdir), str(src)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ConvertError("bad_file", "timeout") from e
    except subprocess.CalledProcessError as e:
        raise ConvertError("bad_file", e.stderr.decode("utf-8", "replace")[:200]) from e
    res = Path(outdir) / (Path(src).stem + "." + dst_ext)
    if not res.exists():
        raise ConvertError("bad_file", "no output")
    return res


# ---------- таблица возможностей и главная функция

BASE = {
    "pdf": ["docx", "xlsx", "txt", "png"],
    "docx": ["pdf", "xlsx", "txt"],
    "xlsx": ["pdf", "docx", "csv"],
    "xls": ["xlsx", "pdf", "docx", "csv"],
    "csv": ["xlsx", "pdf", "docx"],
    "txt": ["pdf", "docx"],
}
for _e in IMAGES:
    BASE[_e] = ["pdf", "png", "jpg"]
LO_EXTRA = {
    "doc": ["docx", "pdf", "txt"], "odt": ["docx", "pdf"], "rtf": ["docx", "pdf"],
    "ppt": ["pdf", "pptx"], "pptx": ["pdf"], "odp": ["pdf"], "ods": ["xlsx", "pdf"],
}


def targets(ext, lo=None):
    ext = ext.lower().lstrip(".")
    lo = bool(soffice()) if lo is None else lo
    out = list(BASE.get(ext, []))
    if lo:
        for t in LO_EXTRA.get(ext, []):
            if t not in out:
                out.append(t)
        if ext in ("docx", "xlsx") and "doc" not in out:
            out += ["doc"] if ext == "docx" else ["xls"]
    return [t for t in out if t != ext and not (ext in ("jpg", "jpeg") and t == "jpg")]


def convert(src, src_ext, dst_ext, outdir):
    """Конвертирует src в outdir и возвращает путь к результату."""
    src, outdir = Path(src), Path(outdir)
    src_ext, dst_ext = src_ext.lower().lstrip("."), dst_ext.lower().lstrip(".")
    if dst_ext not in targets(src_ext):
        raise ConvertError("unsupported")
    out = outdir / f"result.{dst_ext}"
    lo = bool(soffice())

    if src_ext in LO_EXTRA and src_ext not in BASE:  # doc, odt, rtf, ppt, ...
        res = lo_convert(src, dst_ext, outdir)
        return res
    if lo and src_ext in ("docx", "xlsx", "xls") and dst_ext == "pdf":
        try:
            return lo_convert(src, "pdf", outdir)
        except ConvertError:
            pass  # откат на встроенный рендер
    if lo and dst_ext in ("doc", "xls"):
        return lo_convert(src, dst_ext, outdir)

    if src_ext == "pdf":
        {"docx": pdf_to_docx, "txt": pdf_to_txt, "png": pdf_to_png}.get(dst_ext, lambda s, o: sheets_to_xlsx(pdf_to_sheets(s), o))(src, out)
        if dst_ext == "png" and _is_zip(out):
            out = out.rename(outdir / "result.zip")
    elif src_ext == "docx":
        if dst_ext == "pdf":
            docx_to_pdf(src, out)
        elif dst_ext == "xlsx":
            sheets_to_xlsx(docx_tables(src), out)
        else:
            docx_to_txt(src, out)
    elif src_ext in ("xlsx", "xls", "csv"):
        sheets = load_sheets(src, src_ext)
        {"pdf": sheets_to_pdf, "docx": sheets_to_docx, "csv": sheets_to_csv, "xlsx": sheets_to_xlsx}[dst_ext](sheets, out)
        if dst_ext == "csv" and _is_zip(out):
            out = out.rename(outdir / "result.zip")
    elif src_ext == "txt":
        (text_to_pdf if dst_ext == "pdf" else text_to_docx)(src, out)
    elif src_ext in IMAGES:
        if dst_ext == "pdf":
            image_to_pdf(src, out)
        else:
            image_to_image(src, out, "PNG" if dst_ext == "png" else "JPEG")
    else:
        raise ConvertError("unsupported")
    if not out.exists() or out.stat().st_size == 0:
        raise ConvertError("bad_file", "empty output")
    return out


def _is_zip(p):
    try:
        return zipfile.is_zipfile(p)
    except OSError:
        return False
