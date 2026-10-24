"""Учёт для пирожковой: товары, продажи, выпечка, работники. Без Telegram — легко тестировать."""
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5))  # Ташкент


def now():
    return datetime.now(TZ)


def day_start(d=None):
    d = d or now()
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


class Store:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS workers(id INTEGER PRIMARY KEY, name TEXT, active INTEGER DEFAULT 1, joined TEXT);
        CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY, used_by INTEGER);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, price INTEGER, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS langs(id INTEGER PRIMARY KEY, lang TEXT);
        CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, user_id INTEGER, user_name TEXT,
            kind TEXT, product_id INTEGER, product TEXT, qty INTEGER, amount INTEGER);
        """)

    # (ru, uz, en)
    DEFAULTS = [
        ("Пирожки с мясом", "Go'shtli pirojki", "Meat pies"),
        ("Пирожки с картошкой", "Kartoshkali pirojki", "Potato pies"),
        ("Пирожки с сосиской", "Kolbasali pirojki", "Sausage pies"),
        ("Гумма", "Gumma", "Gumma"),
        ("Чебурек с мясом", "Go'shtli cheburek", "Meat chebureki"),
        ("Чебурек с картошкой", "Kartoshkali chebureki", "Potato chebureki"),
        ("Чебурек со шпинатом", "Ismaloqli chebureki", "Spinach chebureki"),
        ("Кола 0,5", "Kola 0,5", "Cola 0.5"),
        ("Фанта 0,5", "Fanta 0,5", "Fanta 0.5"),
        ("Ice Tea 0,5", "Ice Tea 0,5", "Ice Tea 0.5"),
        ("Fuse Tea 0,5", "Fuse Tea 0,5", "Fuse Tea 0.5"),
        ("Мохито 0,5", "Mojito 0,5", "Mojito 0.5"),
        ("Royal 0,5", "Royal 0,5", "Royal 0.5"),
        ("Вода без газа 0,5", "Gazsiz suv 0,5", "Still water 0.5"),
        ("Вода без газа Sweet 0,5", "Gazsiz suv Sweet 0,5", "Still water Sweet 0.5"),
    ]

    def _migrate_names(self):
        for col in ("name_uz", "name_en"):
            try:
                self.db.execute(f"ALTER TABLE products ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        for ru, uz, en in self.DEFAULTS:
            self.db.execute("UPDATE products SET name_uz=?, name_en=? WHERE name=? AND name_uz IS NULL", (uz, en, ru))
        self.db.commit()

    def seed_defaults(self):
        """Один раз при первом запуске: стартовый список товаров с ценой 0 (админ ставит цены сам)."""
        self._migrate_names()
        if self.db.execute("SELECT 1 FROM meta WHERE k='seeded'").fetchone():
            return False
        for ru, uz, en in self.DEFAULTS:
            self.db.execute("INSERT OR IGNORE INTO products(name,price,active,name_uz,name_en) VALUES(?,0,1,?,?)", (ru, uz, en))
        self.db.execute("INSERT INTO meta(k,v) VALUES('seeded','1')")
        self.db.commit()
        return True

    # --- язык
    def get_lang(self, uid):
        r = self.db.execute("SELECT lang FROM langs WHERE id=?", (uid,)).fetchone()
        return r["lang"] if r else None

    def set_lang(self, uid, lang):
        self.db.execute("INSERT INTO langs(id,lang) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET lang=excluded.lang", (uid, lang))
        self.db.commit()

    @staticmethod
    def pname(p, lang):
        """Название товара на языке пользователя (запасной вариант — основное)."""
        if p is None:
            return ""
        col = {"uz": "name_uz", "en": "name_en"}.get(lang)
        return (p[col] if col and col in p.keys() and p[col] else p["name"])

    def stats_extra(self, start, end):
        """Подробная статистика продаж: топ товаров, лучший день, пиковый час, среднее в день."""
        rows = [r for r in self.between(start, end) if r["kind"] == "sale"]
        top, days, hours = {}, {}, {}
        for r in rows:
            top[r["product_id"]] = top.get(r["product_id"], 0) + r["qty"]
            d = days.setdefault(r["ts"][:10], {"qty": 0, "amount": 0})
            d["qty"] += r["qty"]
            d["amount"] += r["amount"]
            h = r["ts"][11:13]
            hours[h] = hours.get(h, 0) + r["qty"]
        best = max(days.items(), key=lambda kv: (kv[1]["qty"], kv[1]["amount"])) if days else None
        peak = max(hours.items(), key=lambda kv: kv[1]) if hours else None
        span = max((end - start).days, 1)
        total = sum(top.values())
        return {"top": sorted(top.items(), key=lambda kv: -kv[1])[:5], "best_day": best, "peak_hour": peak,
                "avg_qty": round(total / span, 1), "days": days}

    # --- работники
    def new_invite(self):
        code = secrets.token_hex(6)
        self.db.execute("INSERT INTO invites(code) VALUES(?)", (code,))
        self.db.commit()
        return code

    def redeem(self, code, uid, name):
        r = self.db.execute("SELECT used_by FROM invites WHERE code=?", (code,)).fetchone()
        if not r or r["used_by"]:
            return False
        self.db.execute("UPDATE invites SET used_by=? WHERE code=?", (uid, code))
        self.add_worker(uid, name)
        return True

    def add_worker(self, uid, name):
        self.db.execute(
            "INSERT INTO workers(id,name,active,joined) VALUES(?,?,1,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, active=1",
            (uid, name, now().isoformat()))
        self.db.commit()

    def remove_worker(self, uid):
        self.db.execute("UPDATE workers SET active=0 WHERE id=?", (uid,))
        self.db.commit()

    def is_worker(self, uid):
        return bool(self.db.execute("SELECT 1 FROM workers WHERE id=? AND active=1", (uid,)).fetchone())

    def workers(self):
        return self.db.execute("SELECT * FROM workers WHERE active=1 ORDER BY name").fetchall()

    # --- товары
    def add_product(self, name, price):
        self.db.execute(
            "INSERT INTO products(name,price,active) VALUES(?,?,1) "
            "ON CONFLICT(name) DO UPDATE SET price=excluded.price, active=1", (name.strip(), int(price)))
        self.db.commit()

    def remove_product(self, pid):
        self.db.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
        self.db.commit()

    def products(self):
        return self.db.execute("SELECT * FROM products WHERE active=1 ORDER BY name").fetchall()

    def product(self, pid):
        return self.db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()

    # --- записи
    def record(self, uid, uname, kind, pid, qty, ts=None):
        p = self.product(pid)
        if not p or qty <= 0 or kind not in ("sale", "made"):
            return None
        amount = p["price"] * qty if kind == "sale" else 0
        cur = self.db.execute(
            "INSERT INTO records(ts,user_id,user_name,kind,product_id,product,qty,amount) VALUES(?,?,?,?,?,?,?,?)",
            ((ts or now()).isoformat(), uid, uname, kind, pid, p["name"], qty, amount))
        self.db.commit()
        return cur.lastrowid

    def undo_last(self, uid, within_min=60):
        r = self.db.execute("SELECT * FROM records WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        if not r:
            return None
        if now() - datetime.fromisoformat(r["ts"]) > timedelta(minutes=within_min):
            return None
        self.db.execute("DELETE FROM records WHERE id=?", (r["id"],))
        self.db.commit()
        return r

    def between(self, start, end, uid=None):
        q = "SELECT * FROM records WHERE ts>=? AND ts<?"
        a = [start.isoformat(), end.isoformat()]
        if uid:
            q += " AND user_id=?"
            a.append(uid)
        return self.db.execute(q + " ORDER BY id", a).fetchall()

    def summary(self, start, end, uid=None):
        rows = self.between(start, end, uid)
        sold, made, revenue = {}, {}, 0
        by_worker = {}
        for r in rows:
            if r["kind"] == "sale":
                sold[r["product"]] = sold.get(r["product"], 0) + r["qty"]
                revenue += r["amount"]
                w = by_worker.setdefault(r["user_name"], {"qty": 0, "amount": 0})
                w["qty"] += r["qty"]
                w["amount"] += r["amount"]
            else:
                made[r["product"]] = made.get(r["product"], 0) + r["qty"]
        return {"sold": sold, "made": made, "revenue": revenue, "by_worker": by_worker}

    def stock(self, start, end):
        s = self.summary(start, end)
        names = set(s["made"]) | set(s["sold"])
        return {n: s["made"].get(n, 0) - s["sold"].get(n, 0) for n in names}


CUR = {"ru": "сум", "uz": "so'm", "en": "UZS"}


def money(n, lang="ru"):
    return f"{n:,}".replace(",", " ") + " " + CUR.get(lang, "сум")
