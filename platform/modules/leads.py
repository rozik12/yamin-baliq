"""Модуль «Заявки»: форма заявки (тема, имя, телефон, сообщение), воронка статусов, ответ клиенту. Для менеджеров, недвижимости, услуг."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import core
from core import db, get_setting, is_admin, is_btn, main_kb, notify_admins, t, tenant_of

router = Router()
ADM_LEADS, ADM_REPORT = "📋 Заявки", "📈 Отчёт заявок"
STATUS_RU = {"new": "🆕 новая", "work": "🔧 в работе", "done": "✅ выполнена", "rejected": "❌ отказ"}

core.register_texts({
    "b_lead": {"ru": "📝 Оставить заявку", "uz": "📝 Ariza qoldirish", "en": "📝 Leave a request"},
    "b_myleads": {"ru": "📄 Мои заявки", "uz": "📄 Mening arizalarim", "en": "📄 My requests"},
    "ld_q_topic": {"ru": "Что вас интересует?", "uz": "Sizni nima qiziqtiradi?", "en": "What are you interested in?"},
    "ld_q_name": {"ru": "Как вас зовут?", "uz": "Ismingiz?", "en": "Your name?"},
    "ld_q_phone": {"ru": "Отправьте номер телефона:", "uz": "Telefon raqamingizni yuboring:", "en": "Send your phone number:"},
    "ld_q_msg": {"ru": "Опишите задачу или вопрос:", "uz": "Vazifa yoki savolni yozing:", "en": "Describe your task or question:"},
    "ld_ok": {"ru": "Спасибо! Заявка №{n} принята, мы свяжемся с вами.", "uz": "Rahmat! №{n} ariza qabul qilindi, siz bilan bog'lanamiz.",
              "en": "Thank you! Request #{n} received, we will contact you."},
    "ld_empty": {"ru": "У вас пока нет заявок", "uz": "Hozircha arizalaringiz yo'q", "en": "You have no requests yet"},
    "ld_title": {"ru": "📄 Ваши заявки:", "uz": "📄 Arizalaringiz:", "en": "📄 Your requests:"},
    "ld_st_new": {"ru": "принята", "uz": "qabul qilindi", "en": "received"},
    "ld_st_work": {"ru": "в работе 🔧", "uz": "ishda 🔧", "en": "in progress 🔧"},
    "ld_st_done": {"ru": "выполнена ✅", "uz": "bajarildi ✅", "en": "completed ✅"},
    "ld_st_rejected": {"ru": "закрыта", "uz": "yopildi", "en": "closed"},
    "ld_reply": {"ru": "💬 Сообщение по заявке №{n}:", "uz": "💬 №{n} ariza bo'yicha xabar:", "en": "💬 Message about request #{n}:"},
})
core.register_settings("leads", [("lead_topics", "📌 Темы заявки (через запятую)", "text"), ("phone", "📞 Телефон", "text")])


def cleanup(slug):
    db.execute("DELETE FROM leads WHERE tenant=?", (slug,))
    db.commit()


core.register_module("leads", ["b_lead", "b_myleads"], [ADM_LEADS], cleanup=cleanup, more_labels=[ADM_REPORT])
core.register_help("leads", "📝 Приём заявок", [
    "Откройте «⚙️ Ещё» → «⚙️ Настройки»: укажите телефон и темы заявки через запятую (например: Квартира, Дом, Офис).",
    "Нажмите «⚙️ Ещё» → «🔗 Ссылка и QR» и покажите клиентам.",
    "Новые заявки приходят сюда: «В работу», «Выполнена», «✉️ Написать клиенту». Клиент получает уведомления сам.",
])
db.execute("CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY, tenant TEXT, user_id INT, topic TEXT, name TEXT, phone TEXT, message TEXT, status TEXT DEFAULT 'new', created TEXT DEFAULT CURRENT_TIMESTAMP)")
db.commit()


def topics(tenant):
    return [x.strip() for x in (get_setting(tenant, "lead_topics", "") or "").split(",") if x.strip()]


def card(row):
    lid, topic, name, phone, message, status, uid = row
    return (f"Заявка №{lid} · {STATUS_RU[status]}\n" + (f"📌 {topic}\n" if topic else "") +
            f"👤 {name}\n📞 {phone}\n🔗 tg://user?id={uid}\n\n{message or ''}")[:1000]


def card_kb(lid, status):
    b = InlineKeyboardBuilder()
    if status == "new":
        b.button(text="🔧 В работу", callback_data=f"lda:{lid}:work")
    if status in ("new", "work"):
        b.button(text="✅ Выполнена", callback_data=f"lda:{lid}:done")
        b.button(text="❌ Отказ", callback_data=f"lda:{lid}:rejected")
    b.button(text="✉️ Написать клиенту", callback_data=f"ldm:{lid}")
    return b.adjust(2).as_markup()


async def lead_done(bot, uid, ans, ctx):
    tenant = ctx["tenant"]
    cur = db.execute("INSERT INTO leads(tenant,user_id,topic,name,phone,message) VALUES(?,?,?,?,?,?)",
                     (tenant, uid, ans.get("topic"), ans["name"], ans["phone"], ans.get("message")))
    db.commit()
    lid = cur.lastrowid
    await bot.send_message(uid, t(uid, "ld_ok", n=lid), reply_markup=main_kb(uid))
    row = db.execute("SELECT id, topic, name, phone, message, status, user_id FROM leads WHERE id=?", (lid,)).fetchone()
    await notify_admins(bot, tenant, "🆕 " + card(row), reply_markup=card_kb(lid, "new"))


core.register_wizard("lead", [
    {"key": "topic", "prompt": "ld_q_topic", "type": "choice", "choices": lambda uid, a, c: [(x, x) for x in topics(tenant_of(uid))],
     "when": lambda a, c: bool(topics(c["tenant"]))},
    {"key": "name", "prompt": "ld_q_name", "type": "text"},
    {"key": "phone", "prompt": "ld_q_phone", "type": "phone"},
    {"key": "message", "prompt": "ld_q_msg", "type": "text", "optional": True},
], lead_done)


@router.message(is_btn("b_lead"))
async def lead_start(m: Message, state: FSMContext):
    await core.start_wizard(m, state, "lead", m.from_user.id, {"tenant": tenant_of(m.from_user.id)})


@router.message(is_btn("b_myleads"))
async def my_leads(m: Message):
    uid, tenant = m.from_user.id, tenant_of(m.from_user.id)
    rows = db.execute("SELECT id, COALESCE(topic,''), status FROM leads WHERE tenant=? AND user_id=? ORDER BY id DESC LIMIT 10", (tenant, uid)).fetchall()
    if not rows:
        return await m.answer(t(uid, "ld_empty"))
    await m.answer(t(uid, "ld_title") + "\n" + "\n".join(f"№{i} {topic} — {t(uid, 'ld_st_' + st)}" for i, topic, st in rows))


@router.message(F.text == ADM_LEADS)
async def admin_leads(m: Message):
    if not is_admin(m.from_user.id):
        return
    rows = db.execute("SELECT id, topic, name, phone, message, status, user_id FROM leads WHERE tenant=? AND status IN ('new','work') ORDER BY id",
                      (tenant_of(m.from_user.id),)).fetchall()
    if not rows:
        return await m.answer("Открытых заявок нет")
    for row in rows:
        await m.answer(card(row), reply_markup=card_kb(row[0], row[5]))


@router.callback_query(F.data.regexp(r"^lda:\d+:(work|done|rejected)$"))
async def lead_action(c: CallbackQuery):
    aid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    if not (tenant and is_admin(aid)):
        return await c.answer("Нет доступа", show_alert=True)
    _, lid, st = c.data.split(":")
    row = db.execute("SELECT user_id, status FROM leads WHERE id=? AND tenant=?", (lid, tenant)).fetchone()
    if not row or row[1] in ("done", "rejected"):
        return await c.answer("Действие недоступно", show_alert=True)
    db.execute("UPDATE leads SET status=? WHERE id=?", (st, lid))
    db.commit()
    uid = row[0]
    try:
        await c.bot.send_message(uid, f"№{lid}: {t(uid, 'ld_st_' + st)}")
    except Exception:
        logging.warning("lead status not delivered")
    await c.message.edit_text(c.message.text + f"\n→ {STATUS_RU[st]}", reply_markup=card_kb(lid, st))
    await c.answer()


async def reply_sent(bot, uid, ans, ctx):
    row = db.execute("SELECT user_id FROM leads WHERE id=? AND tenant=?", (ctx["lid"], ctx["tenant"])).fetchone()
    if not row:
        return
    try:
        await bot.send_message(row[0], f"{t(row[0], 'ld_reply', n=ctx['lid'])}\n{ans['text']}")
        await bot.send_message(uid, "✅ Отправлено клиенту", reply_markup=main_kb(uid))
    except Exception:
        await bot.send_message(uid, "Не удалось отправить: клиент заблокировал бота", reply_markup=main_kb(uid))


core.register_wizard("lead_reply", [{"key": "text", "prompt": "Напишите сообщение клиенту:", "type": "text"}], reply_sent)


@router.callback_query(F.data.startswith("ldm:"))
async def lead_reply(c: CallbackQuery, state: FSMContext):
    tenant = tenant_of(c.from_user.id)
    lid = int(c.data.split(":")[1])
    if not (tenant and is_admin(c.from_user.id)) or not db.execute("SELECT 1 FROM leads WHERE id=? AND tenant=?", (lid, tenant)).fetchone():
        return await c.answer("Нет доступа", show_alert=True)
    await core.start_wizard(c.message, state, "lead_reply", c.from_user.id, {"tenant": tenant, "lid": lid})
    await c.answer()


@router.message(F.text == ADM_REPORT)
async def report(m: Message):
    if not is_admin(m.from_user.id):
        return
    tenant = tenant_of(m.from_user.id)
    by = dict(db.execute("SELECT status, COUNT(*) FROM leads WHERE tenant=? GROUP BY status", (tenant,)).fetchall())
    topics_rows = db.execute("SELECT COALESCE(topic,'—'), COUNT(*) FROM leads WHERE tenant=? GROUP BY topic ORDER BY 2 DESC LIMIT 5", (tenant,)).fetchall()
    total = sum(by.values())
    done = by.get("done", 0)
    await m.answer(f"📈 Заявки\nВсего: {total}\n" + "\n".join(f"{STATUS_RU[k]}: {by.get(k, 0)}" for k in STATUS_RU)
                   + f"\nКонверсия в «выполнена»: {round(done * 100 / total) if total else 0}%\n\nПо темам:\n"
                   + ("\n".join(f"{n}: {c}" for n, c in topics_rows) or "—"))


def export_leads(tenant):
    rows = db.execute("SELECT id, created, status, COALESCE(topic,''), name, phone, replace(COALESCE(message,''), char(10), ' ') FROM leads WHERE tenant=? ORDER BY id", (tenant,)).fetchall()
    return ["№", "Создана", "Статус", "Тема", "Имя", "Телефон", "Сообщение"], rows


def stats_lines(tenant):
    by = dict(db.execute("SELECT status, COUNT(*) FROM leads WHERE tenant=? GROUP BY status", (tenant,)).fetchall())
    return [f"📝 Заявки: новых {by.get('new', 0)}, в работе {by.get('work', 0)}, выполнено {by.get('done', 0)}, отказов {by.get('rejected', 0)}"]


core.register_export("leads", "leads.csv", export_leads)
core.register_stats("leads", stats_lines)


def daily_lines(tenant, today):
    n = db.execute("SELECT COUNT(*) FROM leads WHERE tenant=? AND status='new'", (tenant,)).fetchone()[0]
    return [f"📝 Новых заявок без ответа: {n}"] if n else ["📝 Новых заявок без ответа нет ✅"]


core.register_daily("leads", daily_lines)
core.register_stale("leads", ("new",), 30, "Заявка")
