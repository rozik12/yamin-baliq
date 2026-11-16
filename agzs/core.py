"""Общее состояние бота АГЗС: база, роли, настройки, периоды. Остальные модули берут всё отсюда (core.db и т.д.)."""
import os
from datetime import timedelta

import calc
from db import DB, now

ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "8219891075").replace(" ", "").split(",") if x.isdigit()}
db = DB(os.getenv("DB_PATH", "agzs.db"), ADMINS)


def role(uid):
    return db.role_of(uid)


def is_admin(uid):
    return role(uid) == "admin"


def today():
    return now().date()


def cfg():
    return calc.cfg_of(db.settings())


def fixed_monthly():
    return sum(f["amount"] for f in db.fixed_list())


def uname(u):
    return u.full_name or u.username or str(u.id)


def lang(uid):
    """Язык интерфейса: у владельцев всегда русский, у операторов — выбранный (ru/uz)."""
    return db.get_lang(uid) if role(uid) == "operator" else "ru"


def rows_for(start, end):
    return calc.day_rows(db.shifts(), db.expenses_map(), db.adjustments(), cfg(), start, end, fixed_monthly())


def tank_state():
    _, st, _ = calc.tank_run(db.shifts(), db.adjustments(), cfg())
    return st


def period_range(code):
    t = today()
    first = db.shifts()[:1]
    if code == "today":
        return t.isoformat(), t.isoformat(), "сегодня"
    if code == "yday":
        y = (t - timedelta(days=1)).isoformat()
        return y, y, "вчера"
    if code == "week":
        return (t - timedelta(days=6)).isoformat(), t.isoformat(), "неделя"
    if code == "month":
        return (t - timedelta(days=29)).isoformat(), t.isoformat(), "месяц"
    return (first[0]["date"] if first else t.isoformat()), t.isoformat(), "всё время"


def stock_forecast():
    """Остаток + прогноз «на сколько дней хватит» по последним 14 дням."""
    st = tank_state()
    t = today()
    rows = rows_for((t - timedelta(days=13)).isoformat(), t.isoformat())
    f = calc.forecast(rows, st["liters"])
    return st, f
