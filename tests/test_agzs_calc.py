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


if __name__ == "__main__":
    unittest.main()
