"""Тесты ядра (заведения, права, изоляция данных) и модуля «Рекрутер»."""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

os.environ.update(DB_PATH=":memory:", ADMIN_CHAT_ID="111", BRAND_NAME="x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core  # noqa: E402
from modules import recruiter as rec  # noqa: E402
from modules import shop, booking, leads, support  # noqa: E402,F401

OWNER, ADM_A, ADM_B, CLI_A, CLI_B = 111, 201, 202, 5, 6


class St:
    def __init__(self, d=None):
        self.d, self.state = d or {}, None

    async def get_data(self):
        return dict(self.d)

    async def update_data(self, **k):
        self.d.update(k)

    async def set_state(self, s):
        self.state = s

    async def clear(self):
        self.d, self.state = {}, None


def mkbot():
    b = MagicMock()
    for n in ("send_message", "send_photo", "send_document"):
        setattr(b, n, AsyncMock())
    b.get_me = AsyncMock(return_value=MagicMock(username="TestBot"))
    return b


def msg(text=None, uid=CLI_A, **kw):
    m = MagicMock()
    m.text, m.from_user.id, m.from_user.full_name = text, uid, f"U{uid}"
    m.contact, m.photo, m.document = kw.get("contact"), kw.get("photo"), kw.get("document")
    for n in ("answer", "edit_text", "edit_caption", "delete"):
        setattr(m, n, AsyncMock())
    m.bot = mkbot()
    return m


def cb(data, uid=CLI_A):
    c = MagicMock()
    c.data, c.from_user.id, c.from_user.full_name = data, uid, f"U{uid}"
    c.answer = AsyncMock()
    c.message = msg("orig", uid)
    c.message.photo = c.message.document = None
    c.message.caption = None
    c.bot = c.message.bot
    return c


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def labels(uid):
    return [b.text for row in core.main_kb(uid).keyboard for b in row]


def cmd(args=None):
    return MagicMock(args=args)


class PlatformTests(unittest.TestCase):
    def setUp(self):
        for t in ("applications", "vacancies", "users", "tenants", "tenant_admins", "user_tenant", "invites", "banned"):
            core.db.execute(f"DELETE FROM {t}")
        core.db.execute("INSERT INTO tenants VALUES('vlad','Влад бизнес','recruiter',1)")
        core.db.execute("INSERT INTO tenants VALUES('shop','Магазин','recruiter',1)")
        core.db.executemany("INSERT INTO tenant_admins VALUES(?,?)", [("vlad", ADM_A), ("shop", ADM_B)])
        core.db.executemany("INSERT INTO user_tenant VALUES(?,?)", [(ADM_A, "vlad"), (ADM_B, "shop"), (CLI_A, "vlad"), (CLI_B, "shop")])
        core.db.executemany("INSERT INTO users VALUES(?,?)", [(CLI_A, "ru"), (CLI_B, "ru")])
        core.db.commit()

    def job(self, tenant, title):
        cur = core.db.execute("INSERT INTO vacancies(tenant,title,city,salary,description) VALUES(?,?,?,?,?)", (tenant, title, "Т", "1", "d"))
        core.db.commit()
        return cur.lastrowid

    def apply(self, jid, uid=CLI_A):
        st = St()
        run(rec.apply_start(cb(f"job:{jid}", uid), st))
        run(rec.apply_name(msg("Иван", uid), st))
        run(rec.apply_phone(msg("+998", uid), st))
        run(rec.apply_about(msg("опыт", uid), st))
        run(rec.apply_cv_skip(cb("cvskip", uid), st))

    # ---------- платформа
    def test_owner_creates_space_and_gets_links(self):
        m = msg(uid=OWNER)
        run(core.new_space(m, cmd("studio recruiter Студия HR")))
        self.assertEqual(core.get_tenant("studio")["name"], "Студия HR")
        text = m.answer.call_args[0][0]
        self.assertIn("t.me/TestBot?start=studio", text)
        self.assertIn("start=adm_", text)
        m = msg(uid=OWNER)
        run(core.new_space(m, cmd("studio recruiter Дубль")))
        self.assertIn("уже есть", m.answer.call_args[0][0])
        m = msg(uid=OWNER)
        run(core.new_space(m, cmd("bad recruiter")))
        self.assertIn("Использование", m.answer.call_args[0][0])
        m = msg(uid=OWNER)
        run(core.new_space(m, cmd("x1 nomodule Имя")))
        self.assertIn("Нет такого модуля", m.answer.call_args[0][0])
        m = msg(uid=ADM_A)
        run(core.new_space(m, cmd("hack recruiter Взлом")))
        self.assertIsNone(core.get_tenant("hack"))

    def test_owner_creates_space_with_buttons_wizard(self):
        self.assertEqual(labels(OWNER)[:2], ["➕ Новое заведение", "🏢 Пространства"]) if not core.tenant_of(OWNER) else None
        st = St()
        run(core.new_space_button(msg(uid=OWNER), st))
        run(core.wizard_input(msg("Влад бизнес", OWNER), st))
        m = msg("Влад!", OWNER)
        run(core.wizard_input(m, st))  # недопустимое имя ссылки
        self.assertIn("Не получилось", m.answer.call_args[0][0])
        run(core.wizard_input(msg("vlad", OWNER), st))  # уже существует
        self.assertEqual(st.d["i"], 1)
        run(core.wizard_input(msg("Studio-1", OWNER), st))
        c = cb("wzc:5", OWNER)
        run(core.wizard_choice(c, st))
        tenant = core.get_tenant("studio-1")
        self.assertEqual((tenant["name"], tenant["module"]), ("Влад бизнес", "shop,support"))
        self.assertEqual(core.tenant_of(OWNER), "studio-1")
        sent = c.message.bot.send_message.call_args[0][1]
        self.assertIn("start=studio-1", sent)
        self.assertIn("start=adm_", sent)
        m = msg(uid=ADM_A)
        run(core.new_space_button(m, St()))
        m.answer.assert_not_awaited()

    def test_spaces_enter_button(self):
        c = cb("enter:shop", OWNER)
        run(core.enter_space_btn(c))
        self.assertEqual(core.tenant_of(OWNER), "shop")
        c = cb("enter:shop", ADM_A)
        run(core.enter_space_btn(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))

    def test_bot_never_stays_silent(self):
        m = msg("что-то непонятное", CLI_A)
        run(core.unknown_message(m, St()))
        self.assertIn("кнопками", m.answer.call_args[0][0])
        core.db.execute("DELETE FROM user_tenant WHERE user_id=?", (OWNER,))
        m = msg("привет", OWNER)
        run(core.unknown_message(m, St()))
        self.assertIn("Новое заведение", m.answer.call_args[0][0])
        m = msg("привет", 999)
        run(core.unknown_message(m, St()))
        self.assertIn("havolasi", m.answer.call_args[0][0])
        c = cb("old:button", CLI_A)
        run(core.stale_button(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))

    def test_space_with_several_modules(self):
        m = msg(uid=OWNER)
        run(core.new_space(m, cmd("combo shop,support,leads Комбо")))
        self.assertEqual(core.get_tenant("combo")["module"], "shop,support,leads")
        m = msg(uid=OWNER)
        run(core.new_space(m, cmd("bad2 shop,nothing Имя")))
        self.assertIn("Нет такого модуля", m.answer.call_args[0][0])

    def test_client_enters_space_by_link(self):
        core.db.execute("DELETE FROM user_tenant WHERE user_id=?", (CLI_A,))
        m = msg("/start", CLI_A)
        run(core.start(m, St(), cmd()))
        self.assertIn("ссылке", m.answer.call_args[0][0])
        m = msg("/start vlad", CLI_A)
        run(core.start(m, St(), cmd("vlad")))
        self.assertEqual(core.tenant_of(CLI_A), "vlad")
        self.assertIn("Влад бизнес", m.answer.call_args[0][0])
        m = msg("/start nope", CLI_A)
        run(core.start(m, St(), cmd("nope")))
        self.assertIn("не найдено", m.answer.call_args[0][0])
        core.db.execute("UPDATE tenants SET active=0 WHERE slug='vlad'")
        m = msg("/start vlad", 77)
        run(core.start(m, St(), cmd("vlad")))
        self.assertIsNone(core.tenant_of(77))

    def test_admin_invite_is_single_use(self):
        code = core.new_invite("vlad")
        m = msg("/start", 300)
        run(core.start(m, St(), cmd(code)))
        self.assertTrue(core.is_admin(300))
        self.assertEqual(core.tenant_of(300), "vlad")
        m = msg("/start", 301)
        run(core.start(m, St(), cmd(code)))
        self.assertFalse(core.is_admin(301))
        self.assertIn("недействительна", m.answer.call_args[0][0])

    def test_keyboards_by_role_and_module(self):
        self.assertEqual(labels(CLI_A), ["💼 Вакансии", "📄 Мои отклики", "🌐 Язык"])
        self.assertEqual(labels(ADM_A), [rec.ADM_CAND, rec.ADM_JOBS, rec.ADM_ADD, rec.ADM_REPORT, core.STATS_BTN, core.EXPORT_BTN])
        self.assertIn(core.SPACES_BTN, labels(OWNER))
        self.assertNotIn(core.SPACES_BTN, labels(ADM_A))

    def test_rights_are_per_space(self):
        self.assertTrue(core.is_admin(ADM_A))
        self.assertFalse(core.is_admin(ADM_A, "shop"))
        self.assertFalse(core.is_admin(CLI_A))
        self.assertTrue(core.is_admin(OWNER))

    def test_spaces_list_enter_and_delete(self):
        m = msg(uid=OWNER)
        run(core.spaces(m))
        self.assertIn("start=vlad", m.answer.call_args[0][0])
        run(core.enter_space(msg(uid=OWNER), cmd("shop")))
        self.assertEqual(core.tenant_of(OWNER), "shop")
        jid = self.job("vlad", "Менеджер")
        self.apply(jid)
        run(core.del_space(msg(uid=ADM_A), cmd("vlad")))
        self.assertIsNotNone(core.get_tenant("vlad"))
        run(core.del_space(msg(uid=OWNER), cmd("vlad")))
        self.assertIsNone(core.get_tenant("vlad"))
        for table in ("vacancies", "applications", "tenant_admins"):
            self.assertEqual(core.db.execute(f"SELECT COUNT(*) FROM {table} WHERE tenant='vlad'").fetchone()[0], 0)
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0], 0 + 0)

    def test_set_unset_admin_by_owner_only(self):
        run(core.set_admin(msg(uid=OWNER), cmd("vlad 400")))
        self.assertTrue(core.is_admin(400, "vlad"))
        run(core.unset_admin(msg(uid=OWNER), cmd("vlad 400")))
        self.assertFalse(core.is_admin(400, "vlad"))
        run(core.set_admin(msg(uid=ADM_A), cmd("vlad 401")))
        self.assertFalse(core.is_admin(401, "vlad"))

    def test_ban_and_broadcast_are_per_space(self):
        run(core.ban_cmd(msg(uid=ADM_A), cmd(str(CLI_A))))
        self.assertTrue(core.is_banned(CLI_A))
        self.assertFalse(core.is_banned(CLI_B))
        called = []

        async def handler(e, d):
            called.append(1)
        ev = MagicMock()
        ev.from_user.id = CLI_A
        run(core.ban_guard(handler, ev, {}))
        self.assertEqual(called, [])
        run(core.unban_cmd(msg(uid=ADM_A), cmd(str(CLI_A))))
        run(core.ban_guard(handler, ev, {}))
        self.assertEqual(called, [1])
        m = msg(uid=ADM_A)
        run(core.broadcast(m, cmd("Привет")))
        self.assertIn("Отправлено: 1", m.answer.call_args[0][0])  # только клиент vlad
        self.assertEqual(m.bot.send_message.call_args[0][0], CLI_A)

    def test_texts_and_modules(self):
        for key, v in core.TXT.items():
            self.assertEqual(set(v), {"ru", "uz", "en"}, key)
        self.assertEqual(len(core.load_modules(["recruiter"])), 1)

    # ---------- рекрутер + изоляция
    def test_vacancy_creation_scoped_to_space(self):
        st = St()
        run(rec.add_job_start(msg(uid=ADM_A), st))
        for step, text in ((rec.add_job_title, "Рекрутер"), (rec.add_job_city, "Ташкент"), (rec.add_job_salary, "6 млн")):
            run(step(msg(text, ADM_A), st))
        run(rec.add_job_desc(msg("Подбор", ADM_A), st))
        self.assertEqual(core.db.execute("SELECT tenant FROM vacancies").fetchone()[0], "vlad")
        m = msg(uid=CLI_A)
        run(rec.jobs(m))
        self.assertIn("Рекрутер", m.answer.call_args[0][0])
        m = msg(uid=CLI_B)
        run(rec.jobs(m))
        self.assertIn("нет", m.answer.call_args[0][0])
        m = msg(uid=CLI_A)
        run(rec.add_job_start(m, St()))
        m.answer.assert_not_awaited()

    def test_application_notifies_only_that_spaces_admins(self):
        jid = self.job("vlad", "Менеджер")
        bot = mkbot()
        st = St({"job": jid, "name": "Иван", "phone": "1", "about": "x"})
        run(rec.finish_application(bot, MagicMock(id=CLI_A), st, "photo:FID"))
        self.assertEqual(bot.send_photo.call_args[0][:2], (ADM_A, "FID"))
        self.assertEqual({c[0][0] for c in bot.send_photo.call_args_list + bot.send_message.call_args_list} - {CLI_A}, {ADM_A})

    def test_notification_falls_back_to_owner_when_no_admins(self):
        core.db.execute("DELETE FROM tenant_admins WHERE tenant='vlad'")
        jid = self.job("vlad", "Менеджер")
        bot = mkbot()
        run(rec.finish_application(bot, MagicMock(id=CLI_A), St({"job": jid, "name": "И", "phone": "1", "about": "x"}), None))
        self.assertIn(OWNER, [c[0][0] for c in bot.send_message.call_args_list])

    def test_cannot_apply_to_other_spaces_vacancy_or_twice(self):
        other = self.job("shop", "Чужая")
        c = cb(f"job:{other}", CLI_A)
        run(rec.apply_start(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        mine = self.job("vlad", "Моя")
        self.apply(mine)
        c = cb(f"job:{mine}", CLI_A)
        run(rec.apply_start(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))

    def test_admin_cannot_touch_other_spaces_data(self):
        mine, other = self.job("vlad", "Моя"), self.job("shop", "Чужая")
        self.apply(mine, CLI_A)
        self.apply(other, CLI_B)
        a_id = core.db.execute("SELECT id FROM applications WHERE tenant='vlad'").fetchone()[0]
        b_id = core.db.execute("SELECT id FROM applications WHERE tenant='shop'").fetchone()[0]
        c = cb(f"cand:{b_id}:hired", ADM_A)
        run(rec.candidate_status(c))
        self.assertEqual(core.db.execute("SELECT status FROM applications WHERE id=?", (b_id,)).fetchone()[0], "new")
        run(rec.candidate_status(cb(f"cand:{a_id}:invited", ADM_A)))
        self.assertEqual(core.db.execute("SELECT status FROM applications WHERE id=?", (a_id,)).fetchone()[0], "invited")
        run(rec.vacancy_action(cb(f"vac:{other}:del", ADM_A)))
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM vacancies WHERE id=?", (other,)).fetchone()[0], 1)
        m = msg(uid=ADM_A)
        run(rec.candidates(m))
        self.assertEqual(m.bot.send_message.await_count, 1)
        m = msg(uid=ADM_A)
        run(rec.manage_jobs(m))
        self.assertNotIn("Чужая", m.answer.call_args[0][0])
        m = msg(uid=ADM_A)
        run(rec.report(m))
        self.assertIn("Всего: 1", m.answer.call_args[0][0])
        m = msg(uid=CLI_A)
        run(rec.my_apps(m))
        self.assertNotIn("Чужая", m.answer.call_args[0][0])
        c = cb(f"cand:{a_id}:hired", CLI_A)
        run(rec.candidate_status(c))
        self.assertEqual(core.db.execute("SELECT status FROM applications WHERE id=?", (a_id,)).fetchone()[0], "invited")


if __name__ == "__main__":
    unittest.main(verbosity=2)
