"""Вторая большая партия: подписки, FAQ-автоответ, остатки, мастера в записи, ответственные по заявкам, собеседования и другое."""
import datetime as dt
import unittest
from unittest.mock import MagicMock

from helpers import St, buttons, cb, cmd, labels, msg, mkbot, run

import core  # noqa: E402
from modules import booking, leads, recruiter, shop, support  # noqa: E402,F401

OWNER, ADM_A, ADM_B, CLI_A, CLI_B = 111, 201, 202, 5, 6


class Base(unittest.TestCase):
    def setUp(self):
        for t in ("products", "shop_orders", "shop_bonuses", "shop_promos", "shop_winback", "leads", "services", "bookings", "dayoff", "profiles", "referrals",
                  "scheduled", "users", "tenants", "tenant_admins", "user_tenant", "settings", "ratings", "faq", "tickets", "invites",
                  "vacancies", "applications"):
            core.db.execute(f"DELETE FROM {t}")
        core.db.execute("INSERT INTO tenants(slug,name,module,active) VALUES('a','Заведение А','shop,booking,leads,recruiter,support',1)")
        core.db.execute("INSERT INTO tenants(slug,name,module,active) VALUES('b','Заведение Б','shop,leads',1)")
        core.db.executemany("INSERT INTO tenant_admins VALUES(?,?)", [("a", ADM_A), ("b", ADM_B)])
        core.db.executemany("INSERT INTO user_tenant VALUES(?,?)", [(ADM_A, "a"), (ADM_B, "b"), (CLI_A, "a"), (CLI_B, "b")])
        core.db.executemany("INSERT INTO users VALUES(?,?)", [(u, "ru") for u in (ADM_A, ADM_B, CLI_A, CLI_B)])
        core.db.commit()
        shop.carts.clear()
        core.more_page.clear()

    def product(self, name="Лагман", price=40000, stock=None, tenant="a"):
        pid = core.db.execute("INSERT INTO products(tenant,category,name,price,descr,stock) VALUES(?,?,?,?,'',?)", (tenant, "Еда", name, price, stock)).lastrowid
        core.db.commit()
        return pid

    def order(self, pid, qty=1, uid=CLI_A):
        shop.cart_of(uid)[pid] = qty
        st = St()
        run(shop.checkout(cb("scheckout", uid), st))
        run(core.wizard_choice(cb("wzc:1", uid), st))
        run(core.wizard_input(msg("-", uid), st))
        if st.d.get("ctx", {}).get("saved_phone"):
            run(core.wizard_choice(cb("wzc:0", uid), st))
        else:
            run(core.wizard_input(msg("+998901112233", uid), st))
        run(core.wizard_choice(cb("wzc:0", uid), st))
        return st

    def count(self, table="shop_orders", where="1=1"):
        return core.db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


# ======================= общие =======================
class CoreTests(Base):
    def test_open_closed_status(self):
        core.set_setting("a", "hours", "10:00-23:00")
        at = lambda h, m=0: dt.datetime(2026, 11, 2, h, m) - dt.timedelta(hours=5)  # локальное время -> UTC
        self.assertTrue(core.open_status("a", at(12)))
        self.assertFalse(core.open_status("a", at(23, 30)))
        self.assertFalse(core.open_status("a", at(9, 59)))
        core.set_setting("a", "hours", "с 20:00 до 04:00")
        self.assertTrue(core.open_status("a", at(22)))
        self.assertTrue(core.open_status("a", at(3)))
        self.assertFalse(core.open_status("a", at(12)))
        core.set_setting("a", "hours", "круглосуточно")
        self.assertIsNone(core.open_status("a", at(12)))
        self.assertIsNone(core.open_status("b"))
        core.set_setting("a", "hours", "00:00-23:59")
        m = msg(uid=CLI_A)
        run(core.about_cmd(m))
        self.assertIn("Сейчас открыто", m.answer.call_args_list[0][0][0])

    def test_low_rating_asks_for_comment(self):
        st = St()
        c = cb("rt:shop:1:3", CLI_A)
        run(core.rate(c, st))
        self.assertEqual(st.d["wiz"], "rating_comment")
        bot = mkbot()
        m = msg("Долго ждали", CLI_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        self.assertIn("Долго ждали", bot.send_message.call_args_list[0][0][1])
        self.assertEqual(bot.send_message.call_args_list[0][0][0], ADM_A)
        st = St()
        run(core.rate(cb("rt:shop:2:5", CLI_A), st))
        self.assertEqual(st.d, {})  # хорошую оценку не допрашиваем

    def test_faq_auto_answer(self):
        core.db.execute("INSERT INTO faq(tenant,q,a) VALUES('a','Как оплатить заказ?','Переводом на карту')")
        core.db.execute("INSERT INTO faq(tenant,q,a) VALUES('b','Где вы находитесь?','На Навои')")
        core.db.execute("INSERT INTO faq(tenant,q,a) VALUES('a','До скольки работаете?','До 23:00')")
        core.db.commit()
        m = msg("а можно оплатить как-нибудь?", CLI_A)
        run(core.unknown_message(m, St()))
        self.assertIn("Переводом на карту", m.answer.call_args[0][0])
        m = msg("до скольки вы работаете сегодня", CLI_A)
        run(core.unknown_message(m, St()))
        self.assertIn("До 23:00", m.answer.call_args[0][0])
        m = msg("где вы находитесь", CLI_A)  # этот вопрос есть только у заведения Б
        run(core.unknown_message(m, St()))
        self.assertIn("кнопками", m.answer.call_args[0][0])
        m = msg("абракадабра", CLI_A)
        run(core.unknown_message(m, St()))
        self.assertIn("кнопками", m.answer.call_args[0][0])

    def test_subscription_warnings_and_shutdown(self):
        run(core.set_paid(msg(uid=ADM_A), cmd("a 2030-01-01")))
        self.assertIsNone(core.db.execute("SELECT paid_until FROM tenants WHERE slug='a'").fetchone()[0])
        m = msg(uid=OWNER)
        run(core.set_paid(m, cmd("a 2026-11-10")))
        self.assertIn("оплачено до 2026-11-10", m.answer.call_args[0][0])
        local = lambda day, hour=12: dt.datetime.strptime(day, "%Y-%m-%d") + dt.timedelta(hours=hour - 5)
        bot = mkbot()
        self.assertEqual(run(core.check_subscriptions(bot, local("2026-11-05"))), 0)
        self.assertEqual(bot.send_message.await_count, 0)
        self.assertEqual(run(core.check_subscriptions(bot, local("2026-11-07"))), 0)  # осталось 3 дня
        self.assertEqual(run(core.check_subscriptions(bot, local("2026-11-07"))), 0)
        self.assertEqual(bot.send_message.await_count, 1)  # предупреждение один раз
        self.assertIn("через 3 дн", bot.send_message.call_args[0][1])
        run(core.check_subscriptions(bot, local("2026-11-09")))
        self.assertIn("через 1 дн", bot.send_message.call_args[0][1])
        self.assertEqual(run(core.check_subscriptions(bot, local("2026-11-11"))), 1)
        self.assertEqual(core.get_tenant("a")["active"], 0)
        self.assertIsNone(core.tenant_of(CLI_A))
        self.assertEqual(run(core.check_subscriptions(bot, local("2026-11-12"))), 0)
        m = msg("/start", CLI_A)
        run(core.start(m, St(), cmd()))
        self.assertIn("Здравствуйте", m.answer.call_args[0][0])
        run(core.set_paid(msg(uid=OWNER), cmd("a 2026-12-31")))
        self.assertEqual(core.get_tenant("a")["active"], 1)
        self.assertEqual(core.tenant_of(CLI_A), "a")
        self.assertEqual(core.get_tenant("b")["active"], 1)  # соседнее заведение не тронуто

    def test_pause_and_resume_space(self):
        run(core.pause_space(msg("/pausespace a", ADM_A), cmd("a")))
        self.assertEqual(core.get_tenant("a")["active"], 1)
        run(core.pause_space(msg("/pausespace a", OWNER), cmd("a")))
        self.assertEqual(core.get_tenant("a")["active"], 0)
        run(core.pause_space(msg("/resumespace a", OWNER), cmd("a")))
        self.assertEqual(core.get_tenant("a")["active"], 1)

    def test_backup_is_valid_sqlite_and_owner_only(self):
        m = msg(uid=OWNER)
        run(core.backup_cmd(m))
        doc = m.answer_document.call_args[0][0]
        self.assertTrue(doc.data.startswith(b"SQLite format 3"))
        m = msg(uid=ADM_A)
        run(core.backup_cmd(m))
        m.answer_document.assert_not_awaited()

    def test_weekly_report_monday_9am_once(self):
        monday = dt.datetime(2026, 11, 2, 9, 5) - dt.timedelta(hours=5)  # понедельник 9:05 локально
        bot = mkbot()
        self.assertEqual(run(core.send_weekly_reports(bot, monday)), 2)
        self.assertEqual(run(core.send_weekly_reports(bot, monday)), 0)
        text = [c[0][1] for c in bot.send_message.call_args_list if c[0][0] == ADM_A][0]
        self.assertIn("Итоги недели", text)
        self.assertIn("За неделю: заказов", text)
        self.assertIn("Новых клиентов", text)
        tuesday = monday + dt.timedelta(days=1)
        core.db.execute("DELETE FROM settings WHERE key='last_weekly'")
        self.assertEqual(run(core.send_weekly_reports(bot, tuesday)), 0)

    def test_traffic_sources(self):
        core.db.execute("DELETE FROM user_tenant WHERE user_id=?", (60,))
        run(core.start(msg("/start", 60), St(), cmd("a--sinstagram")))
        run(core.start(msg("/start", 61), St(), cmd("a--sinstagram")))
        run(core.start(msg("/start", 62), St(), cmd("a--stelegram--r5")))
        self.assertEqual(core.db.execute("SELECT source FROM profiles WHERE user_id=60").fetchone()[0], "instagram")
        self.assertIn("instagram 2", " ".join(core.sources_stats("a")))
        self.assertEqual(core.sources_stats("b"), [])
        header, rows = core.export_clients("a")
        self.assertIn("Источник", header)
        # заявка получает источник клиента
        core.db.execute("INSERT INTO users VALUES(60,'ru')")
        core.db.commit()
        st = St()
        run(leads.lead_start(msg(uid=60), st))
        run(core.wizard_input(msg("Али", 60), st))
        run(core.wizard_input(msg("+998901234567", 60), st))
        run(core.wizard_input(msg("-", 60), st))
        self.assertEqual(core.db.execute("SELECT source FROM leads").fetchone()[0], "instagram")
        m = msg(uid=ADM_A)
        run(leads.report(m))
        self.assertIn("instagram: 1", m.answer.call_args[0][0])

    def test_share_pack_mentions_source_links(self):
        bot = mkbot()
        run(core.share_pack(bot, ADM_A, "a"))
        self.assertIn("--sinstagram", bot.send_photo.call_args[1]["caption"])


# ======================= магазин =======================
class ShopBatch(Base):
    def test_min_order_and_pause(self):
        pid = self.product(price=10000)
        shop.cart_of(CLI_A)[pid] = 1
        core.set_setting("a", "min_order", 50000)
        c = cb("scheckout", CLI_A)
        run(shop.checkout(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        self.assertIn("50 000", c.answer.call_args[0][0])
        shop.cart_of(CLI_A)[pid] = 5
        st = St()
        run(shop.checkout(cb("scheckout", CLI_A), st))
        self.assertEqual(st.d["wiz"], "shop_checkout")
        m = msg(uid=ADM_A)
        run(shop.toggle_pause(m))
        self.assertIn("ПРИОСТАНОВЛЕН", m.answer.call_args[0][0])
        c = cb("scheckout", CLI_A)
        run(shop.checkout(c, St()))
        self.assertIn("приостановлен", c.answer.call_args[0][0])
        self.assertEqual(core.get_setting("b", "orders_paused"), None)
        run(shop.toggle_pause(msg(uid=ADM_A)))
        self.assertEqual(core.get_setting("a", "orders_paused"), "0")
        m = msg(uid=CLI_A)
        run(shop.toggle_pause(m))
        m.answer.assert_not_awaited()

    def test_stock_limits_decrement_and_restore(self):
        pid = self.product(stock=2)
        c = cb(f"sa:{pid}", CLI_A)
        run(shop.add_to_cart(c))
        run(shop.add_to_cart(cb(f"sa:{pid}", CLI_A)))
        c = cb(f"sa:{pid}", CLI_A)
        run(shop.add_to_cart(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        self.assertEqual(shop.cart_of(CLI_A)[pid], 2)
        c = cb(f"si:{pid}", CLI_A)
        run(shop.change_qty(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        shop.cart_of(CLI_A).clear()
        self.order(pid, 2)
        self.assertEqual(core.db.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()[0], 0)
        m = msg(uid=CLI_B)
        core.db.execute("UPDATE user_tenant SET tenant='a' WHERE user_id=?", (CLI_B,))
        run(shop.catalog(m))
        self.assertIn("пуст", m.answer.call_args[0][0])  # товар закончился: в каталоге нет
        run(shop.order_action(cb("so:1:no", ADM_A)))
        self.assertEqual(core.db.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()[0], 2)
        run(shop.order_action(cb("so:1:no", ADM_A)))  # повторно запас не растёт
        self.assertEqual(core.db.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()[0], 2)
        self.order(pid, 1)
        run(shop.cancel_order(cb("scx:2", CLI_A)))
        self.assertEqual(core.db.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()[0], 2)

    def test_two_clients_cannot_oversell(self):
        pid = self.product(stock=1)
        core.db.execute("UPDATE user_tenant SET tenant='a' WHERE user_id=?", (CLI_B,))
        core.db.commit()
        shop.cart_of(CLI_A)[pid] = 1
        shop.cart_of(CLI_B)[pid] = 1
        self.order(pid, 1, CLI_A)
        bot = mkbot()
        run(shop.checkout_done(bot, CLI_B, {"kind": "pickup", "pay": "cash", "phone": "1"}, {"tenant": "a"}))
        self.assertEqual(self.count(), 1)
        self.assertIn("осталось только", bot.send_message.call_args[0][1])

    def test_admin_sets_stock(self):
        pid = self.product()
        st = St()
        run(shop.product_action(cb(f"sp:{pid}:stock", ADM_A), st))
        run(core.wizard_input(msg("7", ADM_A), st))
        self.assertEqual(core.db.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()[0], 7)
        st = St()
        run(shop.product_action(cb(f"sp:{pid}:stock", ADM_A), st))
        run(core.wizard_input(msg("-", ADM_A), st))
        self.assertIsNone(core.db.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()[0])
        m = msg(uid=ADM_A)
        run(shop.manage_catalog(m))
        self.assertIn("sp:%d:stock" % pid, buttons(m.answer.call_args[1]["reply_markup"]))

    def test_saved_phone_reused(self):
        pid = self.product()
        self.order(pid)
        self.assertEqual(core.db.execute("SELECT phone FROM profiles WHERE tenant='a' AND user_id=?", (CLI_A,)).fetchone()[0], "+998901112233")
        shop.cart_of(CLI_A)[pid] = 1
        st = St()
        run(shop.checkout(cb("scheckout", CLI_A), st))
        self.assertEqual(st.d["ctx"]["saved_phone"], "+998901112233")
        run(core.wizard_choice(cb("wzc:1", CLI_A), st))
        run(core.wizard_input(msg("-", CLI_A), st))
        c = cb("wzc:1", CLI_A)  # «Другой номер»
        run(core.wizard_choice(c, st))
        m = msg("+998977777777", CLI_A)
        run(core.wizard_input(m, st))
        run(core.wizard_choice(cb("wzc:0", CLI_A), st))
        self.assertIn("+998977777777", core.db.execute("SELECT text FROM shop_orders WHERE id=2").fetchone()[0])
        self.assertEqual(core.db.execute("SELECT phone FROM profiles WHERE tenant='a' AND user_id=?", (CLI_A,)).fetchone()[0], "+998977777777")

    def test_winback_messages(self):
        pid = self.product()
        self.order(pid)
        run(shop.order_action(cb("so:1:ok", ADM_A)))
        run(shop.order_action(cb("so:1:ready", ADM_A)))
        run(shop.order_action(cb("so:1:done", ADM_A)))
        bot = mkbot()
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(days=20)
        self.assertEqual(run(shop.send_winbacks(bot, now)), 0)  # выключено по умолчанию
        core.set_setting("a", "winback_days", 14)
        self.assertEqual(run(shop.send_winbacks(bot, now - dt.timedelta(days=10))), 0)  # прошло мало
        self.assertEqual(run(shop.send_winbacks(bot, now)), 1)
        self.assertEqual(bot.send_message.call_args[0][0], CLI_A)
        self.assertIn("Давно вас не было", bot.send_message.call_args[0][1])
        self.assertEqual(run(shop.send_winbacks(bot, now)), 0)  # второй раз не беспокоим
        core.set_setting("a", "winback_text", "Скидка 10% по промокоду BACK")
        self.order(pid)
        run(shop.order_action(cb("so:2:ok", ADM_A)))
        run(shop.order_action(cb("so:2:ready", ADM_A)))
        run(shop.order_action(cb("so:2:done", ADM_A)))
        core.db.execute("UPDATE shop_orders SET created=? WHERE id=2", ((now + dt.timedelta(days=25)).strftime("%Y-%m-%d %H:%M:%S"),))
        core.db.commit()  # второй заказ «сделан» уже после первого напоминания
        later = now + dt.timedelta(days=40)
        self.assertEqual(run(shop.send_winbacks(bot, later)), 1)  # после нового заказа и нового простоя снова
        self.assertIn("BACK", bot.send_message.call_args[0][1])
        self.assertEqual(core.get_setting("b", "winback_days"), None)


# ======================= запись =======================
class BookingBatch(Base):
    def setUp(self):
        super().setUp()
        self.sid = core.db.execute("INSERT INTO services(tenant,name,minutes,price) VALUES('a','Стрижка',60,80000)").lastrowid
        core.db.commit()
        self.day = booking.local_now("a").date() + dt.timedelta(days=2)
        self.svc = lambda: booking.service_of(self.sid, "a")

    def add(self, hhmm, status="confirmed", uid=CLI_A):
        core.db.execute("INSERT INTO bookings(tenant,user_id,service_id,start,name,phone,status) VALUES('a',?,?,?,'И','1',?)",
                        (uid, self.sid, f"{self.day.isoformat()} {hhmm}", status))
        core.db.commit()

    def slots(self):
        return booking.free_slots("a", self.svc(), self.day)

    def test_capacity_for_several_masters(self):
        self.add("10:00")
        self.assertNotIn("10:00", self.slots())
        core.set_setting("a", "capacity", 2)
        self.assertIn("10:00", self.slots())
        self.add("10:00")
        self.assertNotIn("10:00", self.slots())
        self.assertIn("11:00", self.slots())

    def test_buffer_break_notice_and_days_ahead(self):
        self.add("10:00")
        core.set_setting("a", "buffer_min", 30)
        self.assertNotIn("11:00", self.slots())  # перерыв 30 минут после клиента
        self.assertIn("12:00", self.slots())
        core.set_setting("a", "break_start", "13:00")
        core.set_setting("a", "break_end", "14:00")
        self.assertNotIn("13:00", self.slots())
        self.assertNotIn("12:00", self.slots())  # час + перерыв упираются в обед
        self.assertIn("14:00", self.slots())
        now = dt.datetime.combine(self.day, dt.time(8, 0))
        core.set_setting("a", "min_notice_min", 120)
        self.assertEqual(booking.free_slots("a", self.svc(), self.day, now), ["14:00", "15:00", "16:00", "17:00"])
        self.assertNotIn("09:00", booking.free_slots("a", self.svc(), self.day, now))  # раньше чем через 2 часа нельзя
        core.set_setting("a", "days_ahead", 3)
        markup = booking.date_markup("a", self.sid)
        self.assertEqual(len(buttons(markup)), 3)
        core.set_setting("a", "days_ahead", 99)
        self.assertEqual(len(buttons(booking.date_markup("a", self.sid))), 30)

    def test_reschedule(self):
        self.add("10:00", "pending")
        c = cb("bkre:1", CLI_B)
        run(booking.reschedule(c))
        self.assertEqual(self.count("bookings", "status='pending'"), 1)
        c = cb("bkre:1", CLI_A)
        run(booking.reschedule(c))
        self.assertEqual(self.count("bookings", "status='cancelled'"), 1)
        self.assertIn(f"bkd:{self.sid}:", " ".join(buttons(c.message.edit_text.call_args[1]["reply_markup"])))
        self.assertIn("переносит запись", c.bot.send_message.call_args[0][1])
        self.assertIn("10:00", self.slots())

    def test_day_before_reminder_and_autocomplete(self):
        self.add("10:00")
        start = dt.datetime.combine(self.day, dt.time(10, 0))
        bot = mkbot()
        self.assertEqual(run(booking.send_day_before_reminders(bot, start - dt.timedelta(hours=30) - dt.timedelta(hours=5))), 0)
        self.assertEqual(run(booking.send_day_before_reminders(bot, start - dt.timedelta(hours=23, minutes=30) - dt.timedelta(hours=5))), 1)
        self.assertEqual(run(booking.send_day_before_reminders(bot, start - dt.timedelta(hours=23, minutes=30) - dt.timedelta(hours=5))), 0)
        self.assertIn("завтра в 10:00", bot.send_message.call_args[0][1])
        self.assertEqual(run(booking.autocomplete_bookings(bot, start + dt.timedelta(minutes=90) - dt.timedelta(hours=5))), 0)
        self.assertEqual(run(booking.autocomplete_bookings(bot, start + dt.timedelta(hours=3) - dt.timedelta(hours=5))), 1)
        self.assertEqual(self.count("bookings", "status='done'"), 1)
        self.assertIn("rt:booking:1:5", buttons(bot.send_message.call_args[1]["reply_markup"]))

    def test_manual_booking_by_admin(self):
        st = St()
        run(booking.manual_start(msg(uid=ADM_A), st))
        run(core.wizard_choice(cb("wzc:0", ADM_A), st))
        run(core.wizard_input(msg(self.day.strftime("%d.%m"), ADM_A), st))
        run(core.wizard_input(msg("11:00", ADM_A), st))
        run(core.wizard_input(msg("Мария", ADM_A), st))
        bot = mkbot()
        m = msg("+998901112233", ADM_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        row = core.db.execute("SELECT user_id, status, start, name FROM bookings").fetchone()
        self.assertEqual(row, (0, "confirmed", f"{self.day.isoformat()} 11:00", "Мария"))
        self.assertNotIn("11:00", self.slots())
        # на занятое время не записывает
        st = St()
        run(booking.manual_start(msg(uid=ADM_A), st))
        run(core.wizard_choice(cb("wzc:0", ADM_A), st))
        run(core.wizard_input(msg(self.day.strftime("%d.%m"), ADM_A), st))
        run(core.wizard_input(msg("11:00", ADM_A), st))
        run(core.wizard_input(msg("Ещё", ADM_A), st))
        bot = mkbot()
        m = msg("1234567", ADM_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        self.assertEqual(self.count("bookings"), 1)
        self.assertIn("занято", bot.send_message.call_args[0][1])
        m = msg(uid=CLI_A)
        run(booking.manual_start(m, St()))
        m.answer.assert_not_awaited()


# ======================= заявки =======================
class LeadsBatch(Base):
    def lead(self, tenant="a", uid=CLI_A):
        lid = core.db.execute("INSERT INTO leads(tenant,user_id,name,phone,message) VALUES(?,?,'Али','1','хочу')", (tenant, uid)).lastrowid
        core.db.commit()
        return lid

    def test_assign_and_notes(self):
        lid = self.lead()
        c = cb(f"ldt:{lid}", CLI_B)
        run(leads.lead_take(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        c = cb(f"ldt:{lid}", ADM_B)
        run(leads.lead_take(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))  # чужое заведение
        c = cb(f"ldt:{lid}", ADM_A)
        run(leads.lead_take(c))
        row = core.db.execute("SELECT assignee, status FROM leads").fetchone()
        self.assertEqual(row, (ADM_A, "work"))
        self.assertIn("взял", c.bot.send_message.call_args_list[0][0][1])
        self.assertIn("Ответственный", c.message.edit_text.call_args[0][0])
        st = St()
        run(leads.lead_note(cb(f"ldn:{lid}", ADM_A), st))
        run(core.wizard_input(msg("Просит скидку", ADM_A), st))
        st = St()
        run(leads.lead_note(cb(f"ldn:{lid}", ADM_A), st))
        run(core.wizard_input(msg("Перезвонить вечером", ADM_A), st))
        self.assertEqual(core.db.execute("SELECT note FROM leads").fetchone()[0], "Просит скидку | Перезвонить вечером")
        c = cb(f"ldn:{lid}", ADM_B)
        run(leads.lead_note(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))

    def test_callback_reminder_once_to_assignee(self):
        lid = self.lead()
        run(leads.lead_take(cb(f"ldt:{lid}", ADM_A)))
        st = St()
        run(leads.lead_callback(cb(f"ldc:{lid}", ADM_A), st))
        day = (dt.date.today() + dt.timedelta(days=1)).strftime("%d.%m")
        run(core.wizard_input(msg(day, ADM_A), st))
        run(core.wizard_input(msg("15:30", ADM_A), st))
        at = core.db.execute("SELECT callback_at FROM leads").fetchone()[0]
        self.assertTrue(at.endswith("10:30"))  # 15:30 по Ташкенту = 10:30 UTC
        bot = mkbot()
        before = dt.datetime.strptime(at, "%Y-%m-%d %H:%M") - dt.timedelta(minutes=5)
        self.assertEqual(run(leads.send_callback_reminders(bot, before)), 0)
        after = before + dt.timedelta(minutes=10)
        self.assertEqual(run(leads.send_callback_reminders(bot, after)), 1)
        self.assertEqual(bot.send_message.call_args[0][0], ADM_A)
        self.assertIn("Пора перезвонить", bot.send_message.call_args[0][1])
        self.assertEqual(run(leads.send_callback_reminders(bot, after)), 0)
        c = cb(f"ldc:{lid}", ADM_B)
        run(leads.lead_callback(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))


# ======================= рекрутинг =======================
class RecruiterBatch(Base):
    def application(self, tenant="a", uid=CLI_A):
        jid = core.db.execute("INSERT INTO vacancies(tenant,title,city,salary,description) VALUES(?,'Менеджер','Т','1','d')", (tenant,)).lastrowid
        aid = core.db.execute("INSERT INTO applications(tenant,vacancy_id,user_id,name,phone,about) VALUES(?,?,?,'Иван','1','опыт')", (tenant, jid, uid)).lastrowid
        core.db.commit()
        return aid

    def test_interview_invite_and_reminder(self):
        aid = self.application()
        st = St()
        run(recruiter.interview_start(cb(f"rci:{aid}", ADM_A), st))
        day = (dt.date.today() + dt.timedelta(days=1)).strftime("%d.%m")
        run(core.wizard_input(msg(day, ADM_A), st))
        run(core.wizard_input(msg("15:00", ADM_A), st))
        bot = mkbot()
        m = msg("Навои 1, офис 3", ADM_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        row = core.db.execute("SELECT status, interview_at, interview_info FROM applications").fetchone()
        self.assertEqual((row[0], row[2]), ("invited", "Навои 1, офис 3"))
        self.assertTrue(row[1].endswith("10:00"))
        self.assertEqual(bot.send_message.call_args_list[0][0][0], CLI_A)
        self.assertIn("Навои 1, офис 3", bot.send_message.call_args_list[0][0][1])
        when = dt.datetime.strptime(row[1], "%Y-%m-%d %H:%M")
        bot = mkbot()
        self.assertEqual(run(recruiter.send_interview_reminders(bot, when - dt.timedelta(hours=5))), 0)
        self.assertEqual(run(recruiter.send_interview_reminders(bot, when - dt.timedelta(hours=1, minutes=30))), 1)
        self.assertEqual(run(recruiter.send_interview_reminders(bot, when - dt.timedelta(hours=1))), 0)
        self.assertEqual({c[0][0] for c in bot.send_message.call_args_list}, {CLI_A, ADM_A})
        c = cb(f"rci:{aid}", ADM_B)
        run(recruiter.interview_start(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))

    def test_message_to_candidate_and_isolation(self):
        aid = self.application()
        st = St()
        run(recruiter.message_start(cb(f"rcm:{aid}", ADM_A), st))
        bot = mkbot()
        m = msg("Принесите паспорт", ADM_A)
        m.bot = bot
        run(core.wizard_input(m, st))
        self.assertEqual(bot.send_message.call_args_list[0][0][0], CLI_A)
        self.assertIn("Принесите паспорт", bot.send_message.call_args_list[0][0][1])
        c = cb(f"rcm:{aid}", ADM_B)
        run(recruiter.message_start(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        kb = recruiter.card_kb(aid, "new")
        self.assertIn(f"rci:{aid}", buttons(kb))
        self.assertIn(f"rcm:{aid}", buttons(kb))


if __name__ == "__main__":
    unittest.main(verbosity=2)
