import unittest

from helpers import St, buttons, cb, cmd, labels, msg, mkbot, run

import core  # noqa: E402
from modules import leads, support  # noqa: E402

ADM_A, ADM_B, CLI_A, CLI_B = 201, 202, 5, 6


class Base(unittest.TestCase):
    def setUp(self):
        for t in ("leads", "faq", "tickets", "users", "tenants", "tenant_admins", "user_tenant", "settings"):
            core.db.execute(f"DELETE FROM {t}")
        core.db.execute("INSERT INTO tenants VALUES('a','Агентство А','leads,support',1)")
        core.db.execute("INSERT INTO tenants VALUES('b','Агентство Б','leads,support',1)")
        core.db.executemany("INSERT INTO tenant_admins VALUES(?,?)", [("a", ADM_A), ("b", ADM_B)])
        core.db.executemany("INSERT INTO user_tenant VALUES(?,?)", [(ADM_A, "a"), (ADM_B, "b"), (CLI_A, "a"), (CLI_B, "b")])
        core.db.executemany("INSERT INTO users VALUES(?,?)", [(u, "ru") for u in (ADM_A, ADM_B, CLI_A, CLI_B)])
        core.db.commit()


class LeadsTests(Base):
    def submit(self, uid=CLI_A, topic=True):
        st = St()
        run(leads.lead_start(msg(uid=uid), st))
        if topic:
            run(core.wizard_choice(cb("wzc:1", uid), st))
        run(core.wizard_input(msg("Иван", uid), st))
        bot = mkbot()
        m = msg("+998901112233", uid)
        m.bot = bot
        run(core.wizard_input(m, st))
        m = msg("Нужна квартира", uid)
        m.bot = bot
        run(core.wizard_input(m, st))
        return bot

    def test_two_modules_keyboard(self):
        self.assertEqual(labels(core.main_kb, CLI_A), ["📝 Оставить заявку", "📄 Мои заявки", "❓ Вопросы", "🆘 Оператор", "🌐 Язык"])
        admin = labels(core.main_kb, ADM_A)
        for b in (leads.ADM_LEADS, leads.ADM_REPORT, support.ADM_ADDFAQ, support.ADM_TICKETS):
            self.assertIn(b, admin)

    def test_lead_with_topics_notifies_only_own_admin(self):
        core.set_setting("a", "lead_topics", "Квартира, Дом, Офис")
        bot = self.submit()
        row = core.db.execute("SELECT tenant, topic, name, status FROM leads").fetchone()
        self.assertEqual(row, ("a", "Дом", "Иван", "new"))
        self.assertEqual({c[0][0] for c in bot.send_message.call_args_list}, {CLI_A, ADM_A})

    def test_lead_without_topics_skips_choice(self):
        self.submit(topic=False)
        self.assertEqual(core.db.execute("SELECT topic, message FROM leads").fetchone(), (None, "Нужна квартира"))

    def test_status_flow_reply_isolation_and_report(self):
        self.submit(topic=False)
        c = cb("lda:1:work", ADM_B)
        run(leads.lead_action(c))
        self.assertEqual(core.db.execute("SELECT status FROM leads").fetchone()[0], "new")
        c = cb("lda:1:work", ADM_A)
        run(leads.lead_action(c))
        self.assertEqual(core.db.execute("SELECT status FROM leads").fetchone()[0], "work")
        self.assertIn("в работе", c.bot.send_message.call_args[0][1])
        st = St()
        run(leads.lead_reply(cb("ldm:1", ADM_A), st))
        bot = mkbot()
        m = msg("Звоните завтра", ADM_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        self.assertEqual(bot.send_message.call_args_list[0][0][0], CLI_A)
        self.assertIn("Звоните завтра", bot.send_message.call_args_list[0][0][1])
        c = cb("ldm:1", ADM_B)
        run(leads.lead_reply(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        run(leads.lead_action(cb("lda:1:done", ADM_A)))
        c = cb("lda:1:rejected", ADM_A)
        run(leads.lead_action(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        m = msg(uid=ADM_A)
        run(leads.report(m))
        self.assertIn("Всего: 1", m.answer.call_args[0][0])
        self.assertIn("100%", m.answer.call_args[0][0])
        m = msg(uid=ADM_B)
        run(leads.report(m))
        self.assertIn("Всего: 0", m.answer.call_args[0][0])
        m = msg(uid=CLI_A)
        run(leads.my_leads(m))
        self.assertIn("выполнена", m.answer.call_args[0][0])
        m = msg(uid=ADM_A)
        run(leads.admin_leads(m))
        self.assertIn("нет", m.answer.call_args[0][0])


class SupportTests(Base):
    def test_faq_admin_add_client_read_and_isolation(self):
        st = St()
        run(support.add_faq(msg(uid=ADM_A), st))
        run(core.wizard_input(msg("Как оплатить?", ADM_A), st))
        run(core.wizard_input(msg("Переводом на карту", ADM_A), st))
        m = msg(uid=CLI_A)
        run(support.faq(m))
        self.assertIn("fq:1", buttons(m.answer.call_args[1]["reply_markup"]))
        c = cb("fq:1", CLI_A)
        run(support.faq_answer(c))
        self.assertIn("Переводом на карту", c.message.answer.call_args[0][0])
        c = cb("fq:1", CLI_B)
        run(support.faq_answer(c))
        c.message.answer.assert_not_awaited()
        m = msg(uid=CLI_B)
        run(support.faq(m))
        self.assertIn("пока нет", m.answer.call_args[0][0])
        run(support.delete_faq(cb("fqd:1", ADM_B)))
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM faq").fetchone()[0], 1)
        run(support.delete_faq(cb("fqd:1", ADM_A)))
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM faq").fetchone()[0], 0)

    def test_ticket_reply_close_and_isolation(self):
        st = St()
        run(support.support_start(msg(uid=CLI_A), st))
        bot = mkbot()
        m = msg("Где мой заказ?", CLI_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        self.assertEqual({c[0][0] for c in bot.send_message.call_args_list}, {CLI_A, ADM_A})
        st = St()
        run(support.ticket_action(cb("tk:1:reply", ADM_A), st))
        bot = mkbot()
        m = msg("Уже в пути", ADM_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        self.assertEqual(bot.send_message.call_args_list[0][0][0], CLI_A)
        self.assertIn("Уже в пути", bot.send_message.call_args_list[0][0][1])
        c = cb("tk:1:close", ADM_B)
        run(support.ticket_action(c, St()))
        self.assertEqual(core.db.execute("SELECT status FROM tickets").fetchone()[0], "open")
        run(support.ticket_action(cb("tk:1:close", ADM_A), St()))
        self.assertEqual(core.db.execute("SELECT status FROM tickets").fetchone()[0], "closed")
        m = msg(uid=ADM_A)
        run(support.open_tickets(m))
        self.assertIn("нет", m.answer.call_args[0][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
