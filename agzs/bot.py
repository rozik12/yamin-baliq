"""Telegram-бот «Личный бухгалтер АГЗС»: сменные отчёты, финансы, налоги, остаток газа, графики, Excel."""
import asyncio
import logging
import os
from datetime import timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

import calc
import charts
import export
from calc import PAY_NAMES, fnum, parse_date, parse_num
from db import DB, now

ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "8219891075").replace(" ", "").split(",") if x.isdigit()}
db = DB(os.getenv("DB_PATH", "agzs.db"), ADMINS)
router = Router()
log = logging.getLogger("agzs")

B_SHIFT, B_STOCK, B_FIN, B_GRAPH = "➕ Сменный отчёт", "⛽ Остаток газа", "📊 Финансы", "📈 Графики"
B_XLS, B_SHIFTS, B_STAFF, B_SET, B_MINE = "📤 Excel", "🧾 Смены", "👥 Персонал", "⚙️ Настройки", "📋 Мои смены"


MENU = {B_SHIFT, B_STOCK, B_FIN, B_GRAPH, B_XLS, B_SHIFTS, B_STAFF, B_SET, B_MINE}


class Shift(StatesGroup):
    step = State()


class SetVal(StatesGroup):
    value = State()


class Stock(StatesGroup):
    liters = State()
    density = State()


def role(uid):
    return db.role_of(uid)


def is_admin(uid):
    return role(uid) == "admin"


def today():
    return now().date()


def cfg():
    return calc.cfg_of(db.settings())


def menu(uid):
    if is_admin(uid):
        rows = [[B_SHIFT, B_STOCK], [B_FIN, B_GRAPH], [B_XLS, B_SHIFTS], [B_STAFF, B_SET]]
    else:
        rows = [[B_SHIFT, B_MINE]]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=c) for t, c in r] for r in rows])


def uname(u):
    return u.full_name or u.username or str(u.id)


# ---------- старт, доступ, персонал

@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith(("op_", "adm_")) and not is_admin(uid):
        code = parts[1].split("_", 1)[1]
        r = db.redeem(code, uid, uname(m.from_user))
        if not r:
            return await m.answer("Эта ссылка уже использована или неверна. Попросите владельца прислать новую.")
        for a in db.staff_ids("admin"):
            try:
                await m.bot.send_message(a, f"👤 Новый {'оператор' if r == 'operator' else 'бухгалтер'}: {uname(m.from_user)} ({uid})")
            except Exception:
                pass
    r = role(uid)
    if not r:
        return await m.answer("Это закрытый бот учёта АГЗС. Попросите владельца прислать вам ссылку-приглашение.")
    text = ("Здравствуйте! Вы — владелец / бухгалтер. Здесь учёт смен, налоги, закуп и остаток газа, графики и Excel."
            if r == "admin" else "Здравствуйте! Вы — оператор. Нажмите «➕ Сменный отчёт» в конце смены.")
    await m.answer(text, reply_markup=menu(uid))


@router.message(F.text == B_STAFF)
async def staff(m: Message):
    if not is_admin(m.from_user.id):
        return
    us = db.users()
    lines = ["👥 Персонал:"] + [f"• {u['name']} — {'оператор' if u['role'] == 'operator' else 'бухгалтер'} ({u['id']})" for u in us]
    lines.append(f"Владельцы (из настроек сервера): {', '.join(map(str, sorted(ADMINS)))}")
    rows = [[("➕ Ссылка для оператора", "st:op"), ("➕ Ссылка для бухгалтера", "st:adm")]]
    rows += [[(f"🗑 Убрать {u['name']}", f"st:rm:{u['id']}")] for u in us]
    await m.answer("\n".join(lines), reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("st:"))
async def staff_cb(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    act = c.data[3:]
    if act in ("op", "adm"):
        me = await c.bot.get_me()
        code = db.new_invite("operator" if act == "op" else "admin")
        await c.message.answer(f"Отправьте эту одноразовую ссылку:\nhttps://t.me/{me.username}?start={act}_{code}")
    elif act.startswith("rm:"):
        db.remove_user(int(act[3:]))
        await c.message.edit_text("Убрано.")
    await c.answer()


@router.message(Command("addoperator"))
async def add_operator(m: Message):
    if not is_admin(m.from_user.id):
        return
    p = (m.text or "").split(maxsplit=2)
    if len(p) < 2 or not p[1].isdigit():
        return await m.answer("Формат: /addoperator 123456789 Имя")
    db.set_user(int(p[1]), p[2] if len(p) > 2 else p[1], "operator")
    await m.answer("✅ Оператор добавлен.")


# ---------- сменный отчёт (мастер ввода)

MONEY = [("cash", "💵 Наличные"), ("terminal", "💳 Терминал"), ("click", "📱 Click"), ("paynet", "🔳 Paynet QR")]


def steps_for(d):
    s = ["date"]
    for k in (1, 2, 3):
        s += [f"m{k}s", f"m{k}e"]
    s.append("arr_kg")
    if (d.get("arr_kg") or 0) > 0:
        s += ["arr_density", "arr_price"]
    s += [k for k, _ in MONEY] + ["exp"]
    return s


def prompt_for(key, d, prev):
    n = key[1] if key.startswith("m") and len(key) == 3 else None
    if key == "date":
        return "📅 За какую дату смена? Нажмите кнопку или напишите дату (например 15.11).", [[("Сегодня", "sh:today"), ("Вчера", "sh:yday")]]
    if n and key.endswith("s"):
        p = prev.get(int(n))
        txt = f"🔢 Счётчик {n}: НАЧАЛЬНОЕ показание (литры)."
        return (txt + (f"\nВ прошлой смене закончили на {fnum(p, 1)}." if p is not None else ""),
                [[(f"✅ {fnum(p, 1)} (как в прошлой смене)", "sh:@prev")]] if p is not None else [])
    if n:
        return (f"🔢 Счётчик {n}: КОНЕЧНОЕ показание (начало было {fnum(d[f'm{n}s'], 1)}).",
                [[("⏭ Не работал (без пролива)", "sh:@skip")]])
    if key == "arr_kg":
        return "🚚 Приход газа за смену: масса в кг (например 12000).", [[("0 — прихода не было", "sh:@zero")]]
    if key == "arr_density":
        return "⚖️ Плотность газа (кг/л):", [[("0,54", "sh:@0.54"), ("0,56", "sh:@0.56")]]
    if key == "arr_price":
        pr = cfg()["price_buy_kg"]
        return ("💲 Цена закупа за 1 кг (сум)." + (f"\nПо умолчанию: {fnum(pr)}." if pr > 0 else ""),
                [[(f"✅ {fnum(pr)}", "sh:@default")]] if pr > 0 else [])
    if key in dict(MONEY):
        return f"{dict(MONEY)[key]}: сколько принято за смену (сум)?", [[("0", "sh:@zero")]]
    total = sum(a for a, _ in d.get("exps", []))
    lines = "\n".join(f"• {fnum(a)} — {c or 'без описания'}" for a, c in d.get("exps", []))
    return (f"🧾 Операционные расходы на месте. Пишите по одному: сумма и описание, например «50000 бензин для генератора».\n"
            + (f"\nДобавлено ({fnum(total)} сум):\n{lines}\n" if lines else "") + "\nКогда закончите — «Готово».",
            [[("✅ Готово" if lines else "✅ Расходов не было", "sh:@done")]])


def nav():
    return [("⬅️ Назад", "sh:@back"), ("✖️ Отмена", "sh:@cancel")]


async def ask(target, state):
    data = await state.get_data()
    d, i = data["d"], data["i"]
    key = steps_for(d)[i]
    text, rows = prompt_for(key, d, data["prev"])
    kb = [[(t, c) for t, c in r] for r in rows] + [nav()]
    await target.answer(f"Шаг {i + 1}/{len(steps_for(d))}\n{text}", reply_markup=ikb(kb))


@router.message(F.text == B_SHIFT)
async def shift_start(m: Message, state: FSMContext):
    if not role(m.from_user.id):
        return
    await state.clear()
    await state.set_state(Shift.step)
    await state.update_data(i=0, d={"exps": []}, prev=db.last_meter_ends())
    await ask(m, state)


def apply_value(key, d, raw, prev):
    """Записывает значение шага в d. Возвращает текст ошибки или None."""
    raw = (raw or "").strip()
    if key == "date":
        v = parse_date({"@today": "сегодня", "@yday": "вчера"}.get(raw, raw), today())
        if not v:
            return "Не понял дату. Напишите, например, 15.11 или нажмите кнопку."
        d["date"] = v
        return None
    if key.startswith("m") and len(key) == 3:
        n = int(key[1])
        if key.endswith("s"):
            v = prev.get(n) if raw == "@prev" else parse_num(raw)
            if v is None:
                return "Нужно число, например 12345,5."
            d[key] = v
            return None
        if raw == "@skip":
            d[key] = d[f"m{n}s"]
            return None
        v = parse_num(raw)
        if v is None:
            return "Нужно число, например 12845,5."
        if v < d[f"m{n}s"]:
            return f"Конечное показание ({fnum(v, 1)}) меньше начального ({fnum(d[f'm{n}s'], 1)}). Проверьте цифры."
        if v - d[f"m{n}s"] > 100000:
            return "Пролив больше 100 000 л за смену — проверьте цифры."
        d[key] = v
        return None
    if key == "arr_kg":
        v = 0.0 if raw == "@zero" else parse_num(raw)
        if v is None or v > 200000:
            return "Нужно число от 0 до 200 000 кг."
        d["arr_kg"] = v
        return None
    if key == "arr_density":
        v = parse_num(raw[1:] if raw.startswith("@") else raw)
        if v is None or not 0.4 <= v <= 0.7:
            return "Плотность должна быть от 0,4 до 0,7 (обычно 0,54 или 0,56)."
        d["arr_density"] = v
        return None
    if key == "arr_price":
        v = cfg()["price_buy_kg"] if raw == "@default" else parse_num(raw)
        if not v or v <= 0:
            return "Нужна цена закупа больше нуля."
        d["arr_price"] = v
        return None
    if key in dict(MONEY):
        v = 0.0 if raw == "@zero" else parse_num(raw)
        if v is None:
            return "Нужна сумма числом (можно 0)."
        d[key] = v
        return None
    if raw == "@done":
        return None
    parts = raw.split(maxsplit=1)
    a = parse_num(parts[0]) if parts else None
    if a is None or a <= 0:
        return "Напишите сумму и описание, например «50000 бензин»."
    d["exps"].append((a, parts[1].strip() if len(parts) > 1 else ""))
    return "stay"


def shift_summary(d, full):
    c = cfg()
    r = calc.shift_calc({**d, **{k: d.get(k, 0) for k in ("arr_kg", "arr_density", "arr_price")}}, [a for a, _ in d.get("exps", [])], c)
    lines = [f"🧾 Сменный отчёт за {d['date'][8:10]}.{d['date'][5:7]}.{d['date'][:4]}", ""]
    for i in range(3):
        a, b = d[f"m{i + 1}s"], d[f"m{i + 1}e"]
        lines.append(f"Счётчик {i + 1}: {fnum(a, 1)} → {fnum(b, 1)} = {fnum(b - a, 1)} л")
    lines.append(f"⛽ Пролив всего: {fnum(r['sold_l'], 1)} л")
    if r["arr_kg"] > 0:
        lines.append(f"🚚 Приход: {fnum(r['arr_kg'], 1)} кг × плотность {str(r['arr_density']).replace('.', ',')} = {fnum(r['arr_l'], 1)} л, "
                     f"закуп {fnum(r['arr_price'])} сум/кг")
    lines += ["", f"💰 Касса: {fnum(r['revenue'])} сум"] + [f"   {PAY_NAMES[k]}: {fnum(r['pay'][k])}" for k, _ in MONEY if r["pay"][k]]
    if r["expenses"]:
        lines.append(f"💸 Расходы на месте: {fnum(r['expenses'])} сум")
    if r["diff"] is not None and r["theory"]:
        pct = 100 * r["diff"] / r["theory"]
        sign = "излишек" if r["diff"] > 0 else "недостача"
        lines.append(f"{'⚠️' if abs(pct) > 2 else '✔️'} По счётчикам должно быть {fnum(r['theory'])} сум: {sign} {fnum(abs(r['diff']))} сум ({abs(pct):.1f}%)")
    if full:
        net_rev = r["revenue"] - r["vat_out"]
        gas = r["purchase"] - r["vat_in"]
        profit = net_rev - gas - r["excise"] - r["expenses"]
        lines += ["", f"НДС: исходящий {fnum(r['vat_out'])} − входящий {fnum(r['vat_in'])} = {fnum(r['vat_net'])}", f"Акциз: {fnum(r['excise'])}",
                  f"Закуп газа: {fnum(r['purchase'])}", f"✅ Чистая прибыль смены (закуп в день прихода): {fnum(profit)} сум"]
        if c["price_buy_kg"] <= 0 and r["arr_kg"] > 0 and not d.get("arr_price"):
            lines.append("⚠️ Не задана цена закупа — прибыль завышена.")
    return "\n".join(lines)


def meter_warnings(d, prev):
    out = []
    for n in (1, 2, 3):
        p = prev.get(n)
        if p is not None and abs(d[f"m{n}s"] - p) > 0.05:
            out.append(f"⚠️ Начало счётчика {n} ({fnum(d[f'm{n}s'], 1)}) не совпадает с концом прошлой смены ({fnum(p, 1)}).")
    return out


async def finish_wizard(target, state, uid):
    data = await state.get_data()
    d = data["d"]
    text = shift_summary(d, is_admin(uid))
    warns = meter_warnings(d, data["prev"])
    await target.answer(text + ("\n\n" + "\n".join(warns) if warns else ""),
                        reply_markup=ikb([[("✅ Сохранить", "sh:@save"), ("✏️ Заново", "sh:@redo")], [("✖️ Отмена", "sh:@cancel")]]))


async def advance(target, state, user, raw):
    data = await state.get_data()
    d, i = data["d"], data["i"]
    keys = steps_for(d)
    if i >= len(keys):
        return
    err = apply_value(keys[i], d, raw, data["prev"])
    if err and err != "stay":
        await target.answer("❗ " + err)
        return
    if err != "stay":
        i += 1
    await state.update_data(d=d, i=i)
    if i >= len(steps_for(d)):
        return await finish_wizard(target, state, user.id)
    await ask(target, state)


@router.message(Shift.step, ~F.text.in_(MENU))
async def shift_text(m: Message, state: FSMContext):
    await advance(m, state, m.from_user, m.text or "")


@router.callback_query(F.data.startswith("sh:"))
async def shift_button(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    raw = c.data[3:]
    await c.answer()
    if not role(uid):
        return
    if raw == "@cancel":
        await state.clear()
        return await c.message.edit_text("Отменено.")
    data = await state.get_data()
    if "d" not in data:
        return await c.message.edit_text("Эта кнопка устарела. Нажмите «➕ Сменный отчёт».")
    if raw == "@back":
        await state.update_data(i=max(data["i"] - 1, 0))
        return await ask(c.message, state)
    if raw == "@redo":
        await state.update_data(i=0, d={"exps": []})
        return await ask(c.message, state)
    if raw == "@save":
        return await save_shift(c, state)
    await advance(c.message, state, c.from_user, {"today": "@today", "yday": "@yday"}.get(raw, raw))


async def save_shift(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    d = data["d"]
    uid = c.from_user.id
    await state.clear()
    sid = db.add_shift(uid, uname(c.from_user), d, d["exps"])
    await c.message.edit_text(shift_summary(d, is_admin(uid)) + f"\n\n✅ Сохранено (смена №{sid}).")
    st = tank_state()
    note = f"⛽ Остаток газа сейчас: {fnum(st['liters'], 0)} л · {fnum(st['kg'], 0)} кг"
    await c.message.answer(note if is_admin(uid) else "✅ Отчёт принят, спасибо!", reply_markup=menu(uid))
    for a in db.staff_ids("admin") - ({uid} if is_admin(uid) else set()):
        try:
            await c.bot.send_message(a, f"📥 Новая смена №{sid} от {uname(c.from_user)}\n\n{shift_summary(d, True)}\n\n{note}")
        except Exception:
            log.warning("cannot notify %s", a)
    low = cfg()["low_stock_l"]
    if low > 0 and st["liters"] < low:
        for a in db.staff_ids("admin"):
            try:
                await c.bot.send_message(a, f"🚨 Газ заканчивается: в резервуаре {fnum(st['liters'], 0)} л (порог {fnum(low)} л). Пора заказывать.")
            except Exception:
                pass


# ---------- остаток газа

def tank_state():
    _, st, _ = calc.tank_run(db.shifts(), db.adjustments(), cfg())
    return st


@router.message(F.text == B_STOCK)
async def stock(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    c = cfg()
    st = tank_state()
    has = bool(db.adjustments())
    lines = ["⛽ Остаток газа в резервуаре", "", f"{fnum(st['liters'], 0)} л · {fnum(st['kg'], 0)} кг · плотность {str(round(st['density'], 3)).replace('.', ',')} кг/л"]
    if c["tank_capacity_l"] > 0:
        lines.append(f"Заполнено: {100 * st['liters'] / c['tank_capacity_l']:.0f}% от {fnum(c['tank_capacity_l'])} л")
    if c["low_stock_l"] > 0:
        lines.append("🚨 Ниже порога — пора заказывать газ." if st["liters"] < c["low_stock_l"] else f"Порог заказа: {fnum(c['low_stock_l'])} л")
    if not has:
        lines.append("\n⚠️ Начальный остаток не задан, поэтому цифры могут быть неверны. Нажмите «Задать остаток» и укажите замер.")
    if st["liters"] < 0:
        lines.append("\n⚠️ Остаток отрицательный: не внесён приход или неверный начальный остаток. Сделайте замер резервуара.")
    await m.answer("\n".join(lines), reply_markup=ikb([[("✏️ Задать остаток (замер)", "sk:set")]]))


@router.callback_query(F.data == "sk:set")
async def stock_set(c: CallbackQuery, state: FSMContext):
    if is_admin(c.from_user.id):
        await state.set_state(Stock.liters)
        await c.message.answer("Сколько литров газа в резервуаре сейчас (по замеру)?")
    await c.answer()


@router.message(Stock.liters, ~F.text.in_(MENU))
async def stock_liters(m: Message, state: FSMContext):
    v = parse_num(m.text)
    if v is None:
        return await m.answer("Нужно число литров, например 8500.")
    await state.update_data(liters=v)
    await state.set_state(Stock.density)
    await m.answer("Плотность газа (кг/л):", reply_markup=ikb([[("0,54", "sd:0.54"), ("0,56", "sd:0.56")]]))


async def stock_save(target, state, user, density):
    d = await state.get_data()
    liters = d["liters"]
    await state.clear()
    db.add_adjustment(today().isoformat(), liters, liters * density, "замер/начальный остаток", uname(user))
    st = tank_state()
    await target.answer(f"✅ Остаток записан: {fnum(liters)} л · {fnum(liters * density)} кг (плотность {str(density).replace('.', ',')}).\n"
                        f"Дальше остаток считается сам по сменным отчётам.")


@router.callback_query(F.data.startswith("sd:"))
async def stock_density_cb(c: CallbackQuery, state: FSMContext):
    if is_admin(c.from_user.id) and "liters" in await state.get_data():
        await stock_save(c.message, state, c.from_user, float(c.data[3:]))
    await c.answer()


@router.message(Stock.density, ~F.text.in_(MENU))
async def stock_density_text(m: Message, state: FSMContext):
    v = parse_num(m.text)
    if v is None or not 0.4 <= v <= 0.7:
        return await m.answer("Плотность от 0,4 до 0,7 (обычно 0,54 или 0,56).")
    await stock_save(m, state, m.from_user, v)


# ---------- периоды, финансы, графики, Excel

PERIODS = [("Сегодня", "today"), ("Вчера", "yday"), ("Неделя", "week"), ("Месяц", "month"), ("Всё время", "all")]


def period_range(code):
    t = today()
    first = db.shifts()[:1]
    if code == "today":
        return t.isoformat(), t.isoformat(), "сегодня"
    if code == "yday":
        y = (t - timedelta(days=1)).isoformat()
        return y, y, "вчера"
    if code == "week":
        return (t - timedelta(days=6)).isoformat(), t.isoformat(), "неделя"
    if code == "month":
        return (t - timedelta(days=29)).isoformat(), t.isoformat(), "месяц"
    return (first[0]["date"] if first else t.isoformat()), t.isoformat(), "всё время"


def rows_for(start, end):
    return calc.day_rows(db.shifts(), db.expenses_map(), db.adjustments(), cfg(), start, end)


def finance_text(rows, label):
    t = calc.totals(rows)
    c = cfg()
    if not t["shifts"]:
        return f"📊 Финансы · {label}\n\nСмен за этот период нет."
    margin = 100 * t["profit"] / t["revenue"] if t["revenue"] else 0
    lines = [f"📊 Финансы · {label}", f"Смен: {t['shifts']}", "",
             f"⛽ Пролив: {fnum(t['sold_l'])} л ({fnum(t['sold_kg'])} кг)",
             "   " + " · ".join(f"сч.{i + 1}: {fnum(t['m'][i])}" for i in range(3)),
             f"🚚 Приход газа: {fnum(t['arr_kg'])} кг ({fnum(t['arr_l'])} л)", "",
             f"💰 Выручка: {fnum(t['revenue'])} сум"] + [f"   {PAY_NAMES[k]}: {fnum(v)}" for k, v in t["pay"].items() if v]
    if c["sale_price_l"] > 0 and t["theory"]:
        diff = t["revenue"] - t["theory"]
        lines.append(f"   По счётчикам: {fnum(t['theory'])} → {'излишек' if diff > 0 else 'недостача'} {fnum(abs(diff))}")
    lines += ["", "🧾 Налоги:", f"   НДС исходящий: {fnum(t['vat_out'])}", f"   НДС входящий (с закупа): {fnum(t['vat_in'])}",
              f"   НДС {'к уплате' if t['vat_net'] >= 0 else 'к зачёту'}: {fnum(abs(t['vat_net']))}", f"   Акциз: {fnum(t['excise'])}",
              f"   Всего налогов: {fnum(t['taxes'])}", "",
              f"🛢 Закуп газа (оплачено): {fnum(t['purchase'])}",
              f"   В прибыли ({'по закупу' if c['profit_mode'] != 'cogs' else 'по себестоимости проданного'}, без НДС): {fnum(t['gas_cost'])}",
              f"💸 Расходы на месте: {fnum(t['expenses'])}", "",
              f"✅ ЧИСТАЯ ПРИБЫЛЬ: {fnum(t['profit'])} сум ({margin:.1f}% выручки)"]
    last = next((r for r in reversed(rows) if r["tank_l"] is not None), None)
    if last:
        lines += ["", f"⛽ Остаток на конец периода: {fnum(last['tank_l'])} л · {fnum(last['tank_kg'])} кг"]
    if c["price_buy_kg"] <= 0 and t["arr_kg"] > 0:
        lines.append("\n⚠️ Не задана цена закупа в настройках: стоимость газа может быть занижена.")
    return "\n".join(lines)


@router.message(F.text == B_FIN)
async def fin_menu(m: Message):
    if is_admin(m.from_user.id):
        await m.answer("За какой период показать финансы?", reply_markup=ikb([[(n, f"fp:{c}") for n, c in PERIODS[:3]], [(n, f"fp:{c}") for n, c in PERIODS[3:]]]))


@router.callback_query(F.data.startswith("fp:"))
async def fin_cb(c: CallbackQuery):
    if is_admin(c.from_user.id):
        s, e, label = period_range(c.data[3:])
        await c.message.answer(finance_text(rows_for(s, e), label))
    await c.answer()


@router.message(F.text == B_GRAPH)
async def graph_menu(m: Message):
    if not is_admin(m.from_user.id):
        return
    rows = [[(name, f"gk:{k}")] for k, name in charts.KINDS.items()]
    await m.answer("📈 Какой график построить?", reply_markup=ikb(rows))


@router.message(Command("graph"))
async def graph_cmd(m: Message):
    await graph_menu(m)


@router.callback_query(F.data.startswith("gk:"))
async def graph_kind(c: CallbackQuery):
    if is_admin(c.from_user.id):
        k = c.data[3:]
        await c.message.answer(f"«{charts.KINDS[k]}»: за какой период?",
                               reply_markup=ikb([[("За неделю", f"gp:{k}:week"), ("За месяц", f"gp:{k}:month"), ("За всё время", f"gp:{k}:all")]]))
    await c.answer()


@router.callback_query(F.data.startswith("gp:"))
async def graph_make(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    _, kind, code = c.data.split(":")
    await c.answer("Строю график…")
    s, e, label = period_range(code)
    rows = rows_for(s, e)
    if not any(r["shifts"] for r in rows) and kind != "stock":
        return await c.message.answer("За этот период нет смен — график строить не из чего.")
    png = await asyncio.to_thread(charts.render, kind, rows, cfg(), label)
    t = calc.totals(rows)
    cap = {"sales": f"Пролив: {fnum(t['sold_l'])} л", "revenue": f"Выручка: {fnum(t['revenue'])} сум", "expenses": f"Закуп {fnum(t['purchase'])} · расходы {fnum(t['expenses'])} сум",
           "profit": f"Чистая прибыль: {fnum(t['profit'])} сум", "stock": "Остаток газа по дням"}[kind]
    await c.message.answer_photo(BufferedInputFile(png, f"{kind}.png"), caption=f"{charts.KINDS[kind]} · {label}\n{cap}")


@router.message(F.text == B_XLS)
async def xls_menu(m: Message):
    if is_admin(m.from_user.id):
        await m.answer("Выгрузить сводную таблицу в Excel:", reply_markup=ikb([[("За всё время", "xl:all"), ("За 30 дней", "xl:month")]]))


@router.message(Command("export"))
async def export_cmd(m: Message):
    await xls_menu(m)


@router.callback_query(F.data.startswith("xl:"))
async def xls_make(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer("Готовлю файл…")
    s, e, label = period_range(c.data[3:])
    rows = rows_for(s, e)
    shifts = db.shifts(s, e)
    ids = {x["id"] for x in shifts}
    exps = [x for x in db.expense_rows() if x["shift_id"] in ids]
    data = await asyncio.to_thread(export.build_xlsx, rows, shifts, exps, db.adjustments(), db.settings())
    await c.message.answer_document(BufferedInputFile(data, f"agzs_{s}_{e}.xlsx"), caption=f"📤 Сводная таблица · {label}")


# ---------- смены: список, удаление

@router.message(F.text == B_SHIFTS)
async def shifts_list(m: Message):
    if not is_admin(m.from_user.id):
        return
    sh = db.shifts()[-10:][::-1]
    if not sh:
        return await m.answer("Смен пока нет.")
    c = cfg()
    lines, rows = ["🧾 Последние смены:"], []
    for s in sh:
        r = calc.shift_calc(s, [], c)
        lines.append(f"№{s['id']} · {s['date'][8:10]}.{s['date'][5:7]} · {s['user_name']} · {fnum(r['sold_l'])} л · {fnum(r['revenue'])} сум")
        rows.append([(f"🗑 Удалить №{s['id']}", f"ds:{s['id']}")])
    await m.answer("\n".join(lines), reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("ds:"))
async def shift_delete(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    sid = int(c.data[3:])
    await c.answer()
    await c.message.edit_text(f"Удалить смену №{sid}? Остаток и финансы пересчитаются. Это нельзя отменить.",
                              reply_markup=ikb([[("🗑 Да, удалить", f"dy:{sid}"), ("Отмена", "dn")]]))


@router.callback_query(F.data.startswith("dy:"))
async def shift_delete_yes(c: CallbackQuery):
    if is_admin(c.from_user.id):
        n = db.delete_shift(int(c.data[3:]))
        await c.message.edit_text("🗑 Смена удалена." if n else "Такой смены уже нет.")
    await c.answer()


@router.callback_query(F.data == "dn")
async def delete_no(c: CallbackQuery):
    await c.message.edit_text("Отменено.")
    await c.answer()


@router.message(F.text == B_MINE)
async def my_shifts(m: Message):
    uid = m.from_user.id
    if not role(uid):
        return
    sh = db.shifts(uid=uid)[-8:][::-1]
    if not sh:
        return await m.answer("Вы ещё не сдавали отчёты.")
    c = cfg()
    await m.answer("📋 Ваши последние смены:\n" + "\n".join(
        f"№{s['id']} · {s['date'][8:10]}.{s['date'][5:7]} · {fnum(calc.shift_calc(s, [], c)['sold_l'])} л · касса {fnum(calc.shift_calc(s, [], c)['revenue'])} сум" for s in sh))


# ---------- настройки

SETTINGS = [("sale_price_l", "Цена продажи, сум/л"), ("price_buy_kg", "Цена закупа, сум/кг"), ("vat_rate", "НДС, %"), ("excise_per_l", "Акциз, сум/л"),
            ("default_density", "Плотность по умолчанию"), ("tank_capacity_l", "Ёмкость резервуара, л"), ("low_stock_l", "Порог остатка, л")]


def settings_text():
    c = cfg()
    lines = ["⚙️ Настройки учёта", ""]
    for k, name in SETTINGS:
        lines.append(f"{name}: {fnum(c[k], 2) if k == 'default_density' else fnum(c[k], 2 if c[k] % 1 else 0)}")
    lines.append(f"Цена закупа задана: {'с НДС' if c['buy_price_with_vat'] else 'без НДС'}")
    lines.append(f"Прибыль считать: {'по закупу в день прихода' if c['profit_mode'] != 'cogs' else 'по себестоимости проданного газа'}")
    return "\n".join(lines)


def settings_kb():
    rows = [[(f"✏️ {name}", f"se:{k}")] for k, name in SETTINGS]
    rows += [[("🔁 Цена закупа: с НДС / без НДС", "se:@vat")], [("🔁 Режим прибыли: закуп / себестоимость", "se:@mode")]]
    return ikb(rows)


@router.message(F.text == B_SET)
async def settings(m: Message, state: FSMContext):
    if is_admin(m.from_user.id):
        await state.clear()
        await m.answer(settings_text(), reply_markup=settings_kb())


@router.callback_query(F.data.startswith("se:"))
async def settings_cb(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    k = c.data[3:]
    await c.answer()
    if k == "@vat":
        db.set_setting("buy_price_with_vat", 0 if cfg()["buy_price_with_vat"] else 1)
        return await c.message.edit_text(settings_text(), reply_markup=settings_kb())
    if k == "@mode":
        db.set_setting("profit_mode", "cogs" if cfg()["profit_mode"] != "cogs" else "purchases")
        return await c.message.edit_text(settings_text(), reply_markup=settings_kb())
    await state.set_state(SetVal.value)
    await state.update_data(key=k)
    await c.message.answer(f"Новое значение: {dict(SETTINGS)[k]} (число).")


@router.message(SetVal.value, ~F.text.in_(MENU))
async def settings_value(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    v = parse_num(m.text)
    k = (await state.get_data())["key"]
    if v is None or (k == "vat_rate" and v > 100) or (k == "default_density" and not 0.4 <= v <= 0.7):
        return await m.answer("Нужно число (для плотности от 0,4 до 0,7, для НДС до 100).")
    db.set_setting(k, v)
    await state.clear()
    await m.answer("✅ Сохранено.\n\n" + settings_text(), reply_markup=settings_kb())


@router.message()
async def fallback(m: Message):
    uid = m.from_user.id
    if role(uid):
        await m.answer("Нажмите кнопку внизу 👇", reply_markup=menu(uid))
    else:
        await m.answer("Это закрытый бот учёта АГЗС. Попросите владельца прислать ссылку-приглашение.")


@router.error()
async def on_error(event):
    log.exception("handler error: %s", event.exception)
    return True


async def main():
    logging.basicConfig(level=logging.INFO)
    proxy = os.getenv("HTTPS_PROXY") if os.getenv("USE_PROXY") else None
    bot = Bot(os.environ["BOT_TOKEN"], session=AiohttpSession(proxy=proxy) if proxy else None, default=DefaultBotProperties(parse_mode=None))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("polling crashed, restart in 5s")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
