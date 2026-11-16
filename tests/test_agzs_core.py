import io
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agzs"))
import calc  # noqa: E402
import charts  # noqa: E402
import export  # noqa: E402
from db import DB  # noqa: E402

SHIFT = {"date": "2026-11-10", "m1s": 1000, "m1e": 1500, "m2s": 2000, "m2e": 2300, "m3s": 0, "m3e": 0, "cash": 1_000_000, "terminal": 500_000,
         "click": 300_000, "paynet": 200_000, "arr_kg": 1000, "arr_density": 0.54, "arr_price": 5000}


def make_db():
    db = DB(":memory:", admin_ids=[8219891075])
    db.add_adjustment("2026-11-09", 1000, 540, "начальный остаток", "Админ")
    return db


class Storage(unittest.TestCase):
    def test_roles_and_invites(self):
        db = make_db()
        self.assertEqual(db.role_of(8219891075), "admin")
        self.assertIsNone(db.role_of(5))
        code = db.new_invite("operator")
        self.assertEqual(db.redeem(code, 5, "Али"), "operator")
        self.assertIsNone(db.redeem(code, 6, "Вали"))  # одноразовая
        self.assertEqual(db.role_of(5), "operator")
        self.assertEqual(db.staff_ids("operator"), {5})
        self.assertIn(8219891075, db.staff_ids("admin"))
        db.remove_user(5)
        self.assertIsNone(db.role_of(5))

    def test_shifts_expenses_meters_delete(self):
        db = make_db()
        self.assertEqual(db.last_meter_ends(), {})
        sid = db.add_shift(5, "Али", SHIFT, [(50000, "бензин"), (10000, "вода")])
        self.assertEqual(db.last_meter_ends(), {1: 1500, 2: 2300, 3: 0})
        self.assertEqual(db.expenses_map()[sid], [50000, 10000])
        self.assertEqual(len(db.shifts("2026-11-10", "2026-11-10")), 1)
        self.assertEqual(len(db.shifts("2026-11-11")), 0)
        self.assertEqual(len(db.shifts(uid=7)), 0)
        self.assertEqual(db.delete_shift(sid), 1)
        self.assertEqual(db.expense_rows(), [])

    def test_settings(self):
        db = make_db()
        db.set_setting("vat_rate", 15)
        db.set_setting("vat_rate", 12)
        self.assertEqual(db.settings(), {"vat_rate": "12"})
        self.assertEqual(calc.cfg_of(db.settings())["vat_rate"], 12.0)


def sample_rows(days=10):
    db = make_db()
    start = date(2026, 11, 1)
    end = 1000
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        s = dict(SHIFT, date=d, m1s=end, m1e=end + 400 + 20 * i, m2s=0, m2e=100, arr_kg=1000 if i % 4 == 0 else 0)
        end = s["m1e"]
        db.add_shift(5, "Али", s, [(20000 + 1000 * i, "расход")])
    cfg = {"sale_price_l": 3000, "excise_per_l": 10, "price_buy_kg": 5000, "low_stock_l": 300, "tank_capacity_l": 5000}
    rows = calc.day_rows(db.shifts(), db.expenses_map(), db.adjustments(), cfg)
    return db, rows, cfg


class Charts(unittest.TestCase):
    def test_every_chart_is_png(self):
        _, rows, cfg = sample_rows()
        for kind in charts.KINDS:
            png = charts.render(kind, rows, calc.cfg_of(cfg), "неделя")
            self.assertTrue(png.startswith(b"\x89PNG"), kind)
            self.assertGreater(len(png), 8000, kind)

    def test_weekly_aggregation_for_long_period_and_empty_stock(self):
        _, rows, cfg = sample_rows(10)
        long_rows = calc.day_rows(make_db().shifts(), {}, [], cfg, "2026-01-01", "2026-06-30")
        agg, step = charts.aggregate(long_rows)
        self.assertEqual(step, "по неделям")
        self.assertLess(len(agg), 30)
        self.assertTrue(charts.render("sales", long_rows, calc.cfg_of(cfg), "всё время").startswith(b"\x89PNG"))
        self.assertTrue(charts.render("stock", long_rows, calc.cfg_of(cfg), "всё время").startswith(b"\x89PNG"))
        self.assertEqual(charts.aggregate(rows)[1], "по дням")

    def test_special_charts(self):
        _, rows, cfg = sample_rows(14)
        c = calc.cfg_of(cfg)
        for kind in ("weekday", "structure"):
            png = charts.render(kind, rows, c, "месяц")
            self.assertTrue(png.startswith(b"\x89PNG"), kind)
        pts = [{"date": "2026-11-01", "price": 5000, "kg": 1000}, {"date": "2026-11-05", "price": 5200, "kg": 900}]
        self.assertGreater(len(charts.render("buy_price", rows, c, "всё время", pts)), 8000)
        self.assertTrue(charts.render("buy_price", rows, c, "всё время", []).startswith(b"\x89PNG"))
        self.assertTrue(charts.render("structure", [], c, "пусто").startswith(b"\x89PNG"))
        fixed = calc.day_rows([], {}, [], c, "2026-11-01", "2026-11-03", fixed_monthly=3_000_000)
        self.assertTrue(charts.render("expenses", fixed, c, "неделя").startswith(b"\x89PNG"))

    def test_chart_totals_match(self):
        _, rows, _ = sample_rows(3)
        agg, _ = charts.aggregate(rows)
        self.assertEqual(sum(r["revenue"] for r in agg), sum(r["revenue"] for r in rows))


class Excel(unittest.TestCase):
    def test_workbook_has_all_sheets_columns_and_totals(self):
        import openpyxl
        db, rows, cfg = sample_rows(5)
        data = export.build_xlsx(rows, db.shifts(), db.expense_rows(), db.adjustments(), cfg)
        wb = openpyxl.load_workbook(io.BytesIO(data))
        self.assertEqual(wb.sheetnames, ["Сводка по дням", "Смены", "Расходы", "Приход газа", "Корректировки остатка", "Поставщик", "Инкассация", "Настройки"])
        ws = wb["Сводка по дням"]
        header = [c.value for c in ws[1]]
        for col in ("Дата", "Пролив счётчик 1, л", "Пролив всего, л", "Приход газа, кг", "Наличные", "Paynet QR", "НДС к уплате", "Акциз",
                    "Закуп газа (оплачено)", "Расходы на месте", "Чистая прибыль", "Остаток, л", "Остаток, кг"):
            self.assertIn(col, header)
        self.assertEqual(ws.max_row, 1 + 5 + 1)  # заголовок + 5 дней + ИТОГО
        last = {h: c.value for h, c in zip(header, ws[ws.max_row])}
        self.assertEqual(last["Дата"], "ИТОГО")
        self.assertAlmostEqual(last["Выручка всего"], 5 * 2_000_000)
        t = calc.totals(rows)
        self.assertAlmostEqual(last["Чистая прибыль"], t["profit"], 4)
        self.assertEqual(wb["Смены"].max_row, 6)
        self.assertEqual(wb["Расходы"].max_row, 6)

    def test_extra_sheets_and_columns(self):
        import openpyxl
        db, rows, cfg = sample_rows(3)
        db.add_adjustment("2026-11-03", 900, 486, "замер", "Владелец", expected_l=1000, expected_kg=540)
        db.add_supplier_payment("2026-11-02", 2_000_000, "оплата", "Владелец")
        db.add_collection("2026-11-02", 500_000, "инкассация", "Владелец")
        data = export.build_xlsx(rows, db.shifts(), db.expense_rows(), db.adjustments(), cfg, db.supplier_payments(), db.collections())
        wb = openpyxl.load_workbook(io.BytesIO(data))
        adj = [[c.value for c in r] for r in wb["Корректировки остатка"].iter_rows(min_row=2)]
        self.assertIn([1000, -100], [r[3:5] for r in adj])
        sup = [[c.value for c in r] for r in wb["Поставщик"].iter_rows(min_row=2)]
        self.assertIn(-2_000_000, [r[2] for r in sup])
        self.assertEqual(wb["Инкассация"]["B2"].value, 500_000)
        header = [c.value for c in wb["Сводка по дням"][1]]
        self.assertIn("Постоянные расходы (доля дня)", header)
        self.assertIn("Статус", [c.value for c in wb["Смены"][1]])

    def test_empty_workbook(self):
        import openpyxl
        data = export.build_xlsx([], [], [], [], {})
        self.assertIn("Сводка по дням", openpyxl.load_workbook(io.BytesIO(data)).sheetnames)


if __name__ == "__main__":
    unittest.main()
