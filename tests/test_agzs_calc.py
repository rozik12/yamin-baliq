import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agzs"))
import calc  # noqa: E402

CFG = calc.cfg_of({"sale_price_l": 3000, "excise_per_l": 10, "price_buy_kg": 5000})
SHIFT = {"id": 1, "date": "2026-11-10", "ts": "t2", "user_name": "Али", "m1s": 1000, "m1e": 1500, "m2s": 2000, "m2e": 2300, "m3s": 0, "m3e": 0,
         "cash": 1_000_000, "terminal": 500_000, "click": 300_000, "paynet": 200_000, "arr_kg": 1000, "arr_density": 0.54, "arr_price": 5000}
OPEN = {"id": 1, "date": "2026-11-09", "ts": "t1", "liters": 1000, "kg": 540}


class Shift(unittest.TestCase):
    def test_numbers(self):
        r = calc.shift_calc(SHIFT, [50000, 10000], CFG)
        self.assertEqual(r["liters"], [500, 300, 0])
        self.assertEqual(r["sold_l"], 800)
        self.assertAlmostEqual(r["arr_l"], 1000 / 0.54, 6)
        self.assertEqual(r["revenue"], 2_000_000)
        self.assertAlmostEqual(r["vat_out"], 2_000_000 * 12 / 112, 4)
        self.assertAlmostEqual(r["vat_in"], 5_000_000 * 12 / 112, 4)
        self.assertEqual(r["excise"], 8000)
        self.assertEqual(r["expenses"], 60000)
        self.assertEqual(r["theory"], 2_400_000)
        self.assertEqual(r["diff"], -400_000)  # недостача кассы

    def test_price_without_vat_adds_vat_to_cost(self):
        cfg = calc.cfg_of({"buy_price_with_vat": 0, "vat_rate": 12})
        r = calc.shift_calc(dict(SHIFT, arr_price=5000), [], cfg)
        self.assertAlmostEqual(r["purchase"], 5_000_000 * 1.12, 4)
        self.assertAlmostEqual(r["vat_in"], 5_000_000 * 0.12, 4)

    def test_negative_meter_is_ignored_and_no_price_no_theory(self):
        r = calc.shift_calc(dict(SHIFT, m1e=900), [], calc.cfg_of({}))
        self.assertEqual(r["liters"][0], 0)
        self.assertIsNone(r["theory"])
        self.assertIsNone(r["diff"])


class Tank(unittest.TestCase):
    def test_arrival_then_sale_keeps_mass(self):
        per, now, per_day = calc.tank_run([SHIFT], [OPEN], CFG)
        self.assertAlmostEqual(now["liters"], 1000 + 1000 / 0.54 - 800, 6)
        self.assertAlmostEqual(now["kg"], 540 + 1000 - 800 * 0.54, 6)
        self.assertAlmostEqual(now["density"], 0.54, 6)
        self.assertAlmostEqual(per[1]["sold_kg"], 432, 6)
        self.assertIn("2026-11-10", per_day)

    def test_mixed_density(self):
        s2 = dict(SHIFT, id=2, ts="t3", date="2026-11-11", arr_kg=560, arr_density=0.56, m1s=1500, m1e=1600, m2s=0, m2e=0)
        _, now, _ = calc.tank_run([SHIFT, s2], [OPEN], CFG)
        liters = 1000 + 1000 / 0.54 - 800 + 560 / 0.56 - 100
        self.assertAlmostEqual(now["liters"], liters, 6)
        self.assertGreater(now["density"], 0.53)
        self.assertLess(now["density"], 0.57)

    def test_adjustment_is_absolute_and_order_by_time(self):
        adj2 = {"id": 2, "date": "2026-11-10", "ts": "t9", "liters": 500, "kg": 270}
        _, now, _ = calc.tank_run([SHIFT], [OPEN, adj2], CFG)
        self.assertEqual((now["liters"], now["kg"]), (500, 270))  # замер после смены перекрывает расчёт
        adj0 = {"id": 3, "date": "2026-11-10", "ts": "t0", "liters": 500, "kg": 270}  # до смены → смена применится
        _, now, _ = calc.tank_run([SHIFT], [adj0], CFG)
        self.assertAlmostEqual(now["liters"], 500 + 1000 / 0.54 - 800, 6)


class Days(unittest.TestCase):
    def test_profit_modes_and_zero_days(self):
        rows = calc.day_rows([SHIFT], {1: [50000, 10000]}, [OPEN], CFG, "2026-11-09", "2026-11-12")
        self.assertEqual([r["date"] for r in rows], ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"])
        d = rows[1]
        net_rev = 2_000_000 - 2_000_000 * 12 / 112
        gas_net = 5_000_000 - 5_000_000 * 12 / 112
        self.assertAlmostEqual(d["profit"], net_rev - gas_net - 8000 - 60000, 4)
        self.assertEqual(rows[0]["profit"], 0)
        self.assertEqual(rows[2]["tank_l"], rows[1]["tank_l"])  # остаток переносится на дни без смен
        cogs = calc.cfg_of({"sale_price_l": 3000, "excise_per_l": 10, "price_buy_kg": 5000, "profit_mode": "cogs"})
        d2 = calc.day_rows([SHIFT], {1: [50000, 10000]}, [OPEN], cogs, "2026-11-10", "2026-11-10")[0]
        cost = 432 * 5000
        self.assertAlmostEqual(d2["gas_cost"], cost - cost * 12 / 112, 4)

    def test_totals(self):
        rows = calc.day_rows([SHIFT, dict(SHIFT, id=2, ts="t4", date="2026-11-11")], {}, [OPEN], CFG)
        t = calc.totals(rows)
        self.assertEqual(t["revenue"], 4_000_000)
        self.assertEqual(t["sold_l"], 1600)
        self.assertEqual(t["pay"]["click"], 600_000)


class Parse(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(calc.parse_num("1 250 000"), 1250000)
        self.assertEqual(calc.parse_num("12,5"), 12.5)
        self.assertEqual(calc.parse_num("0"), 0)
        for bad in ("abc", "-5", "1.2.3", "", None):
            self.assertIsNone(calc.parse_num(bad))

    def test_dates(self):
        t = date(2026, 11, 16)
        self.assertEqual(calc.parse_date("сегодня", t), "2026-11-16")
        self.assertEqual(calc.parse_date("Вчера", t), "2026-11-15")
        self.assertEqual(calc.parse_date("5.11", t), "2026-11-05")
        self.assertEqual(calc.parse_date("05.11.25", t), "2025-11-05")
        self.assertIsNone(calc.parse_date("31.02", t))
        self.assertEqual(calc.fnum(1250000), "1 250 000")
        self.assertEqual(calc.fnum(1250.5, 1), "1 250,5")



class Analytics(unittest.TestCase):
    def rows(self, fixed=0.0):
        return calc.day_rows([SHIFT, dict(SHIFT, id=2, ts="t4", date="2026-11-11", m1s=1500, m1e=1800, m2s=2300, m2e=2300, arr_kg=0, arr_price=0)],
                             {1: [50000]}, [OPEN], CFG, "2026-11-09", "2026-11-12", fixed_monthly=fixed)

    def test_fixed_expenses_reduce_profit_daily(self):
        base, fx = self.rows(0), self.rows(3_000_000)
        daily = 3_000_000 / 30  # ноябрь
        for b, f in zip(base, fx):
            self.assertAlmostEqual(b["profit"] - f["profit"], daily, 4)
            self.assertAlmostEqual(f["profit_pre_fixed"], b["profit"], 4)
        self.assertAlmostEqual(calc.totals(fx)["fixed"], daily * 4, 4)

    def test_compare_totals(self):
        a, b = calc.totals(self.rows()[1:2]), calc.totals(self.rows()[2:3])
        cmp = dict((n, (x, y, d, p)) for n, x, y, d, p in calc.compare_totals(a, b))
        x, y, d, p = cmp["Выручка, сум"]
        self.assertEqual((x, y, d), (2_000_000, 2_000_000, 0))
        self.assertEqual(p, 0)
        self.assertIsNone(dict((n, p) for n, _, _, _, p in calc.compare_totals(calc.totals([]), b))["Выручка, сум"])

    def test_forecast_and_order(self):
        rows = self.rows()
        f = calc.forecast(rows, 1000)
        self.assertGreater(f["avg_l"], 0)
        self.assertAlmostEqual(f["days_left"], 1000 / f["avg_l"], 6)
        self.assertIsNone(calc.forecast([], 1000)["days_left"])
        kg, l = calc.order_suggestion(1000, 540, calc.cfg_of({"tank_capacity_l": 5000}), 500)
        self.assertEqual(kg, 2200)  # (5000-1000) л × 0,54 = 2160 → округление до 100
        self.assertAlmostEqual(l, 2200 / 0.54, 6)
        kg2, _ = calc.order_suggestion(100, 54, calc.cfg_of({}), 400)
        self.assertEqual(kg2, 1500)  # без ёмкости: 7 дней × 400 л × 0,54 ≈ 1512

    def test_weekday_top_days_and_operators(self):
        rows = self.rows()
        w = calc.by_weekday(rows)
        self.assertEqual(len(w), 7)
        self.assertEqual(sum(x["days"] for x in w), 2)
        best, worst = calc.top_days(rows, 1)
        self.assertEqual(len(best), 1)
        ops = calc.operator_stats([SHIFT, dict(SHIFT, id=2, user_name="Вали", cash=2_400_000, terminal=0, click=0, paynet=0)], {}, CFG)
        names = [o["name"] for o in ops]
        self.assertEqual(names[0], "Али")  # у Али недостача — он первый в списке
        self.assertAlmostEqual(ops[0]["diff"], -400_000)
        self.assertAlmostEqual(ops[1]["diff"], 0)

    def test_reconcile_and_losses(self):
        r = calc.reconcile(1000, 950, 800, 5)
        self.assertEqual(r["diff"], -50)
        self.assertAlmostEqual(r["pct"], -5.0)
        self.assertAlmostEqual(r["allowed"], 40)
        self.assertTrue(r["over_norm"])
        self.assertFalse(calc.reconcile(1000, 970, 800, 5)["over_norm"])
        self.assertFalse(calc.reconcile(1000, 1050, 800, 5)["over_norm"])  # излишек — не потеря
        self.assertIsNone(calc.reconcile(1000, 950, 800, 0)["allowed"])
        adjs = [{"date": "2026-11-01", "liters": 950, "expected_l": 1000}, {"date": "2026-11-05", "liters": 1100, "expected_l": 1000},
                {"date": "2026-11-06", "liters": 500, "expected_l": None}]
        self.assertEqual(calc.loss_total(adjs), (50, 100))
        self.assertEqual(calc.loss_total(adjs, "2026-11-02"), (0, 100))
        last = {"date": "2026-11-10", "ts": "t1.5", "id": 9, "liters": 1, "kg": 1}
        self.assertEqual(calc.sold_since_last_adjustment([SHIFT], [last], CFG), 800)  # смена t2 после замера t1.5
        self.assertEqual(calc.sold_since_last_adjustment([SHIFT], [dict(last, ts="t3")], CFG), 0)

    def test_supplier_cash_forecast_unit(self):
        bought, paid, debt = calc.supplier_balance([SHIFT], [{"amount": 2_000_000}])
        self.assertEqual((bought, paid, debt), (5_000_000, 2_000_000, 3_000_000))
        cash, spent, taken, left = calc.cash_on_hand([SHIFT], {1: [50000, 10000]}, [{"amount": 300_000}])
        self.assertEqual((cash, spent, taken, left), (1_000_000, 60_000, 300_000, 640_000))
        rows = self.rows()
        mf = calc.month_forecast(rows, date(2026, 11, 12))
        self.assertEqual(mf["total_days"], 30)
        self.assertAlmostEqual(mf["profit_forecast"], mf["profit"] / 12 * 30, 6)
        t = calc.totals(rows)
        u = calc.unit_economics(t, 3_000_000, 4)
        self.assertAlmostEqual(u["margin_l"], t["profit_pre_fixed"] / t["sold_l"], 6)
        self.assertEqual(calc.month_bounds(2026, 2), ("2026-02-01", "2026-02-28"))
        self.assertEqual(calc.prev_month(date(2026, 1, 15)), date(2025, 12, 1))


class Wave1(unittest.TestCase):
    def test_smart_numbers(self):
        cases = {"500к": 500_000, "500K": 500_000, "1,5 млн": 1_500_000, "2m": 2_000_000, "300 тыс": 300_000, "5 т": 5_000, "1 250 000": 1_250_000,
                 "12,5": 12.5, "0": 0, "0,54": 0.54, "2 000 000": 2_000_000}
        for text, v in cases.items():
            self.assertAlmostEqual(calc.parse_num(text), v, 6, text)
        for bad in ("abc", "-5", "к", "5 ккк", "1.2.3", "млн", ""):
            self.assertIsNone(calc.parse_num(bad), bad)

    def test_expense_categories(self):
        c = calc.categorize
        self.assertEqual(c("50000 бензин для генератора"), "Топливо и генератор")
        self.assertEqual(c("вода питьевая"), "Питание")
        self.assertEqual(c("оплата света"), "Коммунальные")
        self.assertEqual(c("аванс Али"), "Зарплата и премии")
        self.assertEqual(c("замена шланга"), "Ремонт и запчасти")
        self.assertEqual(c(""), "Прочее")
        br = calc.expense_breakdown([{"amount": 50000, "comment": "бензин"}, {"amount": 20000, "comment": "бензин для генератора"}, {"amount": 10000, "comment": "хз"}])
        self.assertEqual(br[0], ["Топливо и генератор", 70000, 2])
        self.assertEqual(br[1][0], "Прочее")

    def test_price_history_changes_theory(self):
        s2 = dict(SHIFT, id=2, ts="t4", date="2026-11-12")
        hist = {"2026-11-10": 3000.0, "2026-11-12": 3300.0}
        at = lambda d: 3300.0 if d >= "2026-11-12" else 3000.0
        rows = calc.day_rows([SHIFT, s2], {}, [OPEN], CFG, "2026-11-10", "2026-11-12", price_at=at)
        self.assertEqual(rows[0]["theory"], 800 * 3000)
        self.assertEqual(rows[2]["theory"], 800 * 3300)
        self.assertEqual(rows[0]["diff"], 2_000_000 - 2_400_000)

    def history(self, n=8, sold=800):
        return [{"date": f"2026-11-{i + 1:02d}", "shifts": 1, "sold_l": sold} for i in range(n)]

    def test_flags_typo_and_prices_and_dates(self):
        cfg = calc.cfg_of({"sale_price_l": 2500})  # 800 л × 2500 = 2 000 000 — касса сходится
        ok = calc.shift_flags(SHIFT, self.history(), cfg, date(2026, 11, 10))
        self.assertEqual(ok, [])
        typo = dict(SHIFT, m1e=15000)
        self.assertTrue(any("выше обычного" in f for f in calc.shift_flags(typo, self.history(), cfg, date(2026, 11, 10))))
        low = dict(SHIFT, m1e=1100, m2e=2000)
        self.assertTrue(any("ниже обычного" in f for f in calc.shift_flags(low, self.history(), cfg, date(2026, 11, 10))))
        zero = dict(SHIFT, m1e=1000, m2e=2000)
        self.assertTrue(any("пролива нет" in f for f in calc.shift_flags(zero, self.history(), cfg, date(2026, 11, 10))))
        cash = dict(SHIFT, cash=100_000)
        self.assertTrue(any("за литр" in f for f in calc.shift_flags(cash, self.history(), cfg, date(2026, 11, 10))))
        dens = dict(SHIFT, arr_density=0.7)
        self.assertTrue(any("плотность" in f for f in calc.shift_flags(dens, self.history(), cfg, date(2026, 11, 10))))
        self.assertTrue(any("в будущем" in f for f in calc.shift_flags(dict(SHIFT, date="2026-11-20"), [], cfg, date(2026, 11, 10))))
        self.assertTrue(any("давняя" in f for f in calc.shift_flags(dict(SHIFT, date="2026-10-01"), [], cfg, date(2026, 11, 10))))
        dup = calc.shift_flags(dict(SHIFT, id=None), [], cfg, date(2026, 11, 10), existing=[SHIFT])
        self.assertTrue(any("дубликат" in f for f in dup))
        self.assertFalse(any("обычного" in f for f in calc.shift_flags(typo, self.history(3), cfg, date(2026, 11, 10))))  # мало истории
        self.assertFalse(any("обычного" in f for f in calc.shift_flags(typo, self.history(), calc.cfg_of({"sale_price_l": 2500, "anomaly_pct": 0}), date(2026, 11, 10))))

    def test_per_liter_goal_deadlines_records(self):
        rows = calc.day_rows([SHIFT], {1: [60000]}, [OPEN], CFG, "2026-11-10", "2026-11-10")
        t = calc.totals(rows)
        pl = calc.per_liter(t)
        self.assertAlmostEqual(pl["revenue"], 2_000_000 / 800)
        self.assertAlmostEqual(pl["profit"], t["profit"] / 800)
        self.assertIsNone(calc.per_liter(calc.totals([])))
        pct, bar = calc.goal_progress(750, 1000)
        self.assertEqual((pct, bar), (75.0, "▰▰▰▰▰▰▰▰▱▱"))
        self.assertIsNone(calc.goal_progress(5, 0))
        over, up = calc.tax_deadlines(date(2026, 11, 17), 20, 3)
        self.assertEqual(up[0], ("2026-10", date(2026, 11, 20)))
        self.assertEqual(up[1], ("2026-11", date(2026, 12, 20)))
        self.assertEqual(over[0], ("2026-09", date(2026, 10, 20)))
        o2, u2 = calc.tax_deadlines(date(2026, 11, 21), 20, 2)
        self.assertEqual(u2[0][1], date(2026, 12, 20))
        lit, rev = calc.records(calc.day_rows([SHIFT, dict(SHIFT, id=2, ts="t9", date="2026-11-11", m1e=1900)], {}, [OPEN], CFG))
        self.assertEqual(lit["date"], "2026-11-11")
        self.assertEqual(calc.records([]), (None, None))


class QuickReport(unittest.TestCase):
    PREV = {1: 1000, 2: 1000, 3: 0}
    TODAY = date(2026, 11, 17)
    FULLTXT = "дата вчера\nсч1 1500\nсч2 1000-2300\nсч3 0\nнал 1 млн\nтерм 500к\nклик 300000\nпейнет 200000\nприход 1000 0.54 5000\nрасход 50000 бензин для генератора\nрасход 10 000 вода\nнакладная №77\nкомментарий всё хорошо"

    def test_full_report(self):
        d, errs = calc.parse_quick_report(self.FULLTXT, self.PREV, self.TODAY)
        self.assertEqual(errs, [])
        self.assertEqual(d["date"], "2026-11-16")
        self.assertEqual((d["m1s"], d["m1e"], d["m2s"], d["m2e"], d["m3s"], d["m3e"]), (1000, 1500, 1000, 2300, 0, 0))
        self.assertEqual((d["cash"], d["terminal"], d["click"], d["paynet"]), (1_000_000, 500_000, 300_000, 200_000))
        self.assertEqual((d["arr_kg"], d["arr_density"], d["arr_price"]), (1000, 0.54, 5000))
        self.assertEqual(d["exps"], [(50000, "бензин для генератора"), (10000, "вода")])
        self.assertEqual((d["arr_note"], d["comment"]), ("№77", "всё хорошо"))

    def test_lenient_formats(self):
        d, errs = calc.parse_quick_report("Сч1: 1500\nСчётчик 2 - 2300\nНал 100к; Терминал 50 тыс\nПейнет 20000", self.PREV, self.TODAY)
        self.assertEqual(errs, [])
        self.assertEqual((d["m1e"], d["m2e"], d["m3e"], d["cash"], d["terminal"], d["paynet"], d["click"]), (1500, 2300, 0, 100_000, 50_000, 20_000, 0))
        self.assertEqual(d["date"], "2026-11-17")

    def test_errors_are_reported(self):
        _, errs = calc.parse_quick_report("сч1 500\nнал abc\nпривет\nприход 1000\nрасход много", self.PREV, self.TODAY)
        text = " | ".join(errs)
        self.assertIn("конец (500) меньше начала (1000)", text)
        self.assertIn("нужна сумма", text)
        self.assertIn("не понял строку", text)
        self.assertIn("нет цены закупа", text)
        _, e2 = calc.parse_quick_report("сч1 100", {}, self.TODAY)
        self.assertEqual(len(e2), 3)  # нет прошлой смены — просят «начало-конец» и показания остальных
        d3, e3 = calc.parse_quick_report("сч1 1000-1500\nсч2 1000-1000\nсч3 0-0", {}, self.TODAY, default_price=5000)
        self.assertEqual(e3, [])

    def test_default_density_and_price(self):
        d, errs = calc.parse_quick_report("сч1 1100\nприход 2000", self.PREV, self.TODAY, default_density=0.56, default_price=4800)
        self.assertEqual(errs, [])
        self.assertEqual((d["arr_density"], d["arr_price"]), (0.56, 4800))

    def test_parse_range(self):
        self.assertEqual(calc.parse_range("01.11 - 15.11", self.TODAY), ("2026-11-01", "2026-11-15"))
        self.assertEqual(calc.parse_range("15.11 - 01.11", self.TODAY), ("2026-11-01", "2026-11-15"))
        self.assertEqual(calc.parse_range("15.11", self.TODAY), ("2026-11-15", "2026-11-15"))
        for bad in ("", "abc", "1.11 2.11 3.11", "31.02"):
            self.assertIsNone(calc.parse_range(bad, self.TODAY), bad)


if __name__ == "__main__":
    unittest.main()
