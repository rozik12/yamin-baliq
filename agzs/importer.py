"""Импорт старых смен из Excel: шаблон для заполнения, разбор файла, добавление без дубликатов."""
import io
from datetime import date, datetime

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

import calc

COLUMNS = ["Дата", "Счётчик 1 начало", "Счётчик 1 конец", "Счётчик 2 начало", "Счётчик 2 конец", "Счётчик 3 начало", "Счётчик 3 конец",
           "Наличные", "Терминал", "Click", "Paynet QR", "Приход, кг", "Плотность", "Цена закупа за кг", "Расходы", "Накладная", "Комментарий"]
MAX_ROWS = 1000


def template_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Смены"
    ws.append(COLUMNS)
    ws.append(["15.11.2026", 1000, 1500, 2000, 2300, 0, 0, 1000000, 500000, 300000, 200000, 1000, 0.54, 5000, "50000 бензин; 10000 вода", "накладная 77", ""])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2E7D5B")
        c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34
    for i, col in enumerate(ws.columns, 1):
        ws.column_dimensions[col[0].column_letter].width = 16 if i != 15 else 30
    ws.freeze_panes = "A2"
    h = wb.create_sheet("Инструкция")
    for line in ["Одна строка = одна смена. Вторую строку (пример) замените своими данными или удалите.",
                 "Дата: 15.11.2026 или обычная дата Excel. Показания счётчиков в литрах: начало и конец.",
                 "Если счётчик не работал, поставьте начало = концу. Деньги в сумах. Можно писать 1,5 млн или 500к.",
                 "Приход: масса в кг, плотность (0,54 или 0,56) и цена закупа за кг. Если прихода не было, оставьте пустым.",
                 "Расходы: «сумма описание» через точку с запятой: 50000 бензин; 10000 вода.",
                 "Бот покажет, что найдено и сколько ошибок, и только после вашего подтверждения добавит смены. Уже существующие (та же дата и показания) пропускаются."]:
        h.append([line])
    h.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cell_num(v):
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v) if v >= 0 else None
    return calc.parse_num(str(v))


def _cell_date(v, today):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return calc.parse_date(str(v or "").strip(), today)


def parse_workbook(data, today):
    """Читает файл. Возвращает (смены, ошибки): смена — словарь для db.add_shift (+ exps); ошибка — строка с номером строки Excel."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception:
        return [], ["Не удалось открыть файл. Нужен Excel (.xlsx), заполненный по шаблону."]
    ws = wb["Смены"] if "Смены" in wb.sheetnames else wb.worksheets[0]
    shifts, errors = [], []
    for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if n - 1 > MAX_ROWS:
            errors.append(f"Слишком много строк: обработаны первые {MAX_ROWS}.")
            break
        row = list(row) + [None] * (len(COLUMNS) - len(row))
        if all(v in (None, "") for v in row[:14]):
            continue
        d = {"exps": [], "photos": []}
        d["date"] = _cell_date(row[0], today)
        bad = []
        if not d["date"]:
            bad.append("не понял дату")
        for i, k in enumerate(("m1s", "m1e", "m2s", "m2e", "m3s", "m3e")):
            v = _cell_num(row[1 + i])
            if v is None:
                bad.append(f"плохое число в «{COLUMNS[1 + i]}»")
            d[k] = v or 0.0
        for k, col in (("cash", 7), ("terminal", 8), ("click", 9), ("paynet", 10), ("arr_kg", 11), ("arr_density", 12), ("arr_price", 13)):
            v = _cell_num(row[col])
            if v is None:
                bad.append(f"плохое число в «{COLUMNS[col]}»")
            d[k] = v or 0.0
        for k in ("m1", "m2", "m3"):
            if d[k + "e"] < d[k + "s"]:
                bad.append(f"конец счётчика {k[1]} меньше начала")
        if d["arr_kg"] > 0 and not (0.4 <= d["arr_density"] <= 0.7):
            d["arr_density"] = 0.54 if not d["arr_density"] else d["arr_density"]
            if not 0.4 <= d["arr_density"] <= 0.7:
                bad.append("плотность должна быть от 0,4 до 0,7")
        if d["arr_kg"] > 0 and d["arr_price"] <= 0:
            bad.append("есть приход, но нет цены закупа")
        for part in str(row[14] or "").split(";"):
            part = part.strip()
            if not part:
                continue
            tokens = part.split(maxsplit=1)
            amount = calc.parse_num(tokens[0])
            if not amount:
                bad.append(f"не понял расход «{part[:20]}»")
            else:
                d["exps"].append((amount, tokens[1].strip() if len(tokens) > 1 else ""))
        d["arr_note"] = str(row[15] or "")[:120]
        d["comment"] = str(row[16] or "")[:300]
        if bad:
            errors.append(f"Строка {n}: " + "; ".join(bad))
        else:
            shifts.append(d)
    wb.close()
    return shifts, errors


def apply_rows(db, shifts, user_name="импорт"):
    """Добавляет смены, пропуская уже существующие (та же дата и те же показания). Возвращает (добавлено, пропущено)."""
    added = skipped = 0
    for d in shifts:
        same = [x for x in db.shifts(d["date"], d["date"]) if all(float(x[k] or 0) == float(d[k]) for k in ("m1s", "m1e", "m2s", "m2e", "m3s", "m3e"))]
        if same:
            skipped += 1
            continue
        db.add_shift(0, user_name, d, d["exps"], status="approved")
        added += 1
    return added, skipped
