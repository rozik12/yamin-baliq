"""Telegram-бот «Личный бухгалтер АГЗС»: точка входа. Меню, финансы, остаток газа, графики, настройки; мастер смены — в wizard.py."""
import asyncio
import logging
import os
from datetime import timedelta

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, CallbackQuery, Message

import calc
import charts
import core
import export
import reports
import texts
import wizard
from calc import PAY_KEYS, PAY_NAMES, fnum, parse_date, parse_num
from core import can, cfg, is_admin, role, today, uname
from db import now
from reports import alert_stock, build_excel, finance_text  # noqa: F401
from ui import *  # noqa: F401,F403  (кнопки, меню, t, ikb)

globals().update({n: getattr(wizard, n) for n in wizard.EXPORTS})  # bot.shift_start и т.д. (для тестов и обратной совместимости)

ADMINS = core.ADMINS
router = Router()
log = logging.getLogger("agzs")


class SetVal(StatesGroup):
    value = State()


class Stock(StatesGroup):
    liters = State()
    density = State()


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


# ---------- остаток газа, прогноз, замер

@router.message(F.text == B_STOCK)
async def stock(m: Message, state: FSMContext):
    if not can(m.from_user.id, "stock_view"):
        return
    await state.clear()
    await m.answer(reports.stock_text(), reply_markup=ikb([[("✏️ Задать остаток (замер)", "sk:set")]]))


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
        await m.answer("За какой период показать финансы?", reply_markup=ikb([
            [(n, f"fp:{c}") for n, c in PERIODS[:3]], [(n, f"fp:{c}") for n, c in PERIODS[3:]],
            [("Квартал", "fp:quarter"), ("Год", "fp:year"), ("🗓 Свой период", "cp:fin")],
            [("📄 PDF за месяц", "pf:month"), ("📄 PDF за квартал", "pf:quarter"), ("📄 PDF свой период", "cp:pdf")],
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
                               reply_markup=ikb([[("За неделю", f"gp:{k}:week"), ("За месяц", f"gp:{k}:month"), ("За всё время", f"gp:{k}:all")],
                                                    [("Квартал", f"gp:{k}:quarter"), ("Год", f"gp:{k}:year"), ("🗓 Свой", f"cp:g:{k}")]]))
    await c.answer()


@router.callback_query(F.data.startswith("gp:"))
async def graph_make(c: CallbackQuery):
    if not can(c.from_user.id, "finance"):
        return await c.answer()
    _, kind, code = c.data.split(":")
    await c.answer("Строю график…")
    s, e, label = core.period_range(code)
    png, cap, err = await asyncio.to_thread(reports.make_chart, kind, s, e, label)
    if err:
        return await c.message.answer(err)
    await c.message.answer_photo(BufferedInputFile(png, f"{kind}.png"), caption=cap)


@router.message(F.text == B_XLS)
async def xls_menu(m: Message):
    if can(m.from_user.id, "finance"):
        await m.answer("Выгрузить сводную таблицу в Excel:", reply_markup=ikb([[("За всё время", "xl:all"), ("За 30 дней", "xl:month")], [("📤 CSV (все смены)", "xc:csv")]]))


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
    if can(m.from_user.id, "shifts_view"):
        import quick
        await quick.shifts_page(m)


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
        if s and core.month_locked(s["date"]) and not can(c.from_user.id, "settings"):
            await c.answer("🔒 Месяц закрыт, удалить может только владелец", show_alert=True)
            return
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
            ("tax_due_day", "День срока сдачи налогов (число месяца)"), ("report_deadline", "Срок сдачи отчёта операторами (ЧЧ:ММ, 0 = выключено)"),
            ("plan_day_l", "План дня, л пролива"), ("plan_month_l", "План месяца, л пролива"), ("plan_month_revenue", "План месяца, выручка"),
            ("cash_alert_pct", "Сообщать о расхождении кассы больше, % (0 = выкл.)"), ("anomaly_pct", "Предупреждать о необычном проливе больше, % (0 = выкл.)")]


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
    if k == "sale_price_l":
        core.db.add_price(today().isoformat(), v, uname(m.from_user))  # история цен: прошлые смены считаются по старой цене
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
    import lookup
    import quick
    dp.include_router(lookup.router)
    dp.include_router(quick.router)
    dp.include_router(staff.router)
    dp.include_router(extras.router)
    dp.include_router(wizard.router)
    dp.include_router(router)  # последним: в нём общий «fallback»
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
