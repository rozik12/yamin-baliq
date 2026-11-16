"""Графики АГЗС (matplotlib): продажи, выручка, расходы, чистая прибыль, остаток газа."""
import io
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from calc import PAY_KEYS, PAY_NAMES, fnum  # noqa: E402

GREEN, RED, BLUE, ORANGE, PURPLE, TEAL, GREY = "#2E9E5B", "#D64545", "#2F6FDE", "#F29F3D", "#8A5CD0", "#17A2A2", "#8C95A3"
KINDS = {"sales": "Продажи (проливы)", "revenue": "Выручка", "expenses": "Расходы", "profit": "Чистая прибыль", "stock": "Остаток газа"}
MAX_DAILY_POINTS = 100


def aggregate(rows):
    """Больше ~3 месяцев — группируем по неделям, чтобы график читался. Возвращает (строки, подпись шага)."""
    if len(rows) <= MAX_DAILY_POINTS:
        return rows, "по дням"
    out, bucket = [], []
    for r in rows:
        if bucket and date.fromisoformat(r["date"]).isocalendar()[:2] != date.fromisoformat(bucket[0]["date"]).isocalendar()[:2]:
            out.append(_merge(bucket))
            bucket = []
        bucket.append(r)
    if bucket:
        out.append(_merge(bucket))
    return out, "по неделям"


def _merge(b):
    m = {"date": b[0]["date"], "m": [sum(x["m"][i] for x in b) for i in range(3)], "pay": {k: sum(x["pay"][k] for x in b) for k in PAY_KEYS}}
    for k in ("sold_l", "revenue", "expenses", "purchase", "vat_net", "excise", "taxes", "gas_cost", "profit", "shifts", "arr_kg"):
        m[k] = sum(x[k] for x in b)
    m["tank_l"], m["tank_kg"] = b[-1]["tank_l"], b[-1]["tank_kg"]
    return m


def short(v):
    """Короткая подпись денег над столбиком: 1,5 млн / 250 тыс."""
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f}".replace(".", ",") + " млн"
    if a >= 1000:
        return f"{v / 1000:.0f} тыс"
    return fnum(v)


def _fmt_axis(ax, unit=""):
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fnum(v) + unit))
    ax.grid(axis="y", alpha=0.3)
    ax.margins(y=0.12)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)


def _xticks(ax, rows):
    n = len(rows)
    step = max(1, n // 14)
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([f"{rows[i]['date'][8:10]}.{rows[i]['date'][5:7]}" for i in range(0, n, step)], rotation=45, ha="right", fontsize=9)


def _labels(ax, rows, totals, fmt=lambda v: fnum(v)):
    if len(rows) > 16:
        return
    for i, v in enumerate(totals):
        if v:
            ax.text(i, v, fmt(v), ha="center", va="bottom", fontsize=8)


def _stack(ax, rows, series):
    bottom = [0.0] * len(rows)
    for name, vals, color in series:
        if not any(vals):
            continue
        ax.bar(range(len(rows)), vals, bottom=bottom, label=name, color=color, width=0.72)
        bottom = [b + v for b, v in zip(bottom, vals)]
    return bottom


def render(kind, rows, cfg, period_label):
    """PNG-байты графика. rows — строки calc.day_rows."""
    if kind not in KINDS:
        raise ValueError(kind)
    rows, step = aggregate(rows)
    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=110)
    fig.patch.set_facecolor("white")
    n = len(rows)
    x = range(n)
    subtitle = ""
    if kind == "sales":
        series = [(f"Счётчик {i + 1}", [r["m"][i] for r in rows], c) for i, c in enumerate((BLUE, TEAL, ORANGE))]
        tot = _stack(ax, rows, series)
        _labels(ax, rows, tot)
        _fmt_axis(ax, " л")
        subtitle = f"Всего: {fnum(sum(tot))} л · в среднем {fnum(sum(tot) / max(n, 1))} л {'в день' if step == 'по дням' else 'в неделю'}"
    elif kind == "revenue":
        colors = {"cash": GREEN, "terminal": BLUE, "click": ORANGE, "paynet": PURPLE}
        tot = _stack(ax, rows, [(PAY_NAMES[k], [r["pay"][k] for r in rows], colors[k]) for k in PAY_KEYS])
        _labels(ax, rows, tot, short)
        _fmt_axis(ax, "")
        subtitle = f"Выручка: {fnum(sum(tot))} сум"
    elif kind == "expenses":
        tot = _stack(ax, rows, [("Закуп газа", [r["purchase"] for r in rows], BLUE), ("Расходы на месте", [r["expenses"] for r in rows], ORANGE),
                                ("Налоги (НДС к уплате + акциз)", [max(r["taxes"], 0) for r in rows], PURPLE)])
        _labels(ax, rows, tot, short)
        _fmt_axis(ax, "")
        subtitle = f"Закуп: {fnum(sum(r['purchase'] for r in rows))} · расходы: {fnum(sum(r['expenses'] for r in rows))} · налоги: {fnum(sum(max(r['taxes'], 0) for r in rows))} сум"
    elif kind == "profit":
        vals = [r["profit"] for r in rows]
        ax.bar(x, vals, color=[GREEN if v >= 0 else RED for v in vals], width=0.72)
        ax.axhline(0, color="black", lw=0.8)
        _labels(ax, rows, vals, short)
        _fmt_axis(ax, "")
        cum, c = [], 0.0
        for v in vals:
            c += v
            cum.append(c)
        ax2 = ax.twinx()
        ax2.plot(x, cum, color=BLUE, lw=2, marker="o", ms=3, label="Нарастающим итогом")
        ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fnum(v)))
        ax2.spines["top"].set_visible(False)
        ax2.legend(loc="upper left", fontsize=9)
        subtitle = f"Итого за период: {fnum(sum(vals))} сум"
    else:  # stock
        lit = [r["tank_l"] for r in rows]
        if not any(v is not None for v in lit):
            ax.text(0.5, 0.5, "Нет данных об остатке.\nЗадайте начальный остаток в разделе «Остаток газа».", ha="center", va="center", transform=ax.transAxes, fontsize=13)
            ax.axis("off")
        else:
            ys = [v if v is not None else float("nan") for v in lit]
            ax.fill_between(x, ys, color=TEAL, alpha=0.25)
            ax.plot(x, ys, color=TEAL, lw=2.4, marker="o", ms=4, label="Литры")
            _fmt_axis(ax, " л")
            low = float(cfg.get("low_stock_l") or 0)
            if low > 0:
                ax.axhline(low, color=RED, ls="--", lw=1.4, label=f"Порог {fnum(low)} л")
            cap = float(cfg.get("tank_capacity_l") or 0)
            if cap > 0:
                ax.axhline(cap, color=GREY, ls=":", lw=1.2, label=f"Ёмкость {fnum(cap)} л")
            ax2 = ax.twinx()
            ax2.plot(x, [r["tank_kg"] if r["tank_kg"] is not None else float("nan") for r in rows], color=ORANGE, lw=1.8, ls="-.", label="Килограммы")
            ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fnum(v) + " кг"))
            ax2.spines["top"].set_visible(False)
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=9)
            last = next((r for r in reversed(rows) if r["tank_l"] is not None), None)
            subtitle = f"Сейчас: {fnum(last['tank_l'])} л · {fnum(last['tank_kg'])} кг" if last else ""
    if kind != "stock" or any(r["tank_l"] is not None for r in rows):
        _xticks(ax, rows)
        if kind in ("sales", "revenue", "expenses"):
            fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=4, fontsize=10, frameon=False, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle(f"{KINDS[kind]} · {period_label} ({step})", fontsize=15, fontweight="bold", y=0.985)
    if subtitle:
        ax.set_title(subtitle, fontsize=11, color="#444", pad=10)
    fig.text(0.99, 0.01, "Navro'z Propan Service", ha="right", fontsize=8, color=GREY)
    fig.tight_layout(rect=(0, 0.07 if kind in ('sales', 'revenue', 'expenses') else 0.02, 1, 0.96))
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
