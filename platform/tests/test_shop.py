import unittest
from unittest.mock import MagicMock

from helpers import St, buttons, cb, cmd, labels, msg, run

import core  # noqa: E402
from modules import shop  # noqa: E402

OWNER, ADM_A, ADM_B, CLI_A, CLI_B = 111, 201, 202, 5, 6


class ShopTests(unittest.TestCase):
    def setUp(self):
        for t in ("products", "shop_orders", "users", "tenants", "tenant_admins", "user_tenant", "settings", "invites", "banned"):
            core.db.execute(f"DELETE FROM {t}")
        core.db.execute("INSERT INTO tenants VALUES('a','Магазин А','shop',1)")
        core.db.execute("INSERT INTO tenants VALUES('b','Магазин Б','shop,support',1)")
        core.db.executemany("INSERT INTO tenant_admins VALUES(?,?)", [("a", ADM_A), ("b", ADM_B)])
        core.db.executemany("INSERT INTO user_tenant VALUES(?,?)", [(ADM_A, "a"), (ADM_B, "b"), (CLI_A, "a"), (CLI_B, "b")])
        core.db.executemany("INSERT INTO users VALUES(?,?)", [(CLI_A, "ru"), (CLI_B, "ru"), (ADM_A, "ru"), (ADM_B, "ru")])
        core.db.commit()
        shop.carts.clear()
        core.set_setting("a", "card_number", "8600 1111 2222 3333")
        core.set_setting("a", "delivery_fee", 15000)
        core.set_setting("a", "free_delivery_from", 200000)
        self.p1 = self.product("a", "Одежда", "Футболка", 100000)
        self.p2 = self.product("b", "Чужое", "Чужой товар", 5000)

    def product(self, tenant, cat, name, price, active=1):
        cur = core.db.execute("INSERT INTO products(tenant,category,name,price,descr,active) VALUES(?,?,?,?,?,?)", (tenant, cat, name, price, "d", active))
        core.db.commit()
        return cur.lastrowid

    def checkout(self, kind=0, pay=0, uid=CLI_A, loc=False, comment="-"):
        shop.cart_of(uid)[self.p1] = 2
        st = St()
        run(shop.checkout(cb("scheckout", uid), st))
        run(core.wizard_choice(cb(f"wzc:{kind}", uid), st))
        if kind == 0:
            if loc:
                run(core.wizard_input(msg(uid=uid, location=MagicMock(latitude=41.2, longitude=69.2)), st))
            else:
                run(core.wizard_input(msg("ул. Навои 1", uid), st))
        run(core.wizard_input(msg(comment, uid), st))
        run(core.wizard_input(msg("+998901112233", uid), st))
        run(core.wizard_choice(cb(f"wzc:{pay}", uid), st))
        return st

    def test_keyboards_merge_modules(self):
        self.assertEqual(labels(core.main_kb, CLI_A), ["🛍 Каталог", "🛒 Корзина", "📜 Мои заказы", "🌐 Язык"])
        self.assertIn(shop.ADM_ORDERS, labels(core.main_kb, ADM_A))
        self.assertIn(core.SETTINGS_BTN, labels(core.main_kb, ADM_A))

    def test_catalog_shows_only_own_active_products(self):
        self.product("a", "Одежда", "Скрытый", 1, active=0)
        m = msg(uid=CLI_A)
        run(shop.catalog(m))
        texts = [c[0][0] for c in m.answer.call_args_list]
        self.assertEqual(len(texts), 1)
        self.assertIn("Футболка", texts[0])
        self.assertNotIn("Чужой", str(texts))
        self.product("a", "Обувь", "Кроссовки", 1)
        m = msg(uid=CLI_A)
        run(shop.catalog(m))
        self.assertIn("Выберите раздел", m.answer.call_args[0][0])

    def test_cart_isolated_plus_minus_and_other_tenant_product(self):
        run(shop.add_to_cart(cb(f"sa:{self.p1}", CLI_A)))
        run(shop.add_to_cart(cb(f"sa:{self.p2}", CLI_A)))  # чужой товар не добавляется
        self.assertEqual(shop.cart_of(CLI_A), {self.p1: 1})
        run(shop.change_qty(cb(f"si:{self.p1}", CLI_A)))
        self.assertEqual(shop.cart_of(CLI_A)[self.p1], 2)
        run(shop.change_qty(cb(f"sd:{self.p1}", CLI_A)))
        run(shop.change_qty(cb(f"sd:{self.p1}", CLI_A)))
        self.assertEqual(shop.cart_of(CLI_A), {})
        text, kb = shop.cart_view(CLI_A)
        self.assertIsNone(kb)
        shop.cart_of(CLI_A)[self.p1] = 3
        text, kb = shop.cart_view(CLI_A)
        self.assertIn("300 000 сум", text)
        self.assertIn("scheckout", buttons(kb))

    def test_checkout_totals_fee_rules(self):
        shop.cart_of(CLI_A)[self.p1] = 1
        self.assertEqual(shop.order_total(CLI_A, {"kind": "delivery"}), (100000, 15000))
        shop.cart_of(CLI_A)[self.p1] = 2
        self.assertEqual(shop.order_total(CLI_A, {"kind": "delivery"}), (200000, 0))
        self.assertEqual(shop.order_total(CLI_A, {"kind": "pickup"}), (200000, 0))

    def test_cash_order_creates_new_order_and_notifies_only_that_admin(self):
        self.checkout(kind=0, pay=0, loc=True, comment="позвонить")
        row = core.db.execute("SELECT status, total, tenant FROM shop_orders").fetchone()
        self.assertEqual(row, ("new", 200000, "a"))
        self.assertEqual(shop.cart_of(CLI_A), {})

    def test_notifications_go_to_that_tenants_admins_with_location(self):
        shop.cart_of(CLI_A)[self.p1] = 1
        st = St()
        bot = msg().bot
        ans = {"kind": "delivery", "addr": {"text": "x", "lat": 41.2, "lon": 69.2, "msg": [5, 705]}, "phone": "1", "pay": "cash"}
        run(shop.checkout_done(bot, CLI_A, ans, {"tenant": "a"}))
        sent = {c[0][0] for c in bot.send_message.call_args_list}
        self.assertEqual(sent, {CLI_A, ADM_A})
        self.assertEqual(bot.forward_message.call_args[0][0], ADM_A)

    def test_transfer_flow_with_proof_and_admin_actions(self):
        st = self.checkout(kind=1, pay=1)
        self.assertIn("8600 1111 2222 3333", shop.transfer_prompt(CLI_A, {"kind": "pickup"}, {}))
        run(core.wizard_input(msg(uid=CLI_A, photo=[MagicMock(file_id="PF")]), st))
        row = core.db.execute("SELECT status, proof FROM shop_orders").fetchone()
        self.assertEqual(row, ("verify", "photo:PF"))
        # чужой админ не может
        c = cb("so:1:paid", ADM_B)
        run(shop.order_action(c))
        self.assertEqual(core.db.execute("SELECT status FROM shop_orders").fetchone()[0], "verify")
        for act, expected in (("paid", "accepted"), ("ready", "ready"), ("done", "done")):
            c = cb(f"so:1:{act}", ADM_A)
            run(shop.order_action(c))
            self.assertEqual(core.db.execute("SELECT status FROM shop_orders").fetchone()[0], expected)
        c = cb("so:1:ok", ADM_A)
        run(shop.order_action(c))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))

    def test_transfer_choice_hidden_without_card(self):
        core.db.execute("DELETE FROM settings WHERE key='card_number'")
        opts = shop.pay_choices(CLI_A, {}, {})
        self.assertEqual([v for _, v in opts], ["cash"])

    def test_my_orders_and_cancel(self):
        self.checkout(kind=1, pay=0)
        m = msg(uid=CLI_A)
        run(shop.my_orders(m))
        self.assertIn("scx:1", buttons(m.answer.call_args[1]["reply_markup"]))
        c = cb("scx:1", CLI_B)
        run(shop.cancel_order(c))
        self.assertEqual(core.db.execute("SELECT status FROM shop_orders").fetchone()[0], "new")
        c = cb("scx:1", CLI_A)
        run(shop.cancel_order(c))
        self.assertEqual(core.db.execute("SELECT status FROM shop_orders").fetchone()[0], "cancelled")

    def test_admin_adds_edits_hides_deletes_products(self):
        st = St()
        run(shop.add_product(msg(uid=ADM_A), st))
        for text in ("Обувь", "Кроссовки", "250 000"):
            run(core.wizard_input(msg(text, ADM_A), st))
        run(core.wizard_input(msg("-", ADM_A), st))
        run(core.wizard_input(msg("-", ADM_A), st))
        row = core.db.execute("SELECT tenant, category, name, price FROM products WHERE name='Кроссовки'").fetchone()
        self.assertEqual(row, ("a", "Обувь", "Кроссовки", 250000))
        pid = core.db.execute("SELECT id FROM products WHERE name='Кроссовки'").fetchone()[0]
        st = St()
        run(shop.product_action(cb(f"sp:{pid}:price", ADM_A), st))
        run(core.wizard_input(msg("200000", ADM_A), st))
        self.assertEqual(core.db.execute("SELECT price FROM products WHERE id=?", (pid,)).fetchone()[0], 200000)
        run(shop.product_action(cb(f"sp:{pid}:toggle", ADM_A), St()))
        self.assertEqual(core.db.execute("SELECT active FROM products WHERE id=?", (pid,)).fetchone()[0], 0)
        run(shop.product_action(cb(f"sp:{self.p2}:del", ADM_A), St()))  # чужой товар
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM products WHERE id=?", (self.p2,)).fetchone()[0], 1)
        run(shop.product_action(cb(f"sp:{pid}:del", ADM_A), St()))
        self.assertEqual(core.db.execute("SELECT COUNT(*) FROM products WHERE id=?", (pid,)).fetchone()[0], 0)
        m = msg(uid=CLI_A)
        run(shop.add_product(m, St()))
        m.answer.assert_not_awaited()

    def test_report_counts_only_own_tenant(self):
        self.checkout(kind=1, pay=0)
        run(shop.order_action(cb("so:1:ok", ADM_A)))
        m = msg(uid=ADM_A)
        run(shop.report(m))
        self.assertIn("Заказов: 1", m.answer.call_args[0][0])
        m = msg(uid=ADM_B)
        run(shop.report(m))
        self.assertIn("Заказов: 0", m.answer.call_args[0][0])

    def test_settings_menu_and_save(self):
        m = msg(uid=ADM_A)
        run(core.settings_menu(m))
        self.assertIn("set:card_number", buttons(m.answer.call_args[1]["reply_markup"]))
        st = St()
        run(core.settings_pick(cb("set:delivery_fee", ADM_A), st))
        run(core.wizard_input(msg("20 000", ADM_A), st))
        self.assertEqual(core.get_setting("a", "delivery_fee"), "20000")
        self.assertEqual(core.get_setting("b", "delivery_fee"), None)
        c = cb("set:card_number", CLI_A)
        run(core.settings_pick(c, St()))
        self.assertTrue(c.answer.call_args[1].get("show_alert"))

    def test_wizard_validation_and_skip(self):
        st = St()
        run(shop.add_product(msg(uid=ADM_A), st))
        run(core.wizard_input(msg("Обувь", ADM_A), st))
        run(core.wizard_input(msg("Кроссовки", ADM_A), st))
        m = msg("abc", ADM_A)
        run(core.wizard_input(m, st))
        self.assertIn("Не получилось", m.answer.call_args[0][0])
        self.assertEqual(st.d["i"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
