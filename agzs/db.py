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
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, user_name TEXT, action TEXT, details TEXT);
        CREATE TABLE IF NOT EXISTS supplier_payments(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, ts TEXT, amount REAL, note TEXT, user_name TEXT);
        CREATE TABLE IF NOT EXISTS collections(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, ts TEXT, amount REAL, note TEXT, user_name TEXT);
        CREATE TABLE IF NOT EXISTS fixed_expenses(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, amount REAL);
        CREATE TABLE IF NOT EXISTS langs(id INTEGER PRIMARY KEY, lang TEXT);
        """)
        for table, col, ddl in (("shifts", "status", "TEXT DEFAULT 'approved'"), ("shifts", "photos", "TEXT DEFAULT ''"), ("shifts", "arr_note", "TEXT DEFAULT ''"),
                                ("adjustments", "expected_l", "REAL"), ("adjustments", "expected_kg", "REAL")):
            try:
                self.c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # колонка уже есть

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
    def add_shift(self, uid, uname, d, expenses=(), status="approved"):
        cur = self.c.execute(
            "INSERT INTO shifts(date,ts,user_id,user_name,m1s,m1e,m2s,m2e,m3s,m3e,cash,terminal,click,paynet,arr_kg,arr_density,arr_price,status,photos,arr_note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["date"], now().isoformat(timespec="microseconds"), uid, uname, d["m1s"], d["m1e"], d["m2s"], d["m2e"], d["m3s"], d["m3e"],
             d["cash"], d["terminal"], d["click"], d["paynet"], d.get("arr_kg", 0), d.get("arr_density", 0), d.get("arr_price", 0),
             status, ",".join(d.get("photos", [])), d.get("arr_note", "")))
        sid = cur.lastrowid
        for amount, comment in expenses:
            self.c.execute("INSERT INTO expenses(shift_id,amount,comment) VALUES(?,?,?)", (sid, amount, comment))
        self.c.commit()
        return sid

    def update_shift(self, sid, d, expenses=(), status="approved"):
        """Исправление смены владельцем: заменяет данные и расходы, дата ввода и автор остаются."""
        self.c.execute(
            "UPDATE shifts SET date=?,m1s=?,m1e=?,m2s=?,m2e=?,m3s=?,m3e=?,cash=?,terminal=?,click=?,paynet=?,arr_kg=?,arr_density=?,arr_price=?,"
            "status=?,photos=?,arr_note=? WHERE id=?",
            (d["date"], d["m1s"], d["m1e"], d["m2s"], d["m2e"], d["m3s"], d["m3e"], d["cash"], d["terminal"], d["click"], d["paynet"],
             d.get("arr_kg", 0), d.get("arr_density", 0), d.get("arr_price", 0), status, ",".join(d.get("photos", [])), d.get("arr_note", ""), sid))
        self.c.execute("DELETE FROM expenses WHERE shift_id=?", (sid,))
        for amount, comment in expenses:
            self.c.execute("INSERT INTO expenses(shift_id,amount,comment) VALUES(?,?,?)", (sid, amount, comment))
        self.c.commit()

    def set_status(self, sid, status):
        n = self.c.execute("UPDATE shifts SET status=? WHERE id=?", (status, sid)).rowcount
        self.c.commit()
        return n

    def expenses_of(self, sid):
        return [(r["amount"], r["comment"]) for r in self.c.execute("SELECT amount, comment FROM expenses WHERE shift_id=? ORDER BY id", (sid,))]

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
    def add_adjustment(self, date, liters, kg, note="", user_name="", expected_l=None, expected_kg=None):
        self.c.execute("INSERT INTO adjustments(date,ts,liters,kg,note,user_name,expected_l,expected_kg) VALUES(?,?,?,?,?,?,?,?)",
                       (date, now().isoformat(timespec="microseconds"), liters, kg, note, user_name, expected_l, expected_kg))
        self.c.commit()

    def adjustments(self):
        return [dict(r) for r in self.c.execute("SELECT * FROM adjustments ORDER BY date, ts, id")]

    # --- журнал, поставщик, инкассация, постоянные расходы, язык
    def log(self, user_name, action, details=""):
        self.c.execute("INSERT INTO audit(ts,user_name,action,details) VALUES(?,?,?,?)", (now().isoformat(timespec="seconds"), user_name, action, details))
        self.c.commit()

    def audit(self, limit=25):
        return [dict(r) for r in self.c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]

    def add_supplier_payment(self, date, amount, note="", user_name=""):
        self.c.execute("INSERT INTO supplier_payments(date,ts,amount,note,user_name) VALUES(?,?,?,?,?)",
                       (date, now().isoformat(timespec="microseconds"), amount, note, user_name))
        self.c.commit()

    def supplier_payments(self):
        return [dict(r) for r in self.c.execute("SELECT * FROM supplier_payments ORDER BY date, id")]

    def add_collection(self, date, amount, note="", user_name=""):
        self.c.execute("INSERT INTO collections(date,ts,amount,note,user_name) VALUES(?,?,?,?,?)",
                       (date, now().isoformat(timespec="microseconds"), amount, note, user_name))
        self.c.commit()

    def collections(self):
        return [dict(r) for r in self.c.execute("SELECT * FROM collections ORDER BY date, id")]

    def fixed_list(self):
        return [dict(r) for r in self.c.execute("SELECT * FROM fixed_expenses ORDER BY id")]

    def add_fixed(self, name, amount):
        self.c.execute("INSERT INTO fixed_expenses(name,amount) VALUES(?,?)", (name, amount))
        self.c.commit()

    def remove_fixed(self, fid):
        self.c.execute("DELETE FROM fixed_expenses WHERE id=?", (fid,))
        self.c.commit()

    def get_lang(self, uid):
        r = self.c.execute("SELECT lang FROM langs WHERE id=?", (uid,)).fetchone()
        return r["lang"] if r else "ru"

    def set_lang(self, uid, lang):
        self.c.execute("INSERT INTO langs(id,lang) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET lang=excluded.lang", (uid, lang))
        self.c.commit()

    def backup_to(self, path):
        """Копия базы в файл (для ежедневной резервной копии владельцу)."""
        dst = sqlite3.connect(path)
        with dst:
            self.c.backup(dst)
        dst.close()
