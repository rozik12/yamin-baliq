"""«📞 Номера сотрудников»: номер по id или @нику через ВАШ Telegram-аккаунт (он подключается один раз).
Telegram показывает номер, только если человек есть в контактах вашего аккаунта или сам открыл номер в настройках приватности;
иначе берётся номер, введённый вручную. Бот-аккаунт чужих номеров не видит.
Переменные: AGZS_TG_API_ID, AGZS_TG_API_HASH, AGZS_TG_SESSION (строка сессии)."""
import asyncio
import logging
import os
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import core
from core import POSITIONS, can, uname
from staff import MENU_BUTTONS, parse_target

log = logging.getLogger("agzs.lookup")
router = Router()
_client = None
_lock = asyncio.Lock()


class Phone(StatesGroup):
    find = State()
    manual = State()


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=x, callback_data=c) for x, c in r] for r in rows])


def configured():
    return bool(os.getenv("AGZS_TG_API_ID") and os.getenv("AGZS_TG_API_HASH") and os.getenv("AGZS_TG_SESSION"))


def norm_phone(text):
    """«+998 90 123-45-67» → «+998901234567»; None, если это не похоже на номер."""
    digits = re.sub(r"\D", "", text or "")
    if not 9 <= len(digits) <= 15:
        return None
    if len(digits) == 9:  # узбекский номер без кода страны
        digits = "998" + digits
    return "+" + digits


async def get_client():
    global _client
    async with _lock:
        if _client is None:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            c = TelegramClient(StringSession(os.environ["AGZS_TG_SESSION"]), int(os.environ["AGZS_TG_API_ID"]), os.environ["AGZS_TG_API_HASH"])
            await c.connect()
            if not await c.is_user_authorized():
                await c.disconnect()
                raise RuntimeError("session not authorized")
            await c.get_dialogs(limit=500)  # загружаем контакты и чаты, чтобы находить людей по id
            _client = c
        return _client


async def tg_lookup(target):
    """Номер из Telegram-аккаунта владельца: target — id (int) или ник (str). Возвращает (id, имя, номер|None) или None, если человека не нашли."""
    c = await get_client()
    ent = await c.get_entity(target)
    phone = getattr(ent, "phone", None)
    name = " ".join(x for x in (getattr(ent, "first_name", None), getattr(ent, "last_name", None)) if x) or str(ent.id)
    return ent.id, name, ("+" + phone.lstrip("+") if phone else None)


async def find_phone(target):
    """(id|None, имя|None, номер|None, источник): сначала Telegram-аккаунт владельца, потом введённый вручную номер."""
    uid = target if isinstance(target, int) else None
    name = None
    if configured():
        try:
            tid, name, phone = await tg_lookup(target)
            uid = tid
            if phone:
                core.db.set_phone(tid, phone, "telegram")
                return tid, name, phone, "из вашего Telegram-аккаунта"
        except Exception as e:  # noqa: BLE001
            log.warning("lookup failed: %s", e)
    if uid is None and isinstance(target, str):
        s = core.db.seen_by_username(target)
        uid = s["id"] if s else None
        name = name or (s or {}).get("name")
    if uid is not None:
        p = core.db.phone(uid)
        if p:
            return uid, name or (core.db.seen_user(uid) or {}).get("name"), p["phone"], "введён вручную" if p["source"] == "manual" else "сохранён раньше"
    return uid, name, None, ""


def screen():
    return ikb([[("🔎 Найти номер по id / @нику", "ph:find")], [("📋 Список с номерами", "ph:list")], [("✍️ Записать номер вручную", "ph:man")]])


@router.callback_query(F.data.startswith("ph:"))
async def ph_cb(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "staff"):
        return await c.answer()
    await c.answer()
    act = c.data[3:]
    if act == "menu":
        note = "" if configured() else "\n\n⚠️ Ваш Telegram-аккаунт ещё не подключён к боту: пока работают только номера, записанные вручную."
        return await c.message.answer("📞 Номера сотрудников. Номер по id берётся из вашего Telegram-аккаунта: виден у тех, кто есть в ваших контактах или открыл номер всем." + note, reply_markup=screen())
    if act == "find":
        await state.set_state(Phone.find)
        return await c.message.answer("Напишите id, @ник или перешлите сообщение сотрудника.")
    if act == "man":
        await state.set_state(Phone.manual)
        return await c.message.answer("Напишите id и номер: «123456789 +998901234567».")
    if act == "list":
        lines = ["📋 Сотрудники и номера:"]
        import staff
        for p in staff.people():
            ph = core.db.phone(p[0])
            lines.append(f"• {p[1]}" + (f" (@{p[2]})" if p[2] else "") + f" — {POSITIONS.get(p[3], p[3])}, id {p[0]}: " + (ph["phone"] if ph else "номера нет"))
        return await c.message.answer("\n".join(lines))


@router.message(Phone.find, ~F.text.in_(MENU_BUTTONS))
async def ph_find(m: Message, state: FSMContext):
    if not can(m.from_user.id, "staff"):
        return
    t = parse_target(m)
    if not t:
        return await m.answer("Не понял. Напишите числовой id, @ник или перешлите сообщение человека.")
    await state.clear()
    target = t[1]
    uid, name, phone, src = await find_phone(target)
    who = name or (f"@{target}" if isinstance(target, str) else str(target))
    if phone:
        core.db.log(uname(m.from_user), "phone_lookup", f"{who} ({uid})")
        return await m.answer(f"📞 {who}" + (f"\nid {uid}" if uid else "") + f"\nНомер: {phone}\nИсточник: {src}")
    hint = "\nЧеловека нет в ваших контактах Telegram или он скрыл номер. Добавьте его в контакты телефона или запишите номер вручную." if configured() else "\nВаш Telegram-аккаунт к боту не подключён."
    await m.answer(f"Номер {who} не нашёл.{hint}", reply_markup=ikb([[("✍️ Записать вручную", "ph:man")]]))


@router.message(Phone.manual, ~F.text.in_(MENU_BUTTONS))
async def ph_manual(m: Message, state: FSMContext):
    if not can(m.from_user.id, "staff"):
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[0].isdigit() or not norm_phone(parts[1]):
        return await m.answer("Формат: «123456789 +998901234567» (id, пробел, номер).")
    await state.clear()
    phone = norm_phone(parts[1])
    core.db.set_phone(int(parts[0]), phone, "manual")
    core.db.log(uname(m.from_user), "phone_saved", parts[0])
    await m.answer(f"✅ Записал: id {parts[0]} — {phone}")
