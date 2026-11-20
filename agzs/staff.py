"""Раздел «👥 Персонал»: должности, приглашения по ссылке, назначение по @нику / id / пересланному сообщению, смена должности."""
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import core
from core import POS_INFO, POS_ORDER, POSITIONS, can, uname

router = Router()
B_STAFF = "👥 Персонал"
MENU_BUTTONS = {"➕ Сменный отчёт", "➕ Smena hisoboti", "📋 Мои смены", "⛽ Остаток газа", "📊 Финансы", "📈 Графики", "📤 Excel", "🧾 Смены",
                "💼 Бухгалтерия", "⚙️ Настройки", B_STAFF, "🌐 Язык", "🌐 Til"}


class Assign(StatesGroup):
    target = State()


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=x, callback_data=c) for x, c in r] for r in rows])


def pos_buttons(prefix, extra=""):
    return [[(POSITIONS[r], f"{prefix}:{r}{extra}")] for r in POS_ORDER]


def people():
    """[(id, имя, @ник, должность)]: владельцы из настроек сервера и все назначенные."""
    out, seen = [], set()
    for uid in sorted(core.db.admin_ids):
        s = core.db.seen_user(uid) or {}
        out.append((uid, s.get("name") or str(uid), s.get("username") or "", "admin", True))
        seen.add(uid)
    for u in core.db.users():
        if u["id"] in seen:
            continue
        s = core.db.seen_user(u["id"]) or {}
        out.append((u["id"], u["name"], s.get("username") or "", u["role"], False))
    return out


def staff_text():
    ppl = people()
    lines = ["👥 Персонал и должности", ""]
    for r in POS_ORDER:
        group = [p for p in ppl if p[3] == r]
        if group:
            lines.append(f"{POSITIONS[r]}:")
            lines += [f"• {p[1]}" + (f" (@{p[2]})" if p[2] else "") + f" — id {p[0]}" + (" · из настроек сервера" if p[4] else "") for p in group]
    pend = core.db.grants()
    if pend:
        lines += ["", "Ждут, пока откроют бота (должность выдастся автоматически):"] + [f"• @{g['username']} — {POSITIONS.get(g['role'], g['role'])}" for g in pend]
    if len(lines) == 2:
        lines.append("Пока никого нет.")
    return "\n".join(lines)


def staff_kb():
    rows = [[("🔗 Пригласить по ссылке", "sf:link")], [("🎯 Назначить по @нику / id", "sf:as")], [("ℹ️ Должности и права", "sf:info")], [("📞 Номера сотрудников", "ph:menu")]]
    for p in people():
        rows.append([(f"✏️ {p[1][:24]} — {POSITIONS.get(p[3], p[3])}", f"sf:u:{p[0]}")])
    for g in core.db.grants():
        rows.append([(f"🗑 Отменить @{g['username']}", f"sf:gr:{g['username']}")])
    return ikb(rows)


@router.message(F.text == B_STAFF)
async def staff(m: Message, state: FSMContext):
    if not can(m.from_user.id, "staff"):
        return
    await state.clear()
    await m.answer(staff_text(), reply_markup=staff_kb())


def info_text():
    return "ℹ️ Должности и права\n\n" + "\n\n".join(f"{POSITIONS[r]}: {POS_INFO[r]}" for r in POS_ORDER)


@router.callback_query(F.data.startswith("sf:"))
async def staff_cb(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    if not can(uid, "staff"):
        return await c.answer()
    await c.answer()
    parts = c.data.split(":")
    act = parts[1]
    if act == "info":
        return await c.message.answer(info_text())
    if act == "link":
        return await c.message.answer("Какую должность получит человек по ссылке?", reply_markup=ikb(pos_buttons("sf:lp")))
    if act == "lp":
        role = parts[2]
        me = await c.bot.get_me()
        code = core.db.new_invite(role)
        core.db.log(uname(c.from_user), "invite_created", POSITIONS[role])
        return await c.message.answer(f"Одноразовая ссылка для должности «{POSITIONS[role]}»:\nhttps://t.me/{me.username}?start=p_{code}\nОтправьте её человеку.")
    if act == "as":
        return await c.message.answer("Какую должность назначить?", reply_markup=ikb(pos_buttons("sf:ap")))
    if act == "ap":
        await state.set_state(Assign.target)
        await state.update_data(role=parts[2])
        return await c.message.answer(f"Назначаем: {POSITIONS[parts[2]]}.\nНапишите @ник или числовой id человека, либо перешлите сюда любое его сообщение.\n"
                                      "Если человек ещё не открывал бота, должность выдастся автоматически, когда он это сделает.")
    if act == "u":
        target = int(parts[2])
        s = core.db.seen_user(target) or {}
        name = (core.db.get_user(target) or s or {}).get("name") or str(target)
        locked = target in core.db.admin_ids
        rows = [[(f"→ {POSITIONS[r]}", f"sf:set:{target}:{r}")] for r in POS_ORDER] if not locked else []
        if not locked:
            rows.append([("🗑 Убрать из персонала", f"sf:rm:{target}")])
        text = f"{name}" + (f" (@{s['username']})" if s.get("username") else "") + f"\nid {target}"
        return await c.message.answer(text + ("\nВладелец из настроек сервера: менять и убирать нельзя." if locked else "\nНовая должность:"), reply_markup=ikb(rows) if rows else None)
    if act == "set":
        target, role = int(parts[2]), parts[3]
        if target in core.db.admin_ids:
            return
        u = core.db.get_user(target)
        if not u:
            return await c.message.answer("Человека нет в списке.")
        if u["role"] == "admin" and role != "admin" and len([x for x in core.db.users() if x["role"] == "admin"]) + len(core.db.admin_ids) <= 1:
            return await c.message.answer("Нельзя снять последнего владельца.")
        core.db.set_user(target, u["name"], role)
        core.db.log(uname(c.from_user), "position_changed", f"{u['name']} → {POSITIONS[role]}")
        await c.message.answer(f"✅ {u['name']}: теперь {POSITIONS[role]}.")
        return await notify_person(c.bot, target, role)
    if act == "rm":
        target = int(parts[2])
        if target in core.db.admin_ids:
            return
        u = core.db.get_user(target)
        core.db.remove_user(target)
        core.db.log(uname(c.from_user), "staff_removed", (u or {}).get("name", str(target)))
        return await c.message.answer("🗑 Убрано из персонала.")
    if act == "gr":
        core.db.remove_grant(parts[2])
        core.db.log(uname(c.from_user), "grant_removed", parts[2])
        return await c.message.answer(f"Назначение для @{parts[2]} отменено.")


async def notify_person(bot, uid, role):
    try:
        await bot.send_message(uid, f"Вам назначена должность: {POSITIONS[role]}. Нажмите /start, чтобы обновить меню.")
    except Exception:
        pass  # человек мог ни разу не открывать бота


def parse_target(m):
    """Что указал владелец: пересланное сообщение, @ник или числовой id. Возвращает ('id', int, имя) / ('user', ник) / None."""
    origin = getattr(m, "forward_origin", None)
    fu = getattr(origin, "sender_user", None) or getattr(m, "forward_from", None)
    if fu is not None and isinstance(getattr(fu, "id", None), int):
        return ("id", fu.id, getattr(fu, "full_name", None) or getattr(fu, "username", None) or str(fu.id))
    text = (m.text or "").strip()
    if text.lstrip("@").isdigit() and len(text.lstrip("@")) >= 5:
        return ("id", int(text.lstrip("@")), None)
    name = text.lstrip("@").split()[0] if text else ""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", name):  # ник в Telegram: латиница, цифры, _
        return ("user", name.lower())
    return None


@router.message(Assign.target, ~F.text.in_(MENU_BUTTONS))
async def assign_target(m: Message, state: FSMContext):
    uid = m.from_user.id
    if not can(uid, "staff"):
        return
    role = (await state.get_data()).get("role")
    t = parse_target(m)
    if not t or role not in POSITIONS:
        return await m.answer("Не понял. Напишите @ник (например @ivanov_ali), числовой id или перешлите сообщение этого человека.")
    if t[0] == "id":
        target = t[1]
        if target in core.db.admin_ids:
            await state.clear()
            return await m.answer("Это владелец из настроек сервера, его должность менять нельзя.")
        s = core.db.seen_user(target) or {}
        name = t[2] or s.get("name") or str(target)
        core.db.set_user(target, name, role)
        core.db.log(uname(m.from_user), "position_assigned", f"{name} → {POSITIONS[role]}")
        await state.clear()
        await m.answer(f"✅ {name}: {POSITIONS[role]}." + ("" if s else "\nЧеловек ещё не открывал бота, пусть нажмёт /start — тогда увидит меню."))
        return await notify_person(m.bot, target, role)
    username = t[1]
    s = core.db.seen_by_username(username)
    await state.clear()
    if s:
        if s["id"] in core.db.admin_ids:
            return await m.answer("Это владелец из настроек сервера, его должность менять нельзя.")
        core.db.set_user(s["id"], s["name"], role)
        core.db.log(uname(m.from_user), "position_assigned", f"@{username} → {POSITIONS[role]}")
        await m.answer(f"✅ @{username} ({s['name']}): {POSITIONS[role]}.")
        return await notify_person(m.bot, s["id"], role)
    core.db.add_grant(username, role, uname(m.from_user))
    core.db.log(uname(m.from_user), "grant_created", f"@{username} → {POSITIONS[role]}")
    me = await m.bot.get_me()
    await m.answer(f"✅ Запомнил: @{username} получит должность «{POSITIONS[role]}», как только откроет бота (нужно нажать Start).\nОтправьте ему ссылку: https://t.me/{me.username}")


@router.message(Command("addoperator"))
async def add_operator(m: Message):
    if not can(m.from_user.id, "staff"):
        return
    p = (m.text or "").split(maxsplit=2)
    if len(p) < 2 or not p[1].isdigit():
        return await m.answer("Формат: /addoperator 123456789 Имя")
    core.db.set_user(int(p[1]), p[2] if len(p) > 2 else p[1], "operator")
    core.db.log(uname(m.from_user), "operator_added", p[1])
    await m.answer("✅ Оператор добавлен.")
