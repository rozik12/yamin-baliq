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

    def test_db_follows_volume_mount(self):
        env = dict(self.BASE, SHABBODA_BOT_TOKEN="B", RAILWAY_VOLUME_MOUNT_PATH="/vol")
        self.assertEqual(dict(run_clients.specs(env))["shabboda"]["DB_PATH"], "/vol/shabboda.db")

    def test_second_bot_own_card(self):
        env = dict(self.BASE, SHABBODA_BOT_TOKEN="B", SHABBODA_CARD_NUMBER="9860", SHABBODA_CARD_HOLDER="Ali")
        c = dict(run_clients.specs(env))["shabboda"]
        self.assertEqual((c["CARD_NUMBER"], c["CARD_HOLDER"]), ("9860", "Ali"))

    def test_aicall_gets_only_its_own_vars(self):
        env = dict(self.BASE, AICALL_TG_SESSION="S", AICALL_GEMINI_API_KEY="g", AICALL_TG_API_ID="1")
        a = dict(run_clients.specs(env))["aicall"]
        self.assertEqual((a["TG_SESSION"], a["GEMINI_API_KEY"], a["TG_API_ID"]), ("S", "g", "1"))
        for k in ("BOT_TOKEN", "CARD_NUMBER", "DB_PATH", "ADMIN_CHAT_ID", "YANDEX_DELIVERY_TOKEN"):
            self.assertNotIn(k, a)

    def test_aicall_starts_for_first_login(self):
        env = dict(self.BASE, AICALL_PHONE="+998", AICALL_LOGIN_SECRET="x")
        a = dict(run_clients.specs(env))["aicall"]
        self.assertEqual((a["PHONE"], a["LOGIN_SECRET"]), ("+998", "x"))

    def test_no_aicall_without_session(self):
        self.assertNotIn("aicall", dict(run_clients.specs(self.BASE)))


if __name__ == "__main__":
    unittest.main()
