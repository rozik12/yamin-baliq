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
        os.environ["ADMIN_IDS"] = "1755207256,8219891075"
        import bot
        self.assertTrue(bot.is_admin(1755207256) and bot.is_admin(8219891075))
        self.assertFalse(bot.is_admin(1))


if __name__ == "__main__":
    unittest.main()
