"""Модуль «Запись»: услуги, свободное время, подтверждение админом, напоминание за час."""
import datetime as dt
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import core
from core import db, get_setting, is_admin, is_btn, main_kb, money, notify_admins, t, tenant_of

router = Router()
ADM_BOOKINGS, ADM_ADDSVC, ADM_SERVICES, ADM_REPORT = "📋 Записи", "➕ Услуга", "🗂 Услуги", "📈 Отчёт записей"
STATUS_RU = {"pending": "🆕 ожидает", "confirmed": "✅ подтверждена", "rejected": "❌ отклонена", "cancelled": "🚫 отменена", "done": "🏁 выполнена"}
BUSY = ("pending", "confirmed")

core.register_texts({
    "b_book": {"ru": "📅 Записаться", "uz": "📅 Yozilish", "en": "📅 Book"},
    "b_mybook": {"ru": "🗓 Мои записи", "uz": "🗓 Mening yozuvlarim", "en": "🗓 My bookings"},
    "bk_no_services": {"ru": "Услуги пока не добавлены", "uz": "Xizmatlar hali qo'shilmagan", "en": "No services yet"},
    "bk_pick_service": {"ru": "Выберите услугу:", "uz": "Xizmatni tanlang:", "en": "Choose a service:"},
    "bk_pick_date": {"ru": "Выберите дату:", "uz": "Sanani tanlang:", "en": "Choose a date:"},
    "bk_pick_time": {"ru": "Выберите время:", "uz": "Vaqtni tanlang:", "en": "Choose a time:"},
    "bk_no_slots": {"ru": "На этот день нет свободного времени, выберите другой", "uz": "Bu kunga bo'sh vaqt yo'q, boshqasini tanlang",
                    "en": "No free time on this day, please pick another"},
    "bk_q_name": {"ru": "Как вас зовут?", "uz": "Ismingiz?", "en": "Your name?"},
    "bk_q_phone": {"ru": "Отправьте номер телефона:", "uz": "Telefon raqamingizni yuboring:", "en": "Send your phone number:"},
    "bk_sent": {"ru": "Запись №{n} отправлена: {svc}, {when}. Ждём подтверждения ⏳", "uz": "№{n} yozuv yuborildi: {svc}, {when}. Tasdiqlashni kutamiz ⏳",
                "en": "Booking #{n} sent: {svc}, {when}. Awaiting confirmation ⏳"},
    "bk_taken": {"ru": "Это время уже занято, выберите другое", "uz": "Bu vaqt band, boshqasini tanlang", "en": "That time is taken, please pick another"},
    "bk_mine_empty": {"ru": "У вас нет предстоящих записей", "uz": "Kelgusi yozuvlaringiz yo'q", "en": "You have no upcoming bookings"},
    "bk_cancel": {"ru": "❌ Отменить запись", "uz": "❌ Yozuvni bekor qilish", "en": "❌ Cancel booking"},
    "bk_cancelled": {"ru": "Запись №{n} отменена", "uz": "№{n} yozuv bekor qilindi", "en": "Booking #{n} cancelled"},
    "bk_confirmed": {"ru": "Запись №{n} подтверждена ✅ {svc}, {when}", "uz": "№{n} yozuv tasdiqlandi ✅ {svc}, {when}", "en": "Booking #{n} confirmed ✅ {svc}, {when}"},
    "bk_rejected": {"ru": "Запись №{n} отклонена ❌ Выберите другое время", "uz": "№{n} yozuv rad etildi ❌ Boshqa vaqtni tanlang", "en": "Booking #{n} declined ❌ Please pick another time"},
    "bk_reminder": {"ru": "⏰ Напоминаем: сегодня в {time} — {svc}", "uz": "⏰ Eslatma: bugun {time} da — {svc}", "en": "⏰ Reminder: today at {time} — {svc}"},
    "bk_status_pending": {"ru": "ожидает", "uz": "kutilmoqda", "en": "pending"},
    "bk_status_confirmed": {"ru": "подтверждена", "uz": "tasdiqlandi", "en": "confirmed"},
})
core.register_settings("booking", [("work_start", "🕘 Начало работы (ЧЧ:ММ)", "time"), ("work_end", "🕕 Конец работы (ЧЧ:ММ)", "time"),
                                   ("tz_offset", "🌍 Часовой пояс (UTC+, например 5)", "int"), ("phone", "📞 Телефон", "text"),
                                   ("currency", "💱 Валюта (сум, $ ...)", "text")])


def cleanup(slug):
    db.execute("DELETE FROM bookings WHERE tenant=?", (slug,))
    db.execute("DELETE FROM services WHERE tenant=?", (slug,))
    db.commit()


core.register_module("booking", ["b_book", "b_mybook"], [ADM_BOOKINGS, ADM_ADDSVC, ADM_SERVICES, ADM_REPORT], cleanup=cleanup)
db.execute("CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY, tenant TEXT, name TEXT, minutes INT, price INT, active INTEGER DEFAULT 1)")
db.execute("CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, tenant TEXT, user_id INT, service_id INT, start TEXT, name TEXT, phone TEXT, status TEXT DEFAULT 'pending', reminded INTEGER DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP)")
db.commit()


def local_now(tenant):
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=int(get_setting(tenant, "tz_offset", 5) or 5))


def hhmm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def free_slots(tenant, service, day, now=None):
    """Свободные времена начала для услуги в день day (date). Слоты идут с шагом длительности услуги."""
    now = now or local_now(tenant)
    start, end = hhmm(get_setting(tenant, "work_start", "09:00")), hhmm(get_setting(tenant, "work_end", "18:00"))
    step = max(15, service["minutes"])
    busy = []
    for st, minutes in db.execute("SELECT b.start, s.minutes FROM bookings b JOIN services s ON s.id=b.service_id "
                                  "WHERE b.tenant=? AND b.status IN ('pending','confirmed') AND b.start LIKE ?", (tenant, f"{day.isoformat()} %")):
        b0 = hhmm(st[11:16])
        busy.append((b0, b0 + minutes))
    out = []
    cur = start
    while cur + service["minutes"] <= end:
        slot_dt = dt.datetime.combine(day, dt.time(cur // 60, cur % 60))
        overlaps = any(cur < b1 and cur + service["minutes"] > b0 for b0, b1 in busy)
        if not overlaps and slot_dt > now:
            out.append(f"{cur // 60:02d}:{cur % 60:02d}")
        cur += step
    return out


def service_of(sid, tenant):
    row = db.execute("SELECT id, name, minutes, price, active FROM services WHERE id=? AND tenant=?", (sid, tenant)).fetchone()
    return dict(zip(("id", "name", "minutes", "price", "active"), row)) if row else None


# ---------------- клиент ----------------
@router.message(is_btn("b_book"))
async def book_start(m: Message):
    uid, tenant = m.from_user.id, tenant_of(m.from_user.id)
    rows = db.execute("SELECT id, name, minutes, price FROM services WHERE tenant=? AND active=1 ORDER BY id", (tenant,)).fetchall()
    if not rows:
        return await m.answer(t(uid, "bk_no_services"))
    b = InlineKeyboardBuilder()
    for sid, name, minutes, price in rows:
        b.button(text=f"{name} · {minutes} мин · {money(tenant, price)}"[:60], callback_data=f"bks:{sid}")
    await m.answer(t(uid, "bk_pick_service"), reply_markup=b.adjust(1).as_markup())


@router.callback_query(F.data.startswith("bks:"))
async def pick_service(c: CallbackQuery):
    uid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    svc = service_of(int(c.data.split(":")[1]), tenant)
    if not svc or not svc["active"]:
        return await c.answer()
    today = local_now(tenant).date()
    b = InlineKeyboardBuilder()
    for i in range(7):
        d = today + dt.timedelta(days=i)
        b.button(text=d.strftime("%d.%m %a"), callback_data=f"bkd:{svc['id']}:{d.isoformat()}")
    await c.message.edit_text(f"{svc['name']}\n{t(uid, 'bk_pick_date')}", reply_markup=b.adjust(2).as_markup())
    await c.answer()


@router.callback_query(F.data.regexp(r"^bkd:\d+:\d{4}-\d\d-\d\d$"))
async def pick_date(c: CallbackQuery):
    uid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    _, sid, day = c.data.split(":")
    svc = service_of(int(sid), tenant)
    if not svc:
        return await c.answer()
    slots = free_slots(tenant, svc, dt.date.fromisoformat(day))
    if not slots:
        return await c.answer(t(uid, "bk_no_slots"), show_alert=True)
    b = InlineKeyboardBuilder()
    for s in slots:
        b.button(text=s, callback_data=f"bkt:{sid}:{day}:{s.replace(':', '')}")
    await c.message.edit_text(f"{svc['name']} · {day}\n{t(uid, 'bk_pick_time')}", reply_markup=b.adjust(4).as_markup())
    await c.answer()


async def booking_done(bot, uid, ans, ctx):
    tenant = ctx["tenant"]
    svc = service_of(ctx["sid"], tenant)
    when = f"{ctx['day']} {ctx['time']}"
    if not svc or ctx["time"] not in free_slots(tenant, svc, dt.date.fromisoformat(ctx["day"])):
        return await bot.send_message(uid, t(uid, "bk_taken"), reply_markup=main_kb(uid))
    cur = db.execute("INSERT INTO bookings(tenant,user_id,service_id,start,name,phone) VALUES(?,?,?,?,?,?)",
                     (tenant, uid, svc["id"], when, ans["name"], ans["phone"]))
    db.commit()
    bid = cur.lastrowid
    await bot.send_message(uid, t(uid, "bk_sent", n=bid, svc=svc["name"], when=when), reply_markup=main_kb(uid))
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=f"bka:{bid}:ok")
    b.button(text="❌ Отклонить", callback_data=f"bka:{bid}:no")
    await notify_admins(bot, tenant, f"🆕 Запись №{bid}\n💇 {svc['name']}\n🕒 {when}\n👤 {ans['name']}\n📞 {ans['phone']}\n🔗 tg://user?id={uid}",
                        reply_markup=b.as_markup())


core.register_wizard("booking", [
    {"key": "name", "prompt": "bk_q_name", "type": "text"},
    {"key": "phone", "prompt": "bk_q_phone", "type": "phone"},
], booking_done)


@router.callback_query(F.data.regexp(r"^bkt:\d+:\d{4}-\d\d-\d\d:\d{4}$"))
async def pick_time(c: CallbackQuery, state: FSMContext):
    uid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    _, sid, day, hm = c.data.split(":")
    time = f"{hm[:2]}:{hm[2:]}"
    svc = service_of(int(sid), tenant)
    if not svc or time not in free_slots(tenant, svc, dt.date.fromisoformat(day)):
        return await c.answer(t(uid, "bk_taken"), show_alert=True)
    await core.start_wizard(c.message, state, "booking", uid, {"tenant": tenant, "sid": int(sid), "day": day, "time": time})
    await c.answer()


@router.message(is_btn("b_mybook"))
async def my_bookings(m: Message):
    uid, tenant = m.from_user.id, tenant_of(m.from_user.id)
    rows = db.execute("SELECT b.id, s.name, b.start, b.status FROM bookings b JOIN services s ON s.id=b.service_id "
                      "WHERE b.tenant=? AND b.user_id=? AND b.status IN ('pending','confirmed') ORDER BY b.start", (tenant, uid)).fetchall()
    if not rows:
        return await m.answer(t(uid, "bk_mine_empty"))
    for bid, name, start, status in rows:
        b = InlineKeyboardBuilder()
        b.button(text=t(uid, "bk_cancel"), callback_data=f"bkx:{bid}")
        await m.answer(f"№{bid} · {name} · {start} · {t(uid, 'bk_status_' + status)}", reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("bkx:"))
async def cancel_booking(c: CallbackQuery):
    uid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    bid = int(c.data.split(":")[1])
    row = db.execute("SELECT status FROM bookings WHERE id=? AND tenant=? AND user_id=?", (bid, tenant, uid)).fetchone()
    if not row or row[0] not in BUSY:
        return await c.answer()
    db.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (bid,))
    db.commit()
    await c.message.edit_text(t(uid, "bk_cancelled", n=bid))
    await notify_admins(c.bot, tenant, f"🚫 Клиент отменил запись №{bid}")
    await c.answer()


# ---------------- админ ----------------
def booking_card(row):
    bid, name, start, cname, phone, status, uid = row
    return f"📋 Запись №{bid} · {STATUS_RU[status]}\n💇 {name}\n🕒 {start}\n👤 {cname}\n📞 {phone}\n🔗 tg://user?id={uid}"


def booking_kb(bid, status):
    b = InlineKeyboardBuilder()
    if status == "pending":
        b.button(text="✅ Подтвердить", callback_data=f"bka:{bid}:ok")
        b.button(text="❌ Отклонить", callback_data=f"bka:{bid}:no")
    elif status == "confirmed":
        b.button(text="🏁 Выполнена", callback_data=f"bka:{bid}:done")
        b.button(text="❌ Отменить", callback_data=f"bka:{bid}:no")
    return b.adjust(2).as_markup()


@router.message(F.text == ADM_BOOKINGS)
async def admin_bookings(m: Message):
    if not is_admin(m.from_user.id):
        return
    tenant = tenant_of(m.from_user.id)
    today = local_now(tenant).date().isoformat()
    rows = db.execute("SELECT b.id, s.name, b.start, b.name, b.phone, b.status, b.user_id FROM bookings b JOIN services s ON s.id=b.service_id "
                      "WHERE b.tenant=? AND b.status IN ('pending','confirmed') AND substr(b.start,1,10)>=? ORDER BY b.start", (tenant, today)).fetchall()
    if not rows:
        return await m.answer("Предстоящих записей нет")
    for row in rows:
        await m.answer(booking_card(row), reply_markup=booking_kb(row[0], row[5]))


@router.callback_query(F.data.regexp(r"^bka:\d+:(ok|no|done)$"))
async def booking_action(c: CallbackQuery):
    aid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    if not (tenant and is_admin(aid)):
        return await c.answer("Нет доступа", show_alert=True)
    _, bid, act = c.data.split(":")
    row = db.execute("SELECT b.user_id, b.status, s.name, b.start FROM bookings b JOIN services s ON s.id=b.service_id WHERE b.id=? AND b.tenant=?",
                     (bid, tenant)).fetchone()
    if not row or row[1] not in BUSY or (act == "done" and row[1] != "confirmed") or (act == "ok" and row[1] != "pending"):
        return await c.answer("Действие недоступно", show_alert=True)
    uid, _status, svc, start = row
    new = {"ok": "confirmed", "no": "rejected", "done": "done"}[act]
    db.execute("UPDATE bookings SET status=? WHERE id=?", (new, bid))
    db.commit()
    if act != "done":
        try:
            await c.bot.send_message(uid, t(uid, "bk_confirmed" if act == "ok" else "bk_rejected", n=bid, svc=svc, when=start))
        except Exception:
            pass
    if act == "done":
        await core.ask_rating(c.bot, uid, "booking", int(bid))
    suffix = f"\n→ {STATUS_RU[new]}"
    await c.message.edit_text(c.message.text + suffix, reply_markup=booking_kb(bid, new))
    await c.answer()


core.register_wizard("booking_addservice", [
    {"key": "name", "prompt": "Название услуги?", "type": "text"},
    {"key": "minutes", "prompt": "Длительность в минутах (например 60)?", "type": "int"},
    {"key": "price", "prompt": "Цена числом?", "type": "int"},
], lambda bot, uid, ans, ctx: _service_added(bot, uid, ans, ctx))


async def _service_added(bot, uid, ans, ctx):
    cur = db.execute("INSERT INTO services(tenant,name,minutes,price) VALUES(?,?,?,?)", (ctx["tenant"], ans["name"], max(5, ans["minutes"]), ans["price"]))
    db.commit()
    await bot.send_message(uid, f"✅ Услуга №{cur.lastrowid} «{ans['name']}» добавлена", reply_markup=main_kb(uid))


@router.message(F.text == ADM_ADDSVC)
async def add_service(m: Message, state: FSMContext):
    if is_admin(m.from_user.id) and tenant_of(m.from_user.id):
        await core.start_wizard(m, state, "booking_addservice", m.from_user.id, {"tenant": tenant_of(m.from_user.id)})


@router.message(F.text == ADM_SERVICES)
async def manage_services(m: Message):
    if not is_admin(m.from_user.id):
        return
    tenant = tenant_of(m.from_user.id)
    rows = db.execute("SELECT id, name, minutes, price, active FROM services WHERE tenant=? ORDER BY id", (tenant,)).fetchall()
    if not rows:
        return await m.answer("Услуг нет. Добавьте кнопкой «➕ Услуга».")
    for sid, name, minutes, price, active in rows:
        b = InlineKeyboardBuilder()
        b.button(text="👁 Скрыть" if active else "👁 Показать", callback_data=f"bkv:{sid}:toggle")
        b.button(text="🗑 Удалить", callback_data=f"bkv:{sid}:del")
        await m.answer(f"№{sid} · {'🟢' if active else '⚪️'} {name} · {minutes} мин · {money(tenant, price)}", reply_markup=b.adjust(2).as_markup())


@router.callback_query(F.data.regexp(r"^bkv:\d+:(toggle|del)$"))
async def service_action(c: CallbackQuery):
    tenant = tenant_of(c.from_user.id)
    if not (tenant and is_admin(c.from_user.id)):
        return await c.answer("Нет доступа", show_alert=True)
    _, sid, act = c.data.split(":")
    svc = service_of(int(sid), tenant)
    if not svc:
        return await c.answer("Не найдено")
    if act == "toggle":
        db.execute("UPDATE services SET active=? WHERE id=?", (0 if svc["active"] else 1, sid))
        await c.message.edit_text(f"№{sid} · {'⚪️ скрыта' if svc['active'] else '🟢 показана'}: {svc['name']}")
    else:
        db.execute("DELETE FROM services WHERE id=?", (sid,))
        db.execute("UPDATE bookings SET status='cancelled' WHERE service_id=? AND status IN ('pending','confirmed')", (sid,))
        await c.message.edit_text(f"🗑 «{svc['name']}» удалена, будущие записи отменены")
    db.commit()
    await c.answer()


@router.message(F.text == ADM_REPORT)
async def report(m: Message):
    if not is_admin(m.from_user.id):
        return
    tenant = tenant_of(m.from_user.id)
    by = dict(db.execute("SELECT status, COUNT(*) FROM bookings WHERE tenant=? GROUP BY status", (tenant,)).fetchall())
    top = db.execute("SELECT s.name, COUNT(*) FROM bookings b JOIN services s ON s.id=b.service_id WHERE b.tenant=? AND b.status IN ('confirmed','done') "
                     "GROUP BY s.id ORDER BY 2 DESC LIMIT 5", (tenant,)).fetchall()
    revenue = db.execute("SELECT COALESCE(SUM(s.price),0) FROM bookings b JOIN services s ON s.id=b.service_id WHERE b.tenant=? AND b.status IN ('confirmed','done')", (tenant,)).fetchone()[0]
    await m.answer("📈 Записи\n" + "\n".join(f"{STATUS_RU[k]}: {by.get(k, 0)}" for k in STATUS_RU) + f"\nОжидаемая выручка: {money(tenant, revenue)}\n\n🔥 Популярные услуги:\n"
                   + ("\n".join(f"{n} — {c}" for n, c in top) or "—"))


# ---------------- напоминания ----------------
async def send_reminders(bot, now_utc=None):
    """Отправляет напоминание за час до подтверждённой записи. Возвращает число отправленных."""
    sent = 0
    for bid, tenant, uid, start, name in db.execute("SELECT b.id, b.tenant, b.user_id, b.start, s.name FROM bookings b JOIN services s ON s.id=b.service_id "
                                                    "WHERE b.status='confirmed' AND b.reminded=0").fetchall():
        local = (now_utc or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)) + dt.timedelta(hours=int(get_setting(tenant, "tz_offset", 5) or 5))
        when = dt.datetime.strptime(start, "%Y-%m-%d %H:%M")
        if dt.timedelta(0) <= when - local <= dt.timedelta(hours=1):
            db.execute("UPDATE bookings SET reminded=1 WHERE id=?", (bid,))
            db.commit()
            try:
                await bot.send_message(uid, t(uid, "bk_reminder", time=start[11:16], svc=name))
                sent += 1
            except Exception:
                logging.warning("reminder not delivered")
    return sent


async def reminder_loop(bot):
    import asyncio
    while True:
        await asyncio.sleep(60)
        try:
            await send_reminders(bot)
        except Exception:
            logging.exception("reminder loop failed")


core.register_background(reminder_loop)


def export_bookings(tenant):
    rows = db.execute("SELECT b.id, b.start, s.name, b.name, b.phone, b.status, s.price FROM bookings b JOIN services s ON s.id=b.service_id WHERE b.tenant=? ORDER BY b.start", (tenant,)).fetchall()
    return ["№", "Время", "Услуга", "Клиент", "Телефон", "Статус", "Цена"], rows


def stats_lines(tenant):
    pend = db.execute("SELECT COUNT(*) FROM bookings WHERE tenant=? AND status='pending'", (tenant,)).fetchone()[0]
    conf = db.execute("SELECT COUNT(*) FROM bookings WHERE tenant=? AND status IN ('confirmed','done')", (tenant,)).fetchone()[0]
    avg, cnt = core.avg_rating(tenant, "booking")
    return [f"💇 Записи: подтверждено {conf}, ждут решения {pend}, услуг {db.execute('SELECT COUNT(*) FROM services WHERE tenant=?', (tenant,)).fetchone()[0]}",
            f"⭐ Оценка услуг: {avg or '—'} ({cnt})"]


core.register_export("booking", "bookings.csv", export_bookings)
core.register_stats("booking", stats_lines)
