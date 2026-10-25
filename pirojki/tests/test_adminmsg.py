import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", ":memory:")
os.environ["ADMIN_IDS"] = "1755207256,8219891075,6984514139"


class AdminMsg(unittest.TestCase):
    def test_button_only_for_8219891075(self):
        import bot
        has = lambda uid: bot.T["b_msg"]["ru"] in [b.text for r in bot.menu(uid).keyboard for b in r]
        self.assertTrue(has(8219891075))
        self.assertFalse(has(1755207256))
        self.assertFalse(has(6984514139))
        bot.store.add_worker(55, "w")
        self.assertFalse(has(55))
        self.assertEqual(bot.ADMIN_MSG_FROM, {8219891075})

    def test_texts_in_three_languages(self):
        from i18n import T, LANGS
        for k in ("b_msg", "msg_ask", "msg_from", "msg_sent"):
            self.assertEqual(set(T[k]), set(LANGS))


if __name__ == "__main__":
    unittest.main()
