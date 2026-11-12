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
        bot.db.execute("DELETE FROM usage")

    def msg(self, uid=5):
        m = MagicMock()
        m.from_user = SimpleNamespace(id=uid)
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

    def test_offer_shows_targets(self):
        m = self.msg()
        run(bot.offer(m, "f", "a.pdf", "pdf", 1000))
        kb = m.answer.call_args.kwargs["reply_markup"].inline_keyboard
        labels = [b.text for r in kb for b in r]
        self.assertIn("➡️ DOCX", labels)
        self.assertIn("➡️ XLSX", labels)

    def _callback(self, uid, token, dst, sample):
        c = MagicMock()
        c.from_user = SimpleNamespace(id=uid)
        c.data = f"cv:{token}:{dst}"
        c.answer = AsyncMock()
        c.message.edit_text = AsyncMock()
        c.message.answer_document = AsyncMock()
        c.message.delete = AsyncMock()

        async def download(file_id, destination):
            shutil.copy(sample, destination)
        c.bot.download = download
        return c

    def test_full_conversion_and_limit(self):
        import docx
        import tempfile
        d = Path(tempfile.mkdtemp())
        sample = d / "s.docx"
        doc = docx.Document()
        doc.add_paragraph("Привет, мир")
        doc.save(sample)
        token = bot.remember(5, "fid", "Договор.docx", "docx")
        c = self._callback(5, token, "pdf", sample)
        run(bot.convert_cb(c))
        sent = c.message.answer_document.call_args[0][0]
        self.assertEqual(sent.filename, "Договор.pdf")
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
        self.assertEqual(c.message.edit_text.call_args[0][0], bot.T["bad_file"]["ru"])
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("cv_*")), before)
        self.assertEqual(bot.used_today(5), 0)
        shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
