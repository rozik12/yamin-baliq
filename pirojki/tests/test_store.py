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


if __name__ == "__main__":
    unittest.main()
