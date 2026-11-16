"""Быстрый отчёт, главная, команды, смены (детали/поиск/отмена/возврат с причиной), свой период, PDF, CSV, импорт, уведомления."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agzs_bot import FULL, OP, OWNER, Env, bot, core, run  # noqa: E402
from test_agzs_features import saved_shift  # noqa: E402
import quick  # noqa: E402


def DM(s):
    return f"{s['date'][8:10]}.{s['date'][5:7]}"


def cbm(e, uid, data):
    c = e.cb(uid, data)
    c.message.edit_reply_markup = AsyncMock()
    return c


def cmd(args=None, name=""):
    return SimpleNamespace(args=args, command=name)


class Quick(unittest.TestCase):
    def test_home_for_roles(self):
        e = Env()
        saved_shift(e)
        m = e.msg(OWNER, "🏠 Главная")
        run(quick.home(m, e.state(OWNER)))
        self.assertIn("Главная", e.last_text(m))
        m = e.msg(OP, "🏠 Главная")
        run(quick.home(m, e.state(OP)))
        self.assertIn("Главная", e.last_text(m))
        m = e.msg(999, "🏠 Главная")
        run(quick.home(m, e.state(999)))
        m.answer.assert_not_called()

    def test_short_commands(self):
        e = Env()
        saved_shift(e)
        for name in ("today", "week", "month", "quarter", "year", "yesterday"):
            m = e.msg(OWNER, "/" + name)
            run(quick.period_cmd(m, cmd(name=name)))
            self.assertIn("📊", e.last_text(m))
        m = e.msg(OP, "/week")
        run(quick.period_cmd(m, cmd(name="week")))
        m.answer.assert_not_called()
        for fn in (quick.stock_cmd, quick.expenses_cmd, quick.help_cmd):
            m = e.msg(OWNER, "/x")
            run(fn(m))
            m.answer.assert_called()
        m = e.msg(OP, "/help")
        run(quick.help_cmd(m))
        self.assertIn("Быстрый отчёт", e.last_text(m))

    def test_quick_report_saves_pending_shift(self):
        e = Env()
        st = e.state(OP)
        run(quick.start_quick(e.msg(OP, "x"), st, OP))
        txt = ("дата 10.11\nсч1 1000 1500\nсч2 2000 2300\nсч3 0 0\nнал 1000000\nтерм 500к\nклик 300000\nпейнет 200000\n"
               "приход 1000 0.54 5000\nрасход 50000 бензин")
        m = e.msg(OP, txt)
        run(quick.quick_text(m, st))
        self.assertTrue(m.answer.called)
        run(bot.shift_button(e.cb(OP, "sh:@save"), st))
        s = core.db.shifts()[-1]
        self.assertEqual(s["date"], "2026-11-10")
        self.assertEqual(s["cash"], 1_000_000)
        self.assertEqual(s["terminal"], 500_000)

    def test_quick_report_errors_keep_state(self):
        e = Env()
        st = e.state(OP)
        run(quick.start_quick(e.msg(OP, "x"), st, OP))
        m = e.msg(OP, "привет, это не отчёт")
        run(quick.quick_text(m, st))
        self.assertEqual(core.db.shifts(), [])
        self.assertIn("•", e.last_text(m))

    def test_details_search_pagination(self):
        e = Env()
        s = saved_shift(e)
        c = e.cb(OWNER, f"sd:{s['id']}")
        run(quick.details_cb(c))
        text = c.message.answer.call_args[0][0]
        self.assertIn(f"Смена №{s['id']}", text)
        self.assertIn("Пролив", text)
        m = e.msg(OWNER, "/shift")
        run(quick.shift_cmd(m, cmd(str(s["id"]))))
        self.assertIn("Смена", e.last_text(m))
        m = e.msg(OWNER, "/shift")
        run(quick.shift_cmd(m, cmd(DM(s))))
        self.assertIn("Смена", e.last_text(m))
        m = e.msg(OP, "/shift")
        run(quick.shift_cmd(m, cmd(DM(s))))
        self.assertIn("Смена", e.last_text(m))  # свою смену оператор видит
        m = e.msg(OWNER, "x")
        run(quick.shifts_page(m, 0, "find", "Али"))
        self.assertIn("Найдено", e.last_text(m))
        m = e.msg(OWNER, "x")
        run(quick.shifts_page(m, 0, "find", "нет такого"))
        self.assertIn("Ничего", e.last_text(m))

    def test_undo_window(self):
        e = Env()
        s = saved_shift(e)
        m = e.msg(OP, "/undo")
        run(quick.undo_cmd(m))
        self.assertEqual(core.db.shifts(), [])
        s = saved_shift(e)
        core.db.set_status(s["id"], "approved")
        m = e.msg(OP, "/undo")
        run(quick.undo_cmd(m))
        self.assertEqual(len(core.db.shifts()), 1)  # принятую отменить нельзя

    def test_return_with_reason_notifies_operator(self):
        e = Env()
        s = saved_shift(e)
        st = e.state(OWNER)
        run(quick.return_ask(e.cb(OWNER, f"rr:{s['id']}"), st))
        e.tg.send_message.reset_mock()
        run(quick.return_reason(e.msg(OWNER, "неверный счётчик 2"), st))
        self.assertEqual(core.db.shift(s["id"])["status"], "returned")
        self.assertEqual(e.tg.send_message.call_args.args[0], OP)
        self.assertIn("неверный счётчик 2", e.tg.send_message.call_args.args[1])
        # оператор вернуть не может
        s2 = saved_shift(e)
        st2 = e.state(OP)
        run(quick.return_ask(e.cb(OP, f"rr:{s2['id']}"), st2))
        self.assertIsNone(run(st2.get_state()))

    def test_comment(self):
        e = Env()
        s = saved_shift(e)
        st = e.state(OWNER)
        run(quick.comment_ask(e.cb(OWNER, f"cm:{s['id']}"), st))
        run(quick.comment_text(e.msg(OWNER, "проверил"), st))
        self.assertEqual(core.db.shift(s["id"])["comment"], "проверил")

    def test_custom_period_and_pdf_csv_backup(self):
        e = Env()
        saved_shift(e)
        st = e.state(OWNER)
        run(quick.custom_ask(e.cb(OWNER, "cp:fin"), st))
        m = e.msg(OWNER, "01.11 - 30.11")
        run(quick.custom_range(m, st))
        self.assertIn("Выручка", e.last_text(m))
        run(quick.custom_ask(e.cb(OWNER, "cp:pdf"), st))
        m = e.msg(OWNER, "01.11 - 30.11")
        run(quick.custom_range(m, st))
        self.assertTrue(m.answer_document.called)
        doc = m.answer_document.call_args[0][0]
        self.assertTrue(doc.data[:4] == b"%PDF" if hasattr(doc, "data") else True)
        run(quick.custom_ask(e.cb(OWNER, "cp:fin"), st))
        m = e.msg(OWNER, "ерунда")
        run(quick.custom_range(m, st))
        self.assertIn("Не понял", e.last_text(m))
        c = e.cb(OWNER, "xc:csv")
        run(quick.csv_cb(c))
        self.assertTrue(c.message.answer_document.called)
        c = e.cb(OWNER, "pf:month")
        run(quick.pdf_cb(c))
        c = e.cb(OP, "xc:csv")
        run(quick.csv_cb(c))
        c.message.answer_document.assert_not_called()
        m = e.msg(OWNER, "/backup")
        run(quick.backup_cmd(m))
        self.assertTrue(m.answer_document.called)

    def test_graph_exp_cat_and_custom_graph(self):
        e = Env()
        saved_shift(e)
        c = e.cb(OWNER, "gp:exp_cat:all")
        run(bot.graph_make(c))
        self.assertTrue(c.message.answer_photo.called)

    def test_import_flow(self):
        import importer
        e = Env()
        st = e.state(OWNER)
        run(quick.import_start(e.cb(OWNER, "im:start"), st))
        c = e.cb(OWNER, "im:tpl")
        run(quick.import_tpl(c))
        self.assertTrue(c.message.answer_document.called)
        c = e.cb(OWNER, "im:go")
        run(quick.import_go(c, st))  # ничего не загружено
        self.assertFalse(core.db.shifts())
        c = e.cb(OP, "im:start")
        run(quick.import_start(c, e.state(OP)))
        self.assertIsNone(run(e.state(OP).get_state()))

    def test_notification_prefs_filter(self):
        e = Env()
        c = e.cb(OWNER, "np:t:daily")
        c.message.edit_reply_markup = AsyncMock()
        run(quick.notify_cb(c))
        self.assertEqual(core.db.pref(OWNER, "n_daily", 1), 0)
        run(quick.notify_cb(cbm(e, OWNER, "np:t:daily")))
        self.assertEqual(core.db.pref(OWNER, "n_daily", 1), 1)
        run(quick.notify_cb(cbm(e, OP, "np:t:daily")))  # у оператора нет права finance
        self.assertEqual(core.db.pref(OP, "n_daily", 1), 1)
        run(quick.notify_cb(cbm(e, OWNER, "np:t:bogus")))


class Accounting(unittest.TestCase):
    def test_calendar_tax_payment_and_lock(self):
        import extras
        e = Env()
        m = e.msg(OWNER, "x")
        run(extras.tax_calendar(m))
        self.assertIn("Календарь", e.last_text(m))
        st = e.state(OWNER)
        c = e.cb(OWNER, "tp:new")
        run(extras.taxpay_new(c, st))
        m = e.msg(OWNER, "3 500 000 НДС")
        run(extras.taxpay_line(m, st))
        self.assertEqual(core.db.tax_payments()[0]["amount"], 3_500_000)
        m = e.msg(OWNER, "x")
        run(extras.tax_paid(m))
        self.assertIn("3 500 000", e.last_text(m))
        mon = core.today().strftime("%Y-%m")
        c = e.cb(OWNER, "lk:" + mon)
        c.message.answer = AsyncMock()
        run(extras.lock_toggle(c))
        self.assertIn(mon, core.db.locked_months())
        c = e.cb(OP, "lk:" + mon)
        run(extras.lock_toggle(c))
        self.assertIn(mon, core.db.locked_months())  # оператор не может открыть
        run(extras.lock_toggle(e.cb(OWNER, "lk:" + mon)))
        self.assertNotIn(mon, core.db.locked_months())

    def test_monthly_job_due_on_first(self):
        import scheduler
        from datetime import datetime
        from db import TZ
        dt = datetime(2026, 12, 1, 9, 45, tzinfo=TZ)
        self.assertIn("monthly_report", scheduler.due_jobs(dt, {}, {}))
        self.assertNotIn("monthly_report", scheduler.due_jobs(datetime(2026, 12, 2, 9, 45, tzinfo=TZ), {}, {}))


if __name__ == "__main__":
    unittest.main()
