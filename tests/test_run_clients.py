import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_clients


class Specs(unittest.TestCase):
    BASE = {"BOT_TOKEN": "A", "CLIENT": "yamin-baliq", "ADMIN_CHAT_ID": "1", "CARD_NUMBER": "5614", "CARD_HOLDER": "X",
            "COURIER_CHAT_ID": "-100", "DB_PATH": "/data/bot.db", "YANDEX_DELIVERY_TOKEN": "yd"}

    def test_single_bot_when_no_second_token(self):
        s = run_clients.specs(self.BASE)
        self.assertEqual([n for n, _ in s], ["main"])
        self.assertEqual(s[0][1]["BOT_TOKEN"], "A")

    def test_second_bot_is_isolated(self):
        env = dict(self.BASE, SHABBODA_BOT_TOKEN="B", SHABBODA_ADMIN_CHAT_ID="8219891075")
        s = dict(run_clients.specs(env))
        m, c = s["main"], s["shabboda"]
        self.assertEqual(m["BOT_TOKEN"], "A")
        self.assertEqual(m["CARD_NUMBER"], "5614")
        self.assertEqual((c["BOT_TOKEN"], c["CLIENT"], c["DB_PATH"]), ("B", "shabboda", "/data/shabboda.db"))
        self.assertEqual(c["ADMIN_CHAT_ID"], "8219891075")
        for k in ("CARD_NUMBER", "CARD_HOLDER", "COURIER_CHAT_ID", "YANDEX_DELIVERY_TOKEN"):
            self.assertNotIn(k, c)

    def test_second_bot_own_card(self):
        env = dict(self.BASE, SHABBODA_BOT_TOKEN="B", SHABBODA_CARD_NUMBER="9860", SHABBODA_CARD_HOLDER="Ali")
        c = dict(run_clients.specs(env))["shabboda"]
        self.assertEqual((c["CARD_NUMBER"], c["CARD_HOLDER"]), ("9860", "Ali"))


if __name__ == "__main__":
    unittest.main()
