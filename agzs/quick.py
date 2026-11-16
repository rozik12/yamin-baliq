"""Быстрые действия и удобство: главная страница, быстрый отчёт одним сообщением, команды /today и др., поиск и подробности смен,
отмена своей смены, причина возврата, свой период, PDF, импорт из Excel, CSV, резервная копия, настройка уведомлений, справка."""
import asyncio
import csv
import io
import os
import tempfile
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message

import calc
import core
import extras
import importer
import pdfreport
import reports
import texts
import wizard
from calc import PAY_NAMES, fnum, parse_date, parse_num
from core import can, cfg, role, today, uname
from ui import MENU, V_HOME, V_QUICK, ikb, menu, t

router = Router()
TEMPLATE = "дата сегодня\nсч1 1500\nсч2 2300\nсч3 0\nнал 1000000\nтерм 500к\nклик 300000\nпейнет 200000\nприход 1000 0.54 5000\nрасход 50000 бензин\nкомментарий всё в порядке"
PAGE = 8


class QuickRep(StatesGroup):
    text = State()


class Search(StatesGroup):
    query = State()


class Reason(StatesGroup):
    text = State()


class Custom(StatesGroup):
    range = State()


class Comment(StatesGroup):
    text = State()


class ImportX(StatesGroup):
    file = State()


def dm(d):
    return f"{d[8:10]}.{d[5:7]}"


ST = texts.ST_ICON


# ---------- главная страница

def short_text(rows, label):
    tot = calc.totals(rows)
    if not tot["shifts"]:
        return f"📊 {label}: смен не было."
    margin = 100 * tot["profit"] / tot["revenue"] if tot["revenue"] else 0
    lines = [f"📊 {label}: смен {tot['shifts']}", f"⛽ Пролив {fnum(tot['sold_l'])} л · выручка {fnum(tot['revenue'])} сум",
             f"🧾 Налоги {fnum(tot['taxes'])} · закуп {fnum(tot['purchase'])} · расходы {fnum(tot['expenses'] + tot['fixed'])}", f"✅ Чистая прибыль {fnum(tot['profit'])} сум ({margin:.1f}%)"]
    last = next((r for r in reversed(rows) if r["tank_l"] is not None), None)
    if last:
        lines.append(f"🛢 Остаток {fnum(last['tank_l'])} л · {fnum(last['tank_kg'])} кг")
    return "\n".join(lines)


def home_text(uid):
    r = role(uid)
    t0 = today()
    lines = [f"🏠 Главная · {t0.strftime('%d.%m.%Y')} · {core.POSITIONS.get(r, r)}", ""]
    if r == "operator":
        mine = core.db.shifts(uid=uid)
        done = [s for s in mine if s["date"] == t0.isoformat()]
        lines.append(t(uid, "home_op_done" if done else "home_op_todo"))
        month = [s for s in mine if s["date"][:7] == t0.isoformat()[:7]]
        c = cfg()
        lines.append(t(uid, "home_op_month", n=len(month), l=fnum(sum(calc.shift_calc(s, [], c)["sold_l"] for s in month))))
        return "\n".join(lines)
    c = cfg()
    if can(uid, "finance"):
        today_rows = core.rows_for(t0.isoformat(), t0.isoformat())
        lines.append(short_text(today_rows, "Сегодня") if any(x["shifts"] for x in today_rows) else "📊 Сегодня смен ещё не было.")
        month_rows = core.rows_for(t0.replace(day=1).isoformat(), t0.isoformat())
        mt = calc.totals(month_rows)
        if mt["shifts"]:
            lines += ["", f"📅 С начала месяца: пролив {fnum(mt['sold_l'])} л · выручка {fnum(mt['revenue'])} · прибыль {fnum(mt['profit'])}"]
            for name, done_v, plan in (("литры", mt["sold_l"], c["plan_month_l"]), ("выручка", mt["revenue"], c["plan_month_revenue"])):
                gp = calc.goal_progress(done_v, plan)
                if gp:
                    lines.append(f"🎯 План по {name}: {gp[0]:.0f}% {gp[1]}")
    if can(uid, "stock_view"):
        st, fc = core.stock_forecast()
        extra = f" · хватит на {fc['days_left']:.1f} дн." if fc["days_left"] else ""
        lines += ["", f"🛢 Остаток газа: {fnum(st['liters'], 0)} л · {fnum(st['kg'], 0)} кг{extra}"]
        if c["low_stock_l"] > 0 and st["liters"] < c["low_stock_l"]:
            lines.append("🚨 Газ ниже порога — пора заказывать.")
    if can(uid, "approve"):
        pend = [s for s in core.db.shifts() if s.get("status") == "pending"]
        if pend:
            lines.append(f"⏳ Смен ждут проверки: {len(pend)}")
    if can(uid, "accounting"):
        _, up = calc.tax_deadlines(t0, c["tax_due_day"], 1)
        if up:
            lines.append(f"⏰ Ближайший срок налогов: {up[0][1].strftime('%d.%m.%Y')} (за {up[0][0]})")
        _, _, debt = calc.supplier_balance(core.db.shifts(), core.db.supplier_payments())
        if debt > 0:
            lines.append(f"🚚 Долг поставщику: {fnum(debt)} сум")
    return "\n".join(lines)


def home_kb(uid):
    rows = []
    if can(uid, "finance"):
        rows.append([("📊 Сегодня", "qp:today"), ("📅 Неделя", "qp:week"), ("🗓 Месяц", "qp:month")])
    if can(uid, "approve") and any(s.get("status") == "pending" for s in core.db.shifts()):
        rows.append([("⏳ Проверить смены", "sl:pending:0")])
    if can(uid, "shift"):
        rows.append([("⚡ Быстрый отчёт", "qk:start")])
    rows.append([("🔔 Уведомления", "np:menu"), ("ℹ️ Помощь", "hp:menu")])
    return ikb(rows)


@router.message(F.text.in_(V_HOME))
async def home(m: Message, state: FSMContext):
    if role(m.from_user.id):
        await state.clear()
        await m.answer(home_text(m.from_user.id), reply_markup=home_kb(m.from_user.id))


@router.message(Command("home"))
async def home_cmd(m: Message, state: FSMContext):
    await home(m, state)


# ---------- команды-ярлыки: /today /yesterday /week /month /quarter /year /stock /debt /cash /taxes /expenses /help

PERIOD_CMDS = ("today", "yesterday", "week", "month", "quarter", "year")


async def short_report(msg, code):
    s, e, label = core.period_range("yday" if code == "yesterday" else code)
    await msg.answer(short_text(core.rows_for(s, e), label.capitalize()),
                     reply_markup=ikb([[("Подробнее", f"fp:{'yday' if code == 'yesterday' else code}"), ("📄 PDF", f"pf:{'yday' if code == 'yesterday' else code}")]]))


@router.message(Command(*PERIOD_CMDS))
async def period_cmd(m: Message, command: CommandObject):
    if can(m.from_user.id, "finance"):
        await short_report(m, command.command)


@router.callback_query(F.data.startswith("qp:"))
async def short_cb(c: CallbackQuery):
    if can(c.from_user.id, "finance"):
        await short_report(c.message, c.data[3:])
    await c.answer()


@router.message(Command("stock"))
async def stock_cmd(m: Message):
    if can(m.from_user.id, "stock_view"):
        await m.answer(reports.stock_text())


@router.message(Command("debt"))
async def debt_cmd(m: Message):
    if can(m.from_user.id, "accounting"):
        await extras.supplier(m)


@router.message(Command("cash"))
async def cash_cmd(m: Message):
    if can(m.from_user.id, "accounting"):
        await extras.cash(m)


@router.message(Command("taxes"))
async def taxes_cmd(m: Message):
    if can(m.from_user.id, "accounting"):
        await extras.tax_menu(m)


@router.message(Command("expenses"))
async def expenses_cmd(m: Message):
    uid = m.from_user.id
    if not can(uid, "finance"):
        return
    s, e, label = core.period_range("month")
    ids = {x["id"] for x in core.db.shifts(s, e)}
    br = calc.expense_breakdown([x for x in core.db.expense_rows() if x["shift_id"] in ids])
    if not br:
        return await m.answer(f"💸 Расходов на месте за {label} нет.")
    total = sum(x[1] for x in br)
    await m.answer(f"💸 Расходы на месте за {label}: {fnum(total)} сум\n\n" + "\n".join(f"• {n}: {fnum(v)} ({100 * v / total:.0f}%, записей {c})" for n, v, c in br),
                   reply_markup=ikb([[("📊 График", "gp:exp_cat:month")]]))


def help_text(uid):
    r = role(uid)
    lines = [f"ℹ️ Помощь · {core.POSITIONS.get(r, r)}", ""]
    if can(uid, "shift"):
        lines += ["➕ Сменный отчёт — по шагам с кнопками. ⚡ Быстрый отчёт — всё одним сообщением (/quick).", "↩️ /undo — отменить свою последнюю смену в течение 15 минут.", "Суммы можно писать как 500к, 2 млн, 1 250 000."]
    if can(uid, "finance"):
        lines += ["📊 /today /yesterday /week /month /quarter /year — короткие итоги; кнопка «Подробнее» и 📄 PDF.", "/expenses — расходы по статьям."]
    if can(uid, "stock_view"):
        lines.append("⛽ /stock — остаток газа и на сколько дней хватит.")
    if can(uid, "accounting"):
        lines.append("💼 /taxes — налоги и сроки; /debt — долг поставщику; /cash — касса.")
    if can(uid, "shifts_view"):
        lines.append("🧾 /shift 15.11 или /shift 12 — подробности смены; 🔎 поиск по дате, номеру, оператору.")
    if can(uid, "approve"):
        lines.append("✅ Принять / ↩️ Вернуть (с причиной) / ✏️ Исправить — у каждой новой смены.")
    if can(uid, "staff"):
        lines.append("👥 Персонал: должности, ссылки, назначение по @нику. /backup — копия базы. 📢 сообщение сотрудникам.")
    lines += ["🏠 /home — главная. 🔔 /notify — какие уведомления получать."]
    return "\n".join(lines)


@router.message(Command("help"))
async def help_cmd(m: Message):
    if role(m.from_user.id):
        await m.answer(help_text(m.from_user.id))


@router.callback_query(F.data == "hp:menu")
async def help_cb(c: CallbackQuery):
    if role(c.from_user.id):
        await c.message.answer(help_text(c.from_user.id))
    await c.answer()


# ---------- быстрый отчёт одним сообщением

async def start_quick(msg, state, uid):
    await state.clear()
    await state.set_state(QuickRep.text)
    await msg.answer(t(uid, "quick_ask", tpl=TEMPLATE))


@router.message(F.text.in_(V_QUICK))
async def quick_btn(m: Message, state: FSMContext):
    if can(m.from_user.id, "shift"):
        await start_quick(m, state, m.from_user.id)


@router.callback_query(F.data == "qk:start")
async def quick_cb(c: CallbackQuery, state: FSMContext):
    if can(c.from_user.id, "shift"):
        await start_quick(c.message, state, c.from_user.id)
    await c.answer()


async def process_quick(m: Message, state: FSMContext, text):
    uid = m.from_user.id
    prev = core.db.last_meter_ends()
    c = cfg()
    d, errs = calc.parse_quick_report(text, prev, today(), c["default_density"], c["price_buy_kg"])
    if errs:
        return await m.answer(t(uid, "quick_err", errs="\n".join(f"• {e}" for e in errs[:10])))
    await state.clear()
    await state.set_state(wizard.Shift.step)
    await state.update_data(i=len(wizard.steps_for(d)), d=d, prev=prev, edit=None)
    await wizard.finish_wizard(m, state, uid)


@router.message(Command("quick"))
async def quick_cmd(m: Message, command: CommandObject, state: FSMContext):
    if not can(m.from_user.id, "shift"):
        return
    if command.args:
        return await process_quick(m, state, command.args)
    await start_quick(m, state, m.from_user.id)


@router.message(QuickRep.text, ~F.text.in_(MENU))
async def quick_text(m: Message, state: FSMContext):
    if can(m.from_user.id, "shift"):
        await process_quick(m, state, m.text or "")


# ---------- подробности смены, список, поиск, отмена

def shift_details(s, uid):
    c = cfg()
    exps = core.db.expenses_of(s["id"])
    r = calc.shift_calc(s, [a for a, _ in exps], c, core.price_lookup()(s["date"]))
    m = calc.meters(s)
    lines = [f"🧾 Смена №{s['id']} · {dm(s['date'])}.{s['date'][:4]} · {s['user_name']} · {ST.get(s.get('status') or 'approved', '')} {s.get('status') or 'approved'}", ""]
    lines += [f"Счётчик {i + 1}: {fnum(a, 1)} → {fnum(b, 1)} = {fnum(x, 1)} л" for i, (a, b, x) in enumerate(m)]
    lines.append(f"⛽ Пролив: {fnum(r['sold_l'], 1)} л")
    if r["arr_kg"] > 0:
        lines.append(f"🚚 Приход: {fnum(r['arr_kg'], 1)} кг × {str(r['arr_density']).replace('.', ',')} = {fnum(r['arr_l'], 1)} л, закуп {fnum(r['arr_price'])} сум/кг" + (f" · {s['arr_note']}" if s.get("arr_note") else ""))
    lines += ["", f"💰 Касса: {fnum(r['revenue'])} сум"] + [f"   {PAY_NAMES[k]}: {fnum(v)}" for k, v in r["pay"].items() if v]
    if r["diff"] is not None:
        lines.append(f"   По счётчикам {fnum(r['theory'])}: {'излишек' if r['diff'] > 0 else 'недостача'} {fnum(abs(r['diff']))}")
    if exps:
        lines += ["", "💸 Расходы:"] + [f"• {fnum(a)} — {cm or 'без описания'} ({calc.categorize(cm)})" for a, cm in exps]
    if s.get("photos"):
        lines.append(f"📷 Фото: {len([x for x in s['photos'].split(',') if x])}")
    if s.get("comment"):
        lines += ["", f"💬 {s['comment']}"]
    if can(uid, "finance"):
        lines += ["", f"НДС {fnum(r['vat_net'])} · акциз {fnum(r['excise'])} · закуп {fnum(r['purchase'])}"]
    return "\n".join(lines)


def details_kb(s, uid):
    rows = []
    if can(uid, "approve"):
        row = [("✏️ Исправить", f"ed:{s['id']}")]
        if s.get("status") == "pending":
            row += [("✅ Принять", f"ap:{s['id']}:ok"), ("↩️ Вернуть", f"rr:{s['id']}")]
        rows += [row, [("💬 Комментарий", f"cm:{s['id']}"), ("🗑 Удалить", f"ds:{s['id']}")]]
    elif s["user_id"] == uid and s.get("status") == "pending":
        rows.append([(t(uid, "undo_btn"), f"un:{s['id']}")])
    return ikb(rows) if rows else None


def may_see(uid, s):
    return can(uid, "shifts_view") or (role(uid) and s["user_id"] == uid)


@router.callback_query(F.data.startswith("sd:"))
async def details_cb(c: CallbackQuery):
    s = core.db.shift(int(c.data[3:]))
    if s and may_see(c.from_user.id, s):
        await c.message.answer(shift_details(s, c.from_user.id), reply_markup=details_kb(s, c.from_user.id))
    await c.answer()


@router.message(Command("shift"))
async def shift_cmd(m: Message, command: CommandObject):
    uid = m.from_user.id
    if not role(uid):
        return
    arg = (command.args or "").strip().lstrip("№#")
    if not arg:
        return await m.answer("Напишите номер или дату: /shift 12 или /shift 15.11")
    if arg.isdigit() and len(arg) <= 6:
        s = core.db.shift(int(arg))
        if not s or not may_see(uid, s):
            return await m.answer("Такой смены нет.")
        return await m.answer(shift_details(s, uid), reply_markup=details_kb(s, uid))
    d = parse_date(arg, today())
    found = [s for s in core.db.shifts(d, d) if may_see(uid, s)] if d else []
    if not found:
        return await m.answer("За эту дату смен не нашёл.")
    for s in found[:5]:
        await m.answer(shift_details(s, uid), reply_markup=details_kb(s, uid))


def page_of(mode, page, query=None):
    sh = core.db.shifts()[::-1]
    if mode == "pending":
        sh = [s for s in sh if s.get("status") == "pending"]
    elif mode == "find":
        q = (query or "").strip()
        d = parse_date(q, today())
        if q.lstrip("№#").isdigit() and len(q.lstrip("№#")) <= 6:
            sh = [s for s in sh if s["id"] == int(q.lstrip("№#"))]
        elif d:
            sh = [s for s in sh if s["date"] == d]
        else:
            sh = [s for s in sh if q.lower() in (s["user_name"] or "").lower()]
    total = len(sh)
    return sh[page * PAGE:(page + 1) * PAGE], total


async def shifts_page(msg, page=0, mode="all", query=None, edit=False):
    uid = msg.chat.id if hasattr(msg, "chat") and msg.chat else 0
    c = cfg()
    sh, total = page_of(mode, page, query)
    if not total:
        text = {"pending": "⏳ Смен, ждущих проверки, нет.", "find": "Ничего не нашёл."}.get(mode, "Смен пока нет.")
        return await msg.answer(text)
    title = {"all": "🧾 Смены", "pending": "⏳ Ждут проверки", "find": f"🔎 Найдено: {total}"}[mode]
    lines = [f"{title} (страница {page + 1} из {(total - 1) // PAGE + 1}):"]
    rows = []
    for s in sh:
        r = calc.shift_calc(s, [], c)
        lines.append(f"{ST.get(s.get('status') or 'approved', '')} №{s['id']} · {dm(s['date'])} · {s['user_name']} · {fnum(r['sold_l'])} л · {fnum(r['revenue'])} сум")
        rows.append([(f"🔎 №{s['id']} · {dm(s['date'])} · {s['user_name'][:14]}", f"sd:{s['id']}")])
    nav = []
    tag = "all" if mode == "all" else mode
    if page > 0:
        nav.append(("◀️", f"sl:{tag}:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(("▶️", f"sl:{tag}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("🔎 Найти смену", "sl:find")])
    await msg.answer("\n".join(lines) + "\n\n⏳ ждёт проверки · ✅ принята · ↩️ возвращена", reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("sl:"))
async def shifts_cb(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "shifts_view"):
        return await c.answer()
    await c.answer()
    _, mode, *rest = c.data.split(":")
    if mode == "find":
        await state.set_state(Search.query)
        return await c.message.answer("Что искать? Номер (12), дату (15.11) или имя оператора.")
    await shifts_page(c.message, int(rest[0]) if rest else 0, "all" if mode == "all" else mode)


@router.message(Search.query, ~F.text.in_(MENU))
async def search_text(m: Message, state: FSMContext):
    if not can(m.from_user.id, "shifts_view"):
        return
    await state.clear()
    await shifts_page(m, 0, "find", m.text)


@router.callback_query(F.data.startswith("un:"))
async def undo_cb(c: CallbackQuery):
    await do_undo(c.message, c.from_user.id, int(c.data[3:]))
    await c.answer()


@router.message(Command("undo"))
async def undo_cmd(m: Message):
    uid = m.from_user.id
    mine = core.db.shifts(uid=uid)
    if not can(uid, "shift") or not mine:
        return
    await do_undo(m, uid, mine[-1]["id"])


async def do_undo(msg, uid, sid):
    from datetime import datetime
    s = core.db.shift(sid)
    ok = bool(s) and s["user_id"] == uid and (s.get("status") or "approved") == "pending" and not core.month_locked(s["date"])
    if ok:
        age = core.now() - datetime.fromisoformat(s["ts"])
        ok = age <= timedelta(minutes=15)
    if not ok:
        return await msg.answer(t(uid, "undo_no"))
    core.db.delete_shift(sid)
    core.db.log(core.person_name(uid), "shift_undone", f"№{sid}")
    await msg.answer(t(uid, "undo_done", id=sid))


# ---------- причина возврата и комментарии

@router.callback_query(F.data.startswith("rr:"))
async def return_ask(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "approve"):
        return await c.answer()
    sid = int(c.data[3:])
    await c.answer()
    if not core.db.shift(sid):
        return await c.message.answer("Такой смены уже нет.")
    await state.set_state(Reason.text)
    await state.update_data(sid=sid)
    await c.message.answer(f"Вернуть смену №{sid} оператору. Напишите причину (что исправить) или «-», если без причины.")


@router.message(Reason.text, ~F.text.in_(MENU))
async def return_reason(m: Message, state: FSMContext):
    uid = m.from_user.id
    if not can(uid, "approve"):
        return
    sid = (await state.get_data())["sid"]
    await state.clear()
    s = core.db.shift(sid)
    if not s:
        return await m.answer("Такой смены уже нет.")
    reason = (m.text or "").strip()
    reason = "" if reason == "-" else reason[:300]
    core.db.set_status(sid, "returned")
    if reason:
        core.db.set_comment(sid, ("Возврат: " + reason))
    core.db.log(uname(m.from_user), "shift_returned", f"№{sid} {reason}")
    await m.answer(f"↩️ Смена №{sid} возвращена оператору {s['user_name']}.")
    try:
        text = texts.tx(core.lang(s["user_id"]), "n_returned", id=sid) + (f"\n\n💬 {reason}" if reason else "")
        await m.bot.send_message(s["user_id"], text)
    except Exception:
        pass


@router.callback_query(F.data.startswith("cm:"))
async def comment_ask(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "approve"):
        return await c.answer()
    await c.answer()
    await state.set_state(Comment.text)
    await state.update_data(sid=int(c.data[3:]))
    await c.message.answer("Напишите комментарий к смене (он виден в подробностях смены).")


@router.message(Comment.text, ~F.text.in_(MENU))
async def comment_text(m: Message, state: FSMContext):
    if not can(m.from_user.id, "approve"):
        return
    sid = (await state.get_data())["sid"]
    await state.clear()
    if core.db.shift(sid):
        core.db.set_comment(sid, (m.text or "")[:300])
        core.db.log(uname(m.from_user), "shift_comment", f"№{sid}")
        await m.answer(f"💬 Комментарий к смене №{sid} сохранён.")


# ---------- свой период, PDF, CSV, резервная копия

async def send_pdf(msg, start, end, label):
    rows = core.rows_for(start, end)
    ids = {x["id"] for x in core.db.shifts(start, end)}
    exps = [e for e in core.db.expense_rows() if e["shift_id"] in ids]
    data = await asyncio.to_thread(pdfreport.build_pdf, rows, label, cfg(), exps)
    await msg.answer_document(BufferedInputFile(data, f"agzs_report_{start}_{end}.pdf"), caption=f"📄 Отчёт АГЗС · {label}")


@router.callback_query(F.data.startswith("pf:"))
async def pdf_cb(c: CallbackQuery):
    if not can(c.from_user.id, "finance"):
        return await c.answer()
    await c.answer("Готовлю PDF…")
    s, e, label = core.period_range(c.data[3:])
    await send_pdf(c.message, s, e, label)


@router.callback_query(F.data.startswith("cp:"))
async def custom_ask(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "finance"):
        return await c.answer()
    await c.answer()
    await state.set_state(Custom.range)
    await state.update_data(target=c.data[3:])
    await c.message.answer("Напишите период: две даты (01.11 - 15.11) или одну (15.11).")


@router.message(Custom.range, ~F.text.in_(MENU))
async def custom_range(m: Message, state: FSMContext):
    if not can(m.from_user.id, "finance"):
        return
    rng = calc.parse_range(m.text, today())
    if not rng:
        return await m.answer("Не понял даты. Пример: 01.11 - 15.11")
    target = (await state.get_data())["target"]
    await state.clear()
    s, e = rng
    label = f"{dm(s)}.{s[:4]} — {dm(e)}.{e[:4]}" if s != e else f"{dm(s)}.{s[:4]}"
    if target == "fin":
        await m.answer(reports.finance_text(core.rows_for(s, e), label, s, e))
    elif target == "pdf":
        await send_pdf(m, s, e, label)
    elif target.startswith("g:"):
        kind = target[2:]
        png, cap, err = await asyncio.to_thread(reports.make_chart, kind, s, e, label)
        if err:
            return await m.answer(err)
        await m.answer_photo(BufferedInputFile(png, f"{kind}.png"), caption=cap)


@router.callback_query(F.data == "xc:csv")
async def csv_cb(c: CallbackQuery):
    if not can(c.from_user.id, "finance"):
        return await c.answer()
    await c.answer("Готовлю файл…")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["№", "Дата", "Оператор", "Статус", "Сч1 нач", "Сч1 кон", "Сч2 нач", "Сч2 кон", "Сч3 нач", "Сч3 кон", "Пролив, л", "Наличные", "Терминал", "Click", "Paynet",
                "Выручка", "Приход кг", "Плотность", "Цена закупа", "Расходы"])
    exp = core.db.expenses_map()
    for s in core.db.shifts():
        r = calc.shift_calc(s, exp.get(s["id"], []), cfg())
        w.writerow([s["id"], s["date"], s["user_name"], s.get("status") or "approved", s["m1s"], s["m1e"], s["m2s"], s["m2e"], s["m3s"], s["m3e"], r["sold_l"], s["cash"],
                    s["terminal"], s["click"], s["paynet"], r["revenue"], s["arr_kg"], s["arr_density"], s["arr_price"], r["expenses"]])
    await c.message.answer_document(BufferedInputFile(("﻿" + buf.getvalue()).encode("utf-8"), "agzs_shifts.csv"), caption="📤 Все смены, CSV")


@router.message(Command("backup"))
async def backup_cmd(m: Message):
    if not can(m.from_user.id, "staff"):
        return
    with tempfile.TemporaryDirectory() as wd:
        path = os.path.join(wd, f"agzs_backup_{today().isoformat()}.db")
        core.db.backup_to(path)
        await m.answer_document(FSInputFile(path), caption="💾 Резервная копия базы АГЗС")
    core.db.log(uname(m.from_user), "backup_manual")


# ---------- импорт старых смен из Excel

@router.callback_query(F.data == "im:start")
async def import_start(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "settings"):
        return await c.answer()
    await c.answer()
    await state.set_state(ImportX.file)
    await c.message.answer("📥 Импорт смен из Excel. Скачайте шаблон, заполните и пришлите файл сюда.", reply_markup=ikb([[("📄 Скачать шаблон", "im:tpl")]]))


@router.callback_query(F.data == "im:tpl")
async def import_tpl(c: CallbackQuery):
    if can(c.from_user.id, "settings"):
        await c.message.answer_document(BufferedInputFile(importer.template_bytes(), "agzs_import_template.xlsx"), caption="Шаблон импорта. Инструкция на втором листе.")
    await c.answer()


@router.message(ImportX.file, F.document)
async def import_file(m: Message, state: FSMContext):
    if not can(m.from_user.id, "settings"):
        return
    doc = m.document
    if (doc.file_size or 0) > 5 * 1024 * 1024:
        return await m.answer("Файл слишком большой (максимум 5 МБ).")
    buf = io.BytesIO()
    await m.bot.download(doc.file_id, destination=buf)
    shifts, errors = importer.parse_workbook(buf.getvalue(), today())
    if not shifts:
        return await m.answer("Не нашёл ни одной корректной смены.\n" + "\n".join(errors[:8]))
    await state.update_data(shifts=shifts)
    text = f"📥 Найдено смен: {len(shifts)}" + (f", строк с ошибками: {len(errors)}" if errors else "") + "\n" + "\n".join(errors[:8])
    await m.answer(text + "\n\nДобавить найденные смены? (уже существующие будут пропущены)", reply_markup=ikb([[("✅ Добавить", "im:go"), ("✖️ Отмена", "im:no")]]))


@router.callback_query(F.data == "im:go")
async def import_go(c: CallbackQuery, state: FSMContext):
    if not can(c.from_user.id, "settings"):
        return await c.answer()
    shifts = (await state.get_data()).get("shifts")
    await state.clear()
    await c.answer()
    if not shifts:
        return await c.message.answer("Файл уже обработан или устарел. Пришлите заново.")
    added, skipped = importer.apply_rows(core.db, shifts)
    core.db.log(uname(c.from_user), "import_shifts", f"добавлено {added}, пропущено {skipped}")
    await c.message.answer(f"✅ Добавлено смен: {added}. Пропущено (уже были): {skipped}.")


@router.callback_query(F.data == "im:no")
async def import_no(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer("Отменено.")
    await c.answer()


# ---------- уведомления

KINDS = [("daily", "📅 Итоги дня", "finance"), ("weekly", "🗓 Итоги недели и месяца", "finance"), ("alerts", "🚨 Газ заканчивается", "stock_view"),
         ("shifts", "📥 Новые смены на проверку", "approve"), ("tax", "⏰ Сроки налогов", "accounting")]


def notify_kb(uid):
    rows = []
    for key, label, perm in KINDS:
        if can(uid, perm):
            on = core.db.pref(uid, "n_" + key, 1)
            rows.append([(f"{'🔔' if on else '🔕'} {label}: {'вкл' if on else 'выкл'}", f"np:t:{key}")])
    return ikb(rows)


@router.message(Command("notify"))
async def notify_cmd(m: Message):
    if role(m.from_user.id) and notify_kb(m.from_user.id).inline_keyboard:
        await m.answer("🔔 Какие уведомления присылать вам (нажмите, чтобы включить или выключить):", reply_markup=notify_kb(m.from_user.id))


@router.callback_query(F.data.startswith("np:"))
async def notify_cb(c: CallbackQuery):
    uid = c.from_user.id
    if not role(uid):
        return await c.answer()
    parts = c.data.split(":")
    if parts[1] == "t" and parts[2] in {k for k, _, _ in KINDS}:
        perm = {k: p for k, _, p in KINDS}[parts[2]]
        if can(uid, perm):
            core.db.set_pref(uid, "n_" + parts[2], 0 if core.db.pref(uid, "n_" + parts[2], 1) else 1)
    kb = notify_kb(uid)
    if parts[1] == "menu" or not kb.inline_keyboard:
        await c.message.answer("🔔 Какие уведомления присылать вам:", reply_markup=kb) if kb.inline_keyboard else await c.message.answer("Для вашей должности настраиваемых уведомлений нет.")
    else:
        await c.message.edit_reply_markup(reply_markup=kb)
    await c.answer()
