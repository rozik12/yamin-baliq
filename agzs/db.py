"""SQLite-хранилище АГЗС: пользователи и роли, настройки, смены, расходы, корректировки остатка."""
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5))  # Ташкент


def now():
    return datetime.now(TZ)


class DB:
    def __init__(self, path=":memory:", admin_ids=()):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.admin_ids = set(admin_ids)
        self.c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, role TEXT, joined TEXT);
        CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY, role TEXT, used_by INTEGER);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS shifts(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, ts TEXT, user_id INTEGER, user_name TEXT,
            m1s REAL, m1e REAL, m2s REAL, m2e REAL, m3s REAL, m3e REAL,
            cash REAL, terminal REAL, click REAL, paynet REAL, arr_kg REAL, arr_density REAL, arr_price REAL);
        CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT, shift_id INTEGER, amount REAL, comment TEXT);
        CREATE TABLE IF NOT EXISTS adjustments(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, ts TEXT, liters REAL, kg REAL, note TEXT, user_name TEXT);
        """)

    # --- роли
    def role_of(self, uid):
        if uid in self.admin_ids:
            return "admin"
        r = self.c.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
        return r["role"] if r else None

    def set_user(self, uid, name, role):
        self.c.execute("INSERT INTO users(id,name,role,joined) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, role=excluded.role",
                       (uid, name, role, now().isoformat()))
        self.c.commit()

    def remove_user(self, uid):
        self.c.execute("DELETE FROM users WHERE id=?", (uid,))
        self.c.commit()

    def users(self):
        return [dict(r) for r in self.c.execute("SELECT * FROM users ORDER BY role, name")]

    def new_invite(self, role):
        code = secrets.token_hex(6)
        self.c.execute("INSERT INTO invites(code,role) VALUES(?,?)", (code, role))
        self.c.commit()
        return code

    def redeem(self, code, uid, name):
        r = self.c.execute("SELECT role, used_by FROM invites WHERE code=?", (code,)).fetchone()
        if not r or r["used_by"]:
            return None
        self.c.execute("UPDATE invites SET used_by=? WHERE code=?", (uid, code))
        self.set_user(uid, name, r["role"])
        return r["role"]

    def staff_ids(self, role=None):
        ids = set(self.admin_ids) if role in (None, "admin") else set()
        q = "SELECT id FROM users" + (" WHERE role=?" if role else "")
        ids |= {r["id"] for r in self.c.execute(q, (role,) if role else ())}
        return ids

    # --- настройки
    def settings(self):
        return {r["key"]: r["value"] for r in self.c.execute("SELECT key, value FROM settings")}

    def set_setting(self, key, value):
        self.c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        self.c.commit()

    # --- смены
    def add_shift(self, uid, uname, d, expenses=()):
        cur = self.c.execute(
            "INSERT INTO shifts(date,ts,user_id,user_name,m1s,m1e,m2s,m2e,m3s,m3e,cash,terminal,click,paynet,arr_kg,arr_density,arr_price) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["date"], now().isoformat(timespec="microseconds"), uid, uname, d["m1s"], d["m1e"], d["m2s"], d["m2e"], d["m3s"], d["m3e"],
             d["cash"], d["terminal"], d["click"], d["paynet"], d.get("arr_kg", 0), d.get("arr_density", 0), d.get("arr_price", 0)))
        sid = cur.lastrowid
        for amount, comment in expenses:
            self.c.execute("INSERT INTO expenses(shift_id,amount,comment) VALUES(?,?,?)", (sid, amount, comment))
        self.c.commit()
        return sid

    def shifts(self, start=None, end=None, uid=None):
        q, a = "SELECT * FROM shifts WHERE 1=1", []
        if start:
            q += " AND date>=?"
            a.append(start)
        if end:
            q += " AND date<=?"
            a.append(end)
        if uid:
            q += " AND user_id=?"
            a.append(uid)
        return [dict(r) for r in self.c.execute(q + " ORDER BY date, ts, id", a)]

    def shift(self, sid):
        r = self.c.execute("SELECT * FROM shifts WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None

    def delete_shift(self, sid):
        self.c.execute("DELETE FROM expenses WHERE shift_id=?", (sid,))
        n = self.c.execute("DELETE FROM shifts WHERE id=?", (sid,)).rowcount
        self.c.commit()
        return n

    def expense_rows(self):
        return [dict(r) for r in self.c.execute("SELECT * FROM expenses ORDER BY shift_id, id")]

    def expenses_map(self):
        m = {}
        for r in self.expense_rows():
            m.setdefault(r["shift_id"], []).append(r["amount"])
        return m

    def last_meter_ends(self):
        """Конечные показания счётчиков в последней смене: {1: .., 2: .., 3: ..} или {}."""
        r = self.c.execute("SELECT m1e, m2e, m3e FROM shifts ORDER BY date DESC, ts DESC, id DESC LIMIT 1").fetchone()
        return {1: r["m1e"], 2: r["m2e"], 3: r["m3e"]} if r else {}

    # --- корректировки остатка (начальный остаток, замеры)
    def add_adjustment(self, date, liters, kg, note="", user_name=""):
        self.c.execute("INSERT INTO adjustments(date,ts,liters,kg,note,user_name) VALUES(?,?,?,?,?,?)",
                       (date, now().isoformat(timespec="microseconds"), liters, kg, note, user_name))
        self.c.commit()

    def adjustments(self):
        return [dict(r) for r in self.c.execute("SELECT * FROM adjustments ORDER BY date, ts, id")]
