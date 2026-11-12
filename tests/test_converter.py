import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "converter"))
import converters as cv  # noqa: E402


def pdf_text(path):
    import pymupdf
    return "".join(p.get_text() for p in pymupdf.open(str(path)))


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def make_docx(self):
        import docx
        p = self.d / "in.docx"
        d = docx.Document()
        d.add_heading("Счёт-фактура № 15", 1)
        d.add_paragraph("Оплата за услуги, Toshkent. Сумма 1 250 000 сум.")
        t = d.add_table(rows=3, cols=3)
        t.style = "Table Grid"
        for i, row in enumerate([["Товар", "Кол-во", "Цена"], ["Самса", "10", "12000"], ["Чай", "2", "10000"]]):
            for j, v in enumerate(row):
                t.cell(i, j).text = v
        d.save(p)
        return p

    def make_xlsx(self):
        import openpyxl
        p = self.d / "in.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Продажи"
        ws.append(["Товар", "Кол-во", "Цена"])
        ws.append(["Самса", 10, 12000.0])
        ws.append(["Чай", 2, 10000.5])
        wb.create_sheet("Второй").append(["a", "b"])
        wb.save(p)
        return p

    def make_pdf_with_table(self):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
        from reportlab.lib.styles import ParagraphStyle
        cv._font()
        p = self.d / "in.pdf"
        t = Table([["Товар", "Кол-во"], ["Самса", "10"], ["Чай", "2"]])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black), ("FONT", (0, 0), (-1, -1), "DJ")]))
        SimpleDocTemplate(str(p), pagesize=A4).build([Paragraph("Накладная", ParagraphStyle("x", fontName="DJ", fontSize=14)), t])
        return p


class Conversions(Base):
    def test_docx_to_pdf_keeps_cyrillic_and_table(self):
        out = cv.convert(self.make_docx(), "docx", "pdf", self.d)
        txt = pdf_text(out)
        self.assertTrue(out.read_bytes().startswith(b"%PDF"))
        self.assertIn("Счёт-фактура", txt)
        self.assertIn("Самса", txt)

    def test_docx_to_xlsx_and_txt(self):
        import openpyxl
        x = cv.convert(self.make_docx(), "docx", "xlsx", self.d)
        ws = openpyxl.load_workbook(x).worksheets[0]
        self.assertEqual([c.value for c in ws[2]], ["Самса", 10, 12000])
        t = cv.convert(self.d / "in.docx", "docx", "txt", self.d)
        self.assertIn("Оплата за услуги", t.read_text(encoding="utf-8"))

    def test_xlsx_to_pdf_docx_csv(self):
        src = self.make_xlsx()
        self.assertIn("Самса", pdf_text(cv.convert(src, "xlsx", "pdf", self.d)))
        import docx
        d = docx.Document(str(cv.convert(src, "xlsx", "docx", self.d)))
        self.assertEqual(d.tables[0].cell(1, 0).text, "Самса")
        out = cv.convert(src, "xlsx", "csv", self.d)  # два листа → zip
        self.assertEqual(out.suffix, ".zip")
        self.assertEqual(len(zipfile.ZipFile(out).namelist()), 2)

    def test_pdf_to_docx_roundtrip(self):
        import docx
        out = cv.convert(self.make_pdf_with_table(), "pdf", "docx", self.d)
        d = docx.Document(str(out))
        text = "".join(t.text or "" for t in d.element.body.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        self.assertIn("Накладная", text)
        self.assertIn("Самса", text)

    def test_pdf_to_xlsx_table(self):
        import openpyxl
        out = cv.convert(self.make_pdf_with_table(), "pdf", "xlsx", self.d)
        rows = [[c.value for c in r] for ws in openpyxl.load_workbook(out).worksheets for r in ws.iter_rows()]
        self.assertIn(["Самса", 10], rows)

    def test_pdf_to_txt_and_png(self):
        pdf = self.make_pdf_with_table()
        self.assertIn("Накладная", cv.convert(pdf, "pdf", "txt", self.d).read_text(encoding="utf-8"))
        png = cv.convert(pdf, "pdf", "png", self.d)
        self.assertEqual(png.read_bytes()[:4], b"\x89PNG")

    def test_csv_and_txt_and_images(self):
        import openpyxl
        c = self.d / "in.csv"
        c.write_text("Имя;Сумма\nАли;1500\nВали;12,5\n", encoding="utf-8")
        ws = openpyxl.load_workbook(cv.convert(c, "csv", "xlsx", self.d)).worksheets[0]
        self.assertEqual([x.value for x in ws[2]], ["Али", 1500])
        self.assertEqual(ws["B3"].value, 12.5)
        t = self.d / "in.txt"
        t.write_text("Привет\n\nМир", encoding="utf-8")
        self.assertIn("Привет", pdf_text(cv.convert(t, "txt", "pdf", self.d)))
        from PIL import Image
        Image.new("RGBA", (40, 30), (255, 0, 0, 128)).save(self.d / "a.png")
        self.assertTrue(cv.convert(self.d / "a.png", "png", "pdf", self.d).read_bytes().startswith(b"%PDF"))
        self.assertEqual(cv.convert(self.d / "a.png", "png", "jpg", self.d).read_bytes()[:2], b"\xff\xd8")

    def test_old_xls_reads(self):
        if not cv.soffice():
            self.skipTest("нет LibreOffice, чтобы создать .xls для теста")
        import subprocess
        src = self.make_xlsx()
        subprocess.run([cv.soffice(), "--headless", "--convert-to", "xls", "--outdir", str(self.d), str(src)],
                       check=True, capture_output=True, timeout=120)
        out = cv.convert(self.d / "in.xls", "xls", "xlsx", self.d)
        import openpyxl
        ws = openpyxl.load_workbook(out).worksheets[0]
        self.assertEqual(ws["A2"].value, "Самса")


class Rules(Base):
    def test_targets(self):
        self.assertEqual(cv.targets("pdf", lo=False), ["docx", "xlsx", "txt", "png"])
        self.assertEqual(cv.targets("DOCX", lo=False), ["pdf", "xlsx", "txt"])
        self.assertEqual(cv.targets("jpeg", lo=False), ["pdf", "png"])
        self.assertEqual(cv.targets("doc", lo=False), [])
        self.assertIn("docx", cv.targets("doc", lo=True))
        self.assertEqual(cv.targets("exe", lo=True), [])

    def test_errors(self):
        bad = self.d / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        with self.assertRaises(cv.ConvertError) as e:
            cv.convert(bad, "pdf", "docx", self.d)
        self.assertEqual(e.exception.code, "bad_file")
        with self.assertRaises(cv.ConvertError) as e:
            cv.convert(self.make_docx(), "docx", "png", self.d)
        self.assertEqual(e.exception.code, "unsupported")
        empty = self.d / "e.csv"
        empty.write_text("", encoding="utf-8")
        with self.assertRaises(cv.ConvertError) as e:
            cv.convert(empty, "csv", "xlsx", self.d)
        self.assertEqual(e.exception.code, "empty")

    def test_cli_exit_codes(self):
        import subprocess
        cli = str(Path(cv.__file__).with_name("cli.py"))
        t = self.d / "a.txt"
        t.write_text("hi", encoding="utf-8")
        r = subprocess.run([sys.executable, cli, str(t), "txt", "pdf", str(self.d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(Path(r.stdout.strip()).exists())
        r = subprocess.run([sys.executable, cli, str(t), "txt", "png", str(self.d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.startswith("unsupported"))


if __name__ == "__main__":
    unittest.main()
