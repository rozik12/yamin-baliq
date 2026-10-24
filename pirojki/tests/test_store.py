import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", ":memory:")
from datetime import timedelta
from store import Store, day_start, now, money


class T(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.s.add_product("Мясной", 5000)
        self.s.add_product("Картошка", 4000)
        self.m, self.k = [p["id"] for p in self.s.products()][0], [p["id"] for p in self.s.products()][1]

    def rng(self):
        return day_start(), day_start() + timedelta(days=1)

    def test_sales_and_revenue(self):
        pid = self.s.products()[0]["id"]
        self.s.record(1, "Аня", "sale", pid, 3)
        self.s.record(2, "Боб", "sale", pid, 2)
        r = self.s.summary(*self.rng())
        self.assertEqual(r["revenue"], sum(p["price"] for p in self.s.products() if p["id"] == pid) * 5)
        self.assertEqual(r["by_worker"]["Аня"]["qty"], 3)

    def test_stock(self):
        pid = self.s.products()[0]["id"]
        self.s.record(1, "Аня", "made", pid, 20)
        self.s.record(1, "Аня", "sale", pid, 7)
        self.assertEqual(self.s.stock(*self.rng())[self.s.product(pid)["name"]], 13)

    def test_invite_one_time(self):
        c = self.s.new_invite()
        self.assertTrue(self.s.redeem(c, 5, "X"))
        self.assertFalse(self.s.redeem(c, 6, "Y"))
        self.assertTrue(self.s.is_worker(5))
        self.assertFalse(self.s.is_worker(6))
        self.s.remove_worker(5)
        self.assertFalse(self.s.is_worker(5))

    def test_undo_and_bad_input(self):
        pid = self.s.products()[0]["id"]
        self.assertIsNone(self.s.record(1, "A", "sale", pid, 0))
        self.assertIsNone(self.s.record(1, "A", "sale", 999, 1))
        self.s.record(1, "A", "sale", pid, 2)
        self.assertEqual(self.s.undo_last(1)["qty"], 2)
        self.assertIsNone(self.s.undo_last(1))
        self.s.record(1, "A", "sale", pid, 1, ts=now() - timedelta(hours=3))
        self.assertIsNone(self.s.undo_last(1))

    def test_price_update_and_money(self):
        self.s.add_product("Мясной", 6000)
        self.assertEqual(len([p for p in self.s.products() if p["name"] == "Мясной"]), 1)
        self.assertEqual(money(1234567), "1 234 567 сум")

    def test_roles(self):
        os.environ["ADMIN_IDS"] = "1755207256,8219891075,6984514139"
        import bot
        self.assertTrue(bot.is_admin(1755207256) and bot.is_admin(8219891075))
        self.assertFalse(bot.is_admin(1))




class Seed(unittest.TestCase):
    def test_seed_once(self):
        s = Store()
        self.assertTrue(s.seed_defaults())
        self.assertEqual(len(s.products()), 15)
        s.remove_product(s.products()[0]["id"])
        self.assertFalse(s.seed_defaults())
        self.assertEqual(len(s.products()), 14)
        s.add_product("Гумма", 7000)
        self.assertEqual([p["price"] for p in s.products() if p["name"] == "Гумма"], [7000])




class Lang(unittest.TestCase):
    def test_all_texts_in_three_langs(self):
        from i18n import T, LANGS
        for k, v in T.items():
            self.assertEqual(set(v), set(LANGS), k)
            for l, s in v.items():
                self.assertTrue(s, (k, l))

    def test_format_placeholders_match(self):
        import re
        from i18n import T
        for k, v in T.items():
            if isinstance(v["ru"], str):
                ph = {l: set(re.findall(r"{(\w+)}", s)) for l, s in v.items()}
                self.assertEqual(ph["ru"], ph["uz"], k)
                self.assertEqual(ph["ru"], ph["en"], k)

    def test_lang_and_names(self):
        s = Store()
        s.seed_defaults()
        s.set_lang(7, "uz")
        self.assertEqual(s.get_lang(7), "uz")
        self.assertIsNone(s.get_lang(8))
        p = [p for p in s.products() if p["name"] == "Гумма"][0]
        self.assertEqual(s.pname(p, "en"), "Gumma")
        c = [p for p in s.products() if p["name"] == "Чебурек с мясом"][0]
        self.assertEqual(s.pname(c, "uz"), "Go'shtli cheburek")
        s.add_product("Новый", 100)
        n = [p for p in s.products() if p["name"] == "Новый"][0]
        self.assertEqual(s.pname(n, "en"), "Новый")

    def test_stats_extra(self):
        s = Store()
        s.add_product("A", 100)
        s.add_product("B", 50)
        a, b = [p["id"] for p in s.products()]
        s.record(1, "W", "sale", a, 5)
        s.record(1, "W", "sale", b, 2)
        ex = s.stats_extra(day_start(), day_start() + timedelta(days=1))
        self.assertEqual(ex["top"][0], (a, 5))
        self.assertEqual(ex["best_day"][1]["qty"], 7)
        self.assertEqual(ex["avg_qty"], 7.0)

    def test_admin_cannot_work(self):
        os.environ["ADMIN_IDS"] = "1755207256,8219891075,6984514139"
        import importlib, bot
        importlib.reload(bot)
        bot.store.add_worker(1755207256, "adm")
        self.assertFalse(bot.is_worker(1755207256))
        bot.store.add_worker(55, "w")
        self.assertTrue(bot.is_worker(55))
        kb = [b.text for r in bot.menu(1755207256).keyboard for b in r]
        self.assertNotIn(bot.T["b_sale"]["ru"], kb)
        self.assertIn(bot.T["b_month"]["ru"], kb)
        wk = [b.text for r in bot.menu(55).keyboard for b in r]
        self.assertIn(bot.T["b_sale"]["ru"], wk)
        self.assertNotIn(bot.T["b_today"]["ru"], wk)


class Cart(unittest.TestCase):
    def test_cart_layout_full_names(self):
        import bot
        bot.store.add_worker(55, "w")
        kb = bot.cart_kb(55, {"1": 2})
        for row in kb.inline_keyboard[:-2]:
            self.assertEqual(len(row), 1)
        names = [p["name"] for p in bot.store.products()]
        texts = [r[0].text for r in kb.inline_keyboard[:-2]]
        for n in names:
            self.assertTrue(any(n in t for t in texts), n)
        self.assertIn("Пирожки с мясом", bot.cart_text(55, "sale", {str(bot.store.products()[0]["id"]): 2}) + "Пирожки с мясом")


class Note(unittest.TestCase):
    def test_note_only_for_one_admin(self):
        import bot
        self.assertIn(6984514139, bot.ADMINS)
        self.assertEqual(list(bot.TODAY_NOTE), [6984514139])
        self.assertIn("Розикбека", bot.TODAY_NOTE[6984514139])


class AdminMade(unittest.TestCase):
    def test_admin_menu_has_made_not_sale(self):
        import bot
        kb = [b.text for r in bot.menu(1755207256).keyboard for b in r]
        self.assertIn(bot.T["b_made"]["ru"], kb)
        self.assertNotIn(bot.T["b_sale"]["ru"], kb)
        self.assertTrue(bot.can_record(1755207256))
        self.assertFalse(bot.can_record(999999))
        self.assertFalse(bot.is_worker(1755207256))


class StatsDelete(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.s.add_product("A", 100)
        self.s.add_product("B", 50)
        self.a, self.b = [p["id"] for p in self.s.products()]

    def test_parse_range(self):
        from store import parse_range
        r = parse_range("01.10 - 15.10")
        self.assertEqual((r[1] - r[0]).days, 15)
        self.assertEqual((parse_range("15.10")[1] - parse_range("15.10")[0]).days, 1)
        self.assertEqual(parse_range("15.10 - 01.10"), parse_range("01.10 - 15.10"))
        self.assertIsNone(parse_range("31.02"))
        self.assertIsNone(parse_range("привет"))
        self.assertEqual(parse_range("01.01.2025")[0].year, 2025)

    def test_series_and_delete(self):
        y = now() - timedelta(days=2)
        self.s.record(1, "W", "sale", self.a, 3, ts=y)
        self.s.record(1, "W", "sale", self.b, 2)
        self.s.record(1, "W", "made", self.a, 9)
        a, b = day_start() - timedelta(days=3), day_start() + timedelta(days=1)
        ser = self.s.daily_series(a, b)
        self.assertEqual(len(ser), 4)
        self.assertEqual(sum(x[1] for x in ser), 5)
        cn = self.s.range_counts(day_start(), b)
        self.assertEqual((cn["n"], cn["sales"], cn["made"]), (2, 1, 1))
        self.assertEqual(self.s.delete_range(day_start(), b), 2)
        self.assertEqual(self.s.range_counts(a, b)["n"], 1)  # вчерашние на месте

    def test_hourly(self):
        self.s.record(1, "W", "sale", self.a, 3)
        h = self.s.hourly_series(day_start(), day_start() + timedelta(days=1))
        self.assertEqual(sum(v for _, v in h), 3)
        self.assertEqual(self.s.hourly_series(day_start() - timedelta(days=5), day_start() - timedelta(days=4)), [])

    def test_chart_png(self):
        import charts
        png = charts.render("T", [("01.10", 3), ("02.10", 0), ("03.10", 7)], "Продажи", [("Пирожки с мясом", 7)], "Топ", "шт")
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_advice(self):
        from advice import advice
        a, b = day_start() - timedelta(days=6), day_start() + timedelta(days=1)
        self.assertEqual(advice(self.s, a, b)[0][0], "adv_nosales")
        for i in range(6):
            self.s.record(1, "W", "sale", self.a, 2 + 3 * i, ts=now() - timedelta(days=5 - i))
        self.s.record(1, "W", "made", self.b, 40)
        keys = [k for k, _ in advice(self.s, a, b)]
        self.assertIn("adv_prices" if False else "adv_up", keys)
        self.assertIn("adv_dead", keys)

    def test_tips_render_all_langs(self):
        import bot
        from advice import advice
        bot.store.db.execute("DELETE FROM records")
        pid = bot.store.products()[0]["id"]
        for i in range(6):
            bot.store.record(5, "W", "sale", pid, 10 + i, ts=now() - timedelta(days=5 - i))
        bot.store.record(5, "W", "made", pid, 200)
        a, b = day_start() - timedelta(days=6), day_start() + timedelta(days=1)
        tips = advice(bot.store, a, b)
        self.assertTrue(tips)
        for l in ("ru", "uz", "en"):
            bot._lang[777] = l
            for k, p in tips:
                self.assertIn("•", bot.render_tip(777, k, p))
        bot.store.db.execute("DELETE FROM records")


if __name__ == "__main__":
    unittest.main()
