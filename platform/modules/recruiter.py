"""Модуль «Рекрутер»: вакансии, отклики кандидатов, статусы, отчёт."""
import datetime as dt
import json
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

import core
from core import db, is_admin, is_btn, main_kb, notify_admins, t, tenant_of

router = Router()

ADM_CAND, ADM_JOBS, ADM_ADD, ADM_REPORT = "📋 Кандидаты", "🗂 Вакансии", "➕ Вакансия", "📈 Отчёт"

core.register_texts({
    "b_jobs": {"ru": "💼 Вакансии", "uz": "💼 Vakansiyalar", "en": "💼 Jobs"},
    "b_apps": {"ru": "📄 Мои отклики", "uz": "📄 Mening arizalarim", "en": "📄 My applications"},
    "no_jobs": {"ru": "Сейчас открытых вакансий нет. Загляните позже 🙏", "uz": "Hozircha ochiq vakansiyalar yo'q 🙏",
                "en": "No open positions right now. Please check back later 🙏"},
    "apply": {"ru": "✍️ Откликнуться", "uz": "✍️ Ariza yuborish", "en": "✍️ Apply"},
    "ask_name": {"ru": "Как вас зовут? (Фамилия Имя)", "uz": "Ismingiz? (Familiya Ism)", "en": "What is your full name?"},
    "ask_phone": {"ru": "Отправьте номер телефона:", "uz": "Telefon raqamingizni yuboring:", "en": "Send your phone number:"},
    "send_phone": {"ru": "📱 Отправить номер", "uz": "📱 Raqamni yuborish", "en": "📱 Send number"},
    "ask_about": {"ru": "Расскажите о себе и опыте работы в нескольких предложениях:",
                  "uz": "O'zingiz va ish tajribangiz haqida bir necha gapda yozing:",
                  "en": "Tell us about yourself and your experience in a few sentences:"},
    "ask_cv": {"ru": "Пришлите резюме файлом или фото, либо нажмите «Пропустить»:",
               "uz": "Rezyumeni fayl yoki rasm sifatida yuboring yoki «O'tkazib yuborish»ni bosing:",
               "en": "Send your CV as a file or photo, or tap “Skip”:"},
    "skip": {"ru": "⏭ Пропустить", "uz": "⏭ O'tkazib yuborish", "en": "⏭ Skip"},
    "applied_ok": {"ru": "Спасибо! Ваш отклик №{n} на «{job}» отправлен. Мы свяжемся с вами.",
                   "uz": "Rahmat! «{job}» uchun №{n} arizangiz yuborildi. Siz bilan bog'lanamiz.",
                   "en": "Thank you! Your application #{n} for “{job}” has been sent. We will contact you."},
    "dup": {"ru": "Вы уже откликались на эту вакансию", "uz": "Siz bu vakansiyaga allaqachon ariza yuborgansiz",
            "en": "You have already applied for this position"},
    "closed": {"ru": "Эта вакансия закрыта", "uz": "Bu vakansiya yopilgan", "en": "This position is closed"},
    "apps_empty": {"ru": "У вас пока нет откликов", "uz": "Hozircha arizalaringiz yo'q", "en": "You have no applications yet"},
    "apps_title": {"ru": "📄 Ваши отклики:", "uz": "📄 Arizalaringiz:", "en": "📄 Your applications:"},
    "rc_interview": {"ru": "📅 Приглашаем на собеседование по вакансии «{job}»:\n{when}\n{place}", "uz": "📅 «{job}» vakansiyasi bo'yicha suhbatga taklif qilamiz:\n{when}\n{place}",
                     "en": "📅 You are invited to an interview for “{job}”:\n{when}\n{place}"},
    "rc_reminder": {"ru": "⏰ Напоминаем: собеседование «{job}» сегодня в {time}. {place}", "uz": "⏰ Eslatma: «{job}» suhbati bugun {time} da. {place}", "en": "⏰ Reminder: interview for “{job}” today at {time}. {place}"},
    "rc_message": {"ru": "✉️ Сообщение по вашему отклику №{n}:", "uz": "✉️ №{n} arizangiz bo'yicha xabar:", "en": "✉️ Message about your application #{n}:"},
    "s_new": {"ru": "на рассмотрении", "uz": "ko'rib chiqilmoqda", "en": "under review"},
    "s_invited": {"ru": "вас приглашают на собеседование 🎉", "uz": "sizni suhbatga taklif qilishadi 🎉", "en": "you are invited to an interview 🎉"},
    "s_rejected": {"ru": "к сожалению, отказ. Спасибо за интерес!", "uz": "afsuski, rad etildi. Qiziqishingiz uchun rahmat!",
                   "en": "unfortunately declined. Thank you for your interest!"},
    "s_hired": {"ru": "поздравляем, вы приняты! 🎊", "uz": "tabriklaymiz, siz qabul qilindingiz! 🎊", "en": "congratulations, you are hired! 🎊"},
})


def cleanup(slug):
    db.execute("DELETE FROM applications WHERE tenant=?", (slug,))
    db.execute("DELETE FROM vacancies WHERE tenant=?", (slug,))
    db.commit()


core.register_module("recruiter", ["b_jobs", "b_apps"], [ADM_CAND, ADM_ADD, ADM_JOBS], cleanup=cleanup, more_labels=[ADM_REPORT])
core.register_help("recruiter", "👔 Подбор персонала", [
    "Нажмите «➕ Вакансия»: название, город, зарплата, описание.",
    "Нажмите «⚙️ Ещё» → «🔗 Ссылка и QR» и отправьте кандидатам.",
    "Отклики приходят сюда с резюме. Нажмите «Пригласить», «Принять» или «Отказ»: кандидат получит уведомление.",
])

db.execute("CREATE TABLE IF NOT EXISTS vacancies(id INTEGER PRIMARY KEY, tenant TEXT, title TEXT, city TEXT, salary TEXT, description TEXT, active INTEGER DEFAULT 1)")
db.execute("CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY, tenant TEXT, vacancy_id INT, user_id INT, name TEXT, phone TEXT, about TEXT, cv TEXT, status TEXT DEFAULT 'new', created TEXT DEFAULT CURRENT_TIMESTAMP)")
for _ddl in ("ALTER TABLE applications ADD COLUMN interview_at TEXT", "ALTER TABLE applications ADD COLUMN interview_info TEXT", "ALTER TABLE applications ADD COLUMN ireminded INTEGER DEFAULT 0"):
    try:
        db.execute(_ddl)
    except Exception:
        pass
db.commit()

STATUS_RU = {"new": "🆕 новый", "invited": "📞 приглашён", "rejected": "❌ отказ", "hired": "🎉 принят"}


class Apply(StatesGroup):
    name = State()
    phone = State()
    about = State()
    cv = State()


class AddJob(StatesGroup):
    title = State()
    city = State()
    salary = State()
    description = State()


# ---------------- кандидат ----------------
@router.message(is_btn("b_jobs"))
async def jobs(m: Message):
    uid = m.from_user.id
    rows = db.execute("SELECT id, title, city, salary, description FROM vacancies WHERE active=1 AND tenant=? ORDER BY id", (tenant_of(uid),)).fetchall()
    if not rows:
        return await m.answer(t(uid, "no_jobs"))
    for jid, title, city, salary, desc in rows:
        b = InlineKeyboardBuilder()
        b.button(text=t(uid, "apply"), callback_data=f"job:{jid}")
        await m.answer(f"💼 <b>{title}</b>\n📍 {city}\n💰 {salary}\n\n{desc}", parse_mode="HTML", reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("job:"))
async def apply_start(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    jid = int(c.data.split(":")[1])
    row = db.execute("SELECT active FROM vacancies WHERE id=? AND tenant=?", (jid, tenant_of(uid))).fetchone()
    if not row or not row[0]:
        return await c.answer(t(uid, "closed"), show_alert=True)
    if db.execute("SELECT 1 FROM applications WHERE vacancy_id=? AND user_id=?", (jid, uid)).fetchone():
        return await c.answer(t(uid, "dup"), show_alert=True)
    await state.clear()
    await state.update_data(job=jid)
    await state.set_state(Apply.name)
    await c.message.answer(t(uid, "ask_name"), reply_markup=ReplyKeyboardRemove())
    await c.answer()


@router.message(Apply.name)
async def apply_name(m: Message, state: FSMContext):
    uid = m.from_user.id
    await state.update_data(name=(m.text or "").strip()[:100])
    await state.set_state(Apply.phone)
    kb = ReplyKeyboardBuilder()
    kb.button(text=t(uid, "send_phone"), request_contact=True)
    await m.answer(t(uid, "ask_phone"), reply_markup=kb.as_markup(resize_keyboard=True, one_time_keyboard=True))


@router.message(Apply.phone)
async def apply_phone(m: Message, state: FSMContext):
    await state.update_data(phone=m.contact.phone_number if m.contact else (m.text or "").strip()[:30])
    await state.set_state(Apply.about)
    await m.answer(t(m.from_user.id, "ask_about"), reply_markup=ReplyKeyboardRemove())


@router.message(Apply.about)
async def apply_about(m: Message, state: FSMContext):
    uid = m.from_user.id
    await state.update_data(about=(m.text or "").strip()[:1500])
    await state.set_state(Apply.cv)
    b = InlineKeyboardBuilder()
    b.button(text=t(uid, "skip"), callback_data="cvskip")
    await m.answer(t(uid, "ask_cv"), reply_markup=b.as_markup())


async def finish_application(bot, user, state, cv):
    d = await state.get_data()
    uid = user.id
    cur = db.execute("INSERT INTO applications(tenant,vacancy_id,user_id,name,phone,about,cv) VALUES(?,?,?,?,?,?,?)",
                     (tenant_of(uid), d["job"], uid, d.get("name", ""), d.get("phone", ""), d.get("about", ""), cv))
    db.commit()
    aid = cur.lastrowid
    title = db.execute("SELECT title FROM vacancies WHERE id=?", (d["job"],)).fetchone()[0]
    await state.clear()
    await bot.send_message(uid, t(uid, "applied_ok", n=aid, job=title), reply_markup=main_kb(uid))
    await send_application_card(bot, aid, notify=True)


@router.message(Apply.cv, F.photo)
async def apply_cv_photo(m: Message, state: FSMContext):
    await finish_application(m.bot, m.from_user, state, f"photo:{m.photo[-1].file_id}")


@router.message(Apply.cv, F.document)
async def apply_cv_doc(m: Message, state: FSMContext):
    await finish_application(m.bot, m.from_user, state, f"doc:{m.document.file_id}")


@router.callback_query(Apply.cv, F.data == "cvskip")
async def apply_cv_skip(c: CallbackQuery, state: FSMContext):
    await finish_application(c.bot, c.from_user, state, None)
    await c.answer()


@router.message(Apply.cv)
async def apply_cv_text(m: Message):
    await m.answer(t(m.from_user.id, "ask_cv"))


@router.message(is_btn("b_apps"))
async def my_apps(m: Message):
    uid = m.from_user.id
    rows = db.execute("SELECT a.id, v.title, a.status FROM applications a JOIN vacancies v ON v.id=a.vacancy_id "
                      "WHERE a.user_id=? AND a.tenant=? ORDER BY a.id DESC LIMIT 10", (uid, tenant_of(uid))).fetchall()
    if not rows:
        return await m.answer(t(uid, "apps_empty"))
    await m.answer(t(uid, "apps_title") + "\n" + "\n".join(f"№{i} · {title} — {t(uid, 's_' + st)}" for i, title, st in rows))


# ---------------- рекрутер ----------------
def card_kb(aid, status):
    b = InlineKeyboardBuilder()
    if status in ("new", "invited"):
        if status == "new":
            b.button(text="📞 Пригласить", callback_data=f"cand:{aid}:invited")
        b.button(text="📅 Собеседование", callback_data=f"rci:{aid}")
        b.button(text="🎉 Принять", callback_data=f"cand:{aid}:hired")
        b.button(text="❌ Отказ", callback_data=f"cand:{aid}:rejected")
    if status in ("new", "invited", "hired"):
        b.button(text="✉️ Написать", callback_data=f"rcm:{aid}")
    return b.adjust(1).as_markup()


async def send_application_card(bot, aid, notify=False, to=None):
    row = db.execute("SELECT a.name, a.phone, a.about, a.cv, a.status, v.title, a.user_id, a.tenant FROM applications a "
                     "JOIN vacancies v ON v.id=a.vacancy_id WHERE a.id=?", (aid,)).fetchone()
    if not row:
        return
    name, phone, about, cv, status, title, uid, tenant = row
    text = (f"{'🆕 ' if notify else ''}Отклик №{aid} · {STATUS_RU[status]}\n💼 {title}\n👤 {name}\n📞 {phone}\n"
            f"🔗 tg://user?id={uid}\n\n{about}")[:1000]
    kb = card_kb(aid, status)
    for a in ([to] if to else core.admins_of(tenant)):
        try:
            if cv:
                kind, _, fid = cv.partition(":")
                send = bot.send_document if kind == "doc" else bot.send_photo
                await send(a, fid, caption=text, reply_markup=kb)
            else:
                await bot.send_message(a, text, reply_markup=kb)
        except Exception:
            core.logging.exception("card send failed")


@router.message(F.text == ADM_CAND)
async def candidates(m: Message):
    if not is_admin(m.from_user.id):
        return
    ids = [r[0] for r in db.execute("SELECT id FROM applications WHERE status IN ('new','invited') AND tenant=? ORDER BY id",
                                    (tenant_of(m.from_user.id),))]
    if not ids:
        return await m.answer("Новых кандидатов нет")
    for aid in ids:
        await send_application_card(m.bot, aid, to=m.from_user.id)


@router.callback_query(F.data.regexp(r"^cand:\d+:(invited|hired|rejected)$"))
async def candidate_status(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    _, aid, st = c.data.split(":")
    row = db.execute("SELECT a.user_id, v.title FROM applications a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.id=? AND a.tenant=?",
                     (aid, tenant_of(c.from_user.id))).fetchone()
    if not row:
        return await c.answer("Не найдено")
    db.execute("UPDATE applications SET status=? WHERE id=?", (st, aid))
    db.commit()
    uid, title = row
    try:
        await c.bot.send_message(uid, f"«{title}»: {t(uid, 's_' + st)}")
    except Exception:
        pass
    suffix = f"\n→ {STATUS_RU[st]}"
    if c.message.photo or c.message.document:
        await c.message.edit_caption(caption=((c.message.caption or "") + suffix)[:1024], reply_markup=card_kb(aid, st))
    else:
        await c.message.edit_text(c.message.text + suffix, reply_markup=card_kb(aid, st))
    await c.answer()


@router.message(F.text == ADM_ADD)
@router.message(Command("addvacancy"))
async def add_job_start(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.set_state(AddJob.title)
    await m.answer("Название вакансии? (/cancel — отмена)", reply_markup=ReplyKeyboardRemove())


@router.message(AddJob.title)
async def add_job_title(m: Message, state: FSMContext):
    await state.update_data(title=(m.text or "").strip()[:120])
    await state.set_state(AddJob.city)
    await m.answer("Город / формат работы (например: Ташкент, офис)?")


@router.message(AddJob.city)
async def add_job_city(m: Message, state: FSMContext):
    await state.update_data(city=(m.text or "").strip()[:80])
    await state.set_state(AddJob.salary)
    await m.answer("Зарплата (например: 5 000 000 сум или 'по договорённости')?")


@router.message(AddJob.salary)
async def add_job_salary(m: Message, state: FSMContext):
    await state.update_data(salary=(m.text or "").strip()[:80])
    await state.set_state(AddJob.description)
    await m.answer("Описание: обязанности и требования:")


@router.message(AddJob.description)
async def add_job_desc(m: Message, state: FSMContext):
    d = await state.get_data()
    cur = db.execute("INSERT INTO vacancies(tenant,title,city,salary,description) VALUES(?,?,?,?,?)",
                     (tenant_of(m.from_user.id), d["title"], d["city"], d["salary"], (m.text or "").strip()[:1500]))
    db.commit()
    await state.clear()
    await m.answer(f"✅ Вакансия №{cur.lastrowid} «{d['title']}» опубликована", reply_markup=main_kb(m.from_user.id))


@router.message(F.text == ADM_JOBS)
@router.message(Command("vacancies"))
async def manage_jobs(m: Message):
    if not is_admin(m.from_user.id):
        return
    rows = db.execute("SELECT id, title, active, (SELECT COUNT(*) FROM applications WHERE vacancy_id=vacancies.id) "
                      "FROM vacancies WHERE tenant=? ORDER BY id", (tenant_of(m.from_user.id),)).fetchall()
    if not rows:
        return await m.answer("Вакансий нет. Добавьте кнопкой «➕ Вакансия».")
    for jid, title, active, n in rows:
        b = InlineKeyboardBuilder()
        b.button(text="⏸ Закрыть" if active else "▶️ Открыть", callback_data=f"vac:{jid}:{'close' if active else 'open'}")
        b.button(text="🗑 Удалить", callback_data=f"vac:{jid}:del")
        await m.answer(f"№{jid} · {'🟢' if active else '⚪️'} {title} · откликов: {n}", reply_markup=b.adjust(2).as_markup())


@router.callback_query(F.data.regexp(r"^vac:\d+:(open|close|del)$"))
async def vacancy_action(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    _, jid, act = c.data.split(":")
    tenant = tenant_of(c.from_user.id)
    if not db.execute("SELECT 1 FROM vacancies WHERE id=? AND tenant=?", (jid, tenant)).fetchone():
        return await c.answer("Не найдено")
    if act == "del":
        db.execute("DELETE FROM vacancies WHERE id=?", (jid,))
        db.execute("DELETE FROM applications WHERE vacancy_id=?", (jid,))
        text = f"🗑 Вакансия №{jid} удалена вместе с откликами"
    else:
        db.execute("UPDATE vacancies SET active=? WHERE id=?", (1 if act == "open" else 0, jid))
        text = f"Вакансия №{jid} {'открыта' if act == 'open' else 'закрыта'}"
    db.commit()
    await c.message.edit_text(text)
    await c.answer()


@router.message(F.text == ADM_REPORT)
@router.message(Command("report"))
async def report(m: Message):
    if not is_admin(m.from_user.id):
        return
    tenant = tenant_of(m.from_user.id)
    by_status = dict(db.execute("SELECT status, COUNT(*) FROM applications WHERE tenant=? GROUP BY status", (tenant,)).fetchall())
    rows = db.execute("SELECT v.title, COUNT(a.id) FROM vacancies v LEFT JOIN applications a ON a.vacancy_id=v.id "
                      "WHERE v.tenant=? GROUP BY v.id ORDER BY 2 DESC", (tenant,)).fetchall()
    total = sum(by_status.values())
    lines = [f"📈 Отчёт по откликам\nВсего: {total}"] + [f"{STATUS_RU[s]}: {by_status.get(s, 0)}" for s in STATUS_RU]
    lines += ["", "По вакансиям:"] + [f"• {title}: {n}" for title, n in rows]
    await m.answer("\n".join(lines))


def export_applications(tenant):
    rows = db.execute("SELECT a.id, a.created, v.title, a.name, a.phone, a.status, replace(a.about, char(10), ' ') FROM applications a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.tenant=? ORDER BY a.id", (tenant,)).fetchall()
    return ["№", "Дата", "Вакансия", "Кандидат", "Телефон", "Статус", "О себе"], rows


def stats_lines(tenant):
    by = dict(db.execute("SELECT status, COUNT(*) FROM applications WHERE tenant=? GROUP BY status", (tenant,)).fetchall())
    return [f"👔 Рекрутинг: вакансий {db.execute('SELECT COUNT(*) FROM vacancies WHERE tenant=? AND active=1', (tenant,)).fetchone()[0]}, откликов {sum(by.values())}, "
            f"приглашено {by.get('invited', 0)}, принято {by.get('hired', 0)}"]


core.register_export("recruiter", "applications.csv", export_applications)
core.register_stats("recruiter", stats_lines)


def daily_lines(tenant, today):
    n = db.execute("SELECT COUNT(*) FROM applications WHERE tenant=? AND status='new'", (tenant,)).fetchone()[0]
    return [f"👔 Откликов ждут решения: {n}"] if n else []


core.register_daily("recruiter", daily_lines)
core.register_stale("applications", ("new",), 60, "Отклик")


# ---------------- собеседование и сообщения кандидату ----------------
def own_app(aid, admin_id):
    tenant = tenant_of(admin_id)
    if not (tenant and is_admin(admin_id)):
        return None
    return db.execute("SELECT a.id, a.user_id, v.title FROM applications a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.id=? AND a.tenant=?", (aid, tenant)).fetchone()


async def interview_saved(bot, uid, ans, ctx):
    row = db.execute("SELECT a.user_id, v.title FROM applications a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.id=? AND a.tenant=?", (ctx["aid"], ctx["tenant"])).fetchone()
    if not row:
        return
    local = dt.datetime.strptime(f"{ans['day']} {ans['time']}", "%Y-%m-%d %H:%M")
    utc = local - dt.timedelta(hours=core.tz_hours(ctx["tenant"]))
    place = ans.get("place") or ""
    db.execute("UPDATE applications SET interview_at=?, interview_info=?, ireminded=0, status='invited' WHERE id=?", (utc.strftime("%Y-%m-%d %H:%M"), place, ctx["aid"]))
    db.commit()
    try:
        await bot.send_message(row[0], t(row[0], "rc_interview", job=row[1], when=f"{ans['day']} {ans['time']}", place=place))
        await bot.send_message(uid, f"✅ Кандидат приглашён на {ans['day']} {ans['time']}. За 2 часа я напомню ему и вам.", reply_markup=main_kb(uid))
    except Exception:
        await bot.send_message(uid, "Не удалось отправить приглашение: кандидат заблокировал бота", reply_markup=main_kb(uid))


core.register_wizard("interview", [{"key": "day", "prompt": "Дата собеседования? Например 25.12", "type": "date"},
                                   {"key": "time", "prompt": "Время? Например 15:00", "type": "time"},
                                   {"key": "place", "prompt": "Где проходит (адрес или ссылка на звонок)?", "type": "text", "optional": True}], interview_saved)


@router.callback_query(F.data.startswith("rci:"))
async def interview_start(c: CallbackQuery, state: FSMContext):
    aid = int(c.data.split(":")[1])
    if not own_app(aid, c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    await core.start_wizard(c.message, state, "interview", c.from_user.id, {"tenant": tenant_of(c.from_user.id), "aid": aid})
    await c.answer()


async def message_sent(bot, uid, ans, ctx):
    row = db.execute("SELECT user_id FROM applications WHERE id=? AND tenant=?", (ctx["aid"], ctx["tenant"])).fetchone()
    if not row:
        return
    try:
        await bot.send_message(row[0], f"{t(row[0], 'rc_message', n=ctx['aid'])}\n{ans['text']}")
        await bot.send_message(uid, "✅ Отправлено кандидату", reply_markup=main_kb(uid))
    except Exception:
        await bot.send_message(uid, "Не удалось отправить: кандидат заблокировал бота", reply_markup=main_kb(uid))


core.register_wizard("candidate_msg", [{"key": "text", "prompt": "Напишите сообщение кандидату:", "type": "text"}], message_sent)


@router.callback_query(F.data.startswith("rcm:"))
async def message_start(c: CallbackQuery, state: FSMContext):
    aid = int(c.data.split(":")[1])
    if not own_app(aid, c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    await core.start_wizard(c.message, state, "candidate_msg", c.from_user.id, {"tenant": tenant_of(c.from_user.id), "aid": aid})
    await c.answer()


async def send_interview_reminders(bot, now_utc=None):
    now = now_utc or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    sent = 0
    for aid, tenant, uid, at, place, title in db.execute("SELECT a.id, a.tenant, a.user_id, a.interview_at, COALESCE(a.interview_info,''), v.title FROM applications a "
                                                         "JOIN vacancies v ON v.id=a.vacancy_id WHERE a.interview_at IS NOT NULL AND a.ireminded=0").fetchall():
        when = dt.datetime.strptime(at, "%Y-%m-%d %H:%M")
        if dt.timedelta(0) <= when - now <= dt.timedelta(hours=2):
            db.execute("UPDATE applications SET ireminded=1 WHERE id=?", (aid,))
            db.commit()
            local = (when + dt.timedelta(hours=core.tz_hours(tenant))).strftime("%H:%M")
            try:
                await bot.send_message(uid, t(uid, "rc_reminder", job=title, time=local, place=place))
            except Exception:
                logging.warning("interview reminder not delivered")
            await notify_admins(bot, tenant, f"⏰ Через 2 часа собеседование: отклик №{aid}, «{title}» в {local}")
            sent += 1
    return sent


core.register_periodic(send_interview_reminders)


def weekly_lines(tenant, today):
    n = db.execute("SELECT COUNT(*) FROM applications WHERE tenant=? AND date(created)>=date('now','-6 days')", (tenant,)).fetchone()[0]
    return [f"👔 Новых откликов за неделю: {n}"]


core.register_weekly("recruiter", weekly_lines)
