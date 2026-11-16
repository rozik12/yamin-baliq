"""Раздел «💼 Бухгалтерия»: налоговый отчёт, поставщик и долг, инкассация и касса, постоянные расходы,
рейтинг операторов, прогноз и заявка на газ, журнал действий, сравнение периодов."""
import re
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import calc
import core
from calc import fnum, parse_num
from core import can, today, uname
from ui import MENU

router = Router()
B_ACC = "💼 Бухгалтерия"
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


class Pay(StatesGroup):
    line = State()


class TaxPay(StatesGroup):
    line = State()


class Coll(StatesGroup):
    line = State()


class Fix(StatesGroup):
    line = State()


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=x, callback_data=c) for x, c in r] for r in rows])


def dm(d):
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}"


def split_amount(text):
    """«2 000 000 за цистерну» -> (2000000, 'за цистерну'); None, если суммы нет."""
    parts = (text or "").strip().split(maxsplit=1)
    # сумма может быть записана с пробелами между тысячами: «2 000 000 оплата»
    m = re.match(r"^((?:\d[\d.,]*)(?:\s\d{3})*)\s*(.*)$", (text or "").strip())
    if not m:
        return None
    amount = parse_num(m.group(1))
    if amount is None or amount <= 0:
        return None
    return amount, m.group(2).strip()


@router.message(F.text == B_ACC)
async def acc_menu(m: Message, state: FSMContext):
    if not can(m.from_user.id, "accounting"):
        return
    await state.clear()
    await m.answer("💼 Бухгалтерия", reply_markup=ikb([
        [("📑 Налоговый отчёт", "ac:tax"), ("🚚 Поставщик и долг", "ac:sup")],
        [("💵 Инкассация и касса", "ac:cash"), ("🏢 Постоянные расходы", "ac:fix")],
        [("👷 Операторы", "ac:ops"), ("🔮 Прогноз и заявка", "ac:fc")],
        [("📜 Журнал действий", "ac:log"), ("🗓 Календарь налогов", "ac:cal")],
        [("💳 Уплата налогов", "ac:tp"), ("🔒 Закрытие месяца", "ac:lock")],
        [("📥 Импорт из Excel", "im:start")]]))


@router.callback_query(F.data.startswith("ac:"))
async def acc_cb(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "accounting"):
        return await c.answer()
    await c.answer()
    act = c.data[3:]
    handler = {"tax": tax_menu, "sup": supplier, "cash": cash, "fix": fixed, "ops": ops_menu, "fc": forecast_screen, "log": journal,
               "cal": tax_calendar, "tp": tax_paid, "lock": lock_menu}[act]
    await handler(c.message)


# ---------- налоги

def month_buttons():
    t = today()
    rows, d = [], t.replace(day=1)
    months = []
    for _ in range(6):
        months.append(d)
        d = (d - timedelta(days=1)).replace(day=1)
    rows = [[(f"{MONTHS[x.month - 1].capitalize()} {x.year}", f"tx:{x.year}-{x.month:02d}") for x in months[i:i + 2]] for i in range(0, 6, 2)]
    q = (t.month - 1) // 3 + 1
    pq = q - 1 or 4
    py = t.year if q > 1 else t.year - 1
    rows.append([(f"{q} кв. {t.year}", f"tq:{t.year}-{q}"), (f"{pq} кв. {py}", f"tq:{py}-{pq}")])
    return rows


async def tax_menu(msg):
    await msg.answer("📑 Налоговый отчёт: за какой период?", reply_markup=ikb(month_buttons()))


def tax_text(start, end, title):
    rows = core.rows_for(start, end)
    tot = calc.totals(rows)
    c = core.cfg()
    if not tot["shifts"]:
        return f"📑 Налоги · {title}\n\nСмен за этот период нет."
    pay = max(tot["vat_net"], 0) + tot["excise"]
    due_day = int(c["tax_due_day"])
    y, mth = int(end[:4]), int(end[5:7])
    ny, nm = (y + 1, 1) if mth == 12 else (y, mth + 1)
    lines = [f"📑 Налоги · {title}", "", f"Выручка: {fnum(tot['revenue'])} сум", f"Пролив: {fnum(tot['sold_l'])} л", "",
             f"НДС {c['vat_rate']:g}% исходящий (с выручки): {fnum(tot['vat_out'])}", f"НДС входящий (с закупа газа): {fnum(tot['vat_in'])}",
             f"НДС {'к уплате' if tot['vat_net'] >= 0 else 'к зачёту (переплата)'}: {fnum(abs(tot['vat_net']))}", f"Акциз ({fnum(c['excise_per_l'], 2)} сум/л × {fnum(tot['sold_l'])} л): {fnum(tot['excise'])}",
             "", f"💳 ИТОГО к уплате за период: {fnum(pay)} сум", f"⏰ Срок: до {due_day} {MONTHS_GEN[nm - 1]} {ny}"]
    if c["excise_per_l"] <= 0:
        lines.append("\nℹ️ Акциз сейчас 0: если на вашу деятельность он начисляется, задайте ставку в «⚙️ Настройки».")
    if tot["vat_net"] < 0:
        lines.append("ℹ️ Входящий НДС больше исходящего (крупный закуп): переплата зачтётся в следующих периодах.")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("tx:"))
async def tax_month(c: CallbackQuery):
    if can(c.from_user.id, "accounting"):
        y, mth = map(int, c.data[3:].split("-"))
        s, e = calc.month_bounds(y, mth)
        await c.message.answer(tax_text(s, e, f"{MONTHS[mth - 1]} {y}"))
    await c.answer()


@router.callback_query(F.data.startswith("tq:"))
async def tax_quarter(c: CallbackQuery):
    if can(c.from_user.id, "accounting"):
        y, q = map(int, c.data[3:].split("-"))
        s, _ = calc.month_bounds(y, 3 * q - 2)
        _, e = calc.month_bounds(y, 3 * q)
        await c.message.answer(tax_text(s, e, f"{q} квартал {y}"))
    await c.answer()


# ---------- поставщик и долг

def price_stats(shifts):
    pr = [(s["date"], s["arr_price"]) for s in shifts if s["arr_kg"] and s["arr_price"]]
    if not pr:
        return None
    vals = [p for _, p in pr]
    return {"last": vals[-1], "avg": sum(vals) / len(vals), "min": min(vals), "max": max(vals), "n": len(vals)}


async def supplier(msg):
    shifts = core.db.shifts()
    pays = core.db.supplier_payments()
    bought, paid, debt = calc.supplier_balance(shifts, pays)
    lines = ["🚚 Поставщик газа", "", f"Закуплено всего: {fnum(bought)} сум ({fnum(sum(s['arr_kg'] for s in shifts))} кг)", f"Оплачено поставщику: {fnum(paid)} сум",
             f"{'🔴 Долг' if debt > 0 else '🟢 Переплата'}: {fnum(abs(debt))} сум"]
    ps = price_stats(shifts)
    if ps:
        lines += ["", f"Цена закупа за кг: последняя {fnum(ps['last'])}, средняя {fnum(ps['avg'])}, мин {fnum(ps['min'])}, макс {fnum(ps['max'])} (приходов: {ps['n']})"]
    if pays:
        lines += ["", "Последние оплаты:"] + [f"• {dm(p['date'])} — {fnum(p['amount'])} сум {p['note'] or ''}".rstrip() for p in pays[-5:][::-1]]
    lines.append("\nУчёт ведётся по приходам, внесённым в сменных отчётах. График цен: «📈 Графики → Цена закупа газа».")
    await msg.answer("\n".join(lines), reply_markup=ikb([[("➕ Записать оплату поставщику", "pay:new")]]))


@router.callback_query(F.data == "pay:new")
async def pay_new(c: CallbackQuery, state: FSMContext):
    if can(c.from_user.id, "accounting"):
        await state.set_state(Pay.line)
        await c.message.answer("Напишите сумму оплаты и, если хотите, комментарий: «2000000 за цистерну».")
    await c.answer()


@router.message(Pay.line)
async def pay_line(m: Message, state: FSMContext):
    if not can(m.from_user.id, "accounting"):
        return
    r = split_amount(m.text)
    if not r:
        return await m.answer("Нужна сумма, например «2000000 за цистерну».")
    core.db.add_supplier_payment(today().isoformat(), r[0], r[1], uname(m.from_user))
    core.db.log(uname(m.from_user), "supplier_payment", f"{fnum(r[0])} {r[1]}")
    await state.clear()
    _, _, debt = calc.supplier_balance(core.db.shifts(), core.db.supplier_payments())
    await m.answer(f"✅ Оплата записана: {fnum(r[0])} сум. {'Долг поставщику' if debt > 0 else 'Переплата'}: {fnum(abs(debt))} сум.")


# ---------- касса и инкассация

async def cash(msg):
    shifts = core.db.shifts()
    cols = core.db.collections()
    got, spent, taken, left = calc.cash_on_hand(shifts, core.db.expenses_map(), cols)
    lines = ["💵 Наличные в кассе (по данным бота)", "", f"Принято наличными: {fnum(got)}", f"Расходы на месте: −{fnum(spent)}", f"Инкассация: −{fnum(taken)}",
             f"🟰 В кассе должно быть: {fnum(left)} сум"]
    if cols:
        lines += ["", "Последние инкассации:"] + [f"• {dm(x['date'])} — {fnum(x['amount'])} сум {x['note'] or ''}".rstrip() for x in cols[-5:][::-1]]
    lines.append("\nКогда забираете наличные из кассы, запишите это — остаток будет сходиться с реальным.")
    await msg.answer("\n".join(lines), reply_markup=ikb([[("➕ Записать инкассацию", "col:new")]]))


@router.callback_query(F.data == "col:new")
async def col_new(c: CallbackQuery, state: FSMContext):
    if can(c.from_user.id, "accounting"):
        await state.set_state(Coll.line)
        await c.message.answer("Напишите сумму, которую забрали из кассы, и комментарий: «5000000 сдано в банк».")
    await c.answer()


@router.message(Coll.line)
async def col_line(m: Message, state: FSMContext):
    if not can(m.from_user.id, "accounting"):
        return
    r = split_amount(m.text)
    if not r:
        return await m.answer("Нужна сумма, например «5000000 сдано в банк».")
    core.db.add_collection(today().isoformat(), r[0], r[1], uname(m.from_user))
    core.db.log(uname(m.from_user), "cash_collection", f"{fnum(r[0])} {r[1]}")
    await state.clear()
    _, _, _, left = calc.cash_on_hand(core.db.shifts(), core.db.expenses_map(), core.db.collections())
    await m.answer(f"✅ Инкассация записана: {fnum(r[0])} сум. В кассе должно остаться: {fnum(left)} сум.")


# ---------- постоянные расходы

async def fixed(msg):
    items = core.db.fixed_list()
    total = sum(i["amount"] for i in items)
    lines = ["🏢 Постоянные расходы (в месяц)", ""] + ([f"• {i['name']}: {fnum(i['amount'])} сум" for i in items] or ["Пока ничего не добавлено."])
    if items:
        lines += ["", f"Всего в месяц: {fnum(total)} сум (≈ {fnum(total / 30.4)} в день)"]
    lines.append("\nАренда, зарплаты, свет, охрана и т.п. Они делятся по дням месяца и вычитаются из чистой прибыли, графики и отчёты считаются уже с ними.")
    rows = [[(f"🗑 {i['name']}", f"fx:rm:{i['id']}")] for i in items] + [[("➕ Добавить расход", "fx:new")]]
    await msg.answer("\n".join(lines), reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("fx:"))
async def fix_cb(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "accounting"):
        return await c.answer()
    act = c.data[3:]
    if act == "new":
        await state.set_state(Fix.line)
        await c.message.answer("Напишите название и сумму в месяц: «Аренда 5000000».")
    elif act.startswith("rm:"):
        core.db.remove_fixed(int(act[3:]))
        core.db.log(uname(c.from_user), "fixed_removed", act[3:])
        await c.message.edit_text("Убрано.")
    await c.answer()


@router.message(Fix.line)
async def fix_line(m: Message, state: FSMContext):
    if not can(m.from_user.id, "accounting"):
        return
    mm = re.match(r"^(.*?)\s*((?:\d[\d.,]*)(?:\s\d{3})*)$", (m.text or "").strip())
    amount = parse_num(mm.group(2)) if mm else None
    if not mm or not mm.group(1) or not amount or amount <= 0:
        return await m.answer("Формат: название и сумма в месяц, например «Аренда 5000000».")
    core.db.add_fixed(mm.group(1).strip(), amount)
    core.db.log(uname(m.from_user), "fixed_added", f"{mm.group(1).strip()} {fnum(amount)}")
    await state.clear()
    await m.answer(f"✅ Добавлено: {mm.group(1).strip()} — {fnum(amount)} сум в месяц.")


# ---------- операторы

async def ops_menu(msg):
    await msg.answer("👷 Рейтинг операторов: за какой период?", reply_markup=ikb([[("Неделя", "op:week"), ("Месяц", "op:month"), ("Всё время", "op:all")]]))


@router.callback_query(F.data.startswith("op:"))
async def ops_cb(c: CallbackQuery):
    if not can(c.from_user.id, "accounting"):
        return await c.answer()
    s, e, label = core.period_range(c.data[3:])
    stats = calc.operator_stats(core.db.shifts(s, e), core.db.expenses_map(), core.cfg())
    if not stats:
        await c.message.answer(f"👷 За период «{label}» смен нет.")
        return await c.answer()
    lines = [f"👷 Операторы · {label}", "Сверху те, у кого хуже сходится касса со счётчиками.", ""]
    for o in stats:
        line = f"• {o['name']}: смен {o['shifts']}, пролив {fnum(o['sold_l'])} л, касса {fnum(o['revenue'])} сум"
        if o["with_price"]:
            line += f"\n   расхождение с расчётом: {'+' if o['diff'] >= 0 else '−'}{fnum(abs(o['diff']))} сум ({o['diff_pct']:+.1f}%)"
        lines.append(line)
    if not any(o["with_price"] for o in stats):
        lines.append("\nℹ️ Чтобы видеть расхождения кассы, задайте цену продажи в «⚙️ Настройки».")
    await c.message.answer("\n".join(lines))
    await c.answer()


# ---------- прогноз и заявка поставщику

async def forecast_screen(msg):
    c = core.cfg()
    st, fc = core.stock_forecast()
    t = today()
    lines = ["🔮 Прогноз", "", f"⛽ Сейчас в резервуаре: {fnum(st['liters'], 0)} л · {fnum(st['kg'], 0)} кг"]
    if fc["days_left"] is not None:
        eta = (t + timedelta(days=int(fc["days_left"]))).strftime("%d.%m")
        lines.append(f"⏳ Хватит примерно на {fc['days_left']:.1f} дн. (до {eta}); средний пролив {fnum(fc['avg_l'])} л в день")
    else:
        lines.append("⏳ Пока мало данных, чтобы оценить расход.")
    rows_m = core.rows_for(t.replace(day=1).isoformat(), t.isoformat())
    mf = calc.month_forecast(rows_m, t)
    if mf:
        lines += ["", f"💰 Выручка к концу месяца при текущем темпе: ≈ {fnum(mf['revenue_forecast'])} сум", f"✅ Чистая прибыль к концу месяца: ≈ {fnum(mf['profit_forecast'])} сум (сейчас {fnum(mf['profit'])} за {mf['elapsed']} дн.)"]
    tot = calc.totals(rows_m)
    u = calc.unit_economics(tot, core.fixed_monthly(), len(rows_m))
    if u["margin_l"] is not None and u["breakeven_l_day"]:
        lines.append(f"📐 Безубыточность: {fnum(u['breakeven_l_day'])} л в день (сейчас {fnum(u['avg_l_day'])}), маржа {fnum(u['margin_l'], 0)} сум/л до постоянных расходов")
    kg, l = calc.order_suggestion(st["liters"], st["kg"], c, fc["avg_l"])
    if kg > 0:
        basis = f"до заполнения резервуара ({fnum(c['tank_capacity_l'])} л)" if c["tank_capacity_l"] > 0 else "на 7 дней продаж"
        lines += ["", f"🚚 Сколько заказать ({basis}): ≈ {fnum(kg)} кг (≈ {fnum(l)} л)", "", "Готовая заявка — перешлите поставщику:",
                  f"«Здравствуйте! Просим доставить газ ориентировочно {fnum(kg)} кг (≈ {fnum(l)} л) на АГЗС Navro'z Propan. Остаток в резервуаре сейчас {fnum(st['liters'], 0)} л. Подтвердите, пожалуйста, дату и время доставки.»"]
    await msg.answer("\n".join(lines))


# ---------- журнал

async def journal(msg):
    log = core.db.audit(25)
    if not log:
        return await msg.answer("📜 Журнал пока пуст.")
    await msg.answer("📜 Последние действия:\n\n" + "\n".join(f"{x['ts'][5:16].replace('T', ' ')} · {x['user_name']} · {x['action']} {x['details'] or ''}".rstrip() for x in log))


# ---------- сравнение периодов

def arrow(p):
    return "—" if p is None else ("▲" if p > 0.05 else "▼" if p < -0.05 else "＝")


@router.callback_query(F.data.startswith("cmp:"))
async def compare_cb(c: CallbackQuery):
    if not can(c.from_user.id, "finance"):
        return await c.answer()
    n = 7 if c.data[4:] == "week" else 30
    t = today()
    cur_s, cur_e = (t - timedelta(days=n - 1)).isoformat(), t.isoformat()
    prev_s, prev_e = (t - timedelta(days=2 * n - 1)).isoformat(), (t - timedelta(days=n)).isoformat()
    cur, prev = calc.totals(core.rows_for(cur_s, cur_e)), calc.totals(core.rows_for(prev_s, prev_e))
    lines = [f"↔️ {'Неделя к неделе' if n == 7 else 'Месяц к месяцу'}", f"Сейчас: {dm(cur_s)} — {dm(cur_e)}", f"Раньше: {dm(prev_s)} — {dm(prev_e)}", ""]
    for name, a, b, d, p in calc.compare_totals(prev, cur):
        pct = f" ({p:+.1f}%)" if p is not None else ""
        lines.append(f"{arrow(p)} {name}: {fnum(a)} → {fnum(b)}{pct}")
    if not prev["shifts"]:
        lines.append("\nℹ️ В прошлом периоде смен не было, сравнивать пока не с чем.")
    await c.message.answer("\n".join(lines))
    await c.answer()


# ---------- календарь налогов, уплата налогов, закрытие месяца

async def tax_calendar(msg):
    t0 = today()
    over, up = calc.tax_deadlines(t0, int(core.cfg()["tax_due_day"]), 4)
    lines = ["🗓 Календарь налогов (НДС и акциз)", ""]
    for mon, d in over:
        lines.append(f"🔴 Просрочено: за {mon}, срок был {d.strftime('%d.%m.%Y')}")
    for mon, d in up:
        left = (d - t0).days
        lines.append(f"{'🟠' if left <= 5 else '🟢'} За {mon}: до {d.strftime('%d.%m.%Y')} (через {left} дн.)")
    await msg.answer("\n".join(lines))


async def tax_paid(msg):
    pays = core.db.tax_payments()
    total = sum(p["amount"] for p in pays)
    lines = ["💳 Уплаченные налоги", "", f"Всего внесено: {fnum(total)} сум"]
    for p in pays[-8:]:
        lines.append(f"• {dm(p['date'])} — {fnum(p['amount'])}" + (f" ({p['note']})" if p["note"] else ""))
    first = today().replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    s, e = calc.month_bounds(prev.year, prev.month)
    tot = calc.totals(core.rows_for(s, e))
    if tot["shifts"]:
        lines.append(f"\nНачислено за {MONTHS[prev.month - 1]}: {fnum(max(tot['vat_net'], 0) + tot['excise'])} сум")
    await msg.answer("\n".join(lines), reply_markup=ikb([[("➕ Внести платёж", "tp:new")]]))


@router.callback_query(F.data == "tp:new")
async def taxpay_new(c: CallbackQuery, state: FSMContext):
    if can(c.from_user.id, "accounting"):
        await state.set_state(TaxPay.line)
        await c.message.answer("Напишите сумму и, если нужно, пояснение: «3 500 000 НДС за октябрь».")
    await c.answer()


@router.message(TaxPay.line, ~F.text.in_(MENU))
async def taxpay_line(m: Message, state: FSMContext):
    if not can(m.from_user.id, "accounting"):
        return
    r = split_amount(m.text)
    if not r:
        return await m.answer("Не понял сумму. Пример: 3 500 000 НДС за октябрь")
    await state.clear()
    core.db.add_tax_payment(today().isoformat(), r[0], r[1], uname(m.from_user))
    core.db.log(uname(m.from_user), "tax_payment", f"{fnum(r[0])} {r[1]}")
    await m.answer(f"✅ Платёж {fnum(r[0])} сум записан.")


async def lock_menu(msg, uid=None):
    locked = core.db.locked_months()
    t0 = today().replace(day=1)
    months = []
    d = t0
    for _ in range(6):
        months.append(d)
        d = (d - timedelta(days=1)).replace(day=1)
    rows = [[(f"{'🔒' if x.strftime('%Y-%m') in locked else '🔓'} {MONTHS[x.month - 1].capitalize()} {x.year}", f"lk:{x.strftime('%Y-%m')}")] for x in months]
    await msg.answer("🔒 Закрытие месяца: в закрытом месяце нельзя менять и удалять смены (кроме владельца). Нажмите на месяц, чтобы закрыть или открыть.", reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("lk:"))
async def lock_toggle(c: CallbackQuery):
    if not can(c.from_user.id, "settings"):
        return await c.answer("Закрывать месяц может только владелец", show_alert=True)
    mon = c.data[3:]
    if mon in core.db.locked_months():
        core.db.unlock_month(mon)
        core.db.log(uname(c.from_user), "month_unlocked", mon)
    else:
        core.db.lock_month(mon, uname(c.from_user))
        core.db.log(uname(c.from_user), "month_locked", mon)
    await c.answer("Готово")
    await lock_menu(c.message)
