import datetime as dt
import unittest
from unittest.mock import MagicMock

from helpers import St, buttons, cb, cmd, labels, msg, mkbot, run

import core  # noqa: E402
from modules import booking as bk  # noqa: E402

ADM_A, ADM_B, CLI_A, CLI_B = 201, 202, 5, 6


class BookingTests(unittest.TestCase):
    def setUp(self):
        for t in ("services", "bookings", "users", "tenants", "tenant_admins", "user_tenant", "settings"):
            core.db.execute(f"DELETE FROM {t}")
        core.db.execute("INSERT INTO tenants VALUES('a','Салон А','booking',1)")
        core.db.execute("INSERT INTO tenants VALUES('b','Салон Б','booking',1)")
        core.db.executemany("INSERT INTO tenant_admins VALUES(?,?)", [("a", ADM_A), ("b", ADM_B)])
        core.db.executemany("INSERT INTO user_tenant VALUES(?,?)", [(ADM_A, "a"), (ADM_B, "b"), (CLI_A, "a"), (CLI_B, "b"), (7, "a")])
        core.db.executemany("INSERT INTO users VALUES(?,?)", [(u, "ru") for u in (ADM_A, ADM_B, CLI_A, CLI_B, 7)])
        core.db.commit()
        core.set_setting("a", "work_start", "09:00")
        core.set_setting("a", "work_end", "18:00")
        self.sid = self.service("a", "Стрижка", 60, 80000)
        self.other = self.service("b", "Чужая", 30, 1)
        self.day = (bk.local_now("a").date() + dt.timedelta(days=1)).isoformat()

    def service(self, tenant, name, minutes, price):
        cur = core.db.execute("INSERT INTO services(tenant,name,minutes,price) VALUES(?,?,?,?)", (tenant, name, minutes, price))
        core.db.commit()
        return cur.lastrowid

    def book(self, uid=CLI_A, time="1000", sid=None):
        st = St()
        run(bk.pick_time(cb(f"bkt:{sid or self.sid}:{self.day}:{time}", uid), st))
        run(core.wizard_input(msg("Иван", uid), st))
        bot = mkbot()
        m = msg("+998901112233", uid)
        m.bot = bot
        run(core.wizard_input(m, st))
        return bot

    def count(self, status=None):
        q = "SELECT COUNT(*) FROM bookings" + (f" WHERE status='{status}'" if status else "")
        return core.db.execute(q).fetchone()[0]

    def test_slots_respect_hours_busy_and_past(self):
        svc = bk.service_of(self.sid, "a")
        day = dt.date.fromisoformat(self.day)
        slots = bk.free_slots("a", svc, day)
        self.assertEqual(slots[0], "09:00")
        self.assertEqual(slots[-1], "17:00")
        self.assertEqual(len(slots), 9)
        self.book(time="1000")
        self.assertNotIn("10:00", bk.free_slots("a", svc, day))
        short = dict(svc, minutes=30)
        self.assertNotIn("10:00", bk.free_slots("a", short, day))  # пересекается с занятым часом
        self.assertNotIn("10:30", bk.free_slots("a", short, day))
        self.assertIn("11:00", bk.free_slots("a", short, day))
        now = dt.datetime.combine(day, dt.time(12, 30))
        self.assertEqual(bk.free_slots("a", svc, day, now)[0], "13:00")
        self.assertEqual(bk.free_slots("a", svc, day - dt.timedelta(days=2)), bk.free_slots("a", svc, day - dt.timedelta(days=2)))

    def test_client_flow_creates_pending_booking_and_notifies_only_own_admin(self):
        c = cb(f"bks:{self.sid}", CLI_A)
        run(bk.pick_service(c))
        self.assertGreaterEqual(len(buttons(c.message.edit_text.call_args[1]["reply_markup"])), 7)
        c = cb(f"bkd:{self.sid}:{self.day}", CLI_A)
        run(bk.pick_date(c))
        self.assertIn(f"bkt:{self.sid}:{self.day}:1000", buttons(c.message.edit_text.call_args[1]["reply_markup"]))
        bot = self.book()
        row = core.db.execute("SELECT tenant, status, start FROM bookings").fetchone()
        self.assertEqual(row, ("a", "pending", f"{self.day} 10:00"))
        self.assertEqual({c_[0][0] for c_ in bot.send_message.call_args_list}, {CLI_A, ADM_A})

    def test_double_booking_blocked_and_other_tenants_service_rejected(self):
        self.book()
        c = cb(f"bkt:{self.sid}:{self.day}:1000", 7)
        run(bk.pick_time(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        self.assertEqual(self.count(), 1)
        c = cb(f"bkt:{self.other}:{self.day}:1000", CLI_A)
        run(bk.pick_time(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        self.assertEqual(self.count(), 1)

    def test_admin_confirm_reject_done_and_isolation(self):
        self.book()
        c = cb("bka:1:ok", ADM_B)
        run(bk.booking_action(c))
        self.assertEqual(self.count("pending"), 1)
        c = cb("bka:1:ok", ADM_A)
        run(bk.booking_action(c))
        self.assertEqual(self.count("confirmed"), 1)
        self.assertIn("подтверждена", c.bot.send_message.call_args[0][1])
        run(bk.booking_action(cb("bka:1:ok", ADM_A)))  # повторно нельзя
        run(bk.booking_action(cb("bka:1:done", ADM_A)))
        self.assertEqual(self.count("done"), 1)
        self.book(time="1100")
        run(bk.booking_action(cb("bka:2:no", ADM_A)))
        self.assertEqual(self.count("rejected"), 1)
        self.assertIn("11:00", bk.free_slots("a", bk.service_of(self.sid, "a"), dt.date.fromisoformat(self.day)))

    def test_client_cancel_own_only_and_my_list(self):
        self.book()
        m = msg(uid=CLI_A)
        run(bk.my_bookings(m))
        self.assertIn("bkx:1", buttons(m.answer.call_args[1]["reply_markup"]))
        c = cb("bkx:1", CLI_B)
        run(bk.cancel_booking(c))
        self.assertEqual(self.count("pending"), 1)
        run(bk.cancel_booking(cb("bkx:1", CLI_A)))
        self.assertEqual(self.count("cancelled"), 1)
        m = msg(uid=CLI_A)
        run(bk.my_bookings(m))
        self.assertIn("нет", m.answer.call_args[0][0])

    def test_reminder_once_within_hour(self):
        self.book()
        run(bk.booking_action(cb("bka:1:ok", ADM_A)))
        bot = mkbot()
        start = dt.datetime.strptime(f"{self.day} 10:00", "%Y-%m-%d %H:%M")
        far = start - dt.timedelta(hours=5) - dt.timedelta(hours=5)  # локальное = start - 5ч
        self.assertEqual(run(bk.send_reminders(bot, far - dt.timedelta(hours=5))), 0)
        near = start - dt.timedelta(minutes=40) - dt.timedelta(hours=5)  # utc, local = start - 40 мин
        self.assertEqual(run(bk.send_reminders(bot, near)), 1)
        self.assertEqual(run(bk.send_reminders(bot, near)), 0)
        self.assertIn("10:00", bot.send_message.call_args[0][1])

    def test_admin_services_and_report(self):
        st = St()
        run(bk.add_service(msg(uid=ADM_A), st))
        for text in ("Маникюр", "45", "100 000"):
            run(core.wizard_input(msg(text, ADM_A), st))
        self.assertEqual(core.db.execute("SELECT tenant, minutes, price FROM services WHERE name='Маникюр'").fetchone(), ("a", 45, 100000))
        m = msg(uid=CLI_A)
        run(bk.add_service(m, St()))
        m.answer.assert_not_awaited()
        self.book()
        run(bk.booking_action(cb("bka:1:ok", ADM_A)))
        m = msg(uid=ADM_A)
        run(bk.report(m))
        self.assertIn("80 000 сум", m.answer.call_args[0][0])
        m = msg(uid=ADM_B)
        run(bk.report(m))
        self.assertIn("0 сум", m.answer.call_args[0][0])
        run(bk.service_action(cb(f"bkv:{self.other}:del", ADM_A)))  # чужая услуга
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM services WHERE id=?", (self.other,)).fetchone()[0], 1)
        run(bk.service_action(cb(f"bkv:{self.sid}:del", ADM_A)))
        self.assertEqual(self.count("cancelled"), 1)

    def test_settings_available_for_booking_admin(self):
        self.assertIn(core.SETTINGS_BTN, labels(core.main_kb, ADM_A))
        m = msg(uid=ADM_A)
        run(core.settings_menu(m))
        data = buttons(m.answer.call_args[1]["reply_markup"])
        self.assertIn("set:work_start", data)
        st = St()
        run(core.settings_pick(cb("set:work_start", ADM_A), st))
        run(core.wizard_input(msg("10:30", ADM_A), st))
        self.assertEqual(core.get_setting("a", "work_start"), "10:30")
        m = msg("25:99", ADM_A)
        st = St()
        run(core.settings_pick(cb("set:work_end", ADM_A), st))
        run(core.wizard_input(m, st))
        self.assertIn("Не получилось", m.answer.call_args[0][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
