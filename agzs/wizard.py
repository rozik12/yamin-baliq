"""Мастер ввода сменного отчёта, проверка смены владельцем (принять / вернуть / исправить) и сохранение."""
import logging
from datetime import timedelta  # noqa: F401

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import calc
import core
import texts
from calc import fnum, parse_date, parse_num
from core import can, cfg, role, today, uname
import reports
from reports import alert_stock
from ui import MENU, MONEY, V_SHIFT, ikb, menu, t

router = Router()
log = logging.getLogger("agzs")


class Shift(StatesGroup):
    step = State()



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
    r = calc.shift_calc({**d, **{k: d.get(k, 0) for k in ("arr_kg", "arr_density", "arr_price")}}, [a for a, _ in d.get("exps", [])], c, core.price_lookup()(d["date"]))
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
        lines.append(t(uid, "s_diff", icon="⚠️" if abs(pct) > (c["cash_alert_pct"] or 2) else "✔️", theory=fnum(r["theory"]), sign=t(uid, "s_over" if r["diff"] > 0 else "s_short"), v=fnum(abs(r["diff"])), pct=f"{abs(pct):.1f}"))
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


def check_flags(d, edit_id=None):
    """Подозрительные цифры в смене: сравнение с обычными днями, ценой, датой, дубликатами."""
    existing = [x for x in core.db.shifts(d["date"], d["date"]) if x["id"] != edit_id]
    return calc.shift_flags(d, core.rows_for(None, None), cfg(), today(), core.price_lookup()(d["date"]), existing)


async def finish_wizard(target, state, uid):
    data = await state.get_data()
    d = data["d"]
    warns = [] if data.get("edit") else meter_warnings(d, data["prev"], uid)
    flags = check_flags(d, data.get("edit"))
    if flags:
        warns += [t(uid, "flags_hdr")] + [f"• {f}" for f in flags]
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
    return ikb([[("✅ Принять", f"ap:{sid}:ok"), ("↩️ Вернуть", f"rr:{sid}")], [("✏️ Исправить", f"ed:{sid}"), ("🔎 Подробно", f"sd:{sid}")]])


async def save_shift(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    d, edit = data["d"], data.get("edit")
    uid = c.from_user.id
    if (core.month_locked(d["date"]) or (edit and core.month_locked((core.db.shift(edit) or {}).get("date", "")))) and not can(uid, "settings"):
        await state.clear()
        return await c.message.edit_text(t(uid, "locked"))
    rows_before = None if edit else core.rows_for(None, None)
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
    marks = [] if edit else reports.milestones(rows_before, core.rows_for(None, None), d["date"], cfg())
    flags = check_flags(d, sid)
    tail = ("\n\n" + "\n".join(marks)) if marks else ""
    await c.message.answer((note + tail) if can(uid, "stock_view") else t(uid, "s_thanks"), reply_markup=menu(uid))
    if not edit and not can(uid, "approve"):
        await c.message.answer(t(uid, "undo_hint"), reply_markup=ikb([[(t(uid, "undo_btn"), f"un:{sid}")]]))
    if edit:
        return
    for a in core.ids_with("approve", "shifts") - {uid}:
        try:
            await c.bot.send_message(a, f"📥 Новая смена №{sid} от {uname(c.from_user)}\n\n{shift_summary(d, can(a, "finance"), a)}"
                                     + (("\n\n⚠️ Проверьте:\n" + "\n".join(f"• {f}" for f in flags)) if flags else "") + f"\n\n{note}" + tail,
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
    if core.month_locked(s["date"]) and not can(uid, "settings"):
        return await c.message.answer("🔒 Этот месяц закрыт, править смену может только владелец.")
    d = {k: s[k] for k in ("date", "m1s", "m1e", "m2s", "m2e", "m3s", "m3e", "cash", "terminal", "click", "paynet", "arr_kg", "arr_density", "arr_price")}
    d["arr_note"] = s.get("arr_note") or ""
    d["photos"] = [x for x in (s.get("photos") or "").split(",") if x]
    d["exps"] = core.db.expenses_of(s["id"])
    await state.clear()
    await state.set_state(Shift.step)
    await state.update_data(i=0, d=d, prev={}, edit=s["id"])
    await c.message.answer(f"✏️ Исправляем смену №{s['id']} ({s['user_name']}). На каждом шаге можно оставить старое значение.")
    await ask(c.message, state, uid)



EXPORTS = ['Shift', 'advance', 'apply_value', 'approval_kb', 'approve_cb', 'ask', 'edit_cb', 'finish_wizard', 'is_ms', 'meter_warnings', 'nav', 'prompt_for', 'save_shift', 'shift_button', 'shift_photo', 'shift_start', 'shift_summary', 'shift_text', 'steps_for']
