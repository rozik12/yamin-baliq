"""Выгрузка, статистика, оценки, рассылки по расписанию, промокоды и бонусы магазина."""
import datetime as dt
import unittest
from unittest.mock import MagicMock

from helpers import St, buttons, cb, cmd, labels, msg, mkbot, run

import core  # noqa: E402
from modules import booking, leads, recruiter, shop, support  # noqa: E402,F401

OWNER, ADM_A, ADM_B, CLI_A, CLI_B = 111, 201, 202, 5, 6


class Base(unittest.TestCase):
    def setUp(self):
        for t in ("products", "shop_orders", "shop_promos", "shop_bonuses", "leads", "services", "bookings", "ratings", "scheduled", "users",
                  "tenants", "tenant_admins", "user_tenant", "settings", "faq", "tickets", "profiles", "referrals", "shop_winback"):
            core.db.execute(f"DELETE FROM {t}")
        core.db.execute("INSERT INTO tenants(slug,name,module,active) VALUES('a','А','shop,booking,leads,support',1)")
        core.db.execute("INSERT INTO tenants(slug,name,module,active) VALUES('b','Б','shop',1)")
        core.db.executemany("INSERT INTO tenant_admins VALUES(?,?)", [("a", ADM_A), ("b", ADM_B)])
        core.db.executemany("INSERT INTO user_tenant VALUES(?,?)", [(ADM_A, "a"), (ADM_B, "b"), (CLI_A, "a"), (CLI_B, "b")])
        core.db.executemany("INSERT INTO users VALUES(?,?)", [(u, "ru") for u in (ADM_A, ADM_B, CLI_A, CLI_B)])
        core.db.commit()
        shop.carts.clear()
        cur = core.db.execute("INSERT INTO products(tenant,category,name,price,descr) VALUES('a','К','Футболка',100000,'d')")
        self.p = cur.lastrowid
        core.db.commit()

    def order(self, uid=CLI_A, kind=1, promo=None, spend=None, qty=1):
        shop.cart_of(uid)[self.p] = qty
        st = St()
        run(shop.checkout(cb("scheckout", uid), st))
        run(core.wizard_choice(cb(f"wzc:{kind}", uid), st))          # самовывоз
        run(core.wizard_input(msg("-", uid), st))                     # комментарий
        if core.db.execute("SELECT 1 FROM shop_promos WHERE tenant='a'").fetchone():
            run(core.wizard_input(msg(promo or "-", uid), st))
        if shop.max_spend(uid, {"promo": promo}) > 0:
            run(core.wizard_choice(cb(f"wzc:{0 if spend else 1}", uid), st))
        if core.db.execute("SELECT phone FROM profiles WHERE tenant=? AND user_id=? AND phone IS NOT NULL AND phone!=''", (core.tenant_of(uid), uid)).fetchone():
            run(core.wizard_choice(cb("wzc:0", uid), st))  # прошлый номер
        else:
            run(core.wizard_input(msg("+998901112233", uid), st))
        run(core.wizard_choice(cb("wzc:0", uid), st))                 # наличными
        return st


class ExportStatsTests(Base):
    def test_export_only_own_tenant_and_modules(self):
        self.order()
        core.db.execute("INSERT INTO leads(tenant,user_id,name,phone,message) VALUES('a',5,'Иван','1','привет')")
        core.db.execute("INSERT INTO leads(tenant,user_id,name,phone,message) VALUES('b',6,'Чужой','2','x')")
        core.db.commit()
        m = msg(uid=ADM_A)
        run(core.export_cmd(m))
        names = [c[1]["caption"].split(":")[0] for c in m.answer_document.call_args_list]
        self.assertIn("orders.csv", names)
        self.assertIn("leads.csv", names)
        doc = [c for c in m.answer_document.call_args_list if c[1]["caption"].startswith("leads.csv")][0][0][0]
        data = doc.data.decode("utf-8-sig")
        self.assertIn("Иван", data)
        self.assertNotIn("Чужой", data)
        m = msg(uid=ADM_B)
        run(core.export_cmd(m))
        self.assertEqual(sorted(c[1]["caption"].split(":")[0] for c in m.answer_document.call_args_list), ["clients.csv", "orders.csv"])
        m = msg(uid=CLI_A)
        run(core.export_cmd(m))
        m.answer_document.assert_not_awaited()

    def test_stats_for_tenant_and_platform(self):
        self.order()
        run(shop.order_action(cb("so:1:ok", ADM_A)))
        m = msg(uid=ADM_A)
        run(core.stats_cmd(m))
        text = m.answer.call_args[0][0]
        self.assertIn("заказов 1", text)
        self.assertIn("Клиентов: 1", text)
        self.assertIn("Записи", text)
        m = msg(uid=OWNER)
        core.db.execute("DELETE FROM user_tenant WHERE user_id=?", (OWNER,))
        run(core.stats_cmd(m))
        self.assertIn("Платформа: заведений 2", m.answer.call_args[0][0])
        m = msg(uid=CLI_A)
        run(core.stats_cmd(m))
        m.answer.assert_not_awaited()


class RatingTests(Base):
    def test_rating_after_done_and_low_rating_alert(self):
        self.order()
        run(shop.order_action(cb("so:1:ok", ADM_A)))
        run(shop.order_action(cb("so:1:ready", ADM_A)))
        c = cb("so:1:done", ADM_A)
        run(shop.order_action(c))
        self.assertIn("rt:shop:1:5", buttons(c.bot.send_message.call_args_list[-1][1]["reply_markup"]))
        c = cb("rt:shop:1:2", CLI_A)
        run(core.rate(c))
        self.assertEqual(core.avg_rating("a", "shop"), (2.0, 1))
        self.assertIn("Низкая оценка", c.bot.send_message.call_args[0][1])
        c = cb("rt:shop:1:5", CLI_A)
        run(core.rate(c))
        self.assertEqual(core.avg_rating("a", "shop"), (5.0, 1))  # переоценка заменяет, а не дублирует
        self.assertEqual(core.avg_rating("b", "shop"), (None, 0))

    def test_rating_after_booking_done(self):
        core.db.execute("INSERT INTO services(tenant,name,minutes,price) VALUES('a','С',60,1)")
        core.db.execute("INSERT INTO bookings(tenant,user_id,service_id,start,name,phone,status) VALUES('a',5,1,'2030-01-01 10:00','И','1','confirmed')")
        core.db.commit()
        c = cb("bka:1:done", ADM_A)
        run(booking.booking_action(c))
        self.assertIn("rt:booking:1:3", buttons(c.bot.send_message.call_args[1]["reply_markup"]))


class ScheduleTests(Base):
    def test_schedule_validation_listing_cancel_and_send(self):
        m = msg(uid=ADM_A)
        run(core.schedule_cmd(m, cmd("вчера 10:00 текст")))
        self.assertIn("Использование", m.answer.call_args[0][0])
        past = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        m = msg(uid=ADM_A)
        run(core.schedule_cmd(m, cmd(f"{past} 10:00 Привет")))
        self.assertIn("будущем", m.answer.call_args[0][0])
        future = (dt.datetime.now() + dt.timedelta(days=2)).strftime("%Y-%m-%d")
        m = msg(uid=ADM_A)
        run(core.schedule_cmd(m, cmd(f"{future} 18:00 Скидки!")))
        self.assertIn("№1", m.answer.call_args[0][0])
        row = core.db.execute("SELECT tenant, run_at FROM scheduled").fetchone()
        self.assertEqual(row, ("a", f"{future} 13:00"))  # 18:00 по Ташкенту = 13:00 UTC
        m = msg(uid=ADM_A)
        run(core.scheduled_list(m))
        self.assertIn("18:00", m.answer.call_args[0][0])
        bot = mkbot()
        self.assertEqual(run(core.send_due_broadcasts(bot, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))), 0)
        far = dt.datetime.strptime(f"{future} 13:01", "%Y-%m-%d %H:%M")
        self.assertEqual(run(core.send_due_broadcasts(bot, far)), 1)
        self.assertEqual(run(core.send_due_broadcasts(bot, far)), 0)
        sent = [c[0][0] for c in bot.send_message.call_args_list]
        self.assertIn(CLI_A, sent)
        self.assertNotIn(CLI_B, sent)
        m = msg(uid=ADM_A)
        run(core.schedule_cmd(m, cmd(f"{future} 19:00 Ещё")))
        run(core.unschedule(msg(uid=ADM_B), cmd("2")))
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM scheduled WHERE sent=0").fetchone()[0], 1)
        run(core.unschedule(msg(uid=ADM_A), cmd("2")))
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM scheduled WHERE sent=0").fetchone()[0], 0)
        m = msg(uid=CLI_A)
        run(core.schedule_cmd(m, cmd(f"{future} 18:00 Взлом")))
        m.answer.assert_not_awaited()


class PromoBonusTests(Base):
    def test_promo_code_management_and_discount(self):
        st = St()
        c = cb("spadd", ADM_A)
        run(shop.promo_add(c, st))
        run(core.wizard_input(msg("sale10", ADM_A), st))
        m = msg("95", ADM_A)
        run(core.wizard_input(m, st))
        self.assertIn("Не получилось", m.answer.call_args[0][0])
        run(core.wizard_input(msg("10", ADM_A), st))
        self.assertEqual(shop.promo_percent("a", "SALE10"), 10)
        self.assertEqual(shop.promo_percent("b", "SALE10"), 0)
        self.order(promo="sale10")
        row = core.db.execute("SELECT total, promo FROM shop_orders").fetchone()
        self.assertEqual(row, (90000, "sale10"))
        run(shop.promo_delete(cb("spd:SALE10", ADM_B)))
        self.assertEqual(shop.promo_percent("a", "SALE10"), 10)
        run(shop.promo_delete(cb("spd:SALE10", ADM_A)))
        self.assertEqual(shop.promo_percent("a", "SALE10"), 0)

    def test_invalid_promo_rejected_in_wizard(self):
        core.db.execute("INSERT INTO shop_promos VALUES('a','OK',10)")
        core.db.commit()
        shop.cart_of(CLI_A)[self.p] = 1
        st = St()
        run(shop.checkout(cb("scheckout", CLI_A), st))
        run(core.wizard_choice(cb("wzc:1", CLI_A), st))
        run(core.wizard_input(msg("-", CLI_A), st))
        m = msg("NOPE", CLI_A)
        run(core.wizard_input(m, st))
        self.assertIn("Не получилось", m.answer.call_args[0][0])

    def test_cashback_earn_spend_refund(self):
        core.set_setting("a", "cashback_percent", 10)
        self.order()
        for act in ("ok", "ready", "done"):
            run(shop.order_action(cb(f"so:1:{act}", ADM_A)))
        self.assertEqual(shop.balance("a", CLI_A), 10000)
        run(shop.order_action(cb("so:1:done", ADM_A)))  # повторно не начисляется
        self.assertEqual(shop.balance("a", CLI_A), 10000)
        self.assertEqual(shop.balance("b", CLI_A), 0)
        # трата: максимум 50% суммы заказа и не больше баланса
        self.assertEqual(shop.max_spend(CLI_A, {}), 0)  # корзина пуста
        shop.cart_of(CLI_A)[self.p] = 1
        self.assertEqual(shop.max_spend(CLI_A, {}), 10000)
        shop.cart_of(CLI_A).clear()
        self.order(spend=True)
        row = core.db.execute("SELECT total, bonus_used FROM shop_orders WHERE id=2").fetchone()
        self.assertEqual(row, (90000, 10000))
        self.assertEqual(shop.balance("a", CLI_A), 0)
        # отмена возвращает баллы
        run(shop.cancel_order(cb("scx:2", CLI_A)))
        self.assertEqual(shop.balance("a", CLI_A), 10000)
        run(shop.cancel_order(cb("scx:2", CLI_A)))
        self.assertEqual(shop.balance("a", CLI_A), 10000)
        # отказ админа тоже возвращает
        self.order(spend=True)
        self.assertEqual(shop.balance("a", CLI_A), 0)
        run(shop.order_action(cb("so:3:no", ADM_A)))
        self.assertEqual(shop.balance("a", CLI_A), 10000)
        m = msg(uid=CLI_A)
        run(shop.my_orders(m))
        self.assertIn("10 000 сум", m.answer.call_args_list[0][0][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
