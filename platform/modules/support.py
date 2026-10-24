"""Модуль «Поддержка»: частые вопросы (FAQ) и обращения к оператору с ответом из бота."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import core
from core import db, is_admin, is_btn, main_kb, notify_admins, t, tenant_of

router = Router()
ADM_ADDFAQ, ADM_FAQ, ADM_TICKETS = "➕ Вопрос", "🗂 FAQ", "🆘 Обращения"

core.register_texts({
    "b_faq": {"ru": "❓ Вопросы", "uz": "❓ Savollar", "en": "❓ FAQ"},
    "b_support": {"ru": "🆘 Оператор", "uz": "🆘 Operator", "en": "🆘 Operator"},
    "sp_no_faq": {"ru": "Частых вопросов пока нет. Напишите оператору 🆘", "uz": "Tez-tez so'raladigan savollar yo'q. Operatorga yozing 🆘",
                  "en": "No FAQ yet. Write to the operator 🆘"},
    "sp_pick": {"ru": "Выберите вопрос:", "uz": "Savolni tanlang:", "en": "Choose a question:"},
    "sp_q_msg": {"ru": "Опишите ваш вопрос одним сообщением:", "uz": "Savolingizni bitta xabarda yozing:", "en": "Describe your question in one message:"},
    "sp_ok": {"ru": "Обращение №{n} отправлено оператору. Ответим здесь же.", "uz": "№{n} murojaat operatorga yuborildi. Javobni shu yerda beramiz.",
              "en": "Request #{n} sent to the operator. We will reply here."},
    "sp_answer": {"ru": "💬 Ответ оператора на обращение №{n}:", "uz": "💬 №{n} murojaatga operator javobi:", "en": "💬 Operator reply to request #{n}:"},
})


def cleanup(slug):
    db.execute("DELETE FROM faq WHERE tenant=?", (slug,))
    db.execute("DELETE FROM tickets WHERE tenant=?", (slug,))
    db.commit()


core.register_module("support", ["b_faq", "b_support"], [ADM_TICKETS], cleanup=cleanup, more_labels=[ADM_ADDFAQ, ADM_FAQ])
core.register_help("support", "🆘 Поддержка", [
    "Откройте «⚙️ Ещё» → «➕ Вопрос» и добавьте частые вопросы с ответами.",
    "Обращения клиентов приходят сюда. Нажмите «✍️ Ответить»: клиент получит ответ в боте.",
])
db.execute("CREATE TABLE IF NOT EXISTS faq(id INTEGER PRIMARY KEY, tenant TEXT, q TEXT, a TEXT)")
db.execute("CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY, tenant TEXT, user_id INT, text TEXT, status TEXT DEFAULT 'open', created TEXT)")
db.commit()


@router.message(is_btn("b_faq"))
async def faq(m: Message):
    uid, tenant = m.from_user.id, tenant_of(m.from_user.id)
    rows = db.execute("SELECT id, q FROM faq WHERE tenant=? ORDER BY id", (tenant,)).fetchall()
    if not rows:
        return await m.answer(t(uid, "sp_no_faq"))
    b = InlineKeyboardBuilder()
    for fid, q in rows:
        b.button(text=q[:60], callback_data=f"fq:{fid}")
    await m.answer(t(uid, "sp_pick"), reply_markup=b.adjust(1).as_markup())


@router.callback_query(F.data.startswith("fq:"))
async def faq_answer(c: CallbackQuery):
    row = db.execute("SELECT q, a FROM faq WHERE id=? AND tenant=?", (int(c.data.split(":")[1]), tenant_of(c.from_user.id))).fetchone()
    if row:
        await c.message.answer(f"❓ {row[0]}\n\n{row[1]}")
    await c.answer()


async def ticket_done(bot, uid, ans, ctx):
    tenant = ctx["tenant"]
    cur = db.execute("INSERT INTO tickets(tenant,user_id,text,created) VALUES(?,?,?,datetime('now'))", (tenant, uid, ans["text"]))
    db.commit()
    tid = cur.lastrowid
    await bot.send_message(uid, t(uid, "sp_ok", n=tid), reply_markup=main_kb(uid))
    await notify_admins(bot, tenant, ticket_text(tid, uid, ans["text"]), reply_markup=ticket_kb(tid))


def ticket_text(tid, uid, text):
    return f"🆘 Обращение №{tid}\n🔗 tg://user?id={uid}\n\n{text}"[:1000]


def ticket_kb(tid):
    b = InlineKeyboardBuilder()
    b.button(text="✍️ Ответить", callback_data=f"tk:{tid}:reply")
    b.button(text="✅ Закрыть", callback_data=f"tk:{tid}:close")
    return b.adjust(2).as_markup()


core.register_wizard("ticket", [{"key": "text", "prompt": "sp_q_msg", "type": "text"}], ticket_done)


@router.message(is_btn("b_support"))
async def support_start(m: Message, state: FSMContext):
    await core.start_wizard(m, state, "ticket", m.from_user.id, {"tenant": tenant_of(m.from_user.id)})


async def ticket_reply_sent(bot, uid, ans, ctx):
    row = db.execute("SELECT user_id FROM tickets WHERE id=? AND tenant=?", (ctx["tid"], ctx["tenant"])).fetchone()
    if not row:
        return
    try:
        await bot.send_message(row[0], f"{t(row[0], 'sp_answer', n=ctx['tid'])}\n{ans['text']}")
        await bot.send_message(uid, "✅ Ответ отправлен", reply_markup=main_kb(uid))
    except Exception:
        await bot.send_message(uid, "Не удалось отправить: клиент заблокировал бота", reply_markup=main_kb(uid))


core.register_wizard("ticket_reply", [{"key": "text", "prompt": "Напишите ответ клиенту:", "type": "text"}], ticket_reply_sent)


@router.callback_query(F.data.regexp(r"^tk:\d+:(reply|close)$"))
async def ticket_action(c: CallbackQuery, state: FSMContext):
    aid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    if not (tenant and is_admin(aid)):
        return await c.answer("Нет доступа", show_alert=True)
    _, tid, act = c.data.split(":")
    if not db.execute("SELECT 1 FROM tickets WHERE id=? AND tenant=?", (tid, tenant)).fetchone():
        return await c.answer("Не найдено")
    if act == "reply":
        await core.start_wizard(c.message, state, "ticket_reply", aid, {"tenant": tenant, "tid": int(tid)})
    else:
        db.execute("UPDATE tickets SET status='closed' WHERE id=?", (tid,))
        db.commit()
        await c.message.edit_text(c.message.text + "\n→ ✅ закрыто")
    await c.answer()


@router.message(F.text == ADM_TICKETS)
async def open_tickets(m: Message):
    if not is_admin(m.from_user.id):
        return
    rows = db.execute("SELECT id, user_id, text FROM tickets WHERE tenant=? AND status='open' ORDER BY id", (tenant_of(m.from_user.id),)).fetchall()
    if not rows:
        return await m.answer("Открытых обращений нет")
    for tid, uid, text in rows:
        await m.answer(ticket_text(tid, uid, text), reply_markup=ticket_kb(tid))


async def faq_added(bot, uid, ans, ctx):
    cur = db.execute("INSERT INTO faq(tenant,q,a) VALUES(?,?,?)", (ctx["tenant"], ans["q"], ans["a"]))
    db.commit()
    await bot.send_message(uid, f"✅ Вопрос №{cur.lastrowid} добавлен", reply_markup=main_kb(uid))


core.register_wizard("faq_add", [{"key": "q", "prompt": "Вопрос (как увидит клиент)?", "type": "text"},
                                 {"key": "a", "prompt": "Ответ?", "type": "text"}], faq_added)


@router.message(F.text == ADM_ADDFAQ)
async def add_faq(m: Message, state: FSMContext):
    if is_admin(m.from_user.id) and tenant_of(m.from_user.id):
        await core.start_wizard(m, state, "faq_add", m.from_user.id, {"tenant": tenant_of(m.from_user.id)})


@router.message(F.text == ADM_FAQ)
async def manage_faq(m: Message):
    if not is_admin(m.from_user.id):
        return
    rows = db.execute("SELECT id, q FROM faq WHERE tenant=? ORDER BY id", (tenant_of(m.from_user.id),)).fetchall()
    if not rows:
        return await m.answer("Вопросов нет. Добавьте кнопкой «➕ Вопрос».")
    for fid, q in rows:
        b = InlineKeyboardBuilder()
        b.button(text="🗑 Удалить", callback_data=f"fqd:{fid}")
        await m.answer(f"№{fid} · {q}", reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("fqd:"))
async def delete_faq(c: CallbackQuery):
    tenant = tenant_of(c.from_user.id)
    if not (tenant and is_admin(c.from_user.id)):
        return await c.answer("Нет доступа", show_alert=True)
    db.execute("DELETE FROM faq WHERE id=? AND tenant=?", (int(c.data.split(":")[1]), tenant))
    db.commit()
    await c.message.edit_text("🗑 Удалено")
    await c.answer()


def export_tickets(tenant):
    rows = db.execute("SELECT id, status, user_id, replace(text, char(10), ' ') FROM tickets WHERE tenant=? ORDER BY id", (tenant,)).fetchall()
    return ["№", "Статус", "ID клиента", "Текст"], rows


def stats_lines(tenant):
    return [f"🆘 Поддержка: открытых обращений {db.execute(chr(83)+'ELECT COUNT(*) FROM tickets WHERE tenant=? AND status=?', (tenant, 'open')).fetchone()[0]}, вопросов в FAQ {db.execute('SELECT COUNT(*) FROM faq WHERE tenant=?', (tenant,)).fetchone()[0]}"]


core.register_export("support", "tickets.csv", export_tickets)
core.register_stats("support", stats_lines)


def daily_lines(tenant, today):
    n = db.execute("SELECT COUNT(*) FROM tickets WHERE tenant=? AND status='open'", (tenant,)).fetchone()[0]
    return [f"🆘 Открытых обращений: {n}"] if n else []


core.register_daily("support", daily_lines)
core.register_stale("tickets", ("open",), 30, "Обращение")
