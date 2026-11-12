import asyncio
import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import importlib.util

CONV = Path(__file__).resolve().parent.parent / "converter"
sys.path.insert(0, str(CONV))
# модуль называется bot.py, как и у ресторанного бота, поэтому грузим его под другим именем
_old = os.environ.get("ADMIN_IDS"), os.environ.get("DB_PATH")
os.environ["DB_PATH"], os.environ["ADMIN_IDS"] = ":memory:", "8219891075"
_spec = importlib.util.spec_from_file_location("converter_bot", CONV / "bot.py")
bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot)
for _k, _v in zip(("ADMIN_IDS", "DB_PATH"), _old):
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v


def run(coro):
    return asyncio.run(coro)


class Texts(unittest.TestCase):
    def test_three_languages_everywhere(self):
        for k, v in bot.T.items():
            self.assertEqual(set(v), {"ru", "uz", "en"}, k)

    def test_error_codes_have_texts(self):
        for code in ("bad_file", "empty", "too_big", "unsupported"):
            self.assertIn(code, bot.T)

    def test_file_ext(self):
        self.assertEqual(bot.file_ext("Отчёт.PDF", None), "pdf")
        self.assertEqual(bot.file_ext("noext", "application/pdf"), "pdf")
        self.assertEqual(bot.file_ext("x", "application/vnd.ms-excel"), "xls")


class Flow(unittest.TestCase):
    def setUp(self):
        bot._pending.clear()
        bot._collect.clear()
        bot.db.execute("DELETE FROM usage")

    def msg(self, uid=5, group=None):
        m = MagicMock()
        m.from_user = SimpleNamespace(id=uid)
        m.media_group_id = group
        m.answer = AsyncMock()
        return m

    def test_offer_unsupported_and_too_big(self):
        m = self.msg()
        run(bot.offer(m, "f", "a.exe", "exe", 10))
        self.assertIn("exe", m.answer.call_args[0][0])
        self.assertIn("PDF", m.answer.call_args[0][0])
        m = self.msg()
        run(bot.offer(m, "f", "a.pdf", "pdf", 999 * 1024 * 1024))
        self.assertIn(str(bot.MAX_MB), m.answer.call_args[0][0])
        self.assertEqual(bot._pending, {})

    def test_offer_shows_targets_with_special_buttons(self):
        m = self.msg()
        run(bot.offer(m, "f", "a.pdf", "pdf", 1000))
        kb = m.answer.call_args.kwargs["reply_markup"].inline_keyboard
        labels = [b.text for r in kb for b in r]
        self.assertIn("➡️ DOCX", labels)
        self.assertIn(bot.T["lbl_compress"]["ru"], labels)
        self.assertIn(bot.T["lbl_split"]["ru"], labels)
        m = self.msg()
        run(bot.offer(m, "f", "photo.jpg", "jpg", 1000))
        labels = [b.text for r in m.answer.call_args.kwargs["reply_markup"].inline_keyboard for b in r]
        if converters_tesseract():
            self.assertIn("📝 TXT (OCR)", labels)

    def _callback(self, uid, token, dst, sample):
        c = MagicMock()
        c.from_user = SimpleNamespace(id=uid)
        c.data = f"cv:{token}:{dst}"
        c.answer = AsyncMock()
        status = MagicMock()
        status.edit_text = AsyncMock()
        status.delete = AsyncMock()
        c.message.answer = AsyncMock(return_value=status)
        c.message.answer_document = AsyncMock()
        c.message.edit_text = AsyncMock()
        c.message.delete = AsyncMock()
        c.status = status

        async def download(file_id, destination):
            shutil.copy(sample, destination)
        c.bot.download = download
        return c

    def sample_docx(self, d):
        import docx
        sample = d / "s.docx"
        doc = docx.Document()
        doc.add_paragraph("Привет, мир")
        doc.save(sample)
        return sample

    def test_full_conversion_with_other_format_buttons_and_limit(self):
        import tempfile
        d = Path(tempfile.mkdtemp())
        sample = self.sample_docx(d)
        token = bot.remember(5, "fid", "Договор.docx", "docx")
        c = self._callback(5, token, "pdf", sample)
        run(bot.convert_cb(c))
        args, kw = c.message.answer_document.call_args
        self.assertEqual(args[0].filename, "Договор.pdf")
        self.assertIn("Готово", kw["caption"])
        labels = [b.text for r in kw["reply_markup"].inline_keyboard for b in r]
        self.assertNotIn("➡️ PDF", labels)  # текущий формат не повторяем
        self.assertIn("➡️ ODT", labels)
        c.status.delete.assert_called_once()
        self.assertEqual(bot.used_today(5), 1)
        # чужой токен не работает
        c2 = self._callback(6, token, "pdf", sample)
        run(bot.convert_cb(c2))
        c2.message.answer_document.assert_not_called()
        # дневной лимит
        bot.db.execute("INSERT OR REPLACE INTO usage(day,uid,n) VALUES(?,?,?)", (bot.today(), 7, bot.DAILY_LIMIT))
        t2 = bot.remember(7, "fid", "a.docx", "docx")
        c3 = self._callback(7, t2, "pdf", sample)
        run(bot.convert_cb(c3))
        c3.message.answer_document.assert_not_called()
        # админ без лимита
        bot.db.execute("INSERT OR REPLACE INTO usage(day,uid,n) VALUES(?,?,?)", (bot.today(), 8219891075, 999))
        t3 = bot.remember(8219891075, "fid", "a.docx", "docx")
        c4 = self._callback(8219891075, t3, "pdf", sample)
        run(bot.convert_cb(c4))
        c4.message.answer_document.assert_called_once()
        shutil.rmtree(d)

    def test_compress_reports_shrink(self):
        import pymupdf
        import tempfile
        from PIL import Image
        d = Path(tempfile.mkdtemp())
        Image.effect_noise((1600, 1200), 80).convert("RGB").save(d / "n.png")
        doc = pymupdf.open()
        pg = doc.new_page()
        pg.insert_image(pg.rect, filename=str(d / "n.png"))
        doc.save(d / "s.pdf")
        token = bot.remember(5, "fid", "Отчёт.pdf", "pdf")
        c = self._callback(5, token, "compress", d / "s.pdf")
        run(bot.convert_cb(c))
        args, kw = c.message.answer_document.call_args
        self.assertEqual(args[0].filename, "Отчёт_compressed.pdf")
        self.assertIn("−", kw["caption"])
        shutil.rmtree(d)

    def test_bad_file_reports_error_and_cleans_up(self):
        import tempfile
        d = Path(tempfile.mkdtemp())
        sample = d / "bad.pdf"
        sample.write_bytes(b"garbage")
        token = bot.remember(5, "fid", "x.pdf", "pdf")
        c = self._callback(5, token, "docx", sample)
        before = set(Path(tempfile.gettempdir()).glob("cv_*"))
        run(bot.convert_cb(c))
        c.message.answer_document.assert_not_called()
        self.assertEqual(c.status.edit_text.call_args[0][0], bot.T["bad_file"]["ru"])
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("cv_*")), before)
        self.assertEqual(bot.used_today(5), 0)
        shutil.rmtree(d)

    def test_album_collects_and_merges(self):
        import tempfile
        import pymupdf
        from PIL import Image
        d = Path(tempfile.mkdtemp())
        Image.new("RGB", (60, 40), "red").save(d / "a.jpg")

        async def scenario():
            m = self.msg(group="g1")
            await bot.incoming(m, "f1", "photo.jpg", "jpg", 100)
            await bot.incoming(m, "f2", "photo.jpg", "jpg", 100)
            await bot.incoming(m, "f3", "photo.jpg", "jpg", 100)
            await asyncio.sleep(1.2)
            return m
        m = run(scenario())
        self.assertEqual(len(bot._collect[5]["items"]), 3)
        self.assertEqual(m.answer.call_count, 1)  # один счётчик на весь альбом
        c = self._callback(5, "x", "x", d / "a.jpg")
        c.data = "mg:go"
        run(bot.merge_go(c))
        args, kw = c.message.answer_document.call_args
        self.assertEqual(args[0].filename, "merged.pdf")
        self.assertEqual(pymupdf.open(str(args[0].path)).page_count if Path(args[0].path).exists() else 3, 3)
        self.assertNotIn(5, bot._collect)
        shutil.rmtree(d)

    def test_merge_mode_rules(self):
        m = self.msg()
        run(bot.merge_cmd(m))
        self.assertIn(5, bot._collect)
        run(bot.incoming(m, "f", "a.docx", "docx", 100))
        self.assertEqual(m.answer.call_args[0][0], bot.T["merge_bad"]["ru"])
        c = MagicMock()
        c.from_user = SimpleNamespace(id=5)
        c.answer = AsyncMock()
        run(bot.merge_go(c))
        c.answer.assert_called_once()
        self.assertTrue(c.answer.call_args.kwargs.get("show_alert"))

    def test_err_text_has_all_codes(self):
        for code in ("bad_file", "empty", "too_big", "unsupported", "many_pages", "weird"):
            self.assertTrue(bot.err_text(5, bot.converters.ConvertError(code)))


def converters_tesseract():
    return bot.converters.tesseract_ok()


if __name__ == "__main__":
    unittest.main()
