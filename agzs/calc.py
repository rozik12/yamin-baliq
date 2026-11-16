"""Ядро учёта АГЗС: пролив, выручка, налоги, закуп, расходы, прибыль, остаток газа в резервуаре.

Без Telegram и без базы: на входе словари смен/расходов/корректировок и настройки.
Деньги в сумах, объём в литрах, масса в кг, плотность в кг/л (0,54 или 0,56).
"""
import calendar
from datetime import date, datetime, timedelta

DEFAULTS = {
    "sale_price_l": 0.0,      # розничная цена за литр (для сверки кассы), 0 = не задана
    "price_buy_kg": 0.0,      # цена закупа за кг по умолчанию
    "buy_price_with_vat": 1,  # 1: цена закупа уже с НДС, 0: без НДС
    "vat_rate": 12.0,         # НДС, %
    "excise_per_l": 0.0,      # акциз, сум за проданный литр
    "default_density": 0.54,  # кг/л, если плотность неизвестна
    "tank_capacity_l": 0.0,   # ёмкость резервуара, л (0 = не задана)
    "low_stock_l": 0.0,       # порог «газ заканчивается», л (0 = выключено)
    "profit_mode": "purchases",  # purchases: закуп в день прихода; cogs: себестоимость проданного
    "loss_norm_pct": 0.0,     # допустимые потери газа, % от пролива между замерами (0 = норма не задана)
    "order_days": 3.0,        # предупреждать, если газа хватит меньше чем на N дней (0 = выключено)
    "tax_due_day": 20.0,      # число месяца, до которого сдаются НДС и акциз (напоминание за 3 дня и в день срока)
    "auto_daily": 1.0,        # 1 = присылать владельцу итоги дня в 21:30 и недели по понедельникам
    "report_deadline": "21:00",  # до какого времени операторы должны сдать отчёт ("0" = не напоминать)
}
STR_KEYS = ("profit_mode", "report_deadline")
PAY_KEYS = ("cash", "terminal", "click", "paynet")
PAY_NAMES = {"cash": "Наличные", "terminal": "Терминал", "click": "Click", "paynet": "Paynet QR"}


def cfg_of(settings):
    c = dict(DEFAULTS)
    for k, v in (settings or {}).items():
        if k in DEFAULTS:
            try:
                c[k] = str(v) if k in STR_KEYS else float(v)
            except (TypeError, ValueError):
                pass
    return c


def meters(s):
    """Пролив по трём счётчикам: [(нач, кон, литры)]; отрицательный пролив не считаем."""
    out = []
    for k in (1, 2, 3):
        a, b = float(s.get(f"m{k}s") or 0), float(s.get(f"m{k}e") or 0)
        out.append((a, b, max(b - a, 0.0)))
    return out


def shift_calc(s, expenses, cfg):
    """Финансы одной смены. expenses — список сумм."""
    cfg = cfg_of(cfg) if "vat_rate" not in cfg else cfg
    m = meters(s)
    sold_l = sum(x[2] for x in m)
    arr_kg = float(s.get("arr_kg") or 0)
    rho = float(s.get("arr_density") or 0) or cfg["default_density"]
    arr_l = arr_kg / rho if arr_kg > 0 else 0.0
    pay = {k: float(s.get(k) or 0) for k in PAY_KEYS}
    revenue = sum(pay.values())
    r = cfg["vat_rate"]
    price = float(s.get("arr_price") or 0)
    purchase_base = arr_kg * price
    purchase = purchase_base if cfg["buy_price_with_vat"] else purchase_base * (1 + r / 100)  # сколько реально заплатили
    vat_out = revenue * r / (100 + r)
    vat_in = purchase * r / (100 + r)
    excise = sold_l * cfg["excise_per_l"]
    exp_total = float(sum(expenses or []))
    theory = sold_l * cfg["sale_price_l"] if cfg["sale_price_l"] > 0 else None
    return {
        "liters": [x[2] for x in m], "sold_l": sold_l, "arr_kg": arr_kg, "arr_density": rho if arr_kg > 0 else 0.0, "arr_l": arr_l,
        "arr_price": price, "pay": pay, "revenue": revenue, "theory": theory,
        "diff": (revenue - theory) if theory is not None else None,
        "vat_out": vat_out, "vat_in": vat_in, "vat_net": vat_out - vat_in, "excise": excise,
        "purchase": purchase, "expenses": exp_total,
    }


def tank_run(shifts, adjustments, cfg):
    """Остаток газа после каждого события по порядку (дата, время ввода).

    Корректировка (замер/начальный остаток) задаёт остаток жёстко; приход добавляет кг и литры;
    продажа снимает литры и массу по плотности смеси в баке (кг/л).
    Возвращает ({shift_id: {...}}, итоговое состояние, {дата: (л, кг) на конец дня}).
    """
    cfg = cfg_of(cfg) if "vat_rate" not in cfg else cfg
    events = [(a["date"], a.get("ts", ""), 0, a) for a in adjustments] + [(s["date"], s.get("ts", ""), 1, s) for s in shifts]
    events.sort(key=lambda e: (e[0], e[1], e[2], e[3].get("id", 0)))
    liters = kg = 0.0
    per_shift, per_day = {}, {}
    for d, _, kind, e in events:
        if kind == 0:
            liters, kg = float(e["liters"]), float(e["kg"])
        else:
            sold_l = sum(x[2] for x in meters(e))
            ak = float(e.get("arr_kg") or 0)
            if ak > 0:
                rho = float(e.get("arr_density") or 0) or cfg["default_density"]
                liters += ak / rho
                kg += ak
            rho_mix = (kg / liters) if liters > 0 else cfg["default_density"]
            sold_kg = sold_l * rho_mix
            liters -= sold_l
            kg -= sold_kg
            per_shift[e["id"]] = {"liters": liters, "kg": kg, "sold_kg": sold_kg}
        per_day[d] = (liters, kg)
    return per_shift, {"liters": liters, "kg": kg, "density": (kg / liters) if liters > 0 else cfg["default_density"]}, per_day


def day_rows(shifts, expenses_by_shift, adjustments, cfg, start=None, end=None, fixed_monthly=0.0):
    """Строки по дням (с нулями для дней без смен) за [start, end] включительно (строки 'YYYY-MM-DD')."""
    cfg = cfg_of(cfg) if "vat_rate" not in cfg else cfg
    per_shift, _, per_day = tank_run(shifts, adjustments, cfg)
    by_day = {}
    last_price = cfg["price_buy_kg"]
    for s in sorted(shifts, key=lambda x: (x["date"], x.get("ts", ""), x["id"])):
        sc = shift_calc(s, expenses_by_shift.get(s["id"], []), cfg)
        if s.get("arr_kg") and s.get("arr_price"):
            last_price = float(s["arr_price"])
        d = by_day.setdefault(s["date"], {"date": s["date"], "shifts": 0, "m": [0.0, 0.0, 0.0], "sold_l": 0.0, "sold_kg": 0.0, "arr_kg": 0.0,
                                          "arr_l": 0.0, "purchase": 0.0, "pay": {k: 0.0 for k in PAY_KEYS}, "revenue": 0.0, "theory": 0.0,
                                          "expenses": 0.0, "vat_out": 0.0, "vat_in": 0.0, "excise": 0.0, "cogs_gross": 0.0, "operators": []})
        d["shifts"] += 1
        for i in range(3):
            d["m"][i] += sc["liters"][i]
        d["sold_l"] += sc["sold_l"]
        d["sold_kg"] += per_shift.get(s["id"], {}).get("sold_kg", 0.0)
        d["arr_kg"] += sc["arr_kg"]
        d["arr_l"] += sc["arr_l"]
        d["purchase"] += sc["purchase"]
        for k in PAY_KEYS:
            d["pay"][k] += sc["pay"][k]
        d["revenue"] += sc["revenue"]
        d["theory"] += sc["theory"] or 0.0
        d["expenses"] += sc["expenses"]
        d["vat_out"] += sc["vat_out"]
        d["vat_in"] += sc["vat_in"]
        d["excise"] += sc["excise"]
        gross_price = last_price if cfg["buy_price_with_vat"] else last_price * (1 + cfg["vat_rate"] / 100)
        d["cogs_gross"] += per_shift.get(s["id"], {}).get("sold_kg", 0.0) * gross_price
        if s.get("user_name") and s["user_name"] not in d["operators"]:
            d["operators"].append(s["user_name"])
    dates = sorted(by_day)
    lo = start or (dates[0] if dates else None)
    hi = end or (dates[-1] if dates else None)
    if not lo or not hi:
        return []
    rows, cur = [], date.fromisoformat(lo)
    stop = date.fromisoformat(hi)
    r = cfg["vat_rate"]
    carry = None
    pd_dates = sorted(per_day)
    while cur <= stop:
        key = cur.isoformat()
        d = by_day.get(key) or {"date": key, "shifts": 0, "m": [0.0, 0.0, 0.0], "sold_l": 0.0, "sold_kg": 0.0, "arr_kg": 0.0, "arr_l": 0.0,
                                "purchase": 0.0, "pay": {k: 0.0 for k in PAY_KEYS}, "revenue": 0.0, "theory": 0.0, "expenses": 0.0,
                                "vat_out": 0.0, "vat_in": 0.0, "excise": 0.0, "cogs_gross": 0.0, "operators": []}
        d["vat_net"] = d["vat_out"] - d["vat_in"]
        d["taxes"] = d["vat_net"] + d["excise"]
        gas_gross = d["purchase"] if cfg["profit_mode"] != "cogs" else d["cogs_gross"]
        gas_net = gas_gross - gas_gross * r / (100 + r)
        d["gas_cost"] = gas_net
        d["profit_pre_fixed"] = (d["revenue"] - d["vat_out"]) - gas_net - d["excise"] - d["expenses"]
        d["fixed"] = fixed_monthly / calendar.monthrange(cur.year, cur.month)[1] if fixed_monthly else 0.0  # постоянные расходы равномерно по дням месяца
        d["profit"] = d["profit_pre_fixed"] - d["fixed"]
        # остаток на конец дня: последняя известная точка не позже этого дня
        last = None
        for pdk in pd_dates:
            if pdk <= key:
                last = per_day[pdk]
            else:
                break
        d["tank_l"], d["tank_kg"] = last if last else (None, None)
        d["diff"] = (d["revenue"] - d["theory"]) if (cfg["sale_price_l"] > 0 and d["sold_l"] > 0) else None
        rows.append(d)
        cur += timedelta(days=1)
    return rows


def totals(rows):
    """Сумма числовых полей по строкам дней."""
    t = {"shifts": 0, "sold_l": 0.0, "sold_kg": 0.0, "arr_kg": 0.0, "arr_l": 0.0, "purchase": 0.0, "revenue": 0.0, "expenses": 0.0,
         "vat_out": 0.0, "vat_in": 0.0, "vat_net": 0.0, "excise": 0.0, "taxes": 0.0, "gas_cost": 0.0, "profit": 0.0, "fixed": 0.0, "profit_pre_fixed": 0.0,
         "pay": {k: 0.0 for k in PAY_KEYS}, "m": [0.0, 0.0, 0.0], "theory": 0.0}
    for d in rows:
        for k in ("shifts", "sold_l", "sold_kg", "arr_kg", "arr_l", "purchase", "revenue", "expenses", "vat_out", "vat_in", "vat_net",
                  "excise", "taxes", "gas_cost", "profit", "theory", "fixed", "profit_pre_fixed"):
            t[k] += d[k]
        for k in PAY_KEYS:
            t["pay"][k] += d["pay"][k]
        for i in range(3):
            t["m"][i] += d["m"][i]
    return t


# ---------- ввод чисел

def parse_num(text):
    """«1 250 000», «12,5», «12.5» → float; None, если это не число или оно отрицательное."""
    if text is None:
        return None
    s = str(text).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not s or s.count(".") > 1:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v >= 0 else None


def parse_date(text, today):
    """«сегодня» / «вчера» / 15.10 / 15.10.2026 → 'YYYY-MM-DD' или None."""
    import re
    t = (text or "").strip().lower()
    if t in ("сегодня", "today"):
        return today.isoformat()
    if t in ("вчера", "yesterday"):
        return (today - timedelta(days=1)).isoformat()
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", t)
    if not m:
        return None
    y = int(m.group(3)) if m.group(3) else today.year
    y += 2000 if y < 100 else 0
    try:
        return date(y, int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def fnum(n, digits=0):
    """Красивое число: 1 250 000 или 1 250,5."""
    s = f"{n:,.{digits}f}".replace(",", " ")
    return s.replace(".", ",")


# ---------- аналитика для бухгалтерии

def month_bounds(year, month):
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1).isoformat(), date(year, month, last).isoformat()


def prev_month(d):
    first = d.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def compare_totals(a, b):
    """Сравнение двух периодов: [(название, было, стало, изменение, % изменения)]; a — прошлый период, b — текущий."""
    items = [("Пролив, л", "sold_l"), ("Выручка, сум", "revenue"), ("Закуп газа, сум", "purchase"), ("Расходы на месте, сум", "expenses"),
             ("Налоги, сум", "taxes"), ("Чистая прибыль, сум", "profit")]
    out = []
    for name, k in items:
        x, y = a[k], b[k]
        out.append((name, x, y, y - x, (100 * (y - x) / abs(x)) if x else None))
    return out


def forecast(rows, tank_l, days=14, default_density=0.54):
    """Прогноз запаса газа: средний пролив в день за последние `days` дней (только дни до сегодняшнего, начиная с первой смены)."""
    days_with = [r for r in rows if r["shifts"] > 0]
    if not days_with or tank_l is None:
        return {"avg_l": 0.0, "days_left": None}
    recent = rows[-days:]
    first = days_with[0]["date"]
    recent = [r for r in recent if r["date"] >= first]
    avg = sum(r["sold_l"] for r in recent) / max(len(recent), 1)
    return {"avg_l": avg, "days_left": (max(tank_l, 0) / avg) if avg > 0 else None}


def order_suggestion(tank_l, tank_kg, cfg, avg_l):
    """Сколько газа заказать: до ёмкости резервуара, а если она не задана — на 7 дней продаж. Возвращает (кг, литры)."""
    rho = (tank_kg / tank_l) if tank_l and tank_l > 0 and tank_kg and tank_kg > 0 else cfg["default_density"]
    need_l = (cfg["tank_capacity_l"] - max(tank_l or 0, 0)) if cfg["tank_capacity_l"] > 0 else avg_l * 7
    need_l = max(need_l, 0)
    kg = round(need_l * rho / 100) * 100
    return kg, (kg / rho if rho else 0)


def by_weekday(rows):
    """Средний пролив и выручка по дням недели (только дни со сменами): [{wd, name, sold_l, revenue, days}] с понедельника."""
    names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    acc = [[0.0, 0.0, 0] for _ in range(7)]
    for r in rows:
        if r["shifts"] > 0:
            w = date.fromisoformat(r["date"]).weekday()
            acc[w][0] += r["sold_l"]
            acc[w][1] += r["revenue"]
            acc[w][2] += 1
    return [{"wd": i, "name": names[i], "sold_l": a[0] / a[2] if a[2] else 0.0, "revenue": a[1] / a[2] if a[2] else 0.0, "days": a[2]} for i, a in enumerate(acc)]


def top_days(rows, n=3):
    live = [r for r in rows if r["shifts"] > 0]
    s = sorted(live, key=lambda r: r["profit"], reverse=True)
    return s[:n], s[::-1][:n]


def operator_stats(shifts, expenses_by_shift, cfg):
    """По операторам: смены, литры, выручка, расхождение кассы (если задана цена продажи)."""
    cfg = cfg_of(cfg) if "vat_rate" not in cfg else cfg
    out = {}
    for s in shifts:
        r = shift_calc(s, expenses_by_shift.get(s["id"], []), cfg)
        o = out.setdefault(s["user_name"], {"name": s["user_name"], "shifts": 0, "sold_l": 0.0, "revenue": 0.0, "theory": 0.0, "diff": 0.0, "with_price": 0})
        o["shifts"] += 1
        o["sold_l"] += r["sold_l"]
        o["revenue"] += r["revenue"]
        if r["theory"] is not None:
            o["theory"] += r["theory"]
            o["diff"] += r["diff"]
            o["with_price"] += 1
    for o in out.values():
        o["diff_pct"] = (100 * o["diff"] / o["theory"]) if o["theory"] else None
    return sorted(out.values(), key=lambda o: (o["diff"] if o["with_price"] else 0, o["name"]))


def sold_since_last_adjustment(shifts, adjustments, cfg):
    """Литры, проданные после последней корректировки (для нормы потерь)."""
    if not adjustments:
        return 0.0
    last = max(adjustments, key=lambda a: (a["date"], a.get("ts", ""), a.get("id", 0)))
    return sum(sum(x[2] for x in meters(s)) for s in shifts if (s["date"], s.get("ts", "")) > (last["date"], last.get("ts", "")))


def reconcile(expected_l, measured_l, sold_since_l, loss_norm_pct):
    """Сверка замера с расчётом. diff<0 — недостача. over_norm — потери больше нормы (% от пролива с прошлого замера)."""
    diff = measured_l - expected_l
    pct = (100 * diff / expected_l) if expected_l and expected_l > 0 else None
    allowed = sold_since_l * loss_norm_pct / 100 if loss_norm_pct > 0 else None
    return {"diff": diff, "pct": pct, "allowed": allowed, "over_norm": bool(allowed is not None and diff < 0 and -diff > allowed)}


def loss_total(adjustments, start=None, end=None):
    """Сумма расхождений по замерам за период: (потери, излишки) в литрах."""
    lost = gained = 0.0
    for a in adjustments:
        if a.get("expected_l") is None:
            continue
        if (start and a["date"] < start) or (end and a["date"] > end):
            continue
        d = a["liters"] - a["expected_l"]
        if d < 0:
            lost += -d
        else:
            gained += d
    return lost, gained


def supplier_balance(shifts, payments):
    """Долг поставщику: сумма закупов (оплачено при приходе считать долгом) минус платежи. Возвращает (закуплено, оплачено, долг)."""
    bought = sum(float(s.get("arr_kg") or 0) * float(s.get("arr_price") or 0) for s in shifts)
    paid = sum(float(p["amount"]) for p in payments)
    return bought, paid, bought - paid


def cash_on_hand(shifts, expenses_by_shift, collections):
    """Наличные в кассе по данным бота: принято наличными − расходы на месте − инкассация."""
    cash = sum(float(s.get("cash") or 0) for s in shifts)
    spent = sum(sum(expenses_by_shift.get(s["id"], [])) for s in shifts)
    taken = sum(float(c["amount"]) for c in collections)
    return cash, spent, taken, cash - spent - taken


def month_forecast(rows, today):
    """Прогноз прибыли на конец месяца по темпу с начала месяца. rows — строки с начала месяца по сегодня."""
    mrows = [r for r in rows if r["date"][:7] == today.isoformat()[:7]]
    if not mrows:
        return None
    elapsed = today.day
    total_days = calendar.monthrange(today.year, today.month)[1]
    profit = sum(r["profit"] for r in mrows)
    revenue = sum(r["revenue"] for r in mrows)
    return {"elapsed": elapsed, "total_days": total_days, "profit": profit, "profit_forecast": profit / elapsed * total_days,
            "revenue": revenue, "revenue_forecast": revenue / elapsed * total_days}


def unit_economics(t, fixed_monthly, days_in_period):
    """Маржа на литр (до постоянных расходов) и точка безубыточности по литрам в день."""
    margin_l = (t["profit_pre_fixed"] / t["sold_l"]) if t["sold_l"] else None
    fixed_day = fixed_monthly / 30.4 if fixed_monthly else 0.0
    breakeven = (fixed_day / margin_l) if (margin_l and margin_l > 0 and fixed_day) else None
    avg_day = t["sold_l"] / days_in_period if days_in_period else 0.0
    return {"margin_l": margin_l, "breakeven_l_day": breakeven, "avg_l_day": avg_day}
