"""Номера сотрудников: поиск через Telegram-аккаунт владельца, ручной ввод, список."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agzs_bot import OP, OWNER, Env, core, run  # noqa: E402
import lookup  # noqa: E402


def fwd(env, uid, text):
    m = env.msg(uid, text)
    m.forward_origin = None
    m.forward_from = None
    return m


class Phones(unittest.TestCase):
    def test_norm_phone(self):
        self.assertEqual(lookup.norm_phone("+998 90 123-45-67"), "+998901234567")
        self.assertEqual(lookup.norm_phone("901234567"), "+998901234567")
        self.assertIsNone(lookup.norm_phone("abc"))
        self.assertIsNone(lookup.norm_phone("123"))

    def test_manual_then_find_without_account(self):
        e = Env()
        st = e.state(OWNER)
        run(lookup.ph_cb(e.cb(OWNER, "ph:man"), st))
        m = e.msg(OWNER, "5550123 +998 90 111-22-33")
        run(lookup.ph_manual(m, st))
        self.assertEqual(core.db.phone(5550123)["phone"], "+998901112233")
        run(lookup.ph_cb(e.cb(OWNER, "ph:find"), st))
        m = fwd(e, OWNER, "5550123")
        run(lookup.ph_find(m, st))
        self.assertIn("+998901112233", e.last_text(m))
        run(lookup.ph_cb(e.cb(OWNER, "ph:find"), st))
        m = fwd(e, OWNER, "99999999")
        run(lookup.ph_find(m, st))
        self.assertIn("не нашёл", e.last_text(m))

    def test_lookup_through_owner_account(self):
        e = Env()
        st = e.state(OWNER)

        async def fake(target):
            return 777001, "Али Валиев", "+998935556677"
        with patch.object(lookup, "configured", return_value=True), patch.object(lookup, "tg_lookup", fake):
            run(lookup.ph_cb(e.cb(OWNER, "ph:find"), st))
            m = fwd(e, OWNER, "777001")
            run(lookup.ph_find(m, st))
        self.assertIn("+998935556677", e.last_text(m))
        self.assertIn("Telegram-аккаунта", e.last_text(m))
        self.assertEqual(core.db.phone(777001)["phone"], "+998935556677")  # запомнили

    def test_account_failure_falls_back_to_saved(self):
        e = Env()
        core.db.set_phone(OP, "+998901112233", "manual")

        async def boom(target):
            raise RuntimeError("no entity")
        with patch.object(lookup, "configured", return_value=True), patch.object(lookup, "tg_lookup", boom):
            r = run(lookup.find_phone(OP))
        self.assertEqual(r[2], "+998901112233")

    def test_only_owner_and_list(self):
        e = Env()
        st = e.state(OP)
        c = e.cb(OP, "ph:list")
        run(lookup.ph_cb(c, st))
        c.message.answer.assert_not_called()
        core.db.set_phone(OP, "+998901112233", "manual")
        c = e.cb(OWNER, "ph:list")
        run(lookup.ph_cb(c, e.state(OWNER)))
        text = c.message.answer.call_args[0][0]
        self.assertIn("+998901112233", text)
        c = e.cb(OWNER, "ph:menu")
        run(lookup.ph_cb(c, e.state(OWNER)))
        self.assertIn("не подключён", c.message.answer.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
