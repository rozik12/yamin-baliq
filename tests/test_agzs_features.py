"""Новые функции бота АГЗС: узбекский, принять/вернуть смену, правка, фото, сверка замера, раздел «Бухгалтерия», планировщик."""
import asyncio
import io
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_agzs_bot as T  # noqa: E402
from test_agzs_bot import FULL, OP, OWNER, Env, bot, core, extras, run, scheduler  # noqa: E402
from db import TZ  # noqa: E402


def saved_shift(e, inputs=FULL, uid=OP):
    e.fill_shift(uid, inputs)
    run(bot.shift_button(e.cb(uid, "sh:@save"), e.state(uid)))
    return core.db.shifts()[-1]


class Uzbek(unittest.TestCase):
    def test_operator_flow_in_uzbek(self):
        e = Env()
        core.db.set_lang(OP, "uz")
        self.assertEqual(core.lang(OP), "uz")
        self.assertEqual(core.lang(OWNER), "ru")  # у владельца всегда русский
        kb = [b.text for r in bot.menu(OP).keyboard for b in r]
        self.assertIn("➕ Smena hisoboti", kb)
        st = e.state(OP)
        m = e.msg(OP, "➕ Smena hisoboti")
        run(bot.shift_start(m, st))
        self.assertIn("-qadam", e.last_text(m))
        self.assertIn("sanasi", e.last_text(m))
        run(bot.shift_button(e.cb(OP, "sh:today"), st))
        m2 = e.msg(OP, "1000")
        run(bot.shift_text(m2, st))
        bad = e.msg(OP, "500")
        run(bot.shift_text(bad, st))
        self.assertIn("kichik", e.last_text(bad))
        word = e.msg(OP, "abc")
        run(bot.shift_text(word, st))
        self.assertIn("Raqam kerak", e.last_text(word))

    def test_summary_saved_and_mine_in_uzbek(self):
        e = Env()
        core.db.set_lang(OP, "uz")
        e.fill_shift(OP, FULL)
        c = e.cb(OP, "sh:@save")
        run(bot.shift_button(c, e.state(OP)))
        text = c.message.edit_text.call_args[0][0]
        self.assertIn("smena hisoboti", text)
        self.assertIn("Jami quyilgan", text)
        self.assertNotIn("НДС", text)
        self.assertTrue(any("rahmat" in call.args[0] for call in c.message.answer.call_args_list))
        m = e.msg(OP, "📋 Mening smenalarim")
        run(bot.my_shifts(m))
        self.assertIn("Oxirgi smenalaringiz", e.last_text(m))
        self.assertIn("so'm", e.last_text(m))

    def test_language_switch(self):
        e = Env()
        c = e.cb(OP, "lg:uz")
        run(bot.lang_set(c))
        self.assertEqual(core.lang(OP), "uz")
        self.assertIn("o'zbekcha", c.message.answer.call_args[0][0])
        c = e.cb(OP, "lg:ru")
        run(bot.lang_set(c))
        self.assertEqual(core.lang(OP), "ru")
        c = e.cb(999, "lg:uz")
        run(bot.lang_set(c))  # посторонний: ничего не меняется
        self.assertEqual(core.db.get_lang(999), "ru")

    def test_all_texts_have_both_languages(self):
        for k, v in T.bot.texts.TX.items():
            self.assertEqual(set(v), {"ru", "uz"}, k)
            import re
            self.assertEqual(set(re.findall(r"{(\w+)}", v["ru"])), set(re.findall(r"{(\w+)}", v["uz"])), k)


class Approval(unittest.TestCase):
    def test_pending_then_approve_and_return(self):
        e = Env()
        s = saved_shift(e)
        self.assertEqual(s["status"], "pending")
        calls = [c for c in e.tg.send_message.call_args_list if c.args[0] == OWNER]
        self.assertTrue(calls)
        kb = calls[0].kwargs["reply_markup"].inline_keyboard
        self.assertEqual([b.callback_data for b in kb[0]], [f"ap:{s['id']}:ok", f"rr:{s['id']}"])
        e.tg.send_message.reset_mock()
        run(bot.approve_cb(e.cb(OWNER, f"ap:{s['id']}:ok")))
        self.assertEqual(core.db.shift(s["id"])["status"], "approved")
        self.assertEqual(e.tg.send_message.call_args.args[0], OP)
        self.assertIn("принята", e.tg.send_message.call_args.args[1])
        run(bot.approve_cb(e.cb(OWNER, f"ap:{s['id']}:ret")))
        self.assertEqual(core.db.shift(s["id"])["status"], "returned")
        self.assertIn("возвращена", e.tg.send_message.call_args.args[1])
        run(bot.approve_cb(e.cb(OP, f"ap:{s['id']}:ok")))  # оператору нельзя
        self.assertEqual(core.db.shift(s["id"])["status"], "returned")
        self.assertIn("shift_approved", [x["action"] for x in core.db.audit()])

    def test_operator_notified_in_uzbek_and_admin_shift_is_approved(self):
        e = Env()
        core.db.set_lang(OP, "uz")
        s = saved_shift(e)
        e.tg.send_message.reset_mock()
        run(bot.approve_cb(e.cb(OWNER, f"ap:{s['id']}:ok")))
        self.assertIn("qabul qilindi", e.tg.send_message.call_args.args[1])
        s2 = saved_shift(e, uid=OWNER)
        self.assertEqual(s2["status"], "approved")

    def test_finance_shows_pending(self):
        e = Env()
        saved_shift(e)
        c = e.cb(OWNER, "fp:all")
        run(bot.fin_cb(c))
        self.assertIn("ждут проверки: 1", c.message.answer.call_args[0][0])


class EditShift(unittest.TestCase):
    def test_edit_keeps_values_and_changes_one(self):
        e = Env()
        s = saved_shift(e)
        st = e.state(OWNER)
        c = e.cb(OWNER, f"ed:{s['id']}")
        run(bot.edit_cb(c, st))
        d = run(st.get_data())
        self.assertEqual(d["edit"], s["id"])
        self.assertEqual(d["d"]["cash"], 1_000_000)
        self.assertEqual(d["d"]["exps"], [(50000.0, "бензин для генератора"), (10000.0, "вода")])
        steps = len(bot.steps_for(d["d"]))
        for _ in range(steps):
            key = bot.steps_for(run(st.get_data())["d"])[run(st.get_data())["i"]]
            if key == "cash":
                run(bot.shift_text(e.msg(OWNER, "1100000"), st))
            elif key in ("exp", "photos"):
                run(bot.shift_button(e.cb(OWNER, "sh:@done"), st))
            else:
                run(bot.shift_button(e.cb(OWNER, "sh:@keep"), st))
        e.tg.send_message.reset_mock()
        run(bot.shift_button(e.cb(OWNER, "sh:@save"), st))
        s2 = core.db.shift(s["id"])
        self.assertEqual((s2["cash"], s2["terminal"], s2["m1e"], s2["status"]), (1_100_000, 500_000, 1500, "approved"))
        self.assertEqual(core.db.expenses_map()[s["id"]], [50000, 10000])
        self.assertEqual(len(core.db.shifts()), 1)
        self.assertIn("shift_edited", [x["action"] for x in core.db.audit()])
        e.tg.send_message.assert_not_called()  # правка не рассылает «новая смена»

    def test_edit_denied_for_operator_and_unknown_shift(self):
        e = Env()
        s = saved_shift(e)
        st = e.state(OP)
        run(bot.edit_cb(e.cb(OP, f"ed:{s['id']}"), st))
        self.assertIsNone(run(st.get_state()))
        c = e.cb(OWNER, "ed:999")
        run(bot.edit_cb(c, e.state(OWNER)))
        self.assertIn("нет", c.message.answer.call_args[0][0])


class PhotosAndNote(unittest.TestCase):
    def test_photos_and_arrival_note_saved_and_sent_to_owner(self):
        e = Env()
        seq = list(FULL[:-1])  # до шага «фото»
        i = seq.index(("btn", "sh:@skip"), 8)  # вместо пропуска накладной — текст
        seq[i] = ("text", "накладная 77, КамАЗ 01A123BC")
        st, _ = e.fill_shift(OP, seq)
        for fid in ("f1", "f2", "f3"):
            m = e.msg(OP, None)
            m.photo = [SimpleNamespace(file_id=fid + "s"), SimpleNamespace(file_id=fid)]
            run(bot.shift_photo(m, st))
        d = run(st.get_data())["d"]
        self.assertEqual(d["photos"], ["f1", "f2", "f3"])
        self.assertEqual(d["arr_note"], "накладная 77, КамАЗ 01A123BC")
        run(bot.shift_button(e.cb(OP, "sh:@done"), st))
        e.tg.send_photo = AsyncMock()
        run(bot.shift_button(e.cb(OP, "sh:@save"), st))
        s = core.db.shifts()[-1]
        self.assertEqual(s["photos"], "f1,f2,f3")
        self.assertIn("КамАЗ", s["arr_note"])
        self.assertEqual(e.tg.send_photo.call_count, 3)
        self.assertEqual(e.tg.send_photo.call_args.args[0], OWNER)

    def test_text_on_photo_step_is_rejected_and_photo_limit(self):
        e = Env()
        st, _ = e.fill_shift(OP, FULL[:-1])  # дошли до шага фото
        m = e.msg(OP, "привет")
        run(bot.shift_text(m, st))
        self.assertIn("фото", e.last_text(m))
        for i in range(9):
            pm = e.msg(OP, None)
            pm.photo = [SimpleNamespace(file_id=f"p{i}")]
            run(bot.shift_photo(pm, st))
        self.assertEqual(len(run(st.get_data())["d"]["photos"]), 6)


class StockReconcile(unittest.TestCase):
    def measure(self, e, liters, density="0.54"):
        st = e.state(OWNER)
        run(bot.stock_set(e.cb(OWNER, "sk:set"), st))
        run(bot.stock_liters(e.msg(OWNER, str(liters)), st))
        c = e.cb(OWNER, f"sd:{density}")
        run(bot.stock_density_cb(c, st))
        return c.message.answer.call_args[0][0]

    def test_first_measure_then_shortage_over_norm(self):
        e = Env()
        first = self.measure(e, 5000)
        self.assertNotIn("Расчётный", first)
        core.db.set_setting("loss_norm_pct", 1)
        saved_shift(e, [("btn", "sh:today"), ("text", "0"), ("text", "800"), ("text", "0"), ("text", "0"), ("text", "0"), ("text", "0"),
                        ("text", "0")] + [("text", "0")] * 4 + [("btn", "sh:@done"), ("btn", "sh:@done")])
        expected = core.tank_state()["liters"]
        self.assertAlmostEqual(expected, 4200, 3)
        text = self.measure(e, 4100)
        self.assertIn("недостача 100 л", text)
        self.assertIn("выше нормы", text)
        adj = core.db.adjustments()[-1]
        self.assertAlmostEqual(adj["expected_l"], 4200, 3)

    def test_within_norm_surplus_and_no_difference(self):
        e = Env()
        self.measure(e, 5000)
        core.db.set_setting("loss_norm_pct", 5)
        saved_shift(e, [("btn", "sh:today"), ("text", "0"), ("text", "1000"), ("text", "0"), ("text", "0"), ("text", "0"), ("text", "0"),
                        ("text", "0")] + [("text", "0")] * 4 + [("btn", "sh:@done"), ("btn", "sh:@done")])
        self.assertIn("В пределах нормы", self.measure(e, 3970))  # 4000 расчёт, -30 л при допустимых 50
        self.assertIn("излишек", self.measure(e, 4100))
        self.assertIn("расхождения нет", self.measure(e, 4100))

    def test_stock_screen_has_forecast_and_losses(self):
        e = Env()
        self.measure(e, 5000)
        saved_shift(e, [("btn", "sh:today"), ("text", "0"), ("text", "500"), ("text", "0"), ("text", "0"), ("text", "0"), ("text", "0"),
                        ("text", "0")] + [("text", "0")] * 4 + [("btn", "sh:@done"), ("btn", "sh:@done")])
        self.measure(e, 4400)
        m = e.msg(OWNER, bot.B_STOCK)
        run(bot.stock(m, e.state(OWNER)))
        text = e.last_text(m)
        self.assertIn("Хватит примерно", text)
        self.assertIn("потери 100", text)


class Accounting(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        core.db.add_adjustment("2026-11-01", 1000, 540, "начальный", "Владелец")
        core.db.set_setting("price_buy_kg", 5000)
        core.db.set_setting("sale_price_l", 3000)
        saved_shift(self.e)
        self.today = core.today()

    def owner_cb(self, data, handler, *a):
        c = self.e.cb(OWNER, data)
        run(handler(c, *a))
        return c

    def test_menu_and_access(self):
        m = self.e.msg(OWNER, extras.B_ACC)
        run(extras.acc_menu(m, self.e.state(OWNER)))
        labels = [b.text for r in m.answer.call_args.kwargs["reply_markup"].inline_keyboard for b in r]
        for x in ("📑 Налоговый отчёт", "🚚 Поставщик и долг", "💵 Инкассация и касса", "🏢 Постоянные расходы", "👷 Операторы", "🔮 Прогноз и заявка", "📜 Журнал действий"):
            self.assertIn(x, labels)
        m = self.e.msg(OP, extras.B_ACC)
        run(extras.acc_menu(m, self.e.state(OP)))
        m.answer.assert_not_called()
        c = self.e.cb(OP, "ac:sup")
        run(extras.acc_cb(c, self.e.state(OP)))
        c.message.answer.assert_not_called()

    def test_tax_report_month_and_quarter(self):
        y, mth = self.today.year, self.today.month
        c = self.owner_cb(f"tx:{y}-{mth:02d}", extras.tax_month)
        text = c.message.answer.call_args[0][0]
        self.assertTrue("Налоги" in text and ("НДС" in text))
        core.db.set_setting("excise_per_l", 10)
        c = self.owner_cb(f"tq:{y}-{(mth - 1) // 3 + 1}", extras.tax_quarter)
        text = c.message.answer.call_args[0][0]
        self.assertIn("Акциз", text)
        self.assertIn("Срок: до 20", text)
        c = self.owner_cb("tx:2020-01", extras.tax_month)
        self.assertIn("Смен за этот период нет", c.message.answer.call_args[0][0])

    def test_supplier_payment_flow(self):
        c = self.owner_cb("ac:sup", extras.acc_cb, self.e.state(OWNER))
        text = c.message.answer.call_args[0][0]
        self.assertIn("Закуплено всего: 5 000 000", text)
        self.assertIn("Долг: 5 000 000", text)
        st = self.e.state(OWNER)
        run(extras.pay_new(self.e.cb(OWNER, "pay:new"), st))
        bad = self.e.msg(OWNER, "много")
        run(extras.pay_line(bad, st))
        self.assertIn("Нужна сумма", self.e.last_text(bad))
        m = self.e.msg(OWNER, "2 000 000 за цистерну")
        run(extras.pay_line(m, st))
        self.assertIn("Долг поставщику: 3 000 000", self.e.last_text(m))
        self.assertEqual(core.db.supplier_payments()[-1]["note"], "за цистерну")
        c = self.owner_cb("ac:sup", extras.acc_cb, self.e.state(OWNER))
        self.assertIn("Оплачено поставщику: 2 000 000", c.message.answer.call_args[0][0])

    def test_cash_collection_flow(self):
        c = self.owner_cb("ac:cash", extras.acc_cb, self.e.state(OWNER))
        self.assertIn("В кассе должно быть: 940 000", c.message.answer.call_args[0][0])  # 1 000 000 − 60 000 расходов
        st = self.e.state(OWNER)
        run(extras.col_new(self.e.cb(OWNER, "col:new"), st))
        m = self.e.msg(OWNER, "500000 сдано в банк")
        run(extras.col_line(m, st))
        self.assertIn("440 000", self.e.last_text(m))

    def test_fixed_expenses_reduce_profit(self):
        before = calc_profit()
        st = self.e.state(OWNER)
        run(extras.fix_cb(self.e.cb(OWNER, "fx:new"), st))
        bad = self.e.msg(OWNER, "Аренда")
        run(extras.fix_line(bad, st))
        self.assertIn("Формат", self.e.last_text(bad))
        m = self.e.msg(OWNER, "Аренда 3 000 000")
        run(extras.fix_line(m, st))
        self.assertEqual(core.fixed_monthly(), 3_000_000)
        after = calc_profit()
        self.assertLess(after, before)
        c = self.owner_cb("ac:fix", extras.acc_cb, self.e.state(OWNER))
        self.assertIn("Аренда: 3 000 000", c.message.answer.call_args[0][0])
        fid = core.db.fixed_list()[0]["id"]
        run(extras.fix_cb(self.e.cb(OWNER, f"fx:rm:{fid}"), self.e.state(OWNER)))
        self.assertEqual(core.fixed_monthly(), 0)

    def test_operators_rating(self):
        core.db.add_shift(OP + 1, "Вали", dict(T.FULL_DICT, cash=2_400_000, terminal=0, click=0, paynet=0), [])
        c = self.owner_cb("op:all", extras.ops_cb)
        text = c.message.answer.call_args[0][0]
        self.assertIn("Али", text)
        self.assertIn("Вали", text)
        self.assertIn("расхождение", text)
        self.assertLess(text.index("Али"), text.index("Вали"))  # у Али недостача кассы

    def test_forecast_and_order_text(self):
        core.db.set_setting("tank_capacity_l", 5000)
        m = self.e.msg(OWNER, "x")
        run(extras.forecast_screen(m))
        text = self.e.last_text(m)
        self.assertIn("Хватит примерно", text)
        self.assertIn("Сколько заказать", text)
        self.assertIn("Здравствуйте! Просим доставить газ", text)
        self.assertIn("к концу месяца", text)

    def test_journal_and_compare(self):
        m = self.e.msg(OWNER, "x")
        run(extras.journal(m))
        self.assertIn("shift_added", self.e.last_text(m))
        c = self.owner_cb("cmp:week", extras.compare_cb)
        text = c.message.answer.call_args[0][0]
        self.assertIn("Неделя к неделе", text)
        self.assertIn("Выручка", text)
        c = self.owner_cb("cmp:month", extras.compare_cb)
        self.assertIn("Месяц к месяцу", c.message.answer.call_args[0][0])

    def test_new_charts_and_excel(self):
        for kind in ("weekday", "structure", "buy_price"):
            c = self.e.cb(OWNER, f"gp:{kind}:all")
            run(bot.graph_make(c))
            c.message.answer_photo.assert_called_once()
            self.assertTrue(c.message.answer_photo.call_args[0][0].data.startswith(b"\x89PNG"), kind)
        import openpyxl
        c = self.e.cb(OWNER, "xl:all")
        run(bot.xls_make(c))
        wb = openpyxl.load_workbook(io.BytesIO(c.message.answer_document.call_args[0][0].data))
        self.assertIn("Поставщик", wb.sheetnames)
        self.assertIn("Инкассация", wb.sheetnames)

    def test_settings_new_keys(self):
        self.assertIsNone(bot.parse_setting("tax_due_day", "40"))
        self.assertEqual(bot.parse_setting("tax_due_day", "25"), 25)
        self.assertEqual(bot.parse_setting("report_deadline", "9:05"), "09:05")
        self.assertEqual(bot.parse_setting("report_deadline", "0"), "0")
        self.assertIsNone(bot.parse_setting("report_deadline", "25:00"))
        st = self.e.state(OWNER)
        run(bot.settings_cb(self.e.cb(OWNER, "se:@auto"), st))
        self.assertEqual(core.cfg()["auto_daily"], 0)
        run(bot.settings_cb(self.e.cb(OWNER, "se:order_days"), st))
        run(bot.settings_value(self.e.msg(OWNER, "5"), st))
        self.assertEqual(core.cfg()["order_days"], 5)


def calc_profit():
    t = core.today()
    rows = core.rows_for("2026-11-01", t.isoformat())
    return sum(r["profit"] for r in rows)


class Scheduler(unittest.TestCase):
    def at(self, y, m, d, hh, mm=0):
        return datetime(y, m, d, hh, mm, tzinfo=TZ)

    def test_due_jobs_times(self):
        cfg = {}
        names = lambda dt, sent=None: scheduler.due_jobs(dt, cfg, sent or {})
        self.assertEqual(names(self.at(2026, 11, 17, 2, 0)), [])
        self.assertIn("backup", names(self.at(2026, 11, 17, 3, 5)))
        self.assertNotIn("stock_alert", names(self.at(2026, 11, 17, 8, 59)))
        self.assertIn("stock_alert", names(self.at(2026, 11, 17, 9, 0)))
        self.assertNotIn("daily_report", names(self.at(2026, 11, 17, 21, 29)))
        self.assertIn("daily_report", names(self.at(2026, 11, 17, 21, 30)))
        self.assertIn("shift_reminder", names(self.at(2026, 11, 17, 21, 0)))
        self.assertNotIn("shift_reminder", names(self.at(2026, 11, 17, 20, 59)))
        self.assertIn("weekly_report", names(self.at(2026, 11, 16, 9, 0)))  # понедельник
        self.assertNotIn("weekly_report", names(self.at(2026, 11, 17, 9, 0)))
        self.assertIn("tax_reminder", names(self.at(2026, 11, 17, 9, 0)))   # 20 − 3
        self.assertIn("tax_reminder", names(self.at(2026, 11, 20, 10, 0)))
        self.assertNotIn("tax_reminder", names(self.at(2026, 11, 18, 10, 0)))

    def test_not_repeated_and_switches(self):
        dt = self.at(2026, 11, 17, 22, 0)
        sent = {"daily_report": "2026-11-17", "backup": "2026-11-17"}
        got = scheduler.due_jobs(dt, {}, sent)
        self.assertNotIn("daily_report", got)
        self.assertNotIn("backup", got)
        off = scheduler.due_jobs(dt, {"auto_daily": 0, "report_deadline": "0"}, {})
        self.assertNotIn("daily_report", off)
        self.assertNotIn("shift_reminder", off)

    def tick(self, e, dt):
        scheduler.now = lambda: dt
        core.now = lambda: dt
        try:
            run(scheduler.tick(e.tg))
        finally:
            from db import now as real
            scheduler.now = real
            core.now = real

    def test_tick_sends_daily_report_once(self):
        e = Env()
        e.tg.send_document = AsyncMock()
        core.db.add_shift(OP, "Али", dict(T.FULL_DICT, date="2026-11-17"), [])
        dt = self.at(2026, 11, 17, 21, 40)
        self.tick(e, dt)
        texts_ = [c.args[1] for c in e.tg.send_message.call_args_list if c.args[0] == OWNER]
        self.assertTrue(any("Итоги дня" in x and "Выручка" in x for x in texts_))
        n = e.tg.send_message.call_count
        self.tick(e, self.at(2026, 11, 17, 21, 50))
        self.assertEqual(e.tg.send_message.call_count, n)  # второй раз не шлёт
        self.assertEqual(core.db.settings()["sched_daily_report"], "2026-11-17")

    def test_shift_reminder_only_without_report(self):
        e = Env()
        e.tg.send_document = AsyncMock()
        core.db.set_lang(OP, "uz")
        self.tick(e, self.at(2026, 11, 17, 21, 5))
        to_op = [c.args[1] for c in e.tg.send_message.call_args_list if c.args[0] == OP]
        self.assertTrue(any("Eslatma" in x for x in to_op))
        to_owner = [c.args[1] for c in e.tg.send_message.call_args_list if c.args[0] == OWNER]
        self.assertTrue(any("ещё не сдан" in x for x in to_owner))
        e2 = Env()
        e2.tg.send_document = AsyncMock()
        core.db.add_shift(OP, "Али", dict(T.FULL_DICT, date="2026-11-17"), [])
        self.tick(e2, self.at(2026, 11, 17, 21, 5))
        self.assertFalse([c for c in e2.tg.send_message.call_args_list if c.args[0] == OP])

    def test_backup_and_weekly_and_tax(self):
        e = Env()
        e.tg.send_document = AsyncMock()
        core.db.add_shift(OP, "Али", dict(T.FULL_DICT, date="2026-11-10"), [])
        self.tick(e, self.at(2026, 11, 16, 9, 30))  # понедельник
        docs = [c.args[1] for c in e.tg.send_document.call_args_list]
        self.assertTrue(any(str(getattr(d, "filename", "")).startswith("agzs_backup") for d in docs))
        self.assertTrue(any(str(getattr(d, "filename", "")).endswith(".xlsx") for d in docs))
        e2 = Env()
        e2.tg.send_document = AsyncMock()
        core.db.add_shift(OP, "Али", dict(T.FULL_DICT, date="2026-10-10"), [])
        self.tick(e2, self.at(2026, 11, 17, 9, 30))
        msgs = [c.args[1] for c in e2.tg.send_message.call_args_list if c.args[0] == OWNER]
        self.assertTrue(any("Напоминание: до 20 числа" in x and "октябрь" in x for x in msgs))

    def test_stock_alert_by_days_left(self):
        e = Env()
        e.tg.send_document = AsyncMock()
        core.db.add_adjustment("2026-11-16", 400, 216, "замер", "В")
        core.db.add_shift(OP, "Али", dict(T.FULL_DICT, date="2026-11-17", arr_kg=0, m1s=0, m1e=300, m2s=0, m2e=0), [])
        core.db.set_setting("order_days", 5)
        self.tick(e, self.at(2026, 11, 17, 9, 30))
        msgs = [c.args[1] for c in e.tg.send_message.call_args_list if c.args[0] == OWNER]
        self.assertTrue(any("Газа хватит примерно" in x for x in msgs))


if __name__ == "__main__":
    unittest.main()
