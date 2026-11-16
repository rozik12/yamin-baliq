import asyncio
import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

AG = Path(__file__).resolve().parent.parent / "agzs"
sys.path.insert(0, str(AG))
_old = {k: os.environ.get(k) for k in ("DB_PATH", "ADMIN_IDS")}
os.environ["DB_PATH"], os.environ["ADMIN_IDS"] = ":memory:", "8219891075"
_spec = importlib.util.spec_from_file_location("agzs_bot", AG / "bot.py")
bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot)
import core  # noqa: E402
import extras  # noqa: E402
import scheduler  # noqa: E402
for _k, _v in _old.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

from db import DB  # noqa: E402

OWNER, OP = 8219891075, 555


def run(coro):
    return asyncio.run(coro)


class Env:
    """Один тест = новая база, новое FSM-хранилище и поддельный Telegram."""

    def __init__(self):
        core.db = DB(":memory:", admin_ids=[OWNER])
        core.db.set_user(OP, "Али", "operator")
        self.storage = MemoryStorage()
        self.tg = MagicMock()
        self.tg.send_message = AsyncMock()
        self.tg.get_me = AsyncMock(return_value=SimpleNamespace(username="navrozpropanbot"))

    def state(self, uid):
        return FSMContext(self.storage, StorageKey(bot_id=1, chat_id=uid, user_id=uid))

    def msg(self, uid, text):
        m = MagicMock()
        m.from_user = SimpleNamespace(id=uid, full_name="Али" if uid == OP else "Владелец", username=None)
        m.text = text
        m.bot = self.tg
        m.answer = AsyncMock()
        m.answer_photo = AsyncMock()
        m.answer_document = AsyncMock()
        return m

    def cb(self, uid, data):
        c = MagicMock()
        c.from_user = SimpleNamespace(id=uid, full_name="Али" if uid == OP else "Владелец", username=None)
        c.data = data
        c.bot = self.tg
        c.answer = AsyncMock()
        c.message.answer = AsyncMock()
        c.message.edit_text = AsyncMock()
        c.message.answer_photo = AsyncMock()
        c.message.answer_document = AsyncMock()
        return c

    def last_text(self, m):
        return m.answer.call_args[0][0]

    def fill_shift(self, uid, inputs):
        """Проходит мастер: inputs — список («text», значение) или («btn», 'sh:..')."""
        st = self.state(uid)
        run(bot.shift_start(self.msg(uid, bot.B_SHIFT), st))
        last = None
        for kind, v in inputs:
            if kind == "text":
                last = self.msg(uid, v)
                run(bot.shift_text(last, st))
            else:
                last = self.cb(uid, v)
                run(bot.shift_button(last, st))
        return st, last


FULL_DICT = {"date": "2026-11-10", "m1s": 1000, "m1e": 1500, "m2s": 2000, "m2e": 2300, "m3s": 0, "m3e": 0, "cash": 1_000_000, "terminal": 500_000,
             "click": 300_000, "paynet": 200_000, "arr_kg": 1000, "arr_density": 0.54, "arr_price": 5000}

FULL = [("btn", "sh:today"), ("text", "1000"), ("text", "1500"), ("text", "2000"), ("text", "2300"), ("text", "0"), ("btn", "sh:@skip"),
        ("text", "1000"), ("btn", "sh:@0.54"), ("text", "5000"), ("btn", "sh:@skip"), ("text", "1 000 000"), ("text", "500000"), ("text", "300000"), ("text", "200000"),
        ("text", "50000 бензин для генератора"), ("text", "10000 вода"), ("btn", "sh:@done"), ("btn", "sh:@done")]


class Wizard(unittest.TestCase):
    def test_operator_full_shift_saved_and_admin_notified(self):
        e = Env()
        e.fill_shift(OP, FULL)  # до «Сохранить»
        st = e.state(OP)
        data = run(st.get_data())
        self.assertEqual(data["i"], len(bot.steps_for(data["d"])))
        c = e.cb(OP, "sh:@save")
        run(bot.shift_button(c, st))
        sh = core.db.shifts()
        self.assertEqual(len(sh), 1)
        s = sh[0]
        self.assertEqual((s["m1s"], s["m1e"], s["m2e"], s["arr_kg"], s["arr_density"], s["arr_price"], s["cash"], s["click"]),
                         (1000, 1500, 2300, 1000, 0.54, 5000, 1_000_000, 300_000))
        self.assertEqual(s["user_name"], "Али")
        self.assertEqual(core.db.expenses_map()[s["id"]], [50000, 10000])
        sent = e.tg.send_message.call_args_list
        self.assertTrue(any(call.args[0] == OWNER and "Новая смена" in call.args[1] and "Чистая прибыль" in call.args[1] for call in sent))
        # оператору не показываем финансы владельца
        self.assertNotIn("Чистая прибыль", c.message.edit_text.call_args[0][0])
        self.assertNotIn("НДС", c.message.edit_text.call_args[0][0])
        self.assertEqual(run(st.get_state()), None)

    def test_validation_back_cancel_and_prev_default(self):
        e = Env()
        core.db.add_shift(OP, "Али", {"date": "2026-11-01", "m1s": 0, "m1e": 900, "m2s": 0, "m2e": 50, "m3s": 0, "m3e": 0, "cash": 0, "terminal": 0, "click": 0, "paynet": 0})
        st, last = e.fill_shift(OP, [("btn", "sh:today"), ("btn", "sh:@prev")])
        self.assertEqual(run(st.get_data())["d"]["m1s"], 900)  # подставилось окончание прошлой смены
        bad = e.msg(OP, "800")  # конец меньше начала
        run(bot.shift_text(bad, st))
        self.assertIn("меньше начального", e.last_text(bad))
        self.assertEqual(run(st.get_data())["i"], 2)  # остались на шаге
        word = e.msg(OP, "много")
        run(bot.shift_text(word, st))
        self.assertIn("число", e.last_text(word))
        run(bot.shift_button(e.cb(OP, "sh:@back"), st))
        self.assertEqual(run(st.get_data())["i"], 1)
        c = e.cb(OP, "sh:@cancel")
        run(bot.shift_button(c, st))
        self.assertEqual(run(st.get_state()), None)
        self.assertEqual(len(core.db.shifts()), 1)  # новую смену не сохранили

    def test_no_arrival_skips_density_and_price(self):
        e = Env()
        d = {"arr_kg": 0}
        self.assertNotIn("arr_density", bot.steps_for(d))
        self.assertIn("arr_density", bot.steps_for({"arr_kg": 5}))
        err = bot.apply_value(OP, "arr_density", {}, "0.9", {})
        self.assertIn("0,4 до 0,7", err)
        self.assertIsNone(bot.apply_value(OP, "arr_density", {}, "0,56", {}))

    def test_unknown_user_cannot_start_wizard(self):
        e = Env()
        st = e.state(999)
        m = e.msg(999, bot.B_SHIFT)
        run(bot.shift_start(m, st))
        self.assertEqual(run(st.get_state()), None)
        m.answer.assert_not_called()

    def test_menu_button_text_does_not_become_input(self):
        self.assertIn(bot.B_FIN, bot.MENU)


class Owner(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        core.db.add_adjustment("2026-11-01", 1000, 540, "начальный", "Владелец")
        core.db.set_setting("price_buy_kg", 5000)
        core.db.set_setting("sale_price_l", 3000)
        self.e.fill_shift(OP, FULL)
        run(bot.shift_button(self.e.cb(OP, "sh:@save"), self.e.state(OP)))

    def test_finance_report(self):
        c = self.e.cb(OWNER, "fp:all")
        run(bot.fin_cb(c))
        text = c.message.answer.call_args[0][0]
        for part in ("Выручка: 2 000 000", "Пролив: 800", "НДС исходящий: 214 286", "Акциз: 0", "Закуп газа (оплачено): 5 000 000", "Расходы на месте: 60 000",
                     "ЧИСТАЯ ПРИБЫЛЬ", "Остаток на конец периода", "недостача 400 000"):
            self.assertIn(part, text)

    def test_stock_after_arrival_and_sale(self):
        st = core.tank_state()
        self.assertAlmostEqual(st["liters"], 1000 + 1000 / 0.54 - 800, 3)
        m = self.e.msg(OWNER, bot.B_STOCK)
        run(bot.stock(m, self.e.state(OWNER)))
        self.assertIn("Остаток газа", self.e.last_text(m))

    def test_set_stock_flow(self):
        stt = self.e.state(OWNER)
        run(bot.stock_set(self.e.cb(OWNER, "sk:set"), stt))
        m = self.e.msg(OWNER, "8 500")
        run(bot.stock_liters(m, stt))
        c = self.e.cb(OWNER, "sd:0.56")
        run(bot.stock_density_cb(c, stt))
        st = core.tank_state()
        self.assertEqual(round(st["liters"]), 8500)
        self.assertAlmostEqual(st["kg"], 8500 * 0.56, 3)

    def test_graphs_all_kinds_and_periods(self):
        for kind in bot.charts.KINDS:
            for period in ("week", "month", "all"):
                c = self.e.cb(OWNER, f"gp:{kind}:{period}")
                run(bot.graph_make(c))
                c.message.answer_photo.assert_called_once()
                photo = c.message.answer_photo.call_args[0][0]
                self.assertTrue(photo.data.startswith(b"\x89PNG"), (kind, period))

    def test_graph_menu_and_operator_denied(self):
        m = self.e.msg(OWNER, bot.B_GRAPH)
        run(bot.graph_menu(m))
        kb = m.answer.call_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual([r[0].text for r in kb], list(bot.charts.KINDS.values()))
        c = self.e.cb(OP, "gp:sales:week")
        run(bot.graph_make(c))
        c.message.answer_photo.assert_not_called()
        c = self.e.cb(OP, "xl:all")
        run(bot.xls_make(c))
        c.message.answer_document.assert_not_called()
        m = self.e.msg(OP, bot.B_FIN)
        run(bot.fin_menu(m))
        m.answer.assert_not_called()

    def test_excel_export(self):
        import openpyxl
        c = self.e.cb(OWNER, "xl:all")
        run(bot.xls_make(c))
        doc = c.message.answer_document.call_args[0][0]
        wb = openpyxl.load_workbook(io.BytesIO(doc.data))
        self.assertIn("Сводка по дням", wb.sheetnames)
        ws = wb["Сводка по дням"]
        self.assertEqual(ws.max_row, 3)  # заголовок + день + итого

    def test_settings_flow_and_toggles(self):
        stt = self.e.state(OWNER)
        run(bot.settings_cb(self.e.cb(OWNER, "se:vat_rate"), stt))
        bad = self.e.msg(OWNER, "abc")
        run(bot.settings_value(bad, stt))
        self.assertIn("Не понял значение", self.e.last_text(bad))
        run(bot.settings_value(self.e.msg(OWNER, "15"), stt))
        self.assertEqual(bot.cfg()["vat_rate"], 15)
        run(bot.settings_cb(self.e.cb(OWNER, "se:@mode"), stt))
        self.assertEqual(bot.cfg()["profit_mode"], "cogs")
        run(bot.settings_cb(self.e.cb(OWNER, "se:@vat"), stt))
        self.assertEqual(bot.cfg()["buy_price_with_vat"], 0)

    def test_delete_shift_recalculates(self):
        sid = core.db.shifts()[0]["id"]
        c = self.e.cb(OWNER, f"dy:{sid}")
        run(bot.shift_delete_yes(c))
        self.assertEqual(core.db.shifts(), [])
        self.assertAlmostEqual(core.tank_state()["liters"], 1000, 3)
        c2 = self.e.cb(OP, f"dy:{sid}")
        run(bot.shift_delete_yes(c2))  # оператору нельзя

    def test_staff_invites(self):
        c = self.e.cb(OWNER, "st:op")
        run(bot.staff_cb(c))
        link = c.message.answer.call_args[0][0]
        code = link.split("start=op_")[1]
        m = self.e.msg(777, f"/start op_{code}")
        run(bot.start(m, self.e.state(777)))
        self.assertEqual(core.db.role_of(777), "operator")
        m2 = self.e.msg(778, f"/start op_{code}")
        run(bot.start(m2, self.e.state(778)))
        self.assertIsNone(core.db.role_of(778))

    def test_low_stock_alert(self):
        core.db.set_setting("low_stock_l", 5000)
        self.e.tg.send_message.reset_mock()
        self.e.fill_shift(OP, FULL[:7] + [("text", "0"), ("text", "0"), ("text", "0"), ("text", "0"), ("text", "0"), ("btn", "sh:@done")])
        run(bot.shift_button(self.e.cb(OP, "sh:@save"), self.e.state(OP)))
        texts = [c.args[1] for c in self.e.tg.send_message.call_args_list]
        self.assertTrue(any("Газ заканчивается" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
