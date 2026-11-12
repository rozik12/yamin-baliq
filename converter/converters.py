"""Конвертация документов между PDF, Word, Excel, CSV, TXT и картинками.

Основа на чистом Python (PDF, Word .docx, Excel, CSV, TXT, картинки). Если на машине есть
LibreOffice, pandoc, rsvg-convert, ddjvu, xpstopdf, подключаются сами: старые форматы (.doc, .xls, .ppt),
ODT/RTF/HTML/EPUB/FB2, презентации, SVG, DJVU, XPS. Недостающие шаги строятся цепочкой (DJVU → PDF → Word).
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
    if tesseract_ok() and not pdf_has_text(src):  # скан без текста: распознаём
        return ocr_to_docx(src, out)
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
    if tesseract_ok() and not pdf_has_text(src):
        return ocr_to_txt(src, out)
    doc = _open_pdf(src)
    text = "\n\n".join(p.get_text() for p in doc)
    if not text.strip():
        raise ConvertError("empty", "no text layer (scan?)")
    Path(out).write_text(text, encoding="utf-8")


def pdf_to_sheets(src):
    if tesseract_ok() and not pdf_has_text(src):
        return ocr_to_sheets(src)
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


# ---------- OCR (tesseract) и PDF-инструменты

OCR_MAX_PAGES = 12
MERGE_MAX_PAGES = 300
_langs_cache = []


def tesseract_ok():
    return bool(shutil.which("tesseract"))


def ocr_langs():
    """rus+uzb+eng из установленных; если ничего нет — eng."""
    if not _langs_cache:
        have = set()
        try:
            out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout.split()
            have = set(out)
        except Exception:
            pass
        want = [l for l in os.environ.get("OCR_LANGS", "rus+uzb+eng").split("+") if l in have]
        _langs_cache.append("+".join(want) or "eng")
    return _langs_cache[0]


def pdf_has_text(src):
    doc = _open_pdf(src)
    n = 0
    for i, page in enumerate(doc):
        if i >= 3:
            break
        n += len(page.get_text().strip())
    return n > 20


def _page_pngs(src, workdir):
    """PNG-файлы страниц PDF (до OCR_MAX_PAGES) или одна картинка → PNG."""
    src = Path(src)
    outs = []
    if src.suffix.lower() == ".pdf":
        doc = _open_pdf(src)
        if doc.page_count > OCR_MAX_PAGES:
            raise ConvertError("many_pages", str(doc.page_count))
        for i, page in enumerate(doc):
            p = Path(workdir) / f"p{i + 1:03d}.png"
            page.get_pixmap(dpi=200).save(str(p))
            outs.append(p)
    else:
        img = _open_image(src).convert("RGB")
        p = Path(workdir) / "p001.png"
        img.save(str(p))
        outs.append(p)
    return outs


def _tess_text(png):
    r = subprocess.run(["tesseract", str(png), "stdout", "-l", ocr_langs(), "--psm", "3"], capture_output=True, timeout=120)
    return r.stdout.decode("utf-8", "replace")


def ocr_pages(src):
    """Список текстов по страницам."""
    import tempfile
    if not tesseract_ok():
        raise ConvertError("unsupported")
    with tempfile.TemporaryDirectory() as wd:
        pages = [_tess_text(p) for p in _page_pngs(src, wd)]
    if not any(t.strip() for t in pages):
        raise ConvertError("empty", "ocr found no text")
    return pages


def ocr_to_txt(src, out):
    Path(out).write_text("\n\n".join(t.strip() for t in ocr_pages(src)), encoding="utf-8")


def ocr_to_docx(src, out):
    import docx
    d = docx.Document()
    for i, text in enumerate(ocr_pages(src)):
        if i:
            d.add_page_break()
        for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
            d.add_paragraph(para.replace("\n", " "))
    d.save(str(out))


def ocr_to_sheets(src):
    lines = [[ln.strip()] for t in ocr_pages(src) for ln in t.splitlines() if ln.strip()]
    return [("Text", lines[:MAX_ROWS])]


def ocr_searchable_pdf(src, out):
    """PDF с невидимым текстовым слоем: можно искать и копировать текст."""
    import tempfile
    import pymupdf
    if not tesseract_ok():
        raise ConvertError("unsupported")
    with tempfile.TemporaryDirectory() as wd:
        result = pymupdf.open()
        for png in _page_pngs(src, wd):
            base = png.with_suffix("")
            subprocess.run(["tesseract", str(png), str(base), "-l", ocr_langs(), "pdf"], capture_output=True, timeout=150, check=False)
            pdf = base.with_suffix(".pdf")
            if not pdf.exists():
                raise ConvertError("bad_file", "tesseract failed")
            with pymupdf.open(str(pdf)) as one:
                result.insert_pdf(one)
        result.save(str(out), garbage=3, deflate=True)


def compress_pdf(src, out):
    doc = _open_pdf(src)
    try:
        doc.rewrite_images(dpi_threshold=130, dpi_target=100, quality=65)
    except Exception:
        pass
    doc.save(str(out), garbage=4, deflate=True, clean=True)
    if Path(out).stat().st_size >= Path(src).stat().st_size:  # лучше не стало — отдаём как есть
        shutil.copy(src, out)


def split_pdf(src, out):
    import pymupdf
    doc = _open_pdf(src)
    if doc.page_count > 150:
        raise ConvertError("too_big", "pages")
    zp = Path(out).with_suffix(".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
        for i in range(doc.page_count):
            one = pymupdf.open()
            one.insert_pdf(doc, from_page=i, to_page=i)
            z.writestr(f"page_{i + 1:03d}.pdf", one.tobytes(garbage=3, deflate=True))
    return zp


def merge_files(paths, out):
    """Склеивает PDF и картинки в один PDF в заданном порядке."""
    import pymupdf
    import tempfile
    res = pymupdf.open()
    with tempfile.TemporaryDirectory() as wd:
        for i, p in enumerate(paths):
            p = Path(p)
            ext = canon(p.suffix)
            if ext == "pdf":
                src = p
            elif ext in IMG_IN:
                src = Path(wd) / f"i{i}.pdf"
                image_to_pdf(p, src)
            else:
                raise ConvertError("unsupported", ext)
            with _open_pdf(src) as d:
                res.insert_pdf(d)
            if res.page_count > MERGE_MAX_PAGES:
                raise ConvertError("too_big", "pages")
    if res.page_count == 0:
        raise ConvertError("empty")
    res.save(str(out), garbage=3, deflate=True)


# ---------- внешние программы

def soffice():
    return shutil.which("soffice") or shutil.which("libreoffice")


def _run(cmd, timeout=120):
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ConvertError("bad_file", "timeout") from e
    except subprocess.CalledProcessError as e:
        raise ConvertError("bad_file", e.stderr.decode("utf-8", "replace")[:300]) from e


# LibreOffice: формат-вход → семейство; (семейство, формат-выход) → фильтр экспорта
LO_FAMILY = {}
for _f in "doc docx docm dot dotx dotm odt ott rtf txt html".split():
    LO_FAMILY[_f] = "writer"
for _f in "xls xlsx xlsm xlsb xlt xltx ods ots csv tsv".split():
    LO_FAMILY[_f] = "calc"
for _f in "ppt pptx pptm pps ppsx pot potx odp otp".split():
    LO_FAMILY[_f] = "impress"
LO_OUT = {
    "writer": {"pdf": "pdf:writer_pdf_Export", "docx": "docx:MS Word 2007 XML", "doc": "doc:MS Word 97", "odt": "odt:writer8",
               "rtf": "rtf:Rich Text Format", "txt": "txt:Text (encoded):UTF8", "html": "html:XHTML Writer File:UTF8"},
    "calc": {"pdf": "pdf:calc_pdf_Export", "xlsx": "xlsx:Calc MS Excel 2007 XML", "xls": "xls:MS Excel 97", "ods": "ods:calc8",
             "csv": "csv:Text - txt - csv (StarCalc):59,34,76,1", "html": "html:HTML (StarCalc)"},
    "impress": {"pdf": "pdf:impress_pdf_Export", "pptx": "pptx:Impress MS PowerPoint 2007 XML", "ppt": "ppt:MS PowerPoint 97",
                "odp": "odp:impress8"},
}
LO_INFILTER = {"html": "HTML (StarWriter)", "txt": "Text (encoded):UTF8"}


def lo_run(src, dst, outdir, timeout=120):
    fam = LO_FAMILY[Path(src).suffix.lstrip(".").lower()]
    src = Path(src)
    profile = Path(outdir) / f"lo_profile_{os.getpid()}"
    cmd = [soffice(), f"-env:UserInstallation=file://{profile}", "--headless"]
    inf = LO_INFILTER.get(src.suffix.lstrip(".").lower())
    if inf:
        cmd.append(f"--infilter={inf}")
    cmd += ["--convert-to", LO_OUT[fam][dst], "--outdir", str(outdir), str(src)]
    _run(cmd, timeout)
    shutil.rmtree(profile, ignore_errors=True)
    ext = dst
    res = Path(outdir) / f"{src.stem}.{ext}"
    if not res.exists():
        raise ConvertError("bad_file", "no output")
    return res


PANDOC_FMT = {"md": "markdown", "html": "html", "docx": "docx", "odt": "odt", "epub": "epub", "fb2": "fb2", "rst": "rst",
              "tex": "latex", "txt": "plain", "rtf": "rtf"}


def pandoc_run(src, dst, outdir, timeout=120):
    src = Path(src)
    sf = PANDOC_FMT[src.suffix.lstrip(".").lower()]
    out = Path(outdir) / f"{src.stem}_pd.{dst}"
    cmd = ["pandoc", "-f", sf, "-t", PANDOC_FMT[dst], "-o", str(out), str(src)]
    if dst in ("epub", "fb2", "html", "docx", "odt"):
        cmd += ["--metadata", f"title={src.stem[:80]}"]
    if dst == "html":
        cmd += ["--standalone", "--embed-resources"] if _pandoc_new() else ["--standalone", "--self-contained"]
    _run(cmd, timeout)
    return out


def _pandoc_new():
    try:
        v = subprocess.run(["pandoc", "--version"], capture_output=True, text=True, timeout=10).stdout.split()[1]
        return tuple(int(x) for x in v.split(".")[:2]) >= (2, 19)
    except Exception:
        return False


def svg_run(src, dst, outdir):
    out = Path(outdir) / f"{Path(src).stem}_svg.{dst}"
    _run(["rsvg-convert", "-f", dst, "-o", str(out), str(src)])
    return out


def djvu_pdf(src, outdir):
    out = Path(outdir) / f"{Path(src).stem}_dj.pdf"
    _run(["ddjvu", "-format=pdf", str(src), str(out)], timeout=150)
    return out


def djvu_txt(src, outdir):
    out = Path(outdir) / f"{Path(src).stem}_dj.txt"
    _run(["djvutxt", str(src), str(out)])
    if not out.exists() or not out.read_text(encoding="utf-8", errors="ignore").strip():
        raise ConvertError("empty", "no text layer")
    return out


def xps_pdf(src, outdir):
    out = Path(outdir) / f"{Path(src).stem}_xps.pdf"
    _run(["xpstopdf", str(src), str(out)])
    return out


# ---------- нормализация форматов

ALIAS = {"jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "htm": "html", "markdown": "md", "heif": "heic", "oxps": "xps",
         "djv": "djvu", "latex": "tex", "text": "txt"}
IMG_IN = {"jpg", "png", "webp", "gif", "bmp", "tiff", "ico", "heic", "avif"}
IMG_OUT = ["png", "jpg", "webp", "gif", "bmp", "tiff", "ico"]


def canon(ext):
    ext = (ext or "").lower().lstrip(".")
    return ALIAS.get(ext, ext)


# что предлагаем пользователю для каждого типа файла (порядок = порядок кнопок)
OFFER = {
    "doc": ["pdf", "docx", "doc", "odt", "rtf", "txt", "html", "epub", "xlsx"],
    "text": ["pdf", "docx", "doc", "odt", "rtf", "txt", "html", "epub", "md"],
    "sheet": ["pdf", "xlsx", "xls", "ods", "csv", "html", "docx"],
    "pres": ["pdf", "pptx", "ppt", "odp", "png", "jpg"],
    "pdf": ["docx", "doc", "odt", "rtf", "txt", "html", "xlsx", "png", "jpg", "epub", "compress", "split", "ocrpdf"],
    "ebook": ["pdf", "docx", "txt", "html", "epub", "fb2", "md", "odt"],
    "image": ["pdf", "ocrpdf", "txt", "docx", "png", "jpg", "webp", "gif", "bmp", "tiff", "ico"],
    "svg": ["png", "jpg", "pdf"],
    "djvu": ["pdf", "png", "txt", "docx"],
    "xps": ["pdf", "docx", "png"],
}
CATEGORY = {}
for _e in "doc docx docm dot dotx dotm odt ott rtf".split():
    CATEGORY[_e] = "doc"
for _e in "txt html md rst tex".split():
    CATEGORY[_e] = "text"
for _e in "xls xlsx xlsm xlsb xlt xltx ods ots csv tsv".split():
    CATEGORY[_e] = "sheet"
for _e in "ppt pptx pptm pps ppsx pot potx odp otp".split():
    CATEGORY[_e] = "pres"
CATEGORY.update({"pdf": "pdf", "epub": "ebook", "fb2": "ebook", "svg": "svg", "djvu": "djvu", "xps": "xps"})
for _e in IMG_IN:
    CATEGORY[_e] = "image"


# ---------- встроенные шаги (чистый Python)

def _n_pdf_docx(src, outdir):
    out = Path(outdir) / f"{Path(src).stem}_n.docx"
    pdf_to_docx(src, out)
    return out


def _n(fn, ext, *pre):
    def run(src, outdir):
        out = Path(outdir) / f"{Path(src).stem}_n.{ext}"
        fn(*pre, src, out) if pre else fn(src, out)
        return out
    return run


def _pdf_images(fmt):
    def run(src, outdir):
        out = Path(outdir) / f"{Path(src).stem}_n.{fmt}"
        pdf_to_images(src, out, fmt)
        return out.with_suffix(".zip") if _is_zip(out) is False and out.with_suffix(".zip").exists() else out
    return run


def pdf_to_images(src, out, fmt):
    doc = _open_pdf(src)
    n = min(doc.page_count, MAX_PDF_PAGES)
    if n == 1:
        pix = doc[0].get_pixmap(dpi=140)
        if fmt == "png":
            pix.save(str(out))
        else:
            Path(out).write_bytes(pix.tobytes("jpg"))
        return
    zp = Path(out).with_suffix(".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
        for i in range(n):
            z.writestr(f"page_{i + 1:02d}.{fmt}", doc[i].get_pixmap(dpi=120).tobytes("png" if fmt == "png" else "jpg"))


def pdf_to_html(src, out):
    doc = _open_pdf(src)
    html = "<meta charset='utf-8'>" + "<hr>".join(p.get_text("html") for p in doc)
    Path(out).write_text(html, encoding="utf-8")


def _from_sheets(loader_ext, fn):
    def run(src, outdir):
        out = Path(outdir) / f"{Path(src).stem}_n.{fn.__name__.split('_to_')[1]}"
        fn(load_sheets(src, loader_ext), out)
        return out
    return run


def _sheet_step(src_ext, dst):
    fn = {"pdf": sheets_to_pdf, "docx": sheets_to_docx, "xlsx": sheets_to_xlsx, "csv": sheets_to_csv}[dst]

    def run(src, outdir):
        out = Path(outdir) / f"{Path(src).stem}_n.{dst}"
        fn(load_sheets(src, src_ext), out)
        if dst == "csv" and _is_zip(out):
            return out.rename(out.with_suffix(".zip"))
        return out
    return run


def _image_step(dst):
    def run(src, outdir):
        out = Path(outdir) / f"{Path(src).stem}_n.{dst}"
        if dst == "pdf":
            image_to_pdf(src, out)
        else:
            image_to_image(src, out, dst)
        return out
    return run


def _register_heif():
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass


def image_to_image(src, out, fmt):
    from PIL import Image
    img = _open_image(src)
    fmt = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF", "ico": "ICO"}[fmt]
    if fmt in ("JPEG", "BMP") and img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, "white")
        base.paste(rgba, mask=rgba.split()[-1])
        img = base
    elif fmt in ("JPEG", "BMP"):
        img = img.convert("RGB")
    elif fmt == "ICO":
        img.thumbnail((256, 256))
    elif fmt == "GIF":
        img = img.convert("P") if img.mode not in ("P", "L") else img
    img.save(str(out), fmt)


def _open_image(src):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    _register_heif()
    try:
        img = Image.open(str(src))
        img.load()
    except Exception as e:
        raise ConvertError("bad_file", str(e)) from e
    return img


# ---------- граф форматов: рёбра = (из, в) -> шаг; цепочки ищем по кратчайшему пути

def build_edges():
    """{(из, в): (стоимость, шаг(src, outdir) -> Path)} по доступным программам."""
    E = {}

    def add(a, b, step, cost=1):
        E[(a, b)] = (cost, step)

    # встроенные
    add("pdf", "docx", _n_pdf_docx)
    add("pdf", "txt", _n(pdf_to_txt, "txt"))
    add("pdf", "xlsx", lambda s, o: _pdf_xlsx(s, o))
    add("pdf", "html", _n(pdf_to_html, "html"))
    add("pdf", "png", _pdf_images("png"))
    add("pdf", "jpg", _pdf_images("jpg"))
    add("docx", "pdf", _n(docx_to_pdf, "pdf"), 2)
    add("docx", "xlsx", lambda s, o: _docx_xlsx(s, o))
    add("docx", "txt", _n(docx_to_txt, "txt"))
    for se in ("xlsx", "xls", "csv"):
        for dst in ("pdf", "docx", "xlsx", "csv"):
            if dst != se:
                add(se, dst, _sheet_step(se, dst), 2 if dst == "pdf" else 1)
    add("txt", "pdf", _n(text_to_pdf, "pdf"), 2)
    add("txt", "docx", _n(text_to_docx, "docx"))
    for ie in IMG_IN:
        add(ie, "pdf", _image_step("pdf"))
        for dst in IMG_OUT:
            if dst != ie:
                add(ie, dst, _image_step(dst))

    add("pdf", "compress", _tool_step(compress_pdf, "pdf"))
    add("pdf", "split", _tool_step(split_pdf, "zip"))
    if tesseract_ok():
        add("pdf", "ocrpdf", _tool_step(ocr_searchable_pdf, "pdf"))
        for ie in IMG_IN:
            add(ie, "ocrpdf", _tool_step(ocr_searchable_pdf, "pdf"))
            add(ie, "txt", _tool_step(ocr_to_txt, "txt"))
            add(ie, "docx", _tool_step(ocr_to_docx, "docx"))

    lo = bool(soffice())
    if lo:
        for src, fam in LO_FAMILY.items():
            for dst in LO_OUT[fam]:
                if dst == src:
                    continue
                if (src, dst) in E and (src in ("csv", "txt") or dst == "csv"):
                    continue  # у встроенных верная кодировка (cp1251) и все листы (zip) при нескольких листах
                add(src, dst, (lambda d: lambda s, o: lo_run(s, d, o))(dst), 1)
    if shutil.which("pandoc"):
        for src in ("md", "html", "docx", "odt", "epub", "fb2", "rst", "tex"):
            for dst in ("docx", "odt", "html", "epub", "fb2", "md", "rst", "tex", "txt"):
                if dst != src and (src, dst) not in E:
                    add(src, dst, (lambda d: lambda s, o: pandoc_run(s, d, o))(dst), 1 if src not in ("docx", "odt") else 2)
        if (("md", "pdf")) not in E and lo:
            pass
    if shutil.which("rsvg-convert"):
        add("svg", "png", lambda s, o: svg_run(s, "png", o))
        add("svg", "pdf", lambda s, o: svg_run(s, "pdf", o))
    if shutil.which("ddjvu"):
        add("djvu", "pdf", djvu_pdf)
    if shutil.which("djvutxt"):
        add("djvu", "txt", djvu_txt)
    if shutil.which("xpstopdf"):
        add("xps", "pdf", xps_pdf)
    return E


def _tool_step(fn, ext):
    def run(src, outdir):
        out = Path(outdir) / f"{Path(src).stem}_t.{ext}"
        res = fn(src, out)
        return Path(res) if res else out
    return run


def _pdf_xlsx(src, outdir):
    out = Path(outdir) / f"{Path(src).stem}_n.xlsx"
    sheets_to_xlsx(pdf_to_sheets(src), out)
    return out


def _docx_xlsx(src, outdir):
    out = Path(outdir) / f"{Path(src).stem}_n.xlsx"
    sheets_to_xlsx(docx_tables(src), out)
    return out


def find_path(src, dst, edges=None, max_steps=3):
    """Кратчайшая цепочка форматов src → … → dst или None."""
    edges = build_edges() if edges is None else edges
    import heapq
    best = {src: 0}
    heap = [(0, 0, src, [])]
    while heap:
        cost, steps, cur, path = heapq.heappop(heap)
        if cur == dst:
            return path
        if steps >= max_steps or cost > best.get(cur, 1e9):
            continue
        for (a, b), (c, _) in edges.items():
            if a != cur or b in (p[1] for p in path) or b == src:
                continue
            # картинки из PDF — всегда в конце цепочки (при нескольких страницах это zip)
            if cur in ("png", "jpg") and path and path[-1][0] == "pdf":
                continue
            nc = cost + c
            if nc < best.get(b, 1e9):
                best[b] = nc
                heapq.heappush(heap, (nc, steps + 1, b, path + [(cur, b)]))
    return None


def supported_inputs():
    """Какие форматы принимаем (по доступным программам)."""
    edges = build_edges()
    return sorted(e for e in CATEGORY if any(find_path(e, t, edges) for t in OFFER[CATEGORY[e]] if t != e))


def targets(ext, lo=None):
    """Форматы, в которые можно превратить файл с таким расширением (по доступным программам)."""
    ext = canon(ext)
    cat = CATEGORY.get(ext)
    if not cat:
        return []
    edges = build_edges()
    return [t for t in OFFER[cat] if t != ext and find_path(ext, t, edges)]


def convert(src, src_ext, dst_ext, outdir):
    """Конвертирует src в outdir и возвращает путь к результату."""
    src, outdir = Path(src), Path(outdir)
    src_ext, dst_ext = canon(src_ext), canon(dst_ext)
    edges = build_edges()
    if CATEGORY.get(src_ext) is None or dst_ext not in OFFER[CATEGORY[src_ext]]:
        raise ConvertError("unsupported")
    path = find_path(src_ext, dst_ext, edges)
    if not path:
        raise ConvertError("unsupported")
    cur = src
    for i, (a, b) in enumerate(path):
        step = edges[(a, b)][1]
        stepdir = outdir / f"step{i}"
        stepdir.mkdir(exist_ok=True)
        if cur.suffix.lstrip(".").lower() != a:  # шаги определяют формат по расширению
            alias = stepdir / f"in.{a}"
            shutil.copy(cur, alias)
            cur = alias
        else:
            alias = stepdir / f"in.{a}"
            shutil.copy(cur, alias)
            cur = alias
        cur = Path(step(cur, stepdir))
        if not cur.exists() or cur.stat().st_size == 0:
            raise ConvertError("bad_file", "empty output")
    final = outdir / f"result{cur.suffix}"
    shutil.move(str(cur), final)
    return final


def _is_zip(p):
    try:
        return zipfile.is_zipfile(p)
    except OSError:
        return False
