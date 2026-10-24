"""Простое подключение бизнеса: самостоятельная регистрация, QR, помощь, сотрудники, страницы кнопок."""
import unittest
from unittest.mock import MagicMock

from helpers import St, admin_pages, buttons, cb, cmd, labels, msg, mkbot, run

import core  # noqa: E402
from modules import booking, leads, recruiter, shop, support  # noqa: E402,F401

OWNER, NEWBIE, NEWBIE2, CLIENT = 111, 301, 302, 303


class SignupTests(unittest.TestCase):
    def setUp(self):
        for t in ("tenants", "tenant_admins", "user_tenant", "invites", "users", "products"):
            core.db.execute(f"DELETE FROM {t}")
        core.db.commit()
        core.more_page.clear()
        core.SELF_SIGNUP = True

    def signup(self, uid=NEWBIE, name="Кафе «Ёлка» 24", choice=0):
        st = St()
        c = cb("signup", uid)
        run(core.signup_start(c, st))
        run(core.wizard_input(msg(name, uid), st))
        c2 = cb(f"wzc:{choice}", uid)
        run(core.wizard_choice(c2, st))
        return c2.bot

    def test_stranger_sees_simple_welcome_with_create_button(self):
        m = msg("/start", NEWBIE)
        run(core.start(m, St(), cmd()))
        self.assertIn("Здравствуйте", m.answer.call_args[0][0])
        self.assertIn("signup", buttons(m.answer.call_args[1]["reply_markup"]))
        core.SELF_SIGNUP = False
        m = msg("/start", NEWBIE)
        run(core.start(m, St(), cmd()))
        self.assertIsNone(m.answer.call_args[1].get("reply_markup"))

    def test_self_signup_creates_space_and_makes_user_admin(self):
        bot = self.signup()
        slug = core.tenant_of(NEWBIE)
        self.assertEqual(slug, "kafe-yolka-24")
        tenant = core.get_tenant(slug)
        self.assertEqual((tenant["name"], tenant["module"]), ("Кафе «Ёлка» 24", "shop"))
        self.assertTrue(core.is_admin(NEWBIE))
        self.assertFalse(core.is_owner(NEWBIE))
        sent = [c[0][1] for c in bot.send_message.call_args_list if c[0][0] == NEWBIE]
        self.assertTrue(any("Что сделать дальше" in x and "➕ Товар" in x for x in sent))
        self.assertIn("start=kafe-yolka-24", bot.send_photo.call_args[1]["caption"])
        self.assertEqual(bot.send_photo.call_args[0][0], NEWBIE)
        self.assertTrue(bot.send_photo.call_args[0][1].data.startswith(b"\x89PNG"))
        owners = [c[0][0] for c in bot.send_message.call_args_list]
        self.assertIn(OWNER, owners)  # владелец платформы получил уведомление

    def test_only_one_space_per_user_and_unique_slugs(self):
        self.signup()
        m = msg(uid=NEWBIE)
        run(core.signup_start(m, St()))
        self.assertIn("уже есть бот", m.answer.call_args[0][0])
        self.signup(uid=NEWBIE2, name="Кафе Ёлка 24")
        self.assertNotEqual(core.tenant_of(NEWBIE), core.tenant_of(NEWBIE2))
        self.assertEqual(core.make_slug("Салон Красоты!"), "salon-krasoty")
        self.assertEqual(core.make_slug("!!!"), "biz")
        self.assertEqual(core.make_slug("Ўзбекистон ҳунар"), "ozbekiston-hunar")
        self.assertEqual(core.make_slug("Объём"), "obyom")
        self.assertEqual(core.make_slug("admin"), "b-admin")

    def test_signup_closed_when_disabled(self):
        core.SELF_SIGNUP = False
        m = msg(uid=NEWBIE)
        run(core.signup_start(m, St()))
        self.assertIn("закрыто", m.answer.call_args[0][0])
        self.assertIsNone(core.tenant_of(NEWBIE))

    def test_owner_gets_admin_invite_for_client(self):
        bot = self.signup(uid=OWNER, name="Салон Роза", choice=1)
        texts = " ".join(str(c) for c in bot.send_message.call_args_list)
        self.assertIn("start=adm_", texts)
        self.assertEqual(core.get_tenant(core.tenant_of(OWNER))["module"], "booking")

    def test_admin_pages_simple_and_toggle(self):
        self.signup()
        first, second = admin_pages(NEWBIE)
        self.assertEqual(first, [shop.ADM_ORDERS, shop.ADM_ADDP, shop.ADM_CATALOG, core.MORE_BTN])
        run(core.more_page_open(msg(uid=NEWBIE)))
        self.assertIn(core.HELP_BTN, labels(core.main_kb, NEWBIE))
        run(core.more_page_close(msg(uid=NEWBIE)))
        self.assertEqual(labels(core.main_kb, NEWBIE), first)
        m = msg(uid=CLIENT)
        run(core.more_page_open(m))
        m.answer.assert_not_awaited()

    def test_help_link_and_staff_invite(self):
        self.signup()
        m = msg(uid=NEWBIE)
        run(core.help_cmd(m))
        text = m.answer.call_args[0][0]
        self.assertIn("➕ Товар", text)
        self.assertIn("Ссылка и QR", text)
        m = msg(uid=NEWBIE)
        run(core.link_cmd(m))
        self.assertIn("kafe-yolka-24", m.bot.send_photo.call_args[1]["caption"])
        m = msg(uid=NEWBIE)
        run(core.staff_cmd(m))
        link = m.answer.call_args[0][0]
        code = link.split("start=")[1].split()[0]
        # сотрудник переходит по ссылке и становится админом именно этого заведения
        st = msg("/start", 777)
        run(core.start(st, St(), cmd(code)))
        self.assertTrue(core.is_admin(777))
        self.assertEqual(core.tenant_of(777), "kafe-yolka-24")
        st = msg("/start", 778)
        run(core.start(st, St(), cmd(code)))  # повторно нельзя
        self.assertFalse(core.is_admin(778))
        for fn in (core.help_cmd, core.link_cmd, core.staff_cmd):
            m = msg(uid=CLIENT)
            run(fn(m))
            m.answer.assert_not_awaited()
            m.bot.send_photo.assert_not_awaited()

    def test_help_covers_every_module(self):
        for mod in core.MODULE_BUTTONS:
            self.assertIn(mod, core.HELP, mod)
        text = core.help_text(["shop", "booking", "leads", "recruiter", "support"])
        for word in ("➕ Товар", "➕ Услуга", "Приём заявок", "➕ Вакансия", "Поддержка"):
            self.assertIn(word, text)

    def test_stats_and_export_reachable_from_second_page(self):
        self.signup()
        m = msg(uid=NEWBIE)
        run(core.stats_cmd(m))
        self.assertIn("Клиентов", m.answer.call_args[0][0])
        m = msg(uid=NEWBIE)
        run(core.export_cmd(m))
        m.answer_document.assert_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
