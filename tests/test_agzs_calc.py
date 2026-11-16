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


if __name__ == "__main__":
    unittest.main()
