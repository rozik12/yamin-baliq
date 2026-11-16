"""PDF-отчёт за период: сводка по деньгам и налогам, расходы по статьям, три графика. Можно переслать бухгалтеру или в налоговую."""
import io
from pathlib import Path

import calc
import charts
from calc import PAY_NAMES, fnum

_fonts_ready = []


def _font_dir():
    import matplotlib
    return Path(matplotlib.get_data_path()) / "fonts" / "ttf"


def _register():
    if _fonts_ready:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    pdfmetrics.registerFont(TTFont("DJ", str(_font_dir() / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DJ-B", str(_font_dir() / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily("DJ", normal="DJ", bold="DJ-B", italic="DJ", boldItalic="DJ-B")
    _fonts_ready.append(1)


def build_pdf(rows, label, cfg, expense_rows=(), station="Navro'z Propan Service", with_charts=True):
    """Возвращает байты PDF."""
    _register()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    cfg = calc.cfg_of(cfg)
    t = calc.totals(rows)
    h1 = ParagraphStyle("h1", fontName="DJ-B", fontSize=17, leading=21, spaceAfter=4)
    h2 = ParagraphStyle("h2", fontName="DJ-B", fontSize=12, leading=15, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", fontName="DJ", fontSize=9, leading=12)
    story = [Paragraph(f"Отчёт АГЗС · {label}", h1)]
    if rows:
        story.append(Paragraph(f"{station} · период {rows[0]['date'][8:10]}.{rows[0]['date'][5:7]}.{rows[0]['date'][:4]} — {rows[-1]['date'][8:10]}.{rows[-1]['date'][5:7]}.{rows[-1]['date'][:4]}", body))
    story.append(Spacer(1, 6))

    def table(data, widths, bold_last=False):
        tb = Table(data, colWidths=widths)
        style = [("FONT", (0, 0), (-1, -1), "DJ", 9), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#BBBBBB")), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                 ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F3EC")), ("FONT", (0, 0), (-1, 0), "DJ-B", 9)]
        if bold_last:
            style += [("FONT", (0, -1), (-1, -1), "DJ-B", 10), ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F2F2F2"))]
        tb.setStyle(TableStyle(style))
        return tb

    margin = 100 * t["profit"] / t["revenue"] if t["revenue"] else 0
    story.append(Paragraph("Итоги", h2))
    story.append(table([["Показатель", "Значение"], ["Смен", fnum(t["shifts"])], ["Пролив, л", fnum(t["sold_l"])], ["Пролив, кг", fnum(t["sold_kg"])],
                        ["Приход газа, кг", fnum(t["arr_kg"])], ["Приход газа, л", fnum(t["arr_l"])]], [100 * mm, 70 * mm]))
    story.append(Paragraph("Выручка", h2))
    pay = [["Способ оплаты", "Сумма, сум", "Доля"]] + [[PAY_NAMES[k], fnum(v), f"{100 * v / t['revenue']:.1f}%" if t["revenue"] else "—"] for k, v in t["pay"].items()]
    pay.append(["Всего", fnum(t["revenue"]), "100%" if t["revenue"] else "—"])
    story.append(table(pay, [80 * mm, 55 * mm, 35 * mm], bold_last=True))
    story.append(Paragraph("Налоги и затраты", h2))
    tax = [["Статья", "Сумма, сум"], [f"НДС исходящий ({cfg['vat_rate']:g}%)", fnum(t["vat_out"])], ["НДС входящий (с закупа газа)", fnum(t["vat_in"])],
           ["НДС к уплате" if t["vat_net"] >= 0 else "НДС к зачёту", fnum(abs(t["vat_net"]))], ["Акциз", fnum(t["excise"])],
           ["Закуп газа (оплачено)", fnum(t["purchase"])], ["Расходы на месте", fnum(t["expenses"])], ["Постоянные расходы (доля периода)", fnum(t["fixed"])],
           ["ЧИСТАЯ ПРИБЫЛЬ", fnum(t["profit"])]]
    story.append(table(tax, [100 * mm, 70 * mm], bold_last=True))
    story.append(Paragraph(f"Маржа: {margin:.1f}% выручки. Прибыль считается {'по закупу в день прихода газа' if cfg['profit_mode'] != 'cogs' else 'по себестоимости проданного газа'}, без налога на прибыль.", body))
    cats = calc.expense_breakdown(list(expense_rows))
    if cats:
        story.append(Paragraph("Расходы на месте по статьям", h2))
        story.append(table([["Статья", "Сумма, сум", "Записей"]] + [[c[0], fnum(c[1]), str(c[2])] for c in cats], [90 * mm, 50 * mm, 30 * mm]))
    if with_charts and any(r["shifts"] for r in rows):
        story.append(PageBreak())
        story.append(Paragraph("Графики", h2))
        for kind in ("sales", "revenue", "profit"):
            png = charts.render(kind, rows, cfg, label)
            story.append(Image(io.BytesIO(png), width=170 * mm, height=170 * mm * 6.2 / 11))
            story.append(Spacer(1, 4))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title=f"Отчёт АГЗС · {label}")
    doc.build(story)
    return buf.getvalue()
