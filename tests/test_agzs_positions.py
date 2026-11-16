"""Должности и права, назначение по @нику / id / пересланному сообщению, меню по должности, адресаты уведомлений."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_agzs_bot as T  # noqa: E402
from test_agzs_bot import FULL, OP, OWNER, Env, bot, core, extras, run  # noqa: E402
import staff  # noqa: E402

MGR, ACC, OBS = 601, 602, 603


def setup_team(e):
    core.db.set_user(MGR, "Управляющий Иван", "manager")
    core.db.set_user(ACC, "Бухгалтер Анна", "accountant")
    core.db.set_user(OBS, "Инвестор Олег", "observer")


def labels(uid):
    return {b.text for r in bot.menu(uid).keyboard for b in r}


def user_msg(e, uid, text, username=None, name="Тест"):
    m = e.msg(uid, text)
    m.from_user = SimpleNamespace(id=uid, full_name=name, username=username)
    return m


class Matrix(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        setup_team(self.e)

    def test_permission_table(self):
        P = {OWNER: {"shift", "finance", "accounting", "shifts_view", "approve", "stock_view", "stock_edit", "settings", "staff"},
             MGR: {"shift", "finance", "accounting", "shifts_view", "approve", "stock_view", "stock_edit"},
             ACC: {"finance", "accounting", "shifts_view", "stock_view"}, OP: {"shift"}, OBS: {"finance", "stock_view"}}
        for uid, perms in P.items():
            for perm in core.ALL_PERMS:
                self.assertEqual(core.can(uid, perm), perm in perms, (uid, perm))
        self.assertFalse(core.can(999, "shift"))
        self.assertEqual(set(core.POSITIONS), set(core.PERMS))

    def test_menus_by_position(self):
        self.assertTrue({bot.B_STOCK, bot.B_FIN, bot.B_GRAPH, bot.B_XLS, bot.B_SHIFTS, bot.B_ACC, bot.B_SET, bot.B_STAFF, "➕ Сменный отчёт"} <= labels(OWNER))
        m = labels(MGR)
        self.assertTrue({"➕ Сменный отчёт", bot.B_SHIFTS, bot.B_ACC, bot.B_FIN, bot.B_STOCK} <= m)
        self.assertFalse({bot.B_SET, bot.B_STAFF} & m)
        a = labels(ACC)
        self.assertTrue({bot.B_FIN, bot.B_ACC, bot.B_SHIFTS, bot.B_STOCK} <= a)
        self.assertFalse({"➕ Сменный отчёт", bot.B_SET, bot.B_STAFF} & a)
        self.assertEqual(labels(OBS), {bot.B_STOCK, bot.B_FIN, bot.B_GRAPH, bot.B_XLS})
        self.assertEqual(labels(OP), {"➕ Сменный отчёт", "📋 Мои смены", "🌐 Язык"})

    def test_welcome_texts_by_position(self):
        m = self.e.msg(ACC, "/start")
        run(bot.start(m, self.e.state(ACC)))
        self.assertIn("Бухгалтер", self.e.last_text(m))
        self.assertIn("финансы", self.e.last_text(m))


class Access(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        setup_team(self.e)
        self.shift = T.core.db.add_shift(OP, "Али", T.FULL_DICT, [], status="pending")

    def test_observer_and_accountant_cannot_submit_or_approve(self):
        for uid in (ACC, OBS):
            st = self.e.state(uid)
            m = self.e.msg(uid, "➕ Сменный отчёт")
            run(bot.shift_start(m, st))
            m.answer.assert_not_called()
            self.assertIsNone(run(st.get_state()))
            c = self.e.cb(uid, "sh:today")
            run(bot.shift_button(c, st))
            c.message.answer.assert_not_called()
        st = self.e.state(MGR)
        run(bot.shift_start(self.e.msg(MGR, "➕ Сменный отчёт"), st))
        self.assertIsNotNone(run(st.get_state()))  # управляющий сдавать смены может
        for uid in (ACC, OBS, OP):
            run(bot.approve_cb(self.e.cb(uid, f"ap:{self.shift}:ok")))
            self.assertEqual(core.db.shift(self.shift)["status"], "pending", uid)
        run(bot.approve_cb(self.e.cb(MGR, f"ap:{self.shift}:ok")))
        self.assertEqual(core.db.shift(self.shift)["status"], "approved")

    def test_finance_open_for_accountant_and_observer_but_not_operator(self):
        for uid, ok in ((ACC, True), (OBS, True), (MGR, True), (OP, False)):
            c = self.e.cb(uid, "fp:all")
            run(bot.fin_cb(c))
            self.assertEqual(c.message.answer.called, ok, uid)

    def test_accounting_open_for_accountant_not_observer(self):
        for uid, ok in ((ACC, True), (MGR, True), (OBS, False), (OP, False)):
            m = self.e.msg(uid, extras.B_ACC)
            run(extras.acc_menu(m, self.e.state(uid)))
            self.assertEqual(m.answer.called, ok, uid)

    def test_settings_and_staff_only_owner(self):
        for uid, ok in ((OWNER, True), (MGR, False), (ACC, False), (OBS, False), (OP, False)):
            m = self.e.msg(uid, bot.B_SET)
            run(bot.settings(m, self.e.state(uid)))
            self.assertEqual(m.answer.called, ok, ("settings", uid))
            m = self.e.msg(uid, staff.B_STAFF)
            run(staff.staff(m, self.e.state(uid)))
            self.assertEqual(m.answer.called, ok, ("staff", uid))
            c = self.e.cb(uid, "sf:info")
            run(staff.staff_cb(c, self.e.state(uid)))
            self.assertEqual(c.message.answer.called, ok, ("staff_cb", uid))

    def test_stock_view_vs_edit(self):
        for uid, view, edit in ((OWNER, True, True), (MGR, True, True), (ACC, True, False), (OBS, True, False), (OP, False, False)):
            m = self.e.msg(uid, bot.B_STOCK)
            run(bot.stock(m, self.e.state(uid)))
            self.assertEqual(m.answer.called, view, ("view", uid))
            st = self.e.state(uid)
            c = self.e.cb(uid, "sk:set")
            run(bot.stock_set(c, st))
            self.assertEqual(c.message.answer.called, edit, ("edit", uid))

    def test_shifts_list_and_delete(self):
        for uid, ok in ((ACC, True), (MGR, True), (OBS, False), (OP, False)):
            m = self.e.msg(uid, bot.B_SHIFTS)
            run(bot.shifts_list(m))
            self.assertEqual(m.answer.called, ok, uid)
        run(bot.shift_delete_yes(self.e.cb(ACC, f"dy:{self.shift}")))
        self.assertIsNotNone(core.db.shift(self.shift))  # бухгалтер не удаляет смены
        run(bot.shift_delete_yes(self.e.cb(MGR, f"dy:{self.shift}")))
        self.assertIsNone(core.db.shift(self.shift))


class Notifications(unittest.TestCase):
    def test_new_shift_goes_to_owner_and_manager_only(self):
        e = Env()
        setup_team(e)
        e.fill_shift(OP, FULL)
        run(bot.shift_button(e.cb(OP, "sh:@save"), e.state(OP)))
        got = {c.args[0] for c in e.tg.send_message.call_args_list if "Новая смена" in c.args[1]}
        self.assertEqual(got, {OWNER, MGR})
        mgr_msg = [c for c in e.tg.send_message.call_args_list if c.args[0] == MGR][0]
        self.assertIsNotNone(mgr_msg.kwargs.get("reply_markup"))  # у управляющего кнопки «Принять / Вернуть»

    def test_manager_shift_is_auto_approved_and_scheduler_targets(self):
        e = Env()
        setup_team(e)
        e.fill_shift(MGR, FULL)
        run(bot.shift_button(e.cb(MGR, "sh:@save"), e.state(MGR)))
        self.assertEqual(core.db.shifts()[-1]["status"], "approved")
        got = {c.args[0] for c in e.tg.send_message.call_args_list if "Новая смена" in c.args[1]}
        self.assertEqual(got, {OWNER})  # автору (управляющему) о его же смене не пишем
        self.assertEqual(core.ids_with("finance"), {OWNER, MGR, ACC, OBS})
        self.assertEqual(core.ids_with("staff"), {OWNER})
        self.assertEqual(core.ids_with("approve"), {OWNER, MGR})
        self.assertEqual(core.ids_with("accounting"), {OWNER, MGR, ACC})


class StaffUi(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        setup_team(self.e)

    def test_staff_screen_groups_people(self):
        m = self.e.msg(OWNER, staff.B_STAFF)
        run(staff.staff(m, self.e.state(OWNER)))
        text = self.e.last_text(m)
        for part in ("Владелец:", "Управляющий:", "Бухгалтер:", "Оператор:", "Наблюдатель:", "Управляющий Иван", "из настроек сервера"):
            self.assertIn(part, text)
        kb = [b.callback_data for r in m.answer.call_args.kwargs["reply_markup"].inline_keyboard for b in r]
        self.assertIn("sf:link", kb)
        self.assertIn("sf:as", kb)
        self.assertIn(f"sf:u:{MGR}", kb)

    def test_info_lists_all_positions(self):
        c = self.e.cb(OWNER, "sf:info")
        run(staff.staff_cb(c, self.e.state(OWNER)))
        text = c.message.answer.call_args[0][0]
        for name in core.POSITIONS.values():
            self.assertIn(name, text)

    def test_invite_link_for_each_position_gives_that_position(self):
        for pos in ("manager", "accountant", "observer", "operator", "admin"):
            c = self.e.cb(OWNER, f"sf:lp:{pos}")
            run(staff.staff_cb(c, self.e.state(OWNER)))
            link = c.message.answer.call_args[0][0]
            code = link.split("start=p_")[1].split()[0]
            uid = 900 + len(pos)
            run(bot.start(self.e.msg(uid, f"/start p_{code}"), self.e.state(uid)))
            self.assertEqual(core.db.role_of(uid), pos)
        notified = [c for c in self.e.tg.send_message.call_args_list if c.args[0] == OWNER and "Новый сотрудник" in c.args[1]]
        self.assertEqual(len(notified), 5)

    def assign(self, role, text=None, forward=None, uid=OWNER):
        st = self.e.state(uid)
        run(staff.staff_cb(self.e.cb(uid, f"sf:ap:{role}"), st))
        m = self.e.msg(uid, text)
        if forward is not None:
            m.forward_origin = SimpleNamespace(sender_user=forward)
        run(staff.assign_target(m, st))
        return m, st

    def test_assign_by_id(self):
        m, st = self.assign("manager", "7001234")
        self.assertEqual(core.db.role_of(7001234), "manager")
        self.assertIn("Управляющий", self.e.last_text(m))
        self.assertEqual(self.e.tg.send_message.call_args.args[0], 7001234)  # человеку ушло уведомление
        self.assertIsNone(run(st.get_state()))

    def test_assign_by_forwarded_message(self):
        fwd = SimpleNamespace(id=7002, full_name="Пересланный Ким", username="kim")
        self.assign("accountant", None, forward=fwd)
        self.assertEqual(core.db.role_of(7002), "accountant")
        self.assertEqual(core.db.get_user(7002)["name"], "Пересланный Ким")

    def test_assign_by_username_pending_then_applied_on_first_contact(self):
        m, _ = self.assign("admin", "@Baxshillayevr_03")
        self.assertIn("как только откроет бота", self.e.last_text(m))
        self.assertEqual([g["username"] for g in core.db.grants()], ["baxshillayevr_03"])
        self.assertIsNone(core.db.role_of(555000))
        u = SimpleNamespace(id=555000, full_name="Розик", username="Baxshillayevr_03")
        self.assertEqual(core.touch_user(u), "admin")
        self.assertEqual(core.db.role_of(555000), "admin")
        self.assertTrue(core.can(555000, "staff"))
        self.assertEqual(core.db.grants(), [])
        self.assertIsNone(core.touch_user(u))  # второй раз ничего не выдаём
        self.assertIn("grant_applied", [x["action"] for x in core.db.audit()])
        # владелец видит нового владельца в списке; /start показывает полное меню
        m2 = user_msg(self.e, 555000, "/start", username="Baxshillayevr_03", name="Розик")
        run(bot.start(m2, self.e.state(555000)))
        self.assertIn(bot.B_STAFF, labels(555000))

    def test_assign_by_username_when_already_seen_is_immediate(self):
        core.db.touch(8001, "olim_k", "Олим")
        m, _ = self.assign("observer", "@olim_k")
        self.assertEqual(core.db.role_of(8001), "observer")
        self.assertEqual(core.db.grants(), [])
        self.assertIn("Олим", self.e.last_text(m))

    def test_bad_input_and_env_owner_locked(self):
        m, st = self.assign("manager", "ы")
        self.assertIn("Не понял", self.e.last_text(m))
        self.assertIsNotNone(run(st.get_state()))
        m2 = self.e.msg(OWNER, str(OWNER))
        run(staff.assign_target(m2, st))
        self.assertIn("настроек сервера", self.e.last_text(m2))
        self.assertEqual(core.db.role_of(OWNER), "admin")

    def test_operator_cannot_assign(self):
        st = self.e.state(OP)
        run(staff.staff_cb(self.e.cb(OP, "sf:ap:admin"), st))
        self.assertIsNone(run(st.get_state()))
        m = self.e.msg(OP, "@someone_x")
        run(staff.assign_target(m, st))
        self.assertEqual(core.db.grants(), [])

    def test_change_and_remove_position(self):
        c = self.e.cb(OWNER, f"sf:u:{ACC}")
        run(staff.staff_cb(c, self.e.state(OWNER)))
        kb = [b.callback_data for r in c.message.answer.call_args.kwargs["reply_markup"].inline_keyboard for b in r]
        self.assertIn(f"sf:set:{ACC}:manager", kb)
        self.assertIn(f"sf:rm:{ACC}", kb)
        run(staff.staff_cb(self.e.cb(OWNER, f"sf:set:{ACC}:manager"), self.e.state(OWNER)))
        self.assertEqual(core.db.role_of(ACC), "manager")
        self.assertIn("position_changed", [x["action"] for x in core.db.audit()])
        run(staff.staff_cb(self.e.cb(OWNER, f"sf:rm:{ACC}"), self.e.state(OWNER)))
        self.assertIsNone(core.db.role_of(ACC))
        c = self.e.cb(OWNER, f"sf:u:{OWNER}")
        run(staff.staff_cb(c, self.e.state(OWNER)))
        self.assertIsNone(c.message.answer.call_args.kwargs.get("reply_markup"))  # владельца из настроек не трогаем
        run(staff.staff_cb(self.e.cb(OWNER, f"sf:set:{OWNER}:operator"), self.e.state(OWNER)))
        run(staff.staff_cb(self.e.cb(OWNER, f"sf:rm:{OWNER}"), self.e.state(OWNER)))
        self.assertEqual(core.db.role_of(OWNER), "admin")

    def test_cancel_pending_grant_and_legacy_command(self):
        core.db.add_grant("temp_user", "manager")
        run(staff.staff_cb(self.e.cb(OWNER, "sf:gr:temp_user"), self.e.state(OWNER)))
        self.assertEqual(core.db.grants(), [])
        m = self.e.msg(OWNER, "/addoperator 4242 Вали")
        run(staff.add_operator(m))
        self.assertEqual(core.db.role_of(4242), "operator")
        m = self.e.msg(MGR, "/addoperator 5252 Нет")
        run(staff.add_operator(m))
        self.assertIsNone(core.db.role_of(5252))

    def test_parse_target(self):
        P = staff.parse_target
        mk = lambda text: SimpleNamespace(text=text, forward_origin=None, forward_from=None)
        self.assertEqual(P(mk("@Ali_Valiev")), ("user", "ali_valiev"))
        self.assertEqual(P(mk("ali_valiev")), ("user", "ali_valiev"))
        self.assertEqual(P(mk("123456789")), ("id", 123456789, None))
        self.assertIsNone(P(mk("ab")))
        self.assertIsNone(P(mk("привет мир")))
        self.assertIsNone(P(mk("")))


class Seeding(unittest.TestCase):
    def test_owner_username_grant_makes_owner_on_first_message(self):
        e = Env()
        core.db.add_grant("baxshillayevr_03", "admin", "настройки сервера")
        m = user_msg(e, 424242, "/start", username="baxshillayevr_03", name="Розикбек")
        run(bot.start(m, e.state(424242)))
        self.assertEqual(core.db.role_of(424242), "admin")
        self.assertIn("владелец", e.last_text(m))
        self.assertIn(bot.B_STAFF, labels(424242))

    def test_touch_ignores_users_without_username_and_records_seen(self):
        e = Env()
        u = SimpleNamespace(id=31337, full_name="Без ника", username=None)
        self.assertIsNone(core.touch_user(u))
        self.assertEqual(core.db.seen_user(31337)["name"], "Без ника")
        self.assertIsNone(core.touch_user(None))


if __name__ == "__main__":
    unittest.main()
