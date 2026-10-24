"""О нас, клиенты, рассылка кнопкой, утренняя сводка, напоминания, рефералы, поиск, повтор заказа, выходные дни."""
import datetime as dt
import unittest
from unittest.mock import MagicMock

from helpers import St, buttons, cb, cmd, labels, msg, mkbot, run

import core  # noqa: E402
from modules import booking, leads, recruiter, shop, support  # noqa: E402,F401

OWNER, ADM_A, ADM_B, CLI_A, CLI_B, CLI_C = 111, 201, 202, 5, 6, 7


class Base(unittest.TestCase):
    def setUp(self):
        for t in ("products", "shop_orders", "shop_bonuses", "shop_promos", "leads", "services", "bookings", "dayoff", "profiles", "referrals",
                  "scheduled", "users", "tenants", "tenant_admins", "user_tenant", "settings", "ratings", "faq", "tickets", "invites", "shop_winback"):
            core.db.execute(f"DELETE FROM {t}")
        core.db.execute("INSERT INTO tenants(slug,name,module,active) VALUES('a','Кафе А','shop,booking,leads',1)")
        core.db.execute("INSERT INTO tenants(slug,name,module,active) VALUES('b','Кафе Б','shop',1)")
        core.db.executemany("INSERT INTO tenant_admins VALUES(?,?)", [("a", ADM_A), ("b", ADM_B)])
        core.db.executemany("INSERT INTO user_tenant VALUES(?,?)", [(ADM_A, "a"), (ADM_B, "b"), (CLI_A, "a"), (CLI_B, "b"), (CLI_C, "a")])
        core.db.executemany("INSERT INTO users VALUES(?,?)", [(u, "ru") for u in (ADM_A, ADM_B, CLI_A, CLI_B, CLI_C)])
        core.db.commit()
        shop.carts.clear()
        core.pending_broadcast.clear()
        self.p1 = core.db.execute("INSERT INTO products(tenant,category,name,price,descr) VALUES('a','Еда','Лагман',40000,'острый')").lastrowid
        self.p2 = core.db.execute("INSERT INTO products(tenant,category,name,price,descr) VALUES('a','Еда','Плов',35000,'')").lastrowid
        core.db.commit()

    def order(self, uid=CLI_A, items=None):
        shop.cart_of(uid).update(items or {self.p1: 1})
        st = St()
        run(shop.checkout(cb("scheckout", uid), st))
        run(core.wizard_choice(cb("wzc:1", uid), st))
        run(core.wizard_input(msg("-", uid), st))
        if shop.max_spend(uid, {}) > 0:
            run(core.wizard_choice(cb("wzc:1", uid), st))
        if core.db.execute("SELECT phone FROM profiles WHERE tenant=? AND user_id=? AND phone IS NOT NULL AND phone!=''", (core.tenant_of(uid), uid)).fetchone():
            run(core.wizard_choice(cb("wzc:0", uid), st))  # прошлый номер
        else:
            run(core.wizard_input(msg("+998901112233", uid), st))
        run(core.wizard_choice(cb("wzc:0", uid), st))

    def finish(self, oid, admin=ADM_A):
        for act in ("ok", "ready", "done"):
            run(shop.order_action(cb(f"so:{oid}:{act}", admin)))


class AboutTests(Base):
    def test_about_us_shows_settings_and_location(self):
        m = msg(uid=CLI_A)
        run(core.about_cmd(m))
        self.assertIn("скоро появится", m.answer.call_args[0][0])
        for key, value in (("about", "Лучший лагман"), ("address", "Ташкент, Навои 1"), ("hours", "10:00-23:00"), ("instagram", "@kafe_a")):
            st = St()
            run(core.settings_pick(cb(f"set:{key}", ADM_A), st))
            run(core.wizard_input(msg(value, ADM_A), st))
        st = St()
        run(core.settings_pick(cb("set:geo", ADM_A), st))
        run(core.wizard_input(msg("не точка", ADM_A), st))  # не геолокация: не принимается
        self.assertIsNone(core.get_setting("a", "geo"))
        run(core.wizard_input(msg(uid=ADM_A, location=MagicMock(latitude=41.31, longitude=69.24)), st))
        m = msg(uid=CLI_A)
        run(core.about_cmd(m))
        text = m.answer.call_args_list[0][0][0]
        for word in ("Кафе А", "Лучший лагман", "Ташкент, Навои 1", "10:00-23:00", "@kafe_a"):
            self.assertIn(word, text)
        self.assertEqual(m.answer_location.call_args[0], (41.31, 69.24))
        m = msg(uid=CLI_B)
        run(core.about_cmd(m))
        self.assertNotIn("Лагман", m.answer.call_args[0][0])
        self.assertIn("scheckout" if False else "set:about", buttons(self.menu(ADM_A)))

    def menu(self, uid):
        m = msg(uid=uid)
        run(core.settings_menu(m))
        return m.answer.call_args[1]["reply_markup"]


class ClientsTests(Base):
    def test_profiles_recorded_and_exported_per_tenant(self):
        async def handler(e, d):
            return None
        for uid, name, tenant in ((CLI_A, "Али Валиев", "a"), (CLI_B, "Чужой", "b")):
            ev = MagicMock()
            ev.from_user.id, ev.from_user.full_name, ev.from_user.username = uid, name, f"u{uid}"
            run(core.ban_guard(handler, ev, {}))
        rows = core.db.execute("SELECT tenant, name, username FROM profiles ORDER BY user_id").fetchall()
        self.assertEqual(rows, [("a", "Али Валиев", "u5"), ("b", "Чужой", "u6")])
        header, data = core.export_clients("a")
        self.assertEqual([r[1] for r in data], ["Али Валиев"])
        self.assertIn("Новых клиентов за 7 дней: 1", core.clients_stats("a")[0])
        m = msg(uid=ADM_A)
        run(core.export_cmd(m))
        names = [c[1]["caption"].split(":")[0] for c in m.answer_document.call_args_list]
        self.assertIn("clients.csv", names)

    def test_top_clients_in_stats(self):
        core.db.execute("INSERT INTO profiles(tenant,user_id,name) VALUES('a',5,'Али Валиев')")
        core.db.commit()
        self.order(CLI_A, {self.p1: 3})
        run(shop.order_action(cb("so:1:ok", ADM_A)))
        self.assertIn("Али Валиев", " ".join(core.tenant_stats("a")))


class BroadcastTests(Base):
    def prepare(self, photo=None):
        st = St()
        run(core.broadcast_button(msg(uid=ADM_A), st))
        run(core.wizard_input(msg("Скидка 20% до воскресенья!", ADM_A), st))
        bot = mkbot()
        m = msg("-" if not photo else None, ADM_A, photo=[MagicMock(file_id="PH")] if photo else None)
        m.bot = bot
        run(core.wizard_input(m, st))
        return bot

    def test_preview_confirm_sends_only_own_clients(self):
        bot = self.prepare()
        self.assertIn("Предпросмотр", bot.send_message.call_args[0][1])
        self.assertIn("2 клиентам", str(bot.send_message.call_args[1]["reply_markup"]))
        c = cb("bc:yes", CLI_A)
        run(core.broadcast_confirm(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        c = cb("bc:yes", ADM_A)
        run(core.broadcast_confirm(c))
        sent = sorted(x[0][0] for x in c.bot.send_message.call_args_list if "Скидка" in x[0][1])
        self.assertEqual(sent, [CLI_A, CLI_C])
        self.assertIn("2 клиентам", c.message.answer.call_args[0][0])
        c = cb("bc:yes", ADM_A)
        run(core.broadcast_confirm(c))  # повторно нельзя
        self.assertTrue(c.answer.call_args[1].get("show_alert"))

    def test_cancel_and_photo(self):
        self.prepare()
        c = cb("bc:no", ADM_A)
        run(core.broadcast_confirm(c))
        self.assertIn("Отменено", c.message.answer.call_args[0][0])
        bot = self.prepare(photo=True)
        self.assertEqual(bot.send_photo.call_args[0][:2], (ADM_A, "PH"))
        c = cb("bc:yes", ADM_A)
        run(core.broadcast_confirm(c))
        self.assertEqual(sorted(x[0][0] for x in c.bot.send_photo.call_args_list), [CLI_A, CLI_C])
        m = msg(uid=CLI_A)
        run(core.broadcast_button(m, St()))
        m.answer.assert_not_awaited()


class SummaryTests(Base):
    def test_morning_summary_once_per_day_and_respects_switch(self):
        core.db.execute("INSERT INTO services(tenant,name,minutes,price) VALUES('a','Стрижка',60,1)")
        local_today = core.local_today("a").date()
        core.db.execute("INSERT INTO bookings(tenant,user_id,service_id,start,name,phone,status) VALUES('a',5,1,?,'Иван','1','confirmed')",
                        (f"{local_today.isoformat()} 10:00",))
        core.db.commit()
        morning = dt.datetime.combine(local_today, dt.time(9, 10)) - dt.timedelta(hours=5)
        bot = mkbot()
        self.assertEqual(run(core.send_daily_summaries(bot, morning)), 2)  # оба заведения
        self.assertEqual(run(core.send_daily_summaries(bot, morning)), 0)
        text_a = [c[0][1] for c in bot.send_message.call_args_list if c[0][0] == ADM_A][0]
        self.assertIn("Доброе утро", text_a)
        self.assertIn("Сегодня записей: 1", text_a)
        self.assertIn("10:00 Стрижка", text_a)
        self.assertNotIn("Сегодня записей", [c[0][1] for c in bot.send_message.call_args_list if c[0][0] == ADM_B][0])
        # не 9 утра: молчим
        core.db.execute("DELETE FROM settings WHERE key='last_summary'")
        noon = morning + dt.timedelta(hours=4)
        self.assertEqual(run(core.send_daily_summaries(bot, noon)), 0)
        # отключено настройкой
        core.set_setting("a", "daily_summary", 0)
        self.assertEqual(run(core.send_daily_summaries(bot, morning)), 1)

    def test_stale_items_nudged_once(self):
        self.order(CLI_A)
        core.db.execute("INSERT INTO leads(tenant,user_id,name,phone,message) VALUES('a',5,'И','1','x')")
        core.db.commit()
        bot = mkbot()
        self.assertEqual(run(core.nudge_stale(bot)), 0)  # только что созданы
        core.db.execute("UPDATE shop_orders SET created=datetime('now','-11 minutes')")
        core.db.execute("UPDATE leads SET created=datetime('now','-31 minutes')")
        core.db.commit()
        self.assertEqual(run(core.nudge_stale(bot)), 2)
        self.assertEqual(run(core.nudge_stale(bot)), 0)
        texts = [c[0][1] for c in bot.send_message.call_args_list]
        self.assertTrue(any("Заказ №1" in x for x in texts))
        self.assertTrue(any("Заявка №1" in x for x in texts))
        self.assertEqual({c[0][0] for c in bot.send_message.call_args_list}, {ADM_A})
        # обработанный заказ не беспокоит
        self.order(CLI_C)
        run(shop.order_action(cb("so:2:ok", ADM_A)))
        core.db.execute("UPDATE shop_orders SET created=datetime('now','-30 minutes') WHERE id=2")
        core.db.commit()
        self.assertEqual(run(core.nudge_stale(bot)), 0)


class ReferralTests(Base):
    def join(self, uid, payload):
        core.db.execute("DELETE FROM user_tenant WHERE user_id=?", (uid,))
        core.db.commit()
        run(core.start(msg("/start", uid), St(), cmd(payload)))

    def test_invite_link_reward_once_on_first_done_order(self):
        core.db.execute("INSERT INTO profiles(tenant,user_id,name) VALUES('a',5,'Али')")
        core.db.commit()
        core.set_setting("a", "referral_bonus", 5000)
        m = msg(uid=CLI_A)
        run(core.invite_cmd(m))
        text = m.answer.call_args[0][0]
        self.assertIn("start=a--r5", text)
        self.assertIn("5000", text)
        # новый клиент приходит по ссылке друга
        self.join(40, "a--r5")
        self.assertEqual(core.db.execute("SELECT referrer, rewarded FROM referrals WHERE user_id=40").fetchone(), (5, 0))
        self.join(40, "a--r5")  # уже клиент: повторно не считается
        self.join(41, "a--r41")  # сам себя нельзя
        self.join(42, "a--r999")  # несуществующий приглашающий
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM referrals").fetchone()[0], 1)
        core.db.execute("INSERT INTO users VALUES(40,'ru')")
        core.db.commit()
        self.order(40)
        self.finish(1)
        self.assertEqual(shop.balance("a", 5), 5000)
        self.order(40)
        self.finish(2)
        self.assertEqual(shop.balance("a", 5), 5000)  # награда только за первый заказ
        self.assertEqual(core.db.execute("SELECT rewarded FROM referrals").fetchone()[0], 1)

    def test_no_reward_when_bonus_off(self):
        core.db.execute("INSERT INTO profiles(tenant,user_id,name) VALUES('a',5,'Али')")
        core.db.commit()
        self.join(40, "a--r5")
        core.db.execute("INSERT INTO users VALUES(40,'ru')")
        core.db.commit()
        self.order(40)
        self.finish(1)
        self.assertEqual(shop.balance("a", 5), 0)


class ShopExtrasTests(Base):
    def test_search_popular_and_repeat(self):
        st = St()
        run(shop.search_start(msg(uid=CLI_A), st))
        bot = mkbot()
        m = msg("лагм", CLI_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        sent = " ".join(str(c) for c in bot.send_message.call_args_list)
        self.assertIn("Лагман", sent)
        self.assertNotIn("Плов", sent)
        st = St()
        run(shop.search_start(msg(uid=CLI_A), st))
        bot = mkbot()
        m = msg("пицца", CLI_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        self.assertIn("Ничего не нашли", bot.send_message.call_args[0][1])
        # популярное: до продаж показывает новинки, после продаж топ
        m = msg(uid=CLI_A)
        run(shop.popular(m))
        self.assertEqual(m.answer.await_count, 2)
        self.order(CLI_A, {self.p2: 3})
        run(shop.order_action(cb("so:1:ok", ADM_A)))
        self.assertEqual(shop.popular_ids("a")[0], self.p2)
        m = msg(uid=CLI_B)
        run(shop.popular(m))
        self.assertEqual(shop.popular_ids("b"), [])
        # повтор заказа
        shop.cart_of(CLI_A).clear()
        m = msg(uid=CLI_A)
        run(shop.my_orders(m))
        self.assertIn("srep:1", buttons(m.answer.call_args[1]["reply_markup"]))
        c = cb("srep:1", CLI_A)
        run(shop.repeat_order(c))
        self.assertEqual(shop.cart_of(CLI_A), {self.p2: 3})
        c = cb("srep:1", CLI_B)
        run(shop.repeat_order(c))
        self.assertEqual(shop.cart_of(CLI_B), {})
        core.db.execute("UPDATE products SET active=0 WHERE id=?", (self.p2,))
        shop.cart_of(CLI_A).clear()
        run(shop.repeat_order(cb("srep:1", CLI_A)))
        self.assertEqual(shop.cart_of(CLI_A), {})


class DayOffTests(Base):
    def setUp(self):
        super().setUp()
        self.sid = core.db.execute("INSERT INTO services(tenant,name,minutes,price) VALUES('a','Стрижка',60,1)").lastrowid
        core.db.commit()
        self.day = (booking.local_now("a").date() + dt.timedelta(days=2))

    def test_dayoff_blocks_slots_dates_and_warns(self):
        svc = booking.service_of(self.sid, "a")
        self.assertTrue(booking.free_slots("a", svc, self.day))
        core.db.execute("INSERT INTO bookings(tenant,user_id,service_id,start,name,phone,status) VALUES('a',5,?,?,'И','1','confirmed')",
                        (self.sid, f"{self.day.isoformat()} 10:00"))
        core.db.commit()
        st = St()
        run(booking.dayoff_add(cb("bkoffadd", ADM_A), st))
        m = msg("abc", ADM_A)
        run(core.wizard_input(m, st))
        self.assertIn("Не получилось", m.answer.call_args[0][0])
        bot = mkbot()
        m = msg(self.day.strftime("%d.%m"), ADM_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        self.assertTrue(booking.is_dayoff("a", self.day))
        self.assertIn("записей: 1", bot.send_message.call_args[0][1])
        self.assertEqual(booking.free_slots("a", svc, self.day), [])
        self.assertFalse(booking.is_dayoff("b", self.day))
        c = cb(f"bks:{self.sid}", CLI_A)
        run(booking.pick_service(c))
        self.assertNotIn(f"bkd:{self.sid}:{self.day.isoformat()}", buttons(c.message.edit_text.call_args[1]["reply_markup"]))
        c = cb(f"bkd:{self.sid}:{self.day.isoformat()}", CLI_A)
        run(booking.pick_date(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        m = msg(uid=ADM_A)
        run(booking.dayoff_menu(m))
        self.assertIn(f"bkoff:{self.day.isoformat()}", buttons(m.answer.call_args[1]["reply_markup"]))
        run(booking.dayoff_remove(cb(f"bkoff:{self.day.isoformat()}", ADM_B)))
        self.assertTrue(booking.is_dayoff("a", self.day))
        run(booking.dayoff_remove(cb(f"bkoff:{self.day.isoformat()}", ADM_A)))
        self.assertFalse(booking.is_dayoff("a", self.day))
        m = msg(uid=CLI_A)
        run(booking.dayoff_menu(m))
        m.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
