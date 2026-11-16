"""Отчёты, которые нужны и боту, и планировщику: финансы за период, предупреждения о газе, выгрузка Excel."""
import asyncio

import calc
import core
import export
from calc import PAY_NAMES, fnum
from core import cfg

def finance_text(rows, label, start=None, end=None):
    tot = calc.totals(rows)
    c = cfg()
    if not tot["shifts"]:
        return f"📊 Финансы · {label}\n\nСмен за этот период нет."
    margin = 100 * tot["profit"] / tot["revenue"] if tot["revenue"] else 0
    lines = [f"📊 Финансы · {label}", f"Смен: {tot['shifts']}", "",
             f"⛽ Пролив: {fnum(tot['sold_l'])} л ({fnum(tot['sold_kg'])} кг)",
             "   " + " · ".join(f"сч.{i + 1}: {fnum(tot['m'][i])}" for i in range(3)),
             f"🚚 Приход газа: {fnum(tot['arr_kg'])} кг ({fnum(tot['arr_l'])} л)", "",
             f"💰 Выручка: {fnum(tot['revenue'])} сум"] + [f"   {PAY_NAMES[k]}: {fnum(v)} ({100 * v / tot['revenue']:.0f}%)" for k, v in tot["pay"].items() if v and tot["revenue"]]
    if c["sale_price_l"] > 0 and tot["theory"]:
        diff = tot["revenue"] - tot["theory"]
        lines.append(f"   По счётчикам: {fnum(tot['theory'])} → {'излишек' if diff > 0 else 'недостача'} {fnum(abs(diff))}")
    lines += ["", "🧾 Налоги:", f"   НДС исходящий: {fnum(tot['vat_out'])}", f"   НДС входящий (с закупа): {fnum(tot['vat_in'])}",
              f"   НДС {'к уплате' if tot['vat_net'] >= 0 else 'к зачёту'}: {fnum(abs(tot['vat_net']))}", f"   Акциз: {fnum(tot['excise'])}",
              f"   Всего налогов: {fnum(tot['taxes'])}", "",
              f"🛢 Закуп газа (оплачено): {fnum(tot['purchase'])}",
              f"   В прибыли ({'по закупу' if c['profit_mode'] != 'cogs' else 'по себестоимости проданного'}, без НДС): {fnum(tot['gas_cost'])}",
              f"💸 Расходы на месте: {fnum(tot['expenses'])}"]
    if tot["fixed"]:
        lines.append(f"🏢 Постоянные расходы (доля периода): {fnum(tot['fixed'])}")
    lines += ["", f"✅ ЧИСТАЯ ПРИБЫЛЬ: {fnum(tot['profit'])} сум ({margin:.1f}% выручки)"]
    pl = calc.per_liter(tot)
    if pl:
        lines.append(f"   На 1 литр: выручка {fnum(pl['revenue'])} · газ {fnum(pl['gas'])} · налоги {fnum(pl['taxes'])} · расходы {fnum(pl['expenses'])} · прибыль {fnum(pl['profit'])} сум")
    u = calc.unit_economics(tot, core.fixed_monthly(), len(rows))
    if u["margin_l"] is not None:
        lines.append(f"   Маржа на литр (до постоянных): {fnum(u['margin_l'], 0)} сум" + (f" · безубыточность {fnum(u['breakeven_l_day'])} л/день (сейчас {fnum(u['avg_l_day'])})" if u["breakeven_l_day"] else ""))
    best, worst = calc.top_days(rows, 1)
    if best and len([r for r in rows if r["shifts"]]) > 1:
        lines.append(f"   Лучший день: {best[0]['date'][8:10]}.{best[0]['date'][5:7]} ({fnum(best[0]['profit'])}), слабый: {worst[0]['date'][8:10]}.{worst[0]['date'][5:7]} ({fnum(worst[0]['profit'])})")
    last = next((r for r in reversed(rows) if r["tank_l"] is not None), None)
    if last:
        lines += ["", f"⛽ Остаток на конец периода: {fnum(last['tank_l'])} л · {fnum(last['tank_kg'])} кг"]
    lost, gained = calc.loss_total(core.db.adjustments(), start, end)
    if lost or gained:
        lines.append(f"🔎 По замерам резервуара: потери {fnum(lost)} л, излишки {fnum(gained)} л")
    pend = [s for s in core.db.shifts(start, end) if s.get("status") == "pending"]
    if pend:
        lines.append(f"⏳ Смен ждут проверки: {len(pend)}")
    if c["price_buy_kg"] <= 0 and tot["arr_kg"] > 0:
        lines.append("\n⚠️ Не задана цена закупа в настройках: стоимость газа может быть занижена.")
    return "\n".join(lines)


async def alert_stock(bot, st, fc):
    """Предупреждения владельцам: газ ниже порога или хватит меньше чем на N дней."""
    c = cfg()
    msgs = []
    if c["low_stock_l"] > 0 and st["liters"] < c["low_stock_l"]:
        msgs.append(f"🚨 Газ заканчивается: в резервуаре {fnum(st['liters'], 0)} л (порог {fnum(c['low_stock_l'])} л). Пора заказывать.")
    if c["order_days"] > 0 and fc["days_left"] is not None and fc["days_left"] < c["order_days"]:
        msgs.append(f"🚨 Газа хватит примерно на {fc['days_left']:.1f} дн. (средний пролив {fnum(fc['avg_l'])} л в день). Закажите газ — см. «💼 Бухгалтерия → 🔮 Прогноз и заявка».")
    for text in msgs:
        for a in core.ids_with("stock_view", "alerts"):
            try:
                await bot.send_message(a, text)
            except Exception:
                pass


async def build_excel(code):
    s, e, label = core.period_range(code)
    rows = core.rows_for(s, e)
    shifts = core.db.shifts(s, e)
    ids = {x["id"] for x in shifts}
    exps = [x for x in core.db.expense_rows() if x["shift_id"] in ids]
    data = await asyncio.to_thread(export.build_xlsx, rows, shifts, exps, core.db.adjustments(), core.db.settings(), core.db.supplier_payments(), core.db.collections())
    return data, f"agzs_{s}_{e}.xlsx", label


def milestones(before, after, day, c):
    """Что стоит отметить после новой смены: рекорд дня, выполнение плана дня и месяца. before/after — строки дней до и после смены."""
    out = []
    dd = lambda d: f"{d[8:10]}.{d[5:7]}"
    b_l, b_r = calc.records(before)
    a_l, a_r = calc.records(after)
    if b_l and a_l and a_l["date"] == day and a_l["value"] > b_l["value"]:
        out.append(f"🏆 Рекорд дня по проливу: {fnum(a_l['value'])} л (прошлый рекорд {fnum(b_l['value'])} л, {dd(b_l['date'])})")
    if b_r and a_r and a_r["date"] == day and a_r["value"] > b_r["value"]:
        out.append(f"🏆 Рекорд дня по выручке: {fnum(a_r['value'])} сум (прошлый рекорд {fnum(b_r['value'])} сум, {dd(b_r['date'])})")
    drow = next((r for r in after if r["date"] == day), None)
    gp = calc.goal_progress(drow["sold_l"], c["plan_day_l"]) if drow else None
    if gp:
        out.append(f"🎯 План дня: {gp[0]:.0f}% {gp[1]} ({fnum(drow['sold_l'])} из {fnum(c['plan_day_l'])} л)")
    month = [r for r in after if r["date"][:7] == day[:7]]
    gl = calc.goal_progress(sum(r["sold_l"] for r in month), c["plan_month_l"])
    if gl:
        out.append(f"📅 План месяца по литрам: {gl[0]:.0f}% {gl[1]}")
    gr = calc.goal_progress(sum(r["revenue"] for r in month), c["plan_month_revenue"])
    if gr:
        out.append(f"📅 План месяца по выручке: {gr[0]:.0f}% {gr[1]}")
    return out


def stock_text():
    """Экран «Остаток газа»: остаток, заполненность, прогноз, порог, потери по замерам."""
    from datetime import timedelta
    c = cfg()
    st, fc = core.stock_forecast()
    has = bool(core.db.adjustments())
    lines = ["⛽ Остаток газа в резервуаре", "", f"{fnum(st['liters'], 0)} л · {fnum(st['kg'], 0)} кг · плотность {str(round(st['density'], 3)).replace('.', ',')} кг/л"]
    if c["tank_capacity_l"] > 0:
        lines.append(f"Заполнено: {100 * st['liters'] / c['tank_capacity_l']:.0f}% от {fnum(c['tank_capacity_l'])} л")
    if fc["days_left"] is not None:
        eta = (core.today() + timedelta(days=int(fc["days_left"]))).strftime("%d.%m")
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
    return "\n".join(lines)


CHART_CAPTIONS = {"sales": lambda t: f"Пролив: {fnum(t['sold_l'])} л", "revenue": lambda t: f"Выручка: {fnum(t['revenue'])} сум",
                  "expenses": lambda t: f"Закуп {fnum(t['purchase'])} · расходы {fnum(t['expenses'])} сум", "profit": lambda t: f"Чистая прибыль: {fnum(t['profit'])} сум",
                  "stock": lambda t: "Остаток газа по дням", "weekday": lambda t: "Средний пролив по дням недели", "structure": lambda t: f"Принято: {fnum(t['revenue'])} сум",
                  "buy_price": lambda t: "История цены закупа", "exp_cat": lambda t: f"Расходы на месте: {fnum(t['expenses'])} сум"}


def make_chart(kind, start, end, label):
    """(png | None, подпись, сообщение-ошибка). Синхронная, тяжёлая: вызывать через asyncio.to_thread."""
    import charts
    rows = core.rows_for(start, end)
    if not any(r["shifts"] for r in rows) and kind != "stock":
        return None, "", "За этот период нет смен — график строить не из чего."
    extra = None
    if kind == "buy_price":
        extra = [{"date": x["date"], "price": x["arr_price"], "kg": x["arr_kg"]} for x in core.db.shifts(start, end) if x["arr_kg"] and x["arr_price"]]
    elif kind == "exp_cat":
        ids = {x["id"] for x in core.db.shifts(start, end)}
        extra = calc.expense_breakdown([e for e in core.db.expense_rows() if e["shift_id"] in ids])
    png = charts.render(kind, rows, cfg(), label, extra)
    return png, f"{charts.KINDS[kind]} · {label}\n{CHART_CAPTIONS[kind](calc.totals(rows))}", ""
