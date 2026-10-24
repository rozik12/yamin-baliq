"""Автоматические советы по продажам. Возвращает список (ключ_текста, параметры) — тексты в i18n."""
from datetime import datetime


def advice(store, start, end):
    out = []
    rows = store.between(start, end)
    sales = [r for r in rows if r["kind"] == "sale"]
    if not sales:
        return [("adv_nosales", {})]
    days = (end - start).days
    sold, made = {}, {}
    for r in rows:
        d = sold if r["kind"] == "sale" else made
        d[r["product_id"]] = d.get(r["product_id"], 0) + r["qty"]
    total = sum(sold.values())

    # 1. цены не заданы
    if sum(r["amount"] for r in sales) == 0:
        out.append(("adv_prices", {}))

    # 2. лидер и доля
    top_id, top_q = max(sold.items(), key=lambda kv: kv[1])
    share = round(100 * top_q / total)
    if share >= 30 and len(sold) > 2:
        out.append(("adv_top", {"pid": top_id, "p": share}))

    # 3. товары без продаж (активные)
    dead = [p["id"] for p in store.products() if p["id"] not in sold]
    if dead and days >= 3:
        out.append(("adv_dead", {"pids": dead[:4]}))

    # 4. перепроизводство / нехватка (только если ведут «Произведено»)
    for pid, m in made.items():
        s = sold.get(pid, 0)
        if m >= 10 and s < 0.7 * m:
            out.append(("adv_over", {"pid": pid, "made": m, "sold": s}))
        elif s > m > 0 and s >= 10:
            out.append(("adv_under", {"pid": pid, "made": m, "sold": s}))
    out = out[:6]

    # 5. пиковый час
    hours = {}
    for r in sales:
        hours[int(r["ts"][11:13])] = hours.get(int(r["ts"][11:13]), 0) + r["qty"]
    ph = max(hours.items(), key=lambda kv: kv[1])
    if len(hours) >= 3:
        out.append(("adv_peak", {"h": f"{ph[0]:02d}", "h2": f"{(ph[0] + 1) % 24:02d}"}))

    # 6. день недели (нужно ≥14 дней)
    if days >= 14:
        wd = {}
        for r in sales:
            k = datetime.fromisoformat(r["ts"]).weekday()
            wd[k] = wd.get(k, 0) + r["qty"]
        if len(wd) >= 5:
            best, worst = max(wd, key=wd.get), min(wd, key=wd.get)
            if wd[best] > 1.5 * wd[worst]:
                out.append(("adv_weekday", {"best": best, "worst": worst}))

    # 7. динамика: вторая половина периода против первой (нужно ≥6 дней)
    if days >= 6:
        ser = store.daily_series(start, end)
        half = len(ser) // 2
        a, b = sum(x[1] for x in ser[:half]), sum(x[1] for x in ser[half:])
        if a > 0:
            ch = round(100 * (b - a) / a)
            if ch >= 15:
                out.append(("adv_up", {"n": ch}))
            elif ch <= -15:
                out.append(("adv_down", {"n": abs(ch)}))
    return out
