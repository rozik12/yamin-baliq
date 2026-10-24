"""Тесты всех функций бота с подставными объектами Telegram (без сети)."""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

os.environ.update(BOT_TOKEN="1:x", DB_PATH=":memory:", ADMIN_CHAT_ID="111", COURIER_CHAT_ID="-100500",
                  ADMIN_SETUP_CODE="CODE123", CARD_NUMBER="8600 1111 2222 3333", YANDEX_DELIVERY_TOKEN="")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot  # noqa: E402
import yandex  # noqa: E402


class St:
    def __init__(self, d=None):
        self.d = d or {}
        self.state = None

    async def get_data(self):
        return dict(self.d)

    async def update_data(self, **k):
        self.d.update(k)

    async def set_state(self, s):
        self.state = s

    async def clear(self):
        self.d = {}
        self.state = None


def msg(text=None, uid=5, **kw):
    m = MagicMock()
    m.text = text
    m.from_user.id = uid
    m.from_user.full_name = f"U{uid}"
    m.contact = kw.get("contact")
    m.location = kw.get("location")
    m.photo = kw.get("photo")
    m.document = kw.get("document")
    m.chat.id = uid
    for n in ("answer", "answer_photo", "answer_document", "edit_text", "edit_caption", "edit_reply_markup", "delete"):
        setattr(m, n, AsyncMock())
    m.bot = MagicMock()
    m.bot.send_message = AsyncMock()
    m.bot.send_photo = AsyncMock()
    m.bot.send_document = AsyncMock()
    m.bot.send_invoice = AsyncMock()
    m.bot.send_location = AsyncMock()
    m.bot.forward_message = AsyncMock()
    m.message_id = 777
    m.answer_location = AsyncMock()
    return m


def cb(data, uid=5, chat=None, photo=False):
    c = MagicMock()
    c.data = data
    c.from_user.id = uid
    c.from_user.full_name = f"U{uid}"
    c.answer = AsyncMock()
    c.message = msg("orig", uid)
    c.message.chat.id = chat if chat is not None else uid
    c.message.caption = "cap"
    c.message.photo = [1] if photo else None
    c.bot = c.message.bot
    return c


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class BotTests(unittest.TestCase):
    def setUp(self):
        for t in ("orders", "bookings", "reviews", "stoplist", "promos", "admins", "users"):
            bot.db.execute(f"DELETE FROM {t}")
        bot.db.execute("INSERT INTO users(id,lang) VALUES(5,'ru')")
        bot.db.commit()
        bot.carts.clear()
        bot.promos_applied.clear()
        bot.INFO["payment_methods"] = ["pay_cash", "pay_card", "pay_transfer", "pay_online"]
        self._prices = {k: v["price"] for k, v in bot.ITEMS.items()}
        self._card, self._phone, self._fee = dict(bot.INFO["card"]), bot.INFO["phone"], bot.INFO["delivery_fee"]
        bot.INFO["delivery_fee"] = 20000  # тесты проверяют расчёт доставки; в menu.json сейчас 0

    def tearDown(self):
        bot.db.execute("DELETE FROM overrides")
        bot.db.commit()
        for iid, p in self._prices.items():
            bot.ITEMS[iid]["price"] = p
            bot.ITEMS[iid].pop("photo_override", None)
        bot.INFO["card"] = self._card
        bot.INFO["paused"] = False
        bot.db.execute("DELETE FROM banned")
        bot.INFO["phone"], bot.INFO["delivery_fee"] = self._phone, self._fee

    def order_row(self, oid=1):
        return bot.db.execute("SELECT status, total, yandex_id FROM orders WHERE id=?", (oid,)).fetchone()

    # ---------- меню и данные
    def test_menu_data_complete(self):
        self.assertEqual(len(bot.ITEMS), 50)
        for item in bot.ITEMS.values():
            for lang in ("ru", "uz", "en"):
                self.assertTrue(item["name"][lang], item["id"])
            self.assertTrue(os.path.exists(os.path.join(bot.CDIR, "photos", item["id"] + ".jpg")), item["id"])
        for cat in bot.CATS.values():
            for lang in ("ru", "uz", "en"):
                self.assertTrue(cat["name"][lang])

    def test_texts_have_all_languages(self):
        for key, v in bot.TXT.items():
            self.assertEqual(set(v), {"ru", "uz", "en"}, key)

    def test_money_format(self):
        self.assertEqual(bot.money(125000, "ru"), "125 000 сум")
        self.assertEqual(bot.money(125000, "uz"), "125 000 so'm")
        self.assertEqual(bot.money(125000, "en"), "125 000 UZS")

    # ---------- язык
    def test_language_selection_and_start(self):
        bot.db.execute("DELETE FROM users")
        m = msg("/start")
        run(bot.start(m, St()))
        self.assertIn("Tilni", m.answer.call_args[0][0])
        c = cb("lang:uz")
        run(bot.set_lang(c))
        self.assertEqual(bot.lang_of(5), "uz")
        c.message.answer_photo.assert_awaited()

    # ---------- меню / корзина
    def test_show_category_with_photos_and_stoplist(self):
        c = cb("cat:fish")
        run(bot.show_cat(c))
        self.assertEqual(c.message.answer_photo.await_count, 9)
        bot.db.execute("INSERT INTO stoplist VALUES('sazan')")
        c = cb("cat:fish")
        run(bot.show_cat(c))
        self.assertEqual(c.message.answer_photo.await_count, 8)

    def test_old_buttons_do_not_crash(self):
        c = cb("cat:pizza")
        run(bot.show_cat(c))
        c.message.answer.assert_awaited()
        run(bot.add(cb("add:p1")))
        run(bot.change_qty(cb("inc:p1")))
        self.assertNotIn(5, bot.carts)

    def test_cart_add_plus_minus_clear(self):
        run(bot.add(cb("add:sazan")))
        run(bot.change_qty(cb("inc:sazan")))
        self.assertEqual(bot.carts[5]["sazan"], 2)
        run(bot.change_qty(cb("dec:sazan")))
        run(bot.change_qty(cb("dec:sazan")))
        self.assertNotIn("sazan", bot.carts[5])
        run(bot.add(cb("add:oliv")))
        c = cb("clear")
        run(bot.clear(c))
        self.assertNotIn(5, bot.carts)

    def test_promo_code(self):
        bot.carts[5] = {"oliv": 1}
        m = msg("/promo FISH10 10", uid=111)
        run(bot.promo_admin(m, MagicMock(args="FISH10 10")))
        run(bot.promo_apply(msg("fish10"), St()))
        self.assertEqual(bot.cart_text(5)[1], 36000)
        m = msg("bad")
        run(bot.promo_apply(m, St()))
        self.assertIn("нет", m.answer.call_args[0][0])

    def test_min_order(self):
        old = bot.INFO.get("min_order", 0)
        bot.INFO["min_order"] = 50000
        try:
            bot.carts[5] = {"luk": 1}
            c = cb("checkout")
            run(bot.checkout(c, St()))
            self.assertTrue(c.answer.call_args[1].get("show_alert"))
        finally:
            bot.INFO["min_order"] = old

    def test_any_amount_allowed_by_default(self):
        self.assertEqual(bot.INFO.get("min_order", 0), 0)
        bot.carts[5] = {"luk": 1}  # самая дешёвая позиция, 7 000 сум
        c = cb("checkout")
        run(bot.checkout(c, St()))
        c.message.answer.assert_awaited()

    # ---------- оформление заказа
    def make_order(self, method="pay_cash", kind="delivery", **extra):
        bot.carts[5] = {"sazan": 1}
        st = St({"kind": kind, "address": "ул. Тест", "phone": "+998901112233", **extra})
        c = cb(f"pay:{method}")
        run(bot.order_pay(c, st))
        return st, c

    def test_order_cash_delivery_notifies_admin(self):
        _, c = self.make_order("pay_cash", comment="без лука", lat=41.3, lon=69.2)
        status, total, _ = self.order_row()
        self.assertEqual((status, total), ("new", 160000))
        sent = [x[0][0] for x in c.bot.send_message.call_args_list]
        self.assertIn(111, sent)
        dest = json.loads(bot.db.execute("SELECT dest FROM orders").fetchone()[0])
        self.assertEqual(dest["lat"], 41.3)

    def test_yandex_maps_link_and_location_forwarded_to_admins(self):
        self.assertEqual(bot.map_link(41.221122, 69.224447), "https://yandex.com/maps/?pt=69.224447,41.221122&z=17&l=map")
        st = St()
        loc = MagicMock(latitude=41.221122, longitude=69.224447)
        run(bot.order_location(msg(location=loc), st))
        self.assertIn("yandex.com/maps", st.d["address"])
        self.assertNotIn("google", st.d["address"])
        # оплата переводом: админу уходит точка сразу при формировании заказа
        bot.INFO["payment_methods"] = ["pay_transfer"]
        bot.carts[5] = {"sazan": 1}
        st.d.update(kind="delivery", phone="+998")
        m = msg("+998901112233")
        run(bot.got_contact(m, st))
        self.assertEqual(m.bot.forward_message.call_args[0], (111, 5, 777))  # исходное сообщение клиента переслано
        m.bot.send_location.assert_not_awaited()
        texts = [c[0][1] for c in m.bot.send_message.call_args_list]
        self.assertFalse([x for x in texts if "Локация клиента" in x])  # отдельного сообщения со ссылкой нет
        # остальные способы: точка уходит при оформлении
        bot.INFO["payment_methods"] = ["pay_cash"]
        bot.carts[5] = {"sazan": 1}
        c = cb("pay:pay_cash")
        run(bot.order_pay(c, St({"kind": "delivery", "address": "x", "phone": "1", "lat": 41.3, "lon": 69.2})))
        self.assertEqual(c.bot.send_location.call_args[0], (111, 41.3, 69.2))  # нет исходного сообщения -> новая точка
        # если пересылка не удалась, отправляем точку по координатам
        b = MagicMock(send_message=AsyncMock(), forward_message=AsyncMock(side_effect=Exception("x")), send_location=AsyncMock())
        run(bot.send_client_location(b, 41.3, 69.2, 9, [5, 1]))
        b.send_location.assert_awaited()
        # карточка заказа в панели тоже показывает точку
        panel = msg(uid=111)
        run(bot.show_order(panel, 2))
        panel.answer_location.assert_awaited()

    def test_free_delivery_over_threshold(self):
        bot.carts[5] = {"sazan": 3}
        run(bot.order_pay(cb("pay:pay_card"), St({"kind": "delivery", "address": "A", "phone": "1"})))
        self.assertEqual(self.order_row()[1], 420000)

    def test_no_delivery_fee_when_zero(self):
        bot.INFO["delivery_fee"] = 0
        _, c = self.make_order("pay_cash")
        self.assertEqual(self.order_row()[1], 140000)
        self.assertNotIn("Доставка", bot.db.execute("SELECT text FROM orders").fetchone()[0])

    def test_pickup_no_fee(self):
        self.make_order("pay_cash", kind="pickup")
        self.assertEqual(self.order_row()[1], 140000)

    def test_location_and_comment_steps(self):
        st = St()
        loc = MagicMock(latitude=41.3, longitude=69.2)
        run(bot.order_location(msg(location=loc), st))
        self.assertEqual(st.d["lat"], 41.3)
        run(bot.got_comment(msg("без лука"), st))
        self.assertEqual(st.d["comment"], "без лука")

    def test_contact_step_offers_card_and_online_payment(self):
        m = msg("+998901112233")
        run(bot.got_contact(m, St()))
        buttons = [b.callback_data for r in m.answer.call_args[1]["reply_markup"].inline_keyboard for b in r]
        self.assertIn("pay:pay_transfer", buttons)

    def test_only_card_transfer_skips_payment_choice(self):
        bot.INFO["payment_methods"] = ["pay_transfer"]
        bot.carts[5] = {"sazan": 1}
        st = St({"kind": "delivery", "address": "A"})
        m = msg("+998901112233")
        run(bot.got_contact(m, st))
        text = m.answer.call_args[0][0]
        self.assertIn("8600 1111 2222 3333", text)
        self.assertEqual(st.d["phone"], "+998901112233")
        c = cb("pay:pay_cash")
        run(bot.order_pay(c, st))
        self.assertEqual(bot.db.execute("SELECT COUNT(*) FROM orders WHERE status!='awaiting'").fetchone()[0], 0)

    def test_admin_notified_immediately_when_order_formed(self):
        bot.INFO["payment_methods"] = ["pay_transfer"]
        bot.carts[5] = {"sazan": 1}
        st = St({"kind": "delivery", "address": "A"})
        m = msg("+998901112233")
        run(bot.got_contact(m, st))
        self.assertEqual(self.order_row()[0], "awaiting")
        self.assertEqual(m.bot.send_message.call_args[0][0], 111)
        self.assertIn("сформирован", m.bot.send_message.call_args[0][1])
        self.assertEqual(st.d["oid"], 1)
        # чек приходит -> тот же заказ, без дубля
        p = msg(photo=[MagicMock(file_id="F")])
        run(bot.got_proof_photo(p, st))
        self.assertEqual(bot.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
        self.assertEqual(self.order_row()[0], "verify")

    def test_awaiting_order_cancel_and_expiry(self):
        bot.INFO["payment_methods"] = ["pay_transfer"]
        bot.carts[5] = {"sazan": 1}
        st = St({"kind": "pickup"})
        run(bot.got_contact(msg("+998"), st))
        m = msg("/cancel")
        run(bot.cancel(m, st))
        self.assertEqual(self.order_row()[0], "cancelled")
        # просрочка
        bot.carts[5] = {"sazan": 1}
        run(bot.got_contact(msg("+998"), St({"kind": "pickup"})))
        bot.db.execute("UPDATE orders SET created=datetime('now','-31 minutes') WHERE status='awaiting'")
        b = MagicMock(send_message=AsyncMock())
        self.assertEqual(run(bot.expire_awaiting(b)), 1)
        self.assertEqual(self.order_row(2)[0], "cancelled")
        # чек после отмены не создаёт заказ
        p = msg(photo=[MagicMock(file_id="F")])
        run(bot.got_proof_photo(p, St({"kind": "pickup", "oid": 2})))
        self.assertEqual(bot.db.execute("SELECT COUNT(*) FROM orders WHERE status='verify'").fetchone()[0], 0)

    def test_new_admin_tools(self):
        self.make_order("pay_cash", kind="pickup")
        run(bot.admin_decision(cb("ord:1:ok", uid=111)))
        run(bot.admin_decision(cb("ord:1:done", uid=111)))
        m = msg(uid=111)
        run(bot.report(m, MagicMock(args="all")))
        text = m.answer.call_args[0][0]
        self.assertIn("Подтверждённых заказов: 1", text)
        self.assertIn("Лосось" if "Лосось" in text else "Сазан", text)
        c = cb("rpt:week", uid=111)
        run(bot.report_period(c))
        self.assertIn("7 дней", c.message.edit_text.call_args[0][0])
        m = msg(uid=111)
        run(bot.one_order(m, MagicMock(args="1")))
        self.assertIn("Заказ №1", m.answer.call_args[0][0])
        m = msg(uid=111)
        run(bot.msg_client(m, MagicMock(args="1 Заказ будет через 20 минут")))
        self.assertIn("Заказ будет через 20 минут", m.bot.send_message.call_args[0][1])
        # пауза приёма заказов
        run(bot.pause_cmd(msg(uid=111)))
        bot.carts[5] = {"sazan": 1}
        c = cb("checkout")
        run(bot.checkout(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        run(bot.resume_cmd(msg(uid=111)))
        self.assertFalse(bot.INFO["paused"])
        # блокировка
        run(bot.ban_cmd(msg(uid=111), MagicMock(args="5")))
        self.assertTrue(bot.is_banned(5))
        called = []

        async def handler(e, d):
            called.append(1)
        ev = MagicMock()
        ev.from_user.id = 5
        run(bot.ban_guard(handler, ev, {}))
        self.assertEqual(called, [])
        run(bot.unban_cmd(msg(uid=111), MagicMock(args="5")))
        run(bot.ban_guard(handler, ev, {}))
        self.assertEqual(called, [1])
        # чужим нельзя
        m = msg(uid=999)
        run(bot.report(m, MagicMock(args="all")))
        m.answer.assert_not_awaited()

    def test_transfer_payment_full_flow(self):
        bot.carts[5] = {"sazan": 1}
        st = St({"kind": "delivery", "address": "A", "phone": "+998"})
        c = cb("pay:pay_transfer")
        run(bot.order_pay(c, st))
        self.assertIn("8600 1111 2222 3333", c.message.answer.call_args[0][0])
        m = msg(photo=[MagicMock(file_id="FID")])
        run(bot.got_proof_photo(m, st))
        self.assertEqual(self.order_row()[0], "verify")
        self.assertEqual(m.bot.send_photo.call_args[0][:2], (111, "FID"))
        panel = msg(uid=111)
        run(bot.active_orders(panel))
        self.assertEqual(panel.answer_photo.call_args[0][0], "FID")
        self.assertIn("Оплата пришла", str(panel.answer_photo.call_args[1]["reply_markup"]))
        a = cb("ord:1:paid", uid=111, photo=True)
        run(bot.admin_decision(a))
        self.assertEqual(self.order_row()[0], "accepted")
        a = cb("ord:1:nopay", uid=111, photo=True)
        run(bot.admin_decision(a))
        self.assertEqual(self.order_row()[0], "rejected")

    def test_online_payment_invoice(self):
        bot.PAY_TOKEN = "tok"
        try:
            bot.carts[5] = {"sazan": 1}
            c = cb("pay:pay_online")
            run(bot.order_pay(c, St({"kind": "pickup", "phone": "1"})))
            kw = c.bot.send_invoice.call_args[1]
            self.assertEqual((kw["currency"], kw["prices"][0].amount), ("UZS", 14000000))
            m = msg()
            bot.carts[5] = {"sazan": 1}
            run(bot.got_payment(m, St({"kind": "pickup", "phone": "1"})))
            self.assertIn("ОПЛАЧЕНО", bot.db.execute("SELECT text FROM orders").fetchone()[0])
        finally:
            bot.PAY_TOKEN = ""

    # ---------- администратор и курьеры
    def test_admin_status_flow_and_rating(self):
        self.make_order("pay_cash", kind="pickup")
        for step, expected in (("ok", "accepted"), ("cook", "cooking"), ("go", "delivering"), ("done", "done")):
            run(bot.admin_decision(cb(f"ord:1:{step}", uid=111)))
            self.assertEqual(self.order_row()[0], expected)
        c = cb("rate:1:5")
        run(bot.rate(c))
        self.assertEqual(bot.db.execute("SELECT rating FROM reviews").fetchone()[0], 5)

    def test_admin_access_denied_for_others(self):
        self.make_order("pay_cash", kind="pickup")
        c = cb("ord:1:ok", uid=999)
        run(bot.admin_decision(c))
        self.assertEqual(self.order_row()[0], "new")

    def test_courier_group_flow(self):
        self.make_order("pay_cash")
        run(bot.admin_decision(cb("ord:1:ok", uid=111)))
        run(bot.admin_decision(cb("ord:1:disp", uid=111)))
        self.assertEqual(self.order_row()[0], "dispatched")
        run(bot.courier_action(cb("crt:1:take", uid=777, chat=-100500)))
        self.assertEqual(self.order_row()[0], "delivering")
        c = cb("crt:1:take", uid=888, chat=-100500)
        run(bot.courier_action(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        c = cb("crt:1:done", uid=888, chat=-100500)
        run(bot.courier_action(c))
        self.assertEqual(self.order_row()[0], "delivering")
        run(bot.courier_action(cb("crt:1:done", uid=777, chat=-100500)))
        self.assertEqual(self.order_row()[0], "done")

    def test_history_repeat_cancel(self):
        self.make_order("pay_cash", kind="pickup")
        m = msg()
        run(bot.history(m))
        self.assertGreaterEqual(m.answer.await_count, 2)
        bot.carts.clear()
        run(bot.repeat_order(cb("rep:1")))
        self.assertEqual(bot.carts[5], {"sazan": 1})
        run(bot.cancel_order(cb("cx:1")))
        self.assertEqual(self.order_row()[0], "cancelled")
        self.make_order("pay_cash", kind="pickup")
        run(bot.admin_decision(cb("ord:2:ok", uid=111)))
        c = cb("cx:2")
        run(bot.cancel_order(c))
        self.assertEqual(self.order_row(2)[0], "accepted")

    def test_booking_flow(self):
        st = St()
        run(bot.book(msg(), st))
        run(bot.book_when(msg("25.10 19:00"), st))
        m = msg("abc")
        run(bot.book_guests(m, st))
        self.assertIn("число", m.answer.call_args[0][0])
        run(bot.book_guests(msg("4"), st))
        m = msg("+998901112233")
        run(bot.book_done(m, st))
        self.assertEqual(bot.db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 1)
        run(bot.admin_decision(cb("book:1:ok", uid=111)))
        self.assertEqual(bot.db.execute("SELECT status FROM bookings").fetchone()[0], "accepted")

    def test_admin_commands(self):
        m = msg("/stats", uid=111)
        self.make_order("pay_cash", kind="pickup")
        run(bot.stats(m))
        self.assertIn("Заказов: 1", m.answer.call_args[0][0])
        m = msg(uid=111)
        run(bot.active_orders(m))
        self.assertIn("Заказ №1", m.answer.call_args[0][0])
        self.assertIn("✅ Принять", str(m.answer.call_args[1]["reply_markup"]))
        self.assertIn(bot.ADMIN_BTN, str(bot.main_kb(111)))
        self.assertNotIn(bot.ADMIN_BTN, str(bot.main_kb(5)))
        m = msg(uid=111)
        run(bot.export_orders(m))
        m.answer_document.assert_awaited()
        m = msg(uid=111)
        run(bot.broadcast(m, MagicMock(args="Акция!")))
        self.assertIn("Отправлено: 1", m.answer.call_args[0][0])
        c = cb("sl:oliv", uid=111)
        run(bot.toggle_stop(c))
        self.assertEqual(bot.db.execute("SELECT COUNT(*) FROM stoplist").fetchone()[0], 1)
        m = msg(uid=999)
        run(bot.stats(m))
        m.answer.assert_not_awaited()

    def test_admin_edits_prices_photos_card_inside_bot(self):
        # навигация по меню настроек
        c = cb("adm:prices", uid=111)
        run(bot.admin_nav(c, St()))
        self.assertIn("Выберите раздел", c.message.edit_text.call_args[0][0])
        c = cb("adm:pc:fish", uid=111)
        run(bot.admin_nav(c, St()))
        self.assertIn("Сазан", str(c.message.edit_text.call_args[1]["reply_markup"]))
        # цена
        st = St()
        run(bot.admin_nav(cb("adm:p:sazan", uid=111), st))
        self.assertEqual(st.d["kind"], "price")
        m = msg("abc", uid=111)
        run(bot.admin_save(m, st))
        self.assertIn("числом", m.answer.call_args[0][0])
        run(bot.admin_save(msg("150 000", uid=111), st))
        self.assertEqual(bot.ITEMS["sazan"]["price"], 150000)
        self.assertEqual(bot.db.execute("SELECT value FROM overrides WHERE key='price:sazan'").fetchone()[0], "150000")
        bot.carts[5] = {"sazan": 1}
        self.assertEqual(bot.cart_text(5)[1], 150000)
        # фото
        st = St()
        run(bot.admin_nav(cb("adm:f:oliv", uid=111), st))
        run(bot.admin_save(msg("нет фото", uid=111), st))
        self.assertNotIn("photo_override", bot.ITEMS["oliv"])
        run(bot.admin_save(msg(photo=[MagicMock(file_id="NEWFID")], uid=111), st))
        self.assertEqual(bot.photo_of(bot.ITEMS["oliv"]), "NEWFID")
        # карта и контакты
        st = St()
        run(bot.admin_nav(cb("adm:s:card_number", uid=111), st))
        m = msg("1234", uid=111)
        run(bot.admin_save(m, st))
        self.assertIn("16 цифр", m.answer.call_args[0][0])
        run(bot.admin_save(msg("9860 1234 5678 9012", uid=111), st))
        self.assertEqual(bot.INFO["card"]["number"], "9860 1234 5678 9012")
        st = St()
        run(bot.admin_nav(cb("adm:s:delivery_fee", uid=111), st))
        run(bot.admin_save(msg("15000", uid=111), st))
        self.assertEqual(bot.INFO["delivery_fee"], 15000)
        # новая карта попадает клиенту при оплате
        bot.carts[5] = {"sazan": 1}
        st = St({"kind": "pickup", "address": "x"})
        c = cb("pay:pay_transfer")
        run(bot.order_pay(c, st))
        self.assertIn("9860 1234 5678 9012", c.message.answer.call_args[0][0])
        # переживает перезапуск
        bot.ITEMS["sazan"]["price"] = 1
        for k, v in bot.db.execute("SELECT key, value FROM overrides").fetchall():
            bot.apply_override(k, v)
        self.assertEqual(bot.ITEMS["sazan"]["price"], 150000)
        # чужим нельзя
        c = cb("adm:prices", uid=999)
        run(bot.admin_nav(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))
        m = msg("/settings", uid=999)
        run(bot.admin_settings(m, St()))
        m.answer.assert_not_awaited()

    def test_admin_keyboard_has_no_client_buttons_and_mode_toggle(self):
        def labels(uid):
            return [b.text for row in bot.main_kb(uid).keyboard for b in row]
        bot.db.execute("INSERT OR REPLACE INTO users(id,lang) VALUES(111,'ru')")
        admin = labels(111)
        self.assertIn(bot.ADMIN_BTN, admin)
        self.assertIn(bot.CLIENT_MODE_BTN, admin)
        for client_btn in ("🍽 Меню", "🛒 Корзина", "📅 Забронировать стол", "📜 Мои заказы"):
            self.assertNotIn(client_btn, admin)
        self.assertIn("🍽 Меню", labels(5))
        self.assertNotIn(bot.ADMIN_BTN, labels(5))
        run(bot.to_client_mode(msg(uid=111)))
        self.assertIn("🍽 Меню", labels(111))
        self.assertIn(bot.ADMIN_MODE_BTN, labels(111))
        run(bot.to_admin_mode(msg(uid=111)))
        self.assertNotIn("🍽 Меню", labels(111))
        run(bot.to_client_mode(msg(uid=999)))
        self.assertNotIn(999, bot.client_mode)
        m = msg("/start", uid=111)
        run(bot.send_welcome(m, 111))
        self.assertIn("Панель администратора", m.answer.call_args[0][0])

    def test_admin_management(self):
        run(bot.claim_admin(msg(uid=222), MagicMock(args="WRONG")))
        self.assertFalse(bot.is_admin(222))
        run(bot.claim_admin(msg(uid=222), MagicMock(args="CODE123")))
        self.assertTrue(bot.is_admin(222))
        run(bot.add_admin(msg(uid=111), MagicMock(args="333")))
        self.assertTrue(bot.is_admin(333))
        run(bot.remove_admin(msg(uid=111), MagicMock(args="333")))
        self.assertFalse(bot.is_admin(333))
        run(bot.add_admin(msg(uid=999), MagicMock(args="444")))
        self.assertFalse(bot.is_admin(444))

    # ---------- Яндекс Доставка
    def test_yandex_flow_with_fake_api(self):
        bot.YCFG = {"pickup": {"fullname": "Т", "lat": 41.3, "lon": 69.2, "phone": "+998900000000"}}
        old_token, old_call = yandex.TOKEN, yandex._call
        yandex.TOKEN = "tok"
        seen = {}

        def fake(path, body=None, query=""):
            if path == "claims/create":
                seen["body"] = body
                return {"id": "C1", "status": "new"}
            return {"status": fake.st, "performer_info": {"courier_name": "Ali"}}
        fake.st = "performer_found"
        yandex._call = fake
        try:
            self.make_order("pay_cash", lat=41.31, lon=69.28)
            run(bot.admin_decision(cb("ord:1:ok", uid=111)))
            self.assertEqual(self.order_row()[2], "C1")
            self.assertEqual(seen["body"]["route_points"][1]["address"]["coordinates"], [69.28, 41.31])
            self.assertTrue(seen["body"]["auto_accept"])
            for st, expected in (("performer_found", "delivering"), ("delivered", "done")):
                fake.st = st
                b = MagicMock(send_message=AsyncMock())

                async def once():
                    real = asyncio.sleep
                    bot.asyncio.sleep = lambda x: real(0)
                    t = asyncio.create_task(bot.yandex_poller(b))
                    await real(0.05)
                    t.cancel()
                    bot.asyncio.sleep = real
                run(once())
                self.assertEqual(self.order_row()[0], expected)
        finally:
            yandex.TOKEN, yandex._call, bot.YCFG = old_token, old_call, bot.INFO.get("yandex")


if __name__ == "__main__":
    unittest.main(verbosity=2)
