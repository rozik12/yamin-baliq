"""Общее состояние бота АГЗС: база, роли, настройки, периоды. Остальные модули берут всё отсюда (core.db и т.д.)."""
import os
from datetime import timedelta

import calc
from db import DB, now

ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "8219891075").replace(" ", "").split(",") if x.isdigit()}
db = DB(os.getenv("DB_PATH", "agzs.db"), ADMINS)


# Должности. Ключ "admin" хранится в базе для владельца (так было с самого начала), остальные — новые.
POSITIONS = {"admin": "Владелец", "manager": "Управляющий", "accountant": "Бухгалтер", "operator": "Оператор", "observer": "Наблюдатель"}
POS_ORDER = ["admin", "manager", "accountant", "operator", "observer"]
ALL_PERMS = {"shift", "finance", "accounting", "shifts_view", "approve", "stock_view", "stock_edit", "settings", "staff"}
PERMS = {
    "admin": set(ALL_PERMS),
    "manager": {"shift", "finance", "accounting", "shifts_view", "approve", "stock_view", "stock_edit"},
    "accountant": {"finance", "accounting", "shifts_view", "stock_view"},
    "operator": {"shift"},
    "observer": {"finance", "stock_view"},
}
POS_INFO = {
    "admin": "всё: финансы, налоги, настройки, персонал, правка и удаление смен, остаток газа",
    "manager": "сдаёт и проверяет смены (принять, вернуть, исправить), финансы, бухгалтерия, остаток газа; без настроек и персонала",
    "accountant": "финансы, налоги, поставщик, касса, постоянные расходы, графики и Excel; смены и остаток только смотрит",
    "operator": "только сдаёт сменные отчёты и видит свои смены",
    "observer": "только смотрит финансы, графики, Excel и остаток газа",
}


def role(uid):
    return db.role_of(uid)


def can(uid, perm):
    r = role(uid)
    return bool(r) and perm in PERMS.get(r, ())


def is_admin(uid):
    """Владелец (полные права)."""
    return role(uid) == "admin"


def ids_with(perm):
    """Кто получает сообщения/кнопки с этим правом (владельцы из настроек сервера тоже)."""
    out = {a for a in db.admin_ids if perm in PERMS["admin"]}
    for u in db.users():
        if perm in PERMS.get(u["role"], ()):
            out.add(u["id"])
    return out


def person_name(uid):
    u = db.get_user(uid) or db.seen_user(uid)
    return (u or {}).get("name") or str(uid)


def touch_user(u):
    """Запоминает, кто писал боту, и применяет назначение по @нику, если оно было заранее. Возвращает выданную должность или None."""
    if not u:
        return None
    nm = u.full_name or u.username or str(u.id)
    db.touch(u.id, u.username, nm)
    g = db.pop_grant(u.username) if u.username else None
    if g:
        db.set_user(u.id, nm, g["role"])
        db.log("система", "grant_applied", f"@{u.username} → {POSITIONS.get(g['role'], g['role'])}")
        return g["role"]
    return None


for _u in os.getenv("OWNER_USERNAMES", "").replace(" ", "").split(","):
    if _u.lstrip("@"):
        db.add_grant(_u.lstrip("@"), "admin", "настройки сервера")


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
