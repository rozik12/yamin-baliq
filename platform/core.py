"""Ядро универсальной платформы: один Telegram-бот, много заведений («пространств»).
Клиент попадает в заведение по ссылке t.me/БОТ?start=slug. У каждого заведения свой модуль, свои админы и свои данные."""
import asyncio
import csv
import datetime as dt
import importlib
import io
import logging
import os
import secrets
import sqlite3

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

LANGS = {"uz": "🇺🇿 O'zbekcha", "ru": "🇷🇺 Русский", "en": "🇬🇧 English"}
OWNERS = {int(x) for x in os.environ.get("ADMIN_CHAT_ID", "").replace(";", ",").split(",")
          if x.strip().lstrip("-").isdigit() and int(x) != 0}
SPACES_BTN = "🏢 Пространства"
SETTINGS_BTN = "⚙️ Настройки"
NEW_SPACE_BTN = "➕ Новое заведение"
STATS_BTN = "📊 Статистика"
EXPORT_BTN = "📥 Экспорт"

db = sqlite3.connect(os.environ.get("DB_PATH") or os.path.join(os.path.dirname(__file__), "data.db"))
for ddl in ("users(id INTEGER PRIMARY KEY, lang TEXT)",
            "tenants(slug TEXT PRIMARY KEY, name TEXT, module TEXT, active INTEGER DEFAULT 1)",
            "tenant_admins(tenant TEXT, user_id INTEGER, PRIMARY KEY(tenant, user_id))",
            "user_tenant(user_id INTEGER PRIMARY KEY, tenant TEXT)",
            "invites(code TEXT PRIMARY KEY, tenant TEXT, used INTEGER DEFAULT 0)",
            "banned(tenant TEXT, id INTEGER, PRIMARY KEY(tenant, id))",
            "settings(tenant TEXT, key TEXT, value TEXT, PRIMARY KEY(tenant, key))",
            "ratings(tenant TEXT, module TEXT, ref INTEGER, user_id INTEGER, rating INTEGER, PRIMARY KEY(tenant, module, ref, user_id))",
            "scheduled(id INTEGER PRIMARY KEY, tenant TEXT, run_at TEXT, text TEXT, sent INTEGER DEFAULT 0)"):
    db.execute(f"CREATE TABLE IF NOT EXISTS {ddl}")
db.commit()

router = Router()
TXT: dict[str, dict[str, str]] = {
    "welcome": {"ru": "Добро пожаловать в «{brand}»! Выберите действие:", "uz": "«{brand}»ga xush kelibsiz! Amalni tanlang:",
                "en": "Welcome to {brand}! Choose an action:"},
    "b_lang": {"ru": "🌐 Язык", "uz": "🌐 Til", "en": "🌐 Language"},
    "cancelled": {"ru": "Отменено", "uz": "Bekor qilindi", "en": "Cancelled"},
    "no_space": {"ru": "Откройте бот по ссылке вашего заведения.", "uz": "Botni muassasangiz havolasi orqali oching.",
                 "en": "Please open the bot using your venue's link."},
}
MODULE_BUTTONS: dict[str, dict] = {}
CLEANUPS = []
BACKGROUND = []
SETTING_DEFS: dict[str, tuple] = {}  # key -> (label, type, module)
WIZARDS: dict[str, dict] = {}
EXPORTS = []
STATS = []


def register_texts(d):
    TXT.update(d)


def register_export(module, filename, fn):
    """fn(tenant) -> (заголовки, строки). Команда /export отправит CSV для Excel."""
    EXPORTS.append((module, filename, fn))


def register_stats(module, fn):
    """fn(tenant) -> список строк статистики."""
    STATS.append((module, fn))


def register_background(fn):
    BACKGROUND.append(fn)


def register_settings(module, defs):
    for key, label, typ in defs:
        prev = SETTING_DEFS.get(key)
        SETTING_DEFS[key] = (label, typ, (prev[2] if prev else set()) | {module})


def register_wizard(name, steps, done):
    WIZARDS[name] = {"steps": steps, "done": done}


def register_module(name, client_keys, admin_labels, cleanup=None):
    MODULE_BUTTONS[name] = {"client": list(client_keys), "admin": list(admin_labels)}
    if cleanup:
        CLEANUPS.append(cleanup)


# ---------- заведения и права ----------
def get_tenant(slug):
    row = db.execute("SELECT slug, name, module, active FROM tenants WHERE slug=?", (slug,)).fetchone()
    return dict(zip(("slug", "name", "module", "active"), row)) if row else None


def tenant_of(uid):
    row = db.execute("SELECT tenant FROM user_tenant WHERE user_id=?", (uid,)).fetchone()
    tenant = get_tenant(row[0]) if row else None
    return tenant["slug"] if tenant and tenant["active"] else None


def is_owner(uid):
    return uid in OWNERS


def tenant_admins(slug):
    return {r[0] for r in db.execute("SELECT user_id FROM tenant_admins WHERE tenant=?", (slug,))}


def admins_of(slug):
    """Кому слать уведомления заведения: его админам, а если их нет, владельцам платформы."""
    return tenant_admins(slug) or set(OWNERS)


def is_admin(uid, tenant=None):
    if is_owner(uid):
        return True
    tenant = tenant or tenant_of(uid)
    return bool(tenant) and uid in tenant_admins(tenant)


def modules_of(uid):
    """Модули заведения пользователя (в настройках заведения: module = 'shop,support')."""
    tenant = get_tenant(tenant_of(uid)) if tenant_of(uid) else None
    return [x for x in (tenant["module"].split(",") if tenant else []) if x in MODULE_BUTTONS]


def get_setting(tenant, key, default=None):
    row = db.execute("SELECT value FROM settings WHERE tenant=? AND key=?", (tenant, key)).fetchone()
    return row[0] if row else default


def set_setting(tenant, key, value):
    db.execute("INSERT INTO settings(tenant,key,value) VALUES(?,?,?) ON CONFLICT(tenant,key) DO UPDATE SET value=excluded.value",
               (tenant, key, str(value)))
    db.commit()


def money(tenant, n):
    return f"{int(n):,}".replace(",", " ") + " " + get_setting(tenant, "currency", "сум")


def lang_of(uid):
    row = db.execute("SELECT lang FROM users WHERE id=?", (uid,)).fetchone()
    return row[0] if row else ("ru" if is_admin(uid) else "uz")


def t(uid, key, **kw):
    s = TXT[key][lang_of(uid)]
    return s.format(**kw) if kw else s


def is_btn(key):
    return F.text.in_(set(TXT[key].values()))


def brand_of(uid):
    tenant = get_tenant(tenant_of(uid)) if tenant_of(uid) else None
    return tenant["name"] if tenant else "Bot"


async def notify_admins(bot, tenant, text, reply_markup=None):
    for a in admins_of(tenant):
        try:
            await bot.send_message(a, text, reply_markup=reply_markup)
        except Exception:
            logging.warning("admin %s unreachable", a)


def main_kb(uid):
    b = ReplyKeyboardBuilder()
    mods = modules_of(uid)
    if is_admin(uid):
        labels = []
        for mod in mods:
            labels += [x for x in MODULE_BUTTONS[mod]["admin"] if x not in labels]
        if any(d[2] & set(mods) for d in SETTING_DEFS.values()):
            labels.append(SETTINGS_BTN)
        if mods:
            labels += [STATS_BTN, EXPORT_BTN]
        for label in labels:
            b.button(text=label)
        if is_owner(uid):
            b.button(text=NEW_SPACE_BTN)
            b.button(text=SPACES_BTN)
    else:
        keys = []
        for mod in mods:
            keys += [x for x in MODULE_BUTTONS[mod]["client"] if x not in keys]
        for key in keys + ["b_lang"]:
            b.button(text=t(uid, key))
    return b.adjust(2).as_markup(resize_keyboard=True)


def is_banned(uid):
    tenant = tenant_of(uid)
    return bool(tenant) and not is_admin(uid) and bool(
        db.execute("SELECT 1 FROM banned WHERE tenant=? AND id=?", (tenant, uid)).fetchone())


async def ban_guard(handler, event, data):
    user = getattr(event, "from_user", None)
    if user and is_banned(user.id):
        return None
    return await handler(event, data)


router.message.outer_middleware(ban_guard)
router.callback_query.outer_middleware(ban_guard)


def lang_kb():
    b = InlineKeyboardBuilder()
    for code, name in LANGS.items():
        b.button(text=name, callback_data=f"lang:{code}")
    return b.adjust(1).as_markup()


_username = {}


async def bot_username(bot):
    if "u" not in _username:
        _username["u"] = (await bot.get_me()).username
    return _username["u"]


def set_user_tenant(uid, slug):
    db.execute("INSERT INTO user_tenant(user_id, tenant) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET tenant=excluded.tenant", (uid, slug))
    db.commit()


# ---------- общие команды ----------
@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    uid = m.from_user.id
    arg = (command.args or "").strip()
    if arg.startswith("adm_"):
        row = db.execute("SELECT tenant FROM invites WHERE code=? AND used=0", (arg,)).fetchone()
        if not row:
            return await m.answer("Ссылка-приглашение недействительна или уже использована.")
        db.execute("INSERT OR IGNORE INTO tenant_admins(tenant, user_id) VALUES(?,?)", (row[0], uid))
        db.execute("UPDATE invites SET used=1 WHERE code=?", (arg,))
        db.commit()
        set_user_tenant(uid, row[0])
        return await m.answer(f"✅ Вы администратор заведения «{get_tenant(row[0])['name']}».", reply_markup=main_kb(uid))
    if arg:
        tenant = get_tenant(arg)
        if not (tenant and tenant["active"]):
            return await m.answer("Заведение не найдено. Проверьте ссылку.")
        set_user_tenant(uid, arg)
    if is_admin(uid):
        tenant = tenant_of(uid)
        text = (f"🛠 Панель администратора «{brand_of(uid)}»" if tenant
                else "🛠 Вы владелец платформы.\n\nНажмите «➕ Новое заведение», чтобы подключить бизнес: бот спросит название и тип "
                     "и выдаст ссылки для клиентов и администратора.\n«🏢 Пространства» показывает все заведения.")
        return await m.answer(text, reply_markup=main_kb(uid))
    if not tenant_of(uid):
        return await m.answer(t(uid, "no_space"))
    if db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone() is None:
        return await m.answer("Tilni tanlang / Выберите язык / Choose your language", reply_markup=lang_kb())
    await m.answer(t(uid, "welcome", brand=brand_of(uid)), reply_markup=main_kb(uid))


@router.message(is_btn("b_lang"))
async def change_lang(m: Message):
    await m.answer("Tilni tanlang / Выберите язык / Choose your language", reply_markup=lang_kb())


@router.callback_query(F.data.startswith("lang:"))
async def set_lang(c: CallbackQuery):
    code = c.data.split(":")[1]
    if code not in LANGS:
        return await c.answer()
    db.execute("INSERT INTO users(id,lang) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET lang=excluded.lang", (c.from_user.id, code))
    db.commit()
    await c.message.delete()
    await c.message.answer(t(c.from_user.id, "welcome", brand=brand_of(c.from_user.id)), reply_markup=main_kb(c.from_user.id))
    await c.answer()


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(t(m.from_user.id, "cancelled"), reply_markup=main_kb(m.from_user.id))


# ---------- команды владельца платформы ----------
async def space_links(bot, slug, code):
    u = await bot_username(bot)
    return f"Ссылка для клиентов:\nhttps://t.me/{u}?start={slug}\n\nСсылка для администратора заведения (одноразовая):\nhttps://t.me/{u}?start={code}"


def new_invite(slug):
    code = "adm_" + secrets.token_urlsafe(8).replace("-", "x").replace("_", "y")
    db.execute("INSERT INTO invites(code, tenant) VALUES(?,?)", (code, slug))
    db.commit()
    return code


@router.message(Command("newspace"))
async def new_space(m: Message, command: CommandObject):
    """/newspace slug модуль Название заведения"""
    if not is_owner(m.from_user.id):
        return
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) < 3:
        return await m.answer(f"Использование: /newspace slug модуль Название\nМодули: {', '.join(MODULE_BUTTONS) or '—'}\n"
                              "Например: /newspace vlad recruiter Влад бизнес")
    slug, module, name = parts[0].lower(), parts[1].lower().replace(" ", ""), parts[2].strip()
    if not slug.replace("_", "").replace("-", "").isalnum() or slug.startswith("adm"):
        return await m.answer("slug: латиница, цифры, - и _ (не начинается с adm)")
    if not module or any(x not in MODULE_BUTTONS for x in module.split(",")):
        return await m.answer(f"Нет такого модуля. Доступно: {', '.join(MODULE_BUTTONS)}. Можно несколько через запятую: shop,support")
    if get_tenant(slug):
        return await m.answer("Такое заведение уже есть")
    db.execute("INSERT INTO tenants(slug, name, module) VALUES(?,?,?)", (slug, name, module))
    db.commit()
    await m.answer(f"✅ Заведение «{name}» ({module}) создано.\n\n" + await space_links(m.bot, slug, new_invite(slug)))


PRESETS = [("🛍 Магазин / кафе / доставка", "shop"), ("💇 Запись на приём", "booking"), ("📝 Заявки", "leads"),
           ("👔 Рекрутинг", "recruiter"), ("🆘 Поддержка и FAQ", "support"), ("🛍 Магазин + 🆘 Поддержка", "shop,support"),
           ("💇 Запись + 🆘 Поддержка", "booking,support"), ("📝 Заявки + 🆘 Поддержка", "leads,support")]


async def space_created(bot, uid, ans, ctx):
    slug, module, name = ans["slug"], ans["module"], ans["name"]
    db.execute("INSERT INTO tenants(slug, name, module) VALUES(?,?,?)", (slug, name, module))
    db.commit()
    set_user_tenant(uid, slug)
    await bot.send_message(uid, f"✅ Заведение «{name}» создано ({module}). Вы вошли в него: кнопки внизу управляют им.\n\n"
                           + await space_links(bot, slug, new_invite(slug)), reply_markup=main_kb(uid))


register_wizard("newspace", [
    {"key": "name", "prompt": "Как называется заведение? (увидят клиенты)", "type": "text"},
    {"key": "slug", "prompt": "Короткое имя для ссылки: латиница, цифры, - или _ (например: vlad)", "type": "slug",
     "check": lambda v, ctx: get_tenant(v) is None},
    {"key": "module", "prompt": "Какой тип бизнеса?", "type": "choice",
     "choices": lambda uid, a, c: [(label, mod) for label, mod in PRESETS if all(x in MODULE_BUTTONS for x in mod.split(","))]},
], space_created)


@router.message(F.text == NEW_SPACE_BTN)
async def new_space_button(m: Message, state: FSMContext):
    if is_owner(m.from_user.id):
        await start_wizard(m, state, "newspace", m.from_user.id)


@router.message(Command("newinvite"))
async def newinvite(m: Message, command: CommandObject):
    slug = (command.args or "").strip().lower()
    if is_owner(m.from_user.id) and get_tenant(slug):
        await m.answer("Новая ссылка для администратора:\nhttps://t.me/" + await bot_username(m.bot) + "?start=" + new_invite(slug))


@router.message(Command("spaces"))
@router.message(F.text == SPACES_BTN)
async def spaces(m: Message):
    if not is_owner(m.from_user.id):
        return
    rows = db.execute("SELECT slug, name, module, active FROM tenants ORDER BY rowid").fetchall()
    if not rows:
        return await m.answer("Заведений пока нет. Создайте: /newspace slug модуль Название")
    u = await bot_username(m.bot)
    b = InlineKeyboardBuilder()
    for slug, name, mod, a in rows:
        b.button(text=f"▶️ Войти: {name}"[:50], callback_data=f"enter:{slug}")
    await m.answer("\n\n".join(
        f"{'🟢' if a else '⚪️'} {name} · {mod}\nhttps://t.me/{u}?start={slug}\nадминов: {len(tenant_admins(slug))}, "
        f"клиентов: {db.execute('SELECT COUNT(*) FROM user_tenant WHERE tenant=?', (slug,)).fetchone()[0]}"
        for slug, name, mod, a in rows) + "\n\nНажмите кнопку, чтобы войти и настроить заведение.", reply_markup=b.adjust(1).as_markup())


@router.callback_query(F.data.startswith("enter:"))
async def enter_space_btn(c: CallbackQuery):
    slug = c.data.split(":", 1)[1]
    if not (is_owner(c.from_user.id) and get_tenant(slug)):
        return await c.answer("Нет доступа", show_alert=True)
    set_user_tenant(c.from_user.id, slug)
    await c.message.answer(f"Вы в заведении «{brand_of(c.from_user.id)}». Кнопки внизу управляют им.", reply_markup=main_kb(c.from_user.id))
    await c.answer()


@router.message(Command("space"))
async def enter_space(m: Message, command: CommandObject):
    slug = (command.args or "").strip().lower()
    if is_owner(m.from_user.id) and get_tenant(slug):
        set_user_tenant(m.from_user.id, slug)
        await m.answer(f"Вы в заведении «{brand_of(m.from_user.id)}»", reply_markup=main_kb(m.from_user.id))


@router.message(Command("delspace"))
async def del_space(m: Message, command: CommandObject):
    slug = (command.args or "").strip().lower()
    if not (is_owner(m.from_user.id) and get_tenant(slug)):
        return
    for fn in CLEANUPS:
        fn(slug)
    for table in ("tenant_admins", "invites", "banned", "user_tenant"):
        db.execute(f"DELETE FROM {table} WHERE tenant=?", (slug,))
    db.execute("DELETE FROM tenants WHERE slug=?", (slug,))
    db.commit()
    await m.answer(f"🗑 Заведение {slug} удалено со всеми данными")


@router.message(Command("setadmin"))
async def set_admin(m: Message, command: CommandObject):
    parts = (command.args or "").split()
    if is_owner(m.from_user.id) and len(parts) == 2 and parts[1].isdigit() and get_tenant(parts[0].lower()):
        db.execute("INSERT OR IGNORE INTO tenant_admins(tenant, user_id) VALUES(?,?)", (parts[0].lower(), int(parts[1])))
        db.commit()
        await m.answer("Администратор заведения добавлен. Ему нужно нажать /start у бота.")


@router.message(Command("unsetadmin"))
async def unset_admin(m: Message, command: CommandObject):
    parts = (command.args or "").split()
    if is_owner(m.from_user.id) and len(parts) == 2 and parts[1].isdigit():
        db.execute("DELETE FROM tenant_admins WHERE tenant=? AND user_id=?", (parts[0].lower(), int(parts[1])))
        db.commit()
        await m.answer("Администратор удалён")


# ---------- команды администратора заведения ----------
@router.message(Command("ban"))
async def ban_cmd(m: Message, command: CommandObject):
    tenant = tenant_of(m.from_user.id)
    if tenant and is_admin(m.from_user.id) and (command.args or "").strip().isdigit():
        db.execute("INSERT OR IGNORE INTO banned(tenant, id) VALUES(?,?)", (tenant, int(command.args.strip())))
        db.commit()
        await m.answer("🚫 Заблокирован в этом заведении")


@router.message(Command("unban"))
async def unban_cmd(m: Message, command: CommandObject):
    tenant = tenant_of(m.from_user.id)
    if tenant and is_admin(m.from_user.id) and (command.args or "").strip().isdigit():
        db.execute("DELETE FROM banned WHERE tenant=? AND id=?", (tenant, int(command.args.strip())))
        db.commit()
        await m.answer("✅ Разблокирован")


async def send_to_clients(bot, tenant, text, pause=0.05):
    sent = 0
    for (uid,) in db.execute("SELECT user_id FROM user_tenant WHERE tenant=? AND user_id NOT IN (SELECT user_id FROM tenant_admins WHERE tenant=?)",
                             (tenant, tenant)).fetchall():
        if uid in OWNERS:
            continue
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            pass
        if pause:
            await asyncio.sleep(pause)
    return sent


@router.message(Command("broadcast"))
async def broadcast(m: Message, command: CommandObject):
    tenant = tenant_of(m.from_user.id)
    if not (tenant and is_admin(m.from_user.id)):
        return
    if not command.args:
        return await m.answer("Использование: /broadcast текст")
    await m.answer(f"Отправлено: {await send_to_clients(m.bot, tenant, command.args)}")


def tz_hours(tenant):
    return int(get_setting(tenant, "tz_offset", 5) or 5)


@router.message(Command("schedule"))
async def schedule_cmd(m: Message, command: CommandObject):
    """/schedule 2026-12-01 18:00 текст рассылки (время по часовому поясу заведения)"""
    tenant = tenant_of(m.from_user.id)
    if not (tenant and is_admin(m.from_user.id)):
        return
    parts = (command.args or "").split(maxsplit=2)
    try:
        local = dt.datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M")
        text = parts[2].strip()
    except (IndexError, ValueError):
        return await m.answer("Использование: /schedule ГГГГ-ММ-ДД ЧЧ:ММ текст\nНапример: /schedule 2026-12-01 18:00 Скидки до воскресенья!")
    run_at = local - dt.timedelta(hours=tz_hours(tenant))
    if run_at <= dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) or not text:
        return await m.answer("Время должно быть в будущем, а текст не пустым")
    cur = db.execute("INSERT INTO scheduled(tenant, run_at, text) VALUES(?,?,?)", (tenant, run_at.strftime("%Y-%m-%d %H:%M"), text))
    db.commit()
    await m.answer(f"⏰ Рассылка №{cur.lastrowid} запланирована на {parts[0]} {parts[1]}. Отменить: /unschedule {cur.lastrowid}")


@router.message(Command("scheduled"))
async def scheduled_list(m: Message):
    tenant = tenant_of(m.from_user.id)
    if not (tenant and is_admin(m.from_user.id)):
        return
    rows = db.execute("SELECT id, run_at, text FROM scheduled WHERE tenant=? AND sent=0 ORDER BY run_at", (tenant,)).fetchall()
    h = tz_hours(tenant)
    await m.answer("\n".join(f"№{i} · {(dt.datetime.strptime(r, '%Y-%m-%d %H:%M') + dt.timedelta(hours=h)).strftime('%Y-%m-%d %H:%M')} · {x[:40]}"
                              for i, r, x in rows) or "Запланированных рассылок нет")


@router.message(Command("unschedule"))
async def unschedule(m: Message, command: CommandObject):
    tenant = tenant_of(m.from_user.id)
    if tenant and is_admin(m.from_user.id) and (command.args or "").strip().isdigit():
        db.execute("DELETE FROM scheduled WHERE id=? AND tenant=? AND sent=0", (int(command.args.strip()), tenant))
        db.commit()
        await m.answer("Рассылка отменена")


async def send_due_broadcasts(bot, now_utc=None):
    now = (now_utc or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)).strftime("%Y-%m-%d %H:%M")
    sent = 0
    for sid, tenant, text in db.execute("SELECT id, tenant, text FROM scheduled WHERE sent=0 AND run_at<=?", (now,)).fetchall():
        db.execute("UPDATE scheduled SET sent=1 WHERE id=?", (sid,))
        db.commit()
        await send_to_clients(bot, tenant, text, pause=0)
        await notify_admins(bot, tenant, f"✅ Запланированная рассылка №{sid} отправлена")
        sent += 1
    return sent


async def schedule_loop(bot):
    while True:
        await asyncio.sleep(30)
        try:
            await send_due_broadcasts(bot)
        except Exception:
            logging.exception("schedule loop failed")


register_background(schedule_loop)


# ---------- выгрузка и статистика ----------
@router.message(Command("export"))
@router.message(F.text == EXPORT_BTN)
async def export_cmd(m: Message):
    tenant = tenant_of(m.from_user.id)
    if not (tenant and is_admin(m.from_user.id)):
        return
    mods = modules_of(m.from_user.id)
    sent = 0
    for mod, filename, fn in EXPORTS:
        if mod not in mods:
            continue
        header, rows = fn(tenant)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(header)
        w.writerows(rows)
        await m.answer_document(BufferedInputFile(out.getvalue().encode("utf-8-sig"), filename=filename), caption=f"{filename}: {len(rows)} строк")
        sent += 1
    if not sent:
        await m.answer("Нет данных для выгрузки")


def tenant_stats(tenant):
    mods = (get_tenant(tenant) or {"module": ""})["module"].split(",")
    clients = db.execute("SELECT COUNT(*) FROM user_tenant WHERE tenant=? AND user_id NOT IN (SELECT user_id FROM tenant_admins WHERE tenant=?)",
                         (tenant, tenant)).fetchone()[0]
    lines = [f"👥 Клиентов: {clients}"]
    for mod, fn in STATS:
        if mod in mods:
            lines += fn(tenant)
    return lines


@router.message(Command("stats"))
@router.message(F.text == STATS_BTN)
async def stats_cmd(m: Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    tenant = tenant_of(uid)
    if tenant:
        return await m.answer(f"📊 «{brand_of(uid)}»\n" + "\n".join(tenant_stats(tenant)))
    rows = db.execute("SELECT slug, name, module, active FROM tenants ORDER BY rowid").fetchall()
    await m.answer(f"📊 Платформа: заведений {len(rows)}\n\n" + "\n\n".join(
        f"{'🟢' if a else '⚪️'} {name} ({mod})\n" + "\n".join(tenant_stats(slug)) for slug, name, mod, a in rows) if rows else "Заведений пока нет")


# ---------- оценки ----------
TXT.update({
    "rate_ask": {"ru": "Оцените, пожалуйста:", "uz": "Iltimos, baholang:", "en": "Please rate:"},
    "rate_thx": {"ru": "Спасибо за оценку! ⭐", "uz": "Bahoyingiz uchun rahmat! ⭐", "en": "Thanks for your rating! ⭐"},
})


async def ask_rating(bot, uid, module, ref):
    b = InlineKeyboardBuilder()
    for n in range(1, 6):
        b.button(text="⭐" * n, callback_data=f"rt:{module}:{ref}:{n}")
    try:
        await bot.send_message(uid, t(uid, "rate_ask"), reply_markup=b.adjust(3).as_markup())
    except Exception:
        logging.warning("rating request not delivered")


@router.callback_query(F.data.regexp(r"^rt:[a-z]+:\d+:[1-5]$"))
async def rate(c: CallbackQuery):
    uid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    _, module, ref, n = c.data.split(":")
    if not tenant:
        return await c.answer()
    db.execute("INSERT OR REPLACE INTO ratings(tenant,module,ref,user_id,rating) VALUES(?,?,?,?,?)", (tenant, module, int(ref), uid, int(n)))
    db.commit()
    await c.message.edit_text(t(uid, "rate_thx"))
    if int(n) <= 2:
        await notify_admins(c.bot, tenant, f"⚠️ Низкая оценка {n}/5 ({module} №{ref}), клиент tg://user?id={uid}")
    await c.answer()


def avg_rating(tenant, module):
    row = db.execute("SELECT ROUND(AVG(rating),1), COUNT(*) FROM ratings WHERE tenant=? AND module=?", (tenant, module)).fetchone()
    return row[0], row[1]


TXT["use_menu"] = {"ru": "Выберите действие кнопками внизу 👇", "uz": "Pastdagi tugmalar bilan amalni tanlang 👇",
                   "en": "Please use the buttons below 👇"}
fallback = Router()


@fallback.message()
async def unknown_message(m: Message, state: FSMContext):
    uid = m.from_user.id
    logging.info("unhandled message from %s: %r", uid, (m.text or "")[:60])
    if is_admin(uid) and not tenant_of(uid):
        return await m.answer("Нажмите «➕ Новое заведение» или «🏢 Пространства». Нужна подсказка: /start", reply_markup=main_kb(uid))
    if not tenant_of(uid):
        return await m.answer(t(uid, "no_space"))
    await m.answer(t(uid, "use_menu"), reply_markup=main_kb(uid))


@fallback.callback_query()
async def stale_button(c: CallbackQuery):
    await c.answer("Кнопка устарела. Нажмите /start", show_alert=True)


def load_modules(names):
    routers = []
    for name in names:
        mod = importlib.import_module(f"modules.{name.strip()}")
        routers.append(mod.router)
    return routers


async def main():
    logging.basicConfig(level=logging.INFO)
    names = [n for n in os.environ.get("MODULES", "recruiter").split(",") if n.strip()]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    session = None
    if proxy:
        from aiogram.client.session.aiohttp import AiohttpSession
        session = AiohttpSession(proxy=proxy)
    bot = Bot(os.environ["BOT_TOKEN"], session=session)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    for r in load_modules(names):
        dp.include_router(r)
    dp.include_router(fallback)
    for fn in BACKGROUND:
        asyncio.create_task(fn(bot))
    for a in OWNERS:
        try:
            await bot.send_message(a, "🔄 Платформа запущена. /spaces — заведения, /newspace — создать новое.", reply_markup=main_kb(a))
        except Exception:
            pass
    await dp.start_polling(bot)


# ====================== пошаговые формы (wizard) ======================
class Wizard(StatesGroup):
    active = State()


TXT.update({
    "wz_invalid": {"ru": "Не получилось, попробуйте ещё раз (или /cancel)", "uz": "Bo'lmadi, qayta urinib ko'ring (yoki /cancel)",
                   "en": "That didn't work, please try again (or /cancel)"},
    "wz_skip_hint": {"ru": "(необязательно: отправьте «-», чтобы пропустить)", "uz": "(ixtiyoriy: o'tkazib yuborish uchun «-» yuboring)",
                     "en": "(optional: send “-” to skip)"},
    "send_phone": {"ru": "📱 Отправить номер", "uz": "📱 Raqamni yuborish", "en": "📱 Send number"},
    "send_loc": {"ru": "📍 Отправить геолокацию", "uz": "📍 Joylashuvni yuborish", "en": "📍 Send location"},
})


def map_link(lat, lon):
    return f"https://yandex.com/maps/?pt={lon},{lat}&z=17&l=map"


def resolve(value, uid, ans, ctx):
    """Подпись/подсказка шага: ключ в TXT, обычная строка или функция."""
    if callable(value):
        return value(uid, ans, ctx)
    if isinstance(value, str) and value in TXT:
        return t(uid, value)
    return value


async def start_wizard(target, state, name, uid, ctx=None):
    await state.clear()
    await state.set_state(Wizard.active)
    await state.update_data(wiz=name, ans={}, i=-1, ctx=ctx or {})
    await advance(target, state, uid)


async def advance(target, state, uid):
    d = await state.get_data()
    wiz = WIZARDS[d["wiz"]]
    steps, ans, ctx = wiz["steps"], d["ans"], d["ctx"]
    i = d["i"] + 1
    while i < len(steps) and steps[i].get("when") and not steps[i]["when"](ans, ctx):
        i += 1
    if i >= len(steps):
        await state.clear()
        return await wiz["done"](target.bot, uid, ans, ctx)
    step = steps[i]
    await state.update_data(i=i)
    text = str(resolve(step["prompt"], uid, ans, ctx))
    if step.get("optional"):
        text += "\\n" + t(uid, "wz_skip_hint")
    kind = step["type"]
    if kind == "choice":
        opts = resolve(step["choices"], uid, ans, ctx)
        b = InlineKeyboardBuilder()
        for idx, (label, _) in enumerate(opts):
            b.button(text=label, callback_data=f"wzc:{idx}")
        return await target.answer(text, reply_markup=b.adjust(1).as_markup())
    kb = ReplyKeyboardRemove()
    if kind == "phone":
        rb = ReplyKeyboardBuilder()
        rb.button(text=t(uid, "send_phone"), request_contact=True)
        kb = rb.as_markup(resize_keyboard=True, one_time_keyboard=True)
    elif kind == "address":
        rb = ReplyKeyboardBuilder()
        rb.button(text=t(uid, "send_loc"), request_location=True)
        kb = rb.as_markup(resize_keyboard=True, one_time_keyboard=True)
    await target.answer(text, reply_markup=kb)


def parse_input(kind, m, optional):
    """Возвращает (ok, value)."""
    text = (m.text or "").strip()
    if optional and text == "-":
        return True, None
    if kind == "text":
        return (bool(text) and not text.startswith("/")), text[:1500]
    if kind == "slug":
        slug = text.lower()
        return (2 <= len(slug) <= 30 and slug.replace("-", "").replace("_", "").isalnum() and slug.isascii()
                and not slug.startswith("adm")), slug
    if kind == "int":
        digits = text.replace(" ", "").replace(",", "")
        return digits.isdigit() and 0 <= int(digits) < 10**10, (int(digits) if digits.isdigit() else None)
    if kind == "time":
        try:
            hh, mm = text.split(":")
            return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59, f"{int(hh):02d}:{int(mm):02d}"
        except ValueError:
            return False, None
    if kind == "phone":
        if m.contact:
            return True, m.contact.phone_number
        return sum(ch.isdigit() for ch in text) >= 7, text[:30]
    if kind == "photo":
        return bool(m.photo), (m.photo[-1].file_id if m.photo else None)
    if kind == "proof":
        if m.photo:
            return True, f"photo:{m.photo[-1].file_id}"
        if m.document:
            return True, f"doc:{m.document.file_id}"
        return False, None
    if kind == "address":
        if m.location:
            return True, {"text": map_link(m.location.latitude, m.location.longitude), "lat": m.location.latitude,
                          "lon": m.location.longitude, "msg": [m.chat.id, m.message_id]}
        return (bool(text) and not text.startswith("/")), {"text": text[:300], "lat": None, "lon": None, "msg": None}
    return False, None


@router.message(Wizard.active)
async def wizard_input(m: Message, state: FSMContext):
    d = await state.get_data()
    wiz = WIZARDS.get(d.get("wiz"))
    if not wiz:
        return await state.clear()
    step = wiz["steps"][d["i"]]
    uid = m.from_user.id
    if step["type"] == "choice":
        return await m.answer(t(uid, "wz_invalid"))
    ok, value = parse_input(step["type"], m, step.get("optional"))
    if ok and step.get("check") and value is not None and not step["check"](value, d["ctx"]):
        ok = False
    if not ok:
        return await m.answer(t(uid, "wz_invalid"))
    ans = dict(d["ans"])
    ans[step["key"]] = value
    await state.update_data(ans=ans)
    await advance(m, state, uid)


@router.callback_query(Wizard.active, F.data.startswith("wzc:"))
async def wizard_choice(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    wiz = WIZARDS.get(d.get("wiz"))
    if not wiz:
        return await c.answer()
    uid = c.from_user.id
    step = wiz["steps"][d["i"]]
    opts = resolve(step["choices"], uid, d["ans"], d["ctx"])
    idx = int(c.data.split(":")[1])
    if step["type"] != "choice" or idx >= len(opts):
        return await c.answer()
    ans = dict(d["ans"])
    ans[step["key"]] = opts[idx][1]
    await state.update_data(ans=ans)
    try:
        await c.message.edit_text(f"{c.message.text}\\n→ {opts[idx][0]}")
    except Exception:
        pass
    await c.answer()
    await advance(c.message, state, uid)


# ====================== настройки заведения ======================
async def _save_setting(bot, uid, ans, ctx):
    set_setting(ctx["tenant"], ctx["key"], ans["value"])
    await bot.send_message(uid, "✅ Сохранено", reply_markup=main_kb(uid))


for _typ in ("text", "int", "time"):
    WIZARDS[f"setting_{_typ}"] = {"steps": [{"key": "value", "prompt": lambda uid, ans, ctx: f"Введите значение: {ctx['label']}", "type": _typ}],
                                  "done": _save_setting}


@router.message(F.text == SETTINGS_BTN)
async def settings_menu(m: Message):
    uid = m.from_user.id
    tenant = tenant_of(uid)
    if not (tenant and is_admin(uid)):
        return
    mods = modules_of(uid)
    b = InlineKeyboardBuilder()
    for key, (label, typ, mod) in SETTING_DEFS.items():
        if mod & set(mods):
            value = get_setting(tenant, key)
            b.button(text=f"{label}: {value if value else '—'}"[:60], callback_data=f"set:{key}")
    await m.answer("⚙️ Настройки заведения. Нажмите, чтобы изменить:", reply_markup=b.adjust(1).as_markup())


@router.callback_query(F.data.startswith("set:"))
async def settings_pick(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    tenant = tenant_of(uid)
    key = c.data.split(":", 1)[1]
    if not (tenant and is_admin(uid)) or key not in SETTING_DEFS:
        return await c.answer("Нет доступа", show_alert=True)
    label, typ, _ = SETTING_DEFS[key]
    await start_wizard(c.message, state, f"setting_{typ}", uid, {"tenant": tenant, "key": key, "label": label})
    await c.answer()


# ====================== фоновые задачи и запуск ======================
