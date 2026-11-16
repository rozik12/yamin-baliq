"""Telegram-бот «Личный бухгалтер АГЗС»: сменные отчёты, финансы, налоги, остаток газа, графики, Excel."""
import asyncio
import logging
import os
from datetime import timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram import BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

import calc
import charts
import core
import export
import texts
from calc import PAY_KEYS, PAY_NAMES, fnum, parse_date, parse_num
from core import can, cfg, is_admin, role, today, uname
from db import now
from reports import alert_stock, build_excel, finance_text  # noqa: F401

ADMINS = core.ADMINS
router = Router()
log = logging.getLogger("agzs")

B_STOCK, B_FIN, B_GRAPH, B_ACC = "⛽ Остаток газа", "📊 Финансы", "📈 Графики", "💼 Бухгалтерия"
B_XLS, B_SHIFTS, B_STAFF, B_SET = "📤 Excel", "🧾 Смены", "👥 Персонал", "⚙️ Настройки"
B_SHIFT, B_MINE, B_LANG = "➕ Сменный отчёт", "📋 Мои смены", "🌐 Язык"  # русские названия; у операторов есть и узбекские
V_SHIFT, V_MINE, V_LANG = texts.variants("btn_shift"), texts.variants("btn_mine"), texts.variants("btn_lang")
MENU = {B_STOCK, B_FIN, B_GRAPH, B_ACC, B_XLS, B_SHIFTS, B_STAFF, B_SET} | V_SHIFT | V_MINE | V_LANG
MONEY = ["cash", "terminal", "click", "paynet"]


class Shift(StatesGroup):
    step = State()


class SetVal(StatesGroup):
    value = State()


class Stock(StatesGroup):
    liters = State()
    density = State()


def L(uid):
    return core.lang(uid)


def t(uid, key, **kw):
    return texts.tx(L(uid), key, **kw)


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=x, callback_data=c) for x, c in r] for r in rows])


def menu(uid):
    """Кнопки по правам должности: оператор видит своё, владелец и остальные — по своим правам."""
    if role(uid) == "operator":
        rows = [[t(uid, "btn_shift"), t(uid, "btn_mine")], [t(uid, "btn_lang")]]
    else:
        items = [t(uid, "btn_shift")] * can(uid, "shift") + [B_STOCK] * can(uid, "stock_view") + [B_FIN, B_GRAPH, B_XLS] * can(uid, "finance") \
            + [B_SHIFTS] * can(uid, "shifts_view") + [B_ACC] * can(uid, "accounting") + [B_SET] * can(uid, "settings") + [B_STAFF] * can(uid, "staff")
        rows = [items[k:k + 2] for k in range(0, len(items), 2)] or [[t(uid, "btn_lang")]]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x) for x in r] for r in rows], resize_keyboard=True)


# ---------- старт, язык, персонал

@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    core.touch_user(m.from_user)  # запоминаем человека и выдаём заранее назначенную должность по @нику
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith(("p_", "op_", "adm_")) and not is_admin(uid):
        r = core.db.redeem(parts[1].split("_", 1)[1], uid, uname(m.from_user))
        if not r:
            return await m.answer(t(uid, "invite_bad"))
        core.db.log(uname(m.from_user), "invite_used", core.POSITIONS.get(r, r))
        for a in core.ids_with("staff"):
            try:
                await m.bot.send_message(a, f"👤 Новый сотрудник: {uname(m.from_user)} ({uid}) — {core.POSITIONS.get(r, r)}")
            except Exception:
                pass
    r = role(uid)
    if not r:
        return await m.answer(t(uid, "no_access"))
    if r in ("admin", "operator"):
        text = t(uid, "welcome_admin" if r == "admin" else "welcome_op")
    else:
        text = f"Здравствуйте! Ваша должность — {core.POSITIONS[r]}. Доступно: {core.POS_INFO[r]}."
    await m.answer(text, reply_markup=menu(uid))


@router.message(F.text.in_(V_LANG))
async def lang_menu(m: Message):
    if role(m.from_user.id):
        await m.answer(t(m.from_user.id, "lang_pick"), reply_markup=ikb([[("🇷🇺 Русский", "lg:ru"), ("🇺🇿 O'zbekcha", "lg:uz")]]))


@router.callback_query(F.data.startswith("lg:"))
async def lang_set(c: CallbackQuery):
    uid = c.from_user.id
    if role(uid) and c.data[3:] in ("ru", "uz"):
        core.db.set_lang(uid, c.data[3:])
        await c.message.answer(t(uid, "lang_set"), reply_markup=menu(uid))
    await c.answer()


# ---------- сменный отчёт (мастер ввода)

def steps_for(d):
    s = ["date"]
    for k in (1, 2, 3):
        s += [f"m{k}s", f"m{k}e"]
    s.append("arr_kg")
    if (d.get("arr_kg") or 0) > 0:
        s += ["arr_density", "arr_price", "arr_note"]
    return s + MONEY + ["exp", "photos"]


def is_ms(key):
    return key.startswith("m") and len(key) == 3 and key[1].isdigit()


def prompt_for(uid, key, d, prev, edit):
    """Текст шага и кнопки (без «Назад/Отмена»)."""
    keep = [[(t(uid, "b_keep", v=fnum(d[key], 1) if isinstance(d.get(key), (int, float)) else d[key]), "sh:@keep")]] if edit and d.get(key) not in (None, "") and key not in ("exp", "photos") else []
    if key == "date":
        return t(uid, "p_date"), keep + [[(t(uid, "b_today"), "sh:today"), (t(uid, "b_yday"), "sh:yday")]]
    if is_ms(key):
        n = key[1]
        if key.endswith("s"):
            p = prev.get(int(n))
            txt = t(uid, "p_ms", n=n) + (t(uid, "p_ms_prev", v=fnum(p, 1)) if p is not None else "")
            return txt, keep + ([[(t(uid, "b_prev", v=fnum(p, 1)), "sh:@prev")]] if p is not None else [])
        return t(uid, "p_me", n=n, v=fnum(d[f"m{n}s"], 1)), keep + [[(t(uid, "b_skip"), "sh:@skip")]]
    if key == "arr_kg":
        return t(uid, "p_arr_kg"), keep + [[(t(uid, "b_zero_arr"), "sh:@zero")]]
    if key == "arr_density":
        return t(uid, "p_density"), keep + [[("0,54", "sh:@0.54"), ("0,56", "sh:@0.56")]]
    if key == "arr_price":
        pr = cfg()["price_buy_kg"]
        return (t(uid, "p_price") + (t(uid, "p_price_def", v=fnum(pr)) if pr > 0 else ""), keep + ([[(f"✅ {fnum(pr)}", "sh:@default")]] if pr > 0 else []))
    if key == "arr_note":
        return t(uid, "p_note"), [[(t(uid, "b_nonote"), "sh:@skip")]]
    if key in MONEY:
        return t(uid, "p_money", label=t(uid, "m_" + key)), keep + [[("0", "sh:@zero")]]
    if key == "exp":
        total = sum(a for a, _ in d.get("exps", []))
        lines = "\n".join(f"• {fnum(a)} — {c or t(uid, 'exp_nodesc')}" for a, c in d.get("exps", []))
        txt = t(uid, "p_exp") + (t(uid, "p_exp_added", total=fnum(total), lines=lines) if lines else "") + t(uid, "p_exp_tail")
        return txt, [[(t(uid, "b_done" if lines else "b_noexp"), "sh:@done")]]
    n = len(d.get("photos", []))
    return t(uid, "p_photos") + (t(uid, "photos_n", n=n) if n else ""), [[(t(uid, "b_done" if n else "b_nophotos"), "sh:@done")]]


def nav(uid):
    return [(t(uid, "b_back"), "sh:@back"), (t(uid, "b_cancel"), "sh:@cancel")]


async def ask(target, state, uid):
    data = await state.get_data()
    d, i = data["d"], data["i"]
    keys = steps_for(d)
    text, rows = prompt_for(uid, keys[i], d, data["prev"], data.get("edit"))
    await target.answer(t(uid, "step_hdr", i=i + 1, n=len(keys), text=text), reply_markup=ikb(rows + [nav(uid)]))


@router.message(F.text.in_(V_SHIFT))
async def shift_start(m: Message, state: FSMContext):
    if not can(m.from_user.id, "shift"):
        return
    await state.clear()
    await state.set_state(Shift.step)
    await state.update_data(i=0, d={"exps": [], "photos": []}, prev=core.db.last_meter_ends(), edit=None)
    await ask(m, state, m.from_user.id)


def apply_value(uid, key, d, raw, prev):
    """Записывает значение шага в d. Возвращает текст ошибки, 'stay' (остаёмся на шаге) или None."""
    raw = (raw or "").strip()
    if key == "date":
        v = parse_date({"@today": "сегодня", "@yday": "вчера"}.get(raw, raw), today())
        if not v:
            return t(uid, "e_date")
        d["date"] = v
        return None
    if is_ms(key):
        n = int(key[1])
        if key.endswith("s"):
            v = prev.get(n) if raw == "@prev" else parse_num(raw)
            if v is None:
                return t(uid, "e_num_s")
            d[key] = v
            return None
        if raw == "@skip":
            d[key] = d[f"m{n}s"]
            return None
        v = parse_num(raw)
        if v is None:
            return t(uid, "e_num_e")
        if v < d[f"m{n}s"]:
            return t(uid, "e_end_lt", v=fnum(v, 1), s=fnum(d[f"m{n}s"], 1))
        if v - d[f"m{n}s"] > 100000:
            return t(uid, "e_big")
        d[key] = v
        return None
    if key == "arr_kg":
        v = 0.0 if raw == "@zero" else parse_num(raw)
        if v is None or v > 200000:
            return t(uid, "e_arr")
        d["arr_kg"] = v
        return None
    if key == "arr_density":
        v = parse_num(raw[1:] if raw.startswith("@") else raw)
        if v is None or not 0.4 <= v <= 0.7:
            return t(uid, "e_dens")
        d["arr_density"] = v
        return None
    if key == "arr_price":
        v = cfg()["price_buy_kg"] if raw == "@default" else parse_num(raw)
        if not v or v <= 0:
            return t(uid, "e_price")
        d["arr_price"] = v
        return None
    if key == "arr_note":
        d["arr_note"] = "" if raw == "@skip" else raw[:120]
        return None
    if key in MONEY:
        v = 0.0 if raw == "@zero" else parse_num(raw)
        if v is None:
            return t(uid, "e_money")
        d[key] = v
        return None
    if key == "photos":
        return None if raw == "@done" else t(uid, "e_photo")
    if raw == "@done":
        return None
    parts = raw.split(maxsplit=1)
    a = parse_num(parts[0]) if parts else None
    if a is None or a <= 0:
        return t(uid, "e_exp")
    d["exps"].append((a, parts[1].strip() if len(parts) > 1 else ""))
    return "stay"


def shift_summary(d, full, uid):
    """Итог смены. Оператор видит свой язык и только кассу/пролив; владелец — ещё налоги и прибыль."""
    c = cfg()
    r = calc.shift_calc({**d, **{k: d.get(k, 0) for k in ("arr_kg", "arr_density", "arr_price")}}, [a for a, _ in d.get("exps", [])], c)
    dd = f"{d['date'][8:10]}.{d['date'][5:7]}.{d['date'][:4]}"
    lines = [t(uid, "s_title", d=dd), ""]
    for i in range(3):
        a, b = d[f"m{i + 1}s"], d[f"m{i + 1}e"]
        lines.append(t(uid, "s_meter", n=i + 1, a=fnum(a, 1), b=fnum(b, 1), c=fnum(b - a, 1)))
    lines.append(t(uid, "s_total", v=fnum(r["sold_l"], 1)))
    if r["arr_kg"] > 0:
        lines.append(t(uid, "s_arrival", kg=fnum(r["arr_kg"], 1), rho=str(r["arr_density"]).replace(".", ","), l=fnum(r["arr_l"], 1), price=fnum(r["arr_price"])))
        if d.get("arr_note"):
            lines.append(f"📄 {d['arr_note']}")
    lines += ["", t(uid, "s_cash", v=fnum(r["revenue"]))] + [f"   {t(uid, 'm_' + k)}: {fnum(r['pay'][k])}" for k in MONEY if r["pay"][k]]
    if r["expenses"]:
        lines.append(t(uid, "s_exp", v=fnum(r["expenses"])))
    if r["diff"] is not None and r["theory"]:
        pct = 100 * r["diff"] / r["theory"]
        lines.append(t(uid, "s_diff", icon="⚠️" if abs(pct) > 2 else "✔️", theory=fnum(r["theory"]), sign=t(uid, "s_over" if r["diff"] > 0 else "s_short"), v=fnum(abs(r["diff"])), pct=f"{abs(pct):.1f}"))
    if d.get("photos"):
        lines.append(f"📷 {len(d['photos'])}")
    if full:
        net_rev = r["revenue"] - r["vat_out"]
        gas = r["purchase"] - r["vat_in"]
        profit = net_rev - gas - r["excise"] - r["expenses"]
        lines += ["", f"НДС: исходящий {fnum(r['vat_out'])} − входящий {fnum(r['vat_in'])} = {fnum(r['vat_net'])}", f"Акциз: {fnum(r['excise'])}",
                  f"Закуп газа: {fnum(r['purchase'])}", f"✅ Чистая прибыль смены (закуп в день прихода, без постоянных расходов): {fnum(profit)} сум"]
        if c["price_buy_kg"] <= 0 and r["arr_kg"] > 0 and not d.get("arr_price"):
            lines.append("⚠️ Не задана цена закупа — прибыль завышена.")
    return "\n".join(lines)


def meter_warnings(d, prev, uid):
    return [t(uid, "w_gap", n=n, a=fnum(d[f"m{n}s"], 1), b=fnum(prev[n], 1)) for n in (1, 2, 3) if prev.get(n) is not None and abs(d[f"m{n}s"] - prev[n]) > 0.05]


async def finish_wizard(target, state, uid):
    data = await state.get_data()
    d = data["d"]
    warns = [] if data.get("edit") else meter_warnings(d, data["prev"], uid)
    await target.answer(shift_summary(d, can(uid, "finance"), uid) + ("\n\n" + "\n".join(warns) if warns else ""),
                        reply_markup=ikb([[(t(uid, "b_save"), "sh:@save"), (t(uid, "b_redo"), "sh:@redo")], [(t(uid, "b_cancel"), "sh:@cancel")]]))


async def advance(target, state, user, raw):
    uid = user.id
    data = await state.get_data()
    d, i = data["d"], data["i"]
    keys = steps_for(d)
    if i >= len(keys):
        return
    if raw == "@keep":
        err = None
    else:
        err = apply_value(uid, keys[i], d, raw, data["prev"])
    if err and err != "stay":
        await target.answer("❗ " + err)
        return
    if err != "stay":
        i += 1
    await state.update_data(d=d, i=i)
    if i >= len(steps_for(d)):
        return await finish_wizard(target, state, uid)
    await ask(target, state, uid)


@router.message(Shift.step, F.photo)
async def shift_photo(m: Message, state: FSMContext):
    data = await state.get_data()
    d = data.get("d")
    if not d or data["i"] >= len(steps_for(d)) or steps_for(d)[data["i"]] != "photos":
        return
    if len(d["photos"]) < 6:
        d["photos"].append(m.photo[-1].file_id)
    await state.update_data(d=d)
    await ask(m, state, m.from_user.id)


@router.message(Shift.step, ~F.text.in_(MENU))
async def shift_text(m: Message, state: FSMContext):
    await advance(m, state, m.from_user, m.text or "")


@router.callback_query(F.data.startswith("sh:"))
async def shift_button(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    raw = c.data[3:]
    await c.answer()
    if not (can(uid, "shift") or can(uid, "approve")):
        return
    if raw == "@cancel":
        await state.clear()
        return await c.message.edit_text(t(uid, "s_cancelled"))
    data = await state.get_data()
    if "d" not in data:
        return await c.message.edit_text(t(uid, "s_stale"))
    if raw == "@back":
        await state.update_data(i=max(data["i"] - 1, 0))
        return await ask(c.message, state, uid)
    if raw == "@redo":
        await state.update_data(i=0, d={"exps": [], "photos": []} if not data.get("edit") else data["d"])
        return await ask(c.message, state, uid)
    if raw == "@save":
        return await save_shift(c, state)
    await advance(c.message, state, c.from_user, {"today": "@today", "yday": "@yday"}.get(raw, raw))


def approval_kb(sid):
    return ikb([[("✅ Принять", f"ap:{sid}:ok"), ("↩️ Вернуть", f"ap:{sid}:ret")], [("✏️ Исправить", f"ed:{sid}")]])


async def save_shift(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    d, edit = data["d"], data.get("edit")
    uid = c.from_user.id
    await state.clear()
    if edit:
        core.db.update_shift(edit, d, d["exps"], status="approved")
        core.db.log(uname(c.from_user), "shift_edited", f"№{edit}")
        sid = edit
    else:
        sid = core.db.add_shift(uid, uname(c.from_user), d, d["exps"], status="approved" if can(uid, "approve") else "pending")
        core.db.log(uname(c.from_user), "shift_added", f"№{sid} {d['date']}")
    await c.message.edit_text(shift_summary(d, can(uid, "finance"), uid) + "\n\n" + t(uid, "s_saved", id=sid))
    st, fc = core.stock_forecast()
    note = f"⛽ Остаток газа сейчас: {fnum(st['liters'], 0)} л · {fnum(st['kg'], 0)} кг" + (f" · хватит примерно на {fc['days_left']:.1f} дн." if fc["days_left"] else "")
    await c.message.answer(note if can(uid, "stock_view") else t(uid, "s_thanks"), reply_markup=menu(uid))
    if edit:
        return
    for a in core.ids_with("approve") - {uid}:
        try:
            await c.bot.send_message(a, f"📥 Новая смена №{sid} от {uname(c.from_user)}\n\n{shift_summary(d, can(a, "finance"), a)}\n\n{note}",
                                     reply_markup=approval_kb(sid) if not can(uid, "approve") else None)
            for fid in d.get("photos", [])[:6]:
                await c.bot.send_photo(a, fid, caption=f"Смена №{sid}")
        except Exception:
            log.warning("cannot notify %s", a)
    await alert_stock(c.bot, st, fc)


@router.callback_query(F.data.startswith("ap:"))
async def approve_cb(c: CallbackQuery):
    if not can(c.from_user.id, "approve"):
        return await c.answer()
    _, sid, act = c.data.split(":")
    s = core.db.shift(int(sid))
    if not s:
        await c.answer("Смены уже нет", show_alert=True)
        return
    status = "approved" if act == "ok" else "returned"
    core.db.set_status(s["id"], status)
    core.db.log(uname(c.from_user), "shift_" + status, f"№{sid}")
    await c.answer("Принята" if act == "ok" else "Возвращена")
    await c.message.answer(f"{'✅ Принята' if act == 'ok' else '↩️ Возвращена оператору'}: смена №{sid} ({s['user_name']}).")
    try:
        await c.bot.send_message(s["user_id"], texts.tx(core.lang(s["user_id"]), "n_approved" if act == "ok" else "n_returned", id=s["id"]))
    except Exception:
        pass


@router.callback_query(F.data.startswith("ed:"))
async def edit_cb(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    if not can(uid, "approve"):
        return await c.answer()
    s = core.db.shift(int(c.data[3:]))
    await c.answer()
    if not s:
        return await c.message.answer("Такой смены нет.")
    d = {k: s[k] for k in ("date", "m1s", "m1e", "m2s", "m2e", "m3s", "m3e", "cash", "terminal", "click", "paynet", "arr_kg", "arr_density", "arr_price")}
    d["arr_note"] = s.get("arr_note") or ""
    d["photos"] = [x for x in (s.get("photos") or "").split(",") if x]
    d["exps"] = core.db.expenses_of(s["id"])
    await state.clear()
    await state.set_state(Shift.step)
    await state.update_data(i=0, d=d, prev={}, edit=s["id"])
    await c.message.answer(f"✏️ Исправляем смену №{s['id']} ({s['user_name']}). На каждом шаге можно оставить старое значение.")
    await ask(c.message, state, uid)


# ---------- остаток газа, прогноз, замер

@router.message(F.text == B_STOCK)
async def stock(m: Message, state: FSMContext):
    if not can(m.from_user.id, "stock_view"):
        return
    await state.clear()
    c = cfg()
    st, fc = core.stock_forecast()
    has = bool(core.db.adjustments())
    lines = ["⛽ Остаток газа в резервуаре", "", f"{fnum(st['liters'], 0)} л · {fnum(st['kg'], 0)} кг · плотность {str(round(st['density'], 3)).replace('.', ',')} кг/л"]
    if c["tank_capacity_l"] > 0:
        lines.append(f"Заполнено: {100 * st['liters'] / c['tank_capacity_l']:.0f}% от {fnum(c['tank_capacity_l'])} л")
    if fc["days_left"] is not None:
        eta = (today() + timedelta(days=int(fc["days_left"]))).strftime("%d.%m")
        lines.append(f"⏳ Хватит примерно на {fc['days_left']:.1f} дн. (до {eta}), средний пролив {fnum(fc['avg_l'])} л/день")
    if c["low_stock_l"] > 0:
        lines.append("🚨 Ниже порога — пора заказывать газ." if st["liters"] < c["low_stock_l"] else f"Порог заказа: {fnum(c['low_stock_l'])} л")
    lost, gained = calc.loss_total(core.db.adjustments())
    if lost or gained:
        lines.append(f"По замерам за всё время: потери {fnum(lost)} л, излишки {fnum(gained)} л")
    if not has:
        lines.append("\n⚠️ Начальный остаток не задан, поэтому цифры могут быть неверны. Нажмите «Задать остаток» и укажите замер.")
    if st["liters"] < 0:
        lines.append("\n⚠️ Остаток отрицательный: не внесён приход или неверный начальный остаток. Сделайте замер резервуара.")
    await m.answer("\n".join(lines), reply_markup=ikb([[("✏️ Задать остаток (замер)", "sk:set")]]))


@router.callback_query(F.data == "sk:set")
async def stock_set(c: CallbackQuery, state: FSMContext):
    if can(c.from_user.id, "stock_edit"):
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
    before = core.tank_state()
    had = bool(core.db.adjustments())
    sold_since = calc.sold_since_last_adjustment(core.db.shifts(), core.db.adjustments(), cfg())
    rec = calc.reconcile(before["liters"], liters, sold_since, cfg()["loss_norm_pct"]) if had else None
    core.db.add_adjustment(today().isoformat(), liters, liters * density, "замер/начальный остаток" if not had else "замер",
                           uname(user), expected_l=before["liters"] if had else None, expected_kg=before["kg"] if had else None)
    core.db.log(uname(user), "stock_measured", f"{fnum(liters)} л, расчёт был {fnum(before['liters'])} л" if had else f"начальный {fnum(liters)} л")
    lines = [f"✅ Остаток записан: {fnum(liters)} л · {fnum(liters * density)} кг (плотность {str(density).replace('.', ',')}).",
             "Дальше остаток считается сам по сменным отчётам."]
    if rec:
        lines.insert(1, f"Расчётный остаток был {fnum(before['liters'])} л, замер {fnum(liters)} л: "
                        + (f"{'недостача' if rec['diff'] < 0 else 'излишек'} {fnum(abs(rec['diff']))} л" + (f" ({abs(rec['pct']):.1f}%)" if rec["pct"] is not None else "")
                           if abs(rec["diff"]) >= 0.5 else "расхождения нет ✔️"))
        if rec["over_norm"]:
            lines.insert(2, f"🚨 Потери выше нормы: допустимо {fnum(rec['allowed'])} л ({cfg()['loss_norm_pct']:g}% от пролива {fnum(sold_since)} л). Проверьте утечки, счётчики и приходы.")
        elif rec["allowed"] is not None and rec["diff"] < 0:
            lines.insert(2, f"В пределах нормы потерь (допустимо {fnum(rec['allowed'])} л).")
    await target.answer("\n".join(lines))


@router.callback_query(F.data.startswith("sd:"))
async def stock_density_cb(c: CallbackQuery, state: FSMContext):
    if can(c.from_user.id, "stock_edit") and "liters" in await state.get_data():
        await stock_save(c.message, state, c.from_user, float(c.data[3:]))
    await c.answer()


@router.message(Stock.density, ~F.text.in_(MENU))
async def stock_density_text(m: Message, state: FSMContext):
    v = parse_num(m.text)
    if v is None or not 0.4 <= v <= 0.7:
        return await m.answer("Плотность от 0,4 до 0,7 (обычно 0,54 или 0,56).")
    await stock_save(m, state, m.from_user, v)


# ---------- финансы, графики, Excel

PERIODS = [("Сегодня", "today"), ("Вчера", "yday"), ("Неделя", "week"), ("Месяц", "month"), ("Всё время", "all")]


@router.message(F.text == B_FIN)
async def fin_menu(m: Message):
    if can(m.from_user.id, "finance"):
        await m.answer("За какой период показать финансы?", reply_markup=ikb([[(n, f"fp:{c}") for n, c in PERIODS[:3]], [(n, f"fp:{c}") for n, c in PERIODS[3:]],
                                                                               [("↔️ Неделя к неделе", "cmp:week"), ("↔️ Месяц к месяцу", "cmp:month")]]))


@router.callback_query(F.data.startswith("fp:"))
async def fin_cb(c: CallbackQuery):
    if can(c.from_user.id, "finance"):
        s, e, label = core.period_range(c.data[3:])
        await c.message.answer(finance_text(core.rows_for(s, e), label, s, e))
    await c.answer()


@router.message(F.text == B_GRAPH)
async def graph_menu(m: Message):
    if not can(m.from_user.id, "finance"):
        return
    await m.answer("📈 Какой график построить?", reply_markup=ikb([[(name, f"gk:{k}")] for k, name in charts.KINDS.items()]))


@router.message(Command("graph"))
async def graph_cmd(m: Message):
    await graph_menu(m)


@router.callback_query(F.data.startswith("gk:"))
async def graph_kind(c: CallbackQuery):
    if can(c.from_user.id, "finance"):
        k = c.data[3:]
        await c.message.answer(f"«{charts.KINDS[k]}»: за какой период?",
                               reply_markup=ikb([[("За неделю", f"gp:{k}:week"), ("За месяц", f"gp:{k}:month"), ("За всё время", f"gp:{k}:all")]]))
    await c.answer()


@router.callback_query(F.data.startswith("gp:"))
async def graph_make(c: CallbackQuery):
    if not can(c.from_user.id, "finance"):
        return await c.answer()
    _, kind, code = c.data.split(":")
    await c.answer("Строю график…")
    s, e, label = core.period_range(code)
    rows = core.rows_for(s, e)
    extra = None
    if kind == "buy_price":
        extra = [{"date": x["date"], "price": x["arr_price"], "kg": x["arr_kg"]} for x in core.db.shifts(s, e) if x["arr_kg"] and x["arr_price"]]
    if not any(r["shifts"] for r in rows) and kind not in ("stock",):
        return await c.message.answer("За этот период нет смен — график строить не из чего.")
    png = await asyncio.to_thread(charts.render, kind, rows, cfg(), label, extra)
    tot = calc.totals(rows)
    cap = {"sales": f"Пролив: {fnum(tot['sold_l'])} л", "revenue": f"Выручка: {fnum(tot['revenue'])} сум",
           "expenses": f"Закуп {fnum(tot['purchase'])} · расходы {fnum(tot['expenses'])} сум", "profit": f"Чистая прибыль: {fnum(tot['profit'])} сум",
           "stock": "Остаток газа по дням", "weekday": "Средний пролив по дням недели", "structure": f"Принято: {fnum(tot['revenue'])} сум",
           "buy_price": "История цены закупа"}[kind]
    await c.message.answer_photo(BufferedInputFile(png, f"{kind}.png"), caption=f"{charts.KINDS[kind]} · {label}\n{cap}")


@router.message(F.text == B_XLS)
async def xls_menu(m: Message):
    if can(m.from_user.id, "finance"):
        await m.answer("Выгрузить сводную таблицу в Excel:", reply_markup=ikb([[("За всё время", "xl:all"), ("За 30 дней", "xl:month")]]))


@router.message(Command("export"))
async def export_cmd(m: Message):
    await xls_menu(m)


@router.callback_query(F.data.startswith("xl:"))
async def xls_make(c: CallbackQuery):
    if not can(c.from_user.id, "finance"):
        return await c.answer()
    await c.answer("Готовлю файл…")
    data, name, label = await build_excel(c.data[3:])
    await c.message.answer_document(BufferedInputFile(data, name), caption=f"📤 Сводная таблица · {label}")


# ---------- смены: список, удаление, мои смены

@router.message(F.text == B_SHIFTS)
async def shifts_list(m: Message):
    if not can(m.from_user.id, "shifts_view"):
        return
    sh = core.db.shifts()[-10:][::-1]
    if not sh:
        return await m.answer("Смен пока нет.")
    c = cfg()
    lines, rows = ["🧾 Последние смены:"], []
    for s in sh:
        r = calc.shift_calc(s, [], c)
        lines.append(f"{texts.ST_ICON.get(s.get('status') or 'approved', '')} №{s['id']} · {s['date'][8:10]}.{s['date'][5:7]} · {s['user_name']} · {fnum(r['sold_l'])} л · {fnum(r['revenue'])} сум")
        rows.append([(f"✏️ №{s['id']}", f"ed:{s['id']}"), (f"🗑 №{s['id']}", f"ds:{s['id']}")] + ([("✅", f"ap:{s['id']}:ok")] if s.get("status") == "pending" else []))
    await m.answer("\n".join(lines) + "\n\n⏳ ждёт проверки · ✅ принята · ↩️ возвращена", reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("ds:"))
async def shift_delete(c: CallbackQuery):
    if not can(c.from_user.id, "approve"):
        return await c.answer()
    sid = int(c.data[3:])
    await c.answer()
    await c.message.edit_text(f"Удалить смену №{sid}? Остаток и финансы пересчитаются. Это нельзя отменить.",
                              reply_markup=ikb([[("🗑 Да, удалить", f"dy:{sid}"), ("Отмена", "dn")]]))


@router.callback_query(F.data.startswith("dy:"))
async def shift_delete_yes(c: CallbackQuery):
    if can(c.from_user.id, "approve"):
        sid = int(c.data[3:])
        s = core.db.shift(sid)
        n = core.db.delete_shift(sid)
        if n:
            core.db.log(uname(c.from_user), "shift_deleted", f"№{sid} {s['date']} {s['user_name']}")
        await c.message.edit_text("🗑 Смена удалена." if n else "Такой смены уже нет.")
    await c.answer()


@router.callback_query(F.data == "dn")
async def delete_no(c: CallbackQuery):
    await c.message.edit_text("Отменено.")
    await c.answer()


@router.message(F.text.in_(V_MINE))
async def my_shifts(m: Message):
    uid = m.from_user.id
    if not role(uid):
        return
    sh = core.db.shifts(uid=uid)[-8:][::-1]
    if not sh:
        return await m.answer(t(uid, "mine_empty"))
    c = cfg()
    rows = [t(uid, "mine_row", id=s["id"], d=f"{s['date'][8:10]}.{s['date'][5:7]}", l=fnum(calc.shift_calc(s, [], c)["sold_l"]),
              v=fnum(calc.shift_calc(s, [], c)["revenue"]), st=texts.ST_ICON.get(s.get("status") or "approved", "")) for s in sh]
    await m.answer(t(uid, "mine_hdr") + "\n" + "\n".join(rows))


# ---------- настройки

SETTINGS = [("sale_price_l", "Цена продажи, сум/л"), ("price_buy_kg", "Цена закупа, сум/кг"), ("vat_rate", "НДС, %"), ("excise_per_l", "Акциз, сум/л"),
            ("default_density", "Плотность по умолчанию"), ("tank_capacity_l", "Ёмкость резервуара, л"), ("low_stock_l", "Порог остатка, л"),
            ("order_days", "Предупреждать, если газа меньше чем на N дней"), ("loss_norm_pct", "Норма потерь, % от пролива"),
            ("tax_due_day", "День срока сдачи налогов (число месяца)"), ("report_deadline", "Срок сдачи отчёта операторами (ЧЧ:ММ, 0 = выключено)")]


def settings_text():
    c = cfg()
    lines = ["⚙️ Настройки учёта", ""]
    for k, name in SETTINGS:
        v = c[k]
        lines.append(f"{name}: {v if isinstance(v, str) else fnum(v, 2 if v % 1 else 0)}")
    lines.append(f"Цена закупа задана: {'с НДС' if c['buy_price_with_vat'] else 'без НДС'}")
    lines.append(f"Прибыль считать: {'по закупу в день прихода' if c['profit_mode'] != 'cogs' else 'по себестоимости проданного газа'}")
    lines.append(f"Ежедневный отчёт владельцу: {'включён' if c['auto_daily'] else 'выключен'}")
    return "\n".join(lines)


def settings_kb():
    rows = [[(f"✏️ {name}", f"se:{k}")] for k, name in SETTINGS]
    rows += [[("🔁 Цена закупа: с НДС / без НДС", "se:@vat")], [("🔁 Режим прибыли: закуп / себестоимость", "se:@mode")], [("🔁 Ежедневный отчёт: вкл / выкл", "se:@auto")]]
    return ikb(rows)


@router.message(F.text == B_SET)
async def settings(m: Message, state: FSMContext):
    if can(m.from_user.id, "settings"):
        await state.clear()
        await m.answer(settings_text(), reply_markup=settings_kb())


@router.callback_query(F.data.startswith("se:"))
async def settings_cb(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "settings"):
        return await c.answer()
    k = c.data[3:]
    await c.answer()
    toggles = {"@vat": ("buy_price_with_vat", lambda: 0 if cfg()["buy_price_with_vat"] else 1),
               "@mode": ("profit_mode", lambda: "cogs" if cfg()["profit_mode"] != "cogs" else "purchases"),
               "@auto": ("auto_daily", lambda: 0 if cfg()["auto_daily"] else 1)}
    if k in toggles:
        key, fn = toggles[k]
        val = fn()
        core.db.set_setting(key, val)
        core.db.log(uname(c.from_user), "setting_changed", f"{key}={val}")
        return await c.message.edit_text(settings_text(), reply_markup=settings_kb())
    await state.set_state(SetVal.value)
    await state.update_data(key=k)
    await c.message.answer(f"Новое значение: {dict(SETTINGS)[k]}" + (" (например 21:00 или 0)." if k == "report_deadline" else " (число)."))


def parse_setting(k, text):
    if k == "report_deadline":
        s = (text or "").strip()
        if s == "0":
            return "0"
        import re
        mm = re.fullmatch(r"(\d{1,2})[:.](\d{2})", s)
        return f"{int(mm.group(1)):02d}:{mm.group(2)}" if mm and int(mm.group(1)) < 24 and int(mm.group(2)) < 60 else None
    v = parse_num(text)
    if v is None or (k == "vat_rate" and v > 100) or (k == "default_density" and not 0.4 <= v <= 0.7) or (k == "tax_due_day" and not 1 <= v <= 28):
        return None
    return v


@router.message(SetVal.value, ~F.text.in_(MENU))
async def settings_value(m: Message, state: FSMContext):
    if not can(m.from_user.id, "settings"):
        return
    k = (await state.get_data())["key"]
    v = parse_setting(k, m.text)
    if v is None:
        return await m.answer("Не понял значение (для плотности от 0,4 до 0,7, для НДС до 100, для дня срока 1–28, для времени ЧЧ:ММ).")
    core.db.set_setting(k, v)
    core.db.log(uname(m.from_user), "setting_changed", f"{k}={v}")
    await state.clear()
    await m.answer("✅ Сохранено.\n\n" + settings_text(), reply_markup=settings_kb())


@router.message()
async def fallback(m: Message):
    uid = m.from_user.id
    if role(uid):
        await m.answer(t(uid, "press_btn"), reply_markup=menu(uid))
    else:
        await m.answer(t(uid, "no_access"))


@router.error()
async def on_error(event):
    log.exception("handler error: %s", event.exception)
    return True


class TouchMiddleware(BaseMiddleware):
    """Каждое сообщение: запоминаем пользователя и выдаём заранее назначенную должность."""

    async def __call__(self, handler, event, data):
        try:
            core.touch_user(getattr(event, "from_user", None))
        except Exception:
            log.exception("touch_user failed")
        return await handler(event, data)


async def main():
    import extras
    import scheduler
    import staff
    logging.basicConfig(level=logging.INFO)
    proxy = os.getenv("HTTPS_PROXY") if os.getenv("USE_PROXY") else None
    bot = Bot(os.environ["BOT_TOKEN"], session=AiohttpSession(proxy=proxy) if proxy else None, default=DefaultBotProperties(parse_mode=None))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(TouchMiddleware())
    dp.callback_query.outer_middleware(TouchMiddleware())
    dp.include_router(staff.router)
    dp.include_router(extras.router)  # раньше общего «fallback»
    dp.include_router(router)
    asyncio.create_task(scheduler.run(bot))
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
