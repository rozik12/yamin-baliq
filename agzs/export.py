"""Выгрузка полной сводной таблицы в Excel (.xlsx)."""
import io

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import calc

DAY_COLS = [
    ("date", "Дата"), ("shifts", "Смен"), ("operators", "Операторы"), ("m1", "Пролив счётчик 1, л"), ("m2", "Пролив счётчик 2, л"),
    ("m3", "Пролив счётчик 3, л"), ("sold_l", "Пролив всего, л"), ("sold_kg", "Пролив, кг"), ("arr_kg", "Приход газа, кг"),
    ("arr_l", "Приход газа, л"), ("cash", "Наличные"), ("terminal", "Терминал"), ("click", "Click"), ("paynet", "Paynet QR"),
    ("revenue", "Выручка всего"), ("theory", "Выручка по счётчикам (расчёт)"), ("diff", "Расхождение кассы"), ("vat_out", "НДС исходящий"),
    ("vat_in", "НДС входящий"), ("vat_net", "НДС к уплате"), ("excise", "Акциз"), ("purchase", "Закуп газа (оплачено)"),
    ("gas_cost", "Стоимость газа в прибыли (без НДС)"), ("expenses", "Расходы на месте"), ("fixed", "Постоянные расходы (доля дня)"), ("profit_pre_fixed", "Прибыль до постоянных расходов"), ("profit", "Чистая прибыль"),
    ("tank_l", "Остаток, л"), ("tank_kg", "Остаток, кг")]


def _day_df(rows):
    data = []
    for r in rows:
        d = {k: r.get(k) for k, _ in DAY_COLS}
        d["operators"] = ", ".join(r["operators"])
        for i in range(3):
            d[f"m{i + 1}"] = r["m"][i]
        for k in calc.PAY_KEYS:
            d[k] = r["pay"][k]
        data.append(d)
    df = pd.DataFrame(data, columns=[k for k, _ in DAY_COLS])
    return df.rename(columns=dict(DAY_COLS))


def build_xlsx(rows, shifts, expense_rows, adjustments, cfg, payments=(), collections=()):
    """rows — calc.day_rows; shifts/expense_rows/adjustments — записи из БД. Возвращает байты файла."""
    days = _day_df(rows)
    t = calc.totals(rows)
    if len(days):
        total = {c: "" for c in days.columns}
        total["Дата"] = "ИТОГО"
        total.update({"Смен": t["shifts"], "Пролив счётчик 1, л": t["m"][0], "Пролив счётчик 2, л": t["m"][1], "Пролив счётчик 3, л": t["m"][2],
                      "Пролив всего, л": t["sold_l"], "Пролив, кг": t["sold_kg"], "Приход газа, кг": t["arr_kg"], "Приход газа, л": t["arr_l"],
                      "Наличные": t["pay"]["cash"], "Терминал": t["pay"]["terminal"], "Click": t["pay"]["click"], "Paynet QR": t["pay"]["paynet"],
                      "Выручка всего": t["revenue"], "Выручка по счётчикам (расчёт)": t["theory"], "НДС исходящий": t["vat_out"],
                      "НДС входящий": t["vat_in"], "НДС к уплате": t["vat_net"], "Акциз": t["excise"], "Закуп газа (оплачено)": t["purchase"],
                      "Стоимость газа в прибыли (без НДС)": t["gas_cost"], "Расходы на месте": t["expenses"], "Постоянные расходы (доля дня)": t["fixed"],
                      "Прибыль до постоянных расходов": t["profit_pre_fixed"], "Чистая прибыль": t["profit"]})
        days = pd.concat([days, pd.DataFrame([total])], ignore_index=True)
    exp_sum = {}
    for e in expense_rows:
        exp_sum[e["shift_id"]] = exp_sum.get(e["shift_id"], 0) + e["amount"]
    sh = []
    for s in shifts:
        m = calc.meters(s)
        sh.append({"№ смены": s["id"], "Дата": s["date"], "Время ввода": s["ts"][:16].replace("T", " "), "Оператор": s["user_name"],
                   "Сч.1 нач.": m[0][0], "Сч.1 кон.": m[0][1], "Сч.1 пролив": m[0][2], "Сч.2 нач.": m[1][0], "Сч.2 кон.": m[1][1],
                   "Сч.2 пролив": m[1][2], "Сч.3 нач.": m[2][0], "Сч.3 кон.": m[2][1], "Сч.3 пролив": m[2][2],
                   "Пролив всего, л": sum(x[2] for x in m), "Приход, кг": s["arr_kg"], "Плотность": s["arr_density"], "Цена закупа за кг": s["arr_price"],
                   "Наличные": s["cash"], "Терминал": s["terminal"], "Click": s["click"], "Paynet QR": s["paynet"],
                   "Выручка": s["cash"] + s["terminal"] + s["click"] + s["paynet"], "Расходы": exp_sum.get(s["id"], 0),
                   "Статус": {"pending": "на проверке", "approved": "принята", "returned": "возвращена"}.get(s.get("status") or "approved", ""),
                   "Накладная / поставщик": s.get("arr_note") or "", "Фото, шт": len([x for x in (s.get("photos") or "").split(",") if x])})
    date_of = {s["id"]: s["date"] for s in shifts}
    exps = pd.DataFrame([{"№ смены": e["shift_id"], "Дата": date_of.get(e["shift_id"], ""), "Сумма": e["amount"], "Описание": e["comment"]}
                         for e in expense_rows], columns=["№ смены", "Дата", "Сумма", "Описание"])
    arr = pd.DataFrame([{"Дата": s["date"], "№ смены": s["id"], "Масса, кг": s["arr_kg"], "Плотность": s["arr_density"],
                         "Объём, л": s["arr_kg"] / s["arr_density"] if s["arr_density"] else 0, "Цена за кг": s["arr_price"],
                         "Сумма закупа": s["arr_kg"] * s["arr_price"]} for s in shifts if s["arr_kg"]],
                       columns=["Дата", "№ смены", "Масса, кг", "Плотность", "Объём, л", "Цена за кг", "Сумма закупа"])
    adj = pd.DataFrame([{"Дата": a["date"], "Замер, л": a["liters"], "Замер, кг": a["kg"], "Расчётный остаток, л": a.get("expected_l"),
                         "Расхождение, л": (a["liters"] - a["expected_l"]) if a.get("expected_l") is not None else None,
                         "Комментарий": a["note"], "Кто": a["user_name"]} for a in adjustments],
                       columns=["Дата", "Замер, л", "Замер, кг", "Расчётный остаток, л", "Расхождение, л", "Комментарий", "Кто"])
    sup_rows = [{"Дата": s["date"], "Тип": "Закуп (приход)", "Сумма": s["arr_kg"] * s["arr_price"], "Описание": s.get("arr_note") or f"смена №{s['id']}"} for s in shifts if s["arr_kg"]]
    sup_rows += [{"Дата": p["date"], "Тип": "Оплата поставщику", "Сумма": -p["amount"], "Описание": p["note"]} for p in payments]
    sup = pd.DataFrame(sorted(sup_rows, key=lambda r: r["Дата"]), columns=["Дата", "Тип", "Сумма", "Описание"])
    col = pd.DataFrame([{"Дата": c["date"], "Сумма": c["amount"], "Комментарий": c["note"], "Кто": c["user_name"]} for c in collections],
                       columns=["Дата", "Сумма", "Комментарий", "Кто"])
    names = {"sale_price_l": "Цена продажи за литр", "price_buy_kg": "Цена закупа за кг (по умолчанию)", "buy_price_with_vat": "Цена закупа с НДС (1/0)",
             "vat_rate": "НДС, %", "excise_per_l": "Акциз, сум/л", "default_density": "Плотность по умолчанию", "tank_capacity_l": "Ёмкость резервуара, л",
             "low_stock_l": "Порог остатка, л", "profit_mode": "Режим прибыли (purchases/cogs)"}
    names.update({"loss_norm_pct": "Норма потерь, % от пролива", "order_days": "Предупреждать, если газа меньше чем на N дней", "tax_due_day": "День срока сдачи налогов",
                  "auto_daily": "Ежедневный отчёт владельцу", "report_deadline": "Срок сдачи отчёта оператором"})
    st = pd.DataFrame([{"Параметр": names.get(k, k), "Значение": v} for k, v in calc.cfg_of(cfg).items()])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        days.to_excel(w, sheet_name="Сводка по дням", index=False)
        pd.DataFrame(sh).to_excel(w, sheet_name="Смены", index=False)
        exps.to_excel(w, sheet_name="Расходы", index=False)
        arr.to_excel(w, sheet_name="Приход газа", index=False)
        adj.to_excel(w, sheet_name="Корректировки остатка", index=False)
        sup.to_excel(w, sheet_name="Поставщик", index=False)
        col.to_excel(w, sheet_name="Инкассация", index=False)
        st.to_excel(w, sheet_name="Настройки", index=False)
        for ws in w.book.worksheets:
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="2E7D5B")
                c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
            ws.row_dimensions[1].height = 36
            ws.freeze_panes = "B2"
            for i, col in enumerate(ws.columns, 1):
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 11), 38)
                for c in col[1:]:
                    if isinstance(c.value, float):
                        c.number_format = "#,##0.00" if abs(c.value) < 10 and c.value != int(c.value) else "#,##0"
                    elif isinstance(c.value, int):
                        c.number_format = "#,##0"
            if ws.title == "Сводка по дням" and ws.max_row > 1:
                for c in ws[ws.max_row]:
                    c.font = Font(bold=True)
    return buf.getvalue()
