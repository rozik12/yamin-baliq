import asyncio
import csv
import io
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup)

from i18n import LANGS, T, variants
from store import Store, day_start, money, now, timedelta

ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "1755207256,8219891075,6984514139").replace(" ", "").split(",") if x}
store = Store(os.getenv("DB_PATH", "pirojki.db"))
store.seed_defaults()
router = Router()
log = logging.getLogger("pirojki")
_lang = {}


# Личная приписка к кнопке «Сегодня» — только для этого администратора
TODAY_NOTE = {6984514139: "Пожалуйста, не забудьте покормить Розикбека (Спасибо)"}


class Add(StatesGroup):
    product = State()


def is_admin(uid):
    return uid in ADMINS


def is_worker(uid):
    """Продажи ведут только работники; у администраторов этих функций нет."""
    return not is_admin(uid) and store.is_worker(uid)


def can_record(uid):
    """Вносить записи могут работники и админы (админы — только «Произведено»)."""
    return is_admin(uid) or store.is_worker(uid)


def lang(uid):
    if uid not in _lang:
        _lang[uid] = store.get_lang(uid) or "ru"
    return _lang[uid]


def tr(uid, key, **kw):
    s = T[key][lang(uid)]
    return s.format(**kw) if kw else s


def pn(uid, p):
    return store.pname(p, lang(uid))


def menu(uid):
    k = lambda key: KeyboardButton(text=tr(uid, key))
    if is_admin(uid):
        rows = [[k("b_made"), k("b_undo")], [k("b_today"), k("b_week"), k("b_month")], [k("b_stock"), k("b_workers")],
                [k("b_products"), k("b_invite")], [k("b_export"), k("b_lang")]]
    else:
        rows = [[k("b_sale"), k("b_made")], [k("b_mine"), k("b_undo")], [k("b_lang")]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def uname(u):
    return u.full_name or u.username or str(u.id)


def fmt_summary(uid, title, s, extra=None):
    L = lang(uid)
    pcs = T["pcs"][L]
    out = [f"<b>{title}</b>", ""]
    names = {p["name"]: store.pname(p, L) for p in store.db.execute("SELECT * FROM products").fetchall()}
    nm = lambda n: names.get(n, n)
    if s["sold"]:
        out.append(T["sold_h"][L])
        out += [f"  • {nm(n)}: {q} {pcs}" for n, q in sorted(s["sold"].items())]
        if s["revenue"]:
            out.append(f"{T['revenue'][L]}: <b>{money(s['revenue'], L)}</b>")
    else:
        out.append(T["no_sales"][L])
    if s["made"]:
        out += ["", T["made_h"][L]] + [f"  • {nm(n)}: {q} {pcs}" for n, q in sorted(s["made"].items())]
    if s["by_worker"]:
        out += ["", T["by_worker"][L]] + [f"  • {n}: {v['qty']} {pcs}" + (f", {money(v['amount'], L)}" if v["amount"] else "")
                                          for n, v in sorted(s["by_worker"].items())]
    if extra and extra["top"]:
        out += ["", T["top_h"][L]]
        for i, (pid, q) in enumerate(extra["top"], 1):
            out.append(f"  {i}. {pn(uid, store.product(pid))}: {q} {pcs}")
        d, v = extra["best_day"]
        out.append(T["best_day"][L].format(d=d, q=v["qty"]))
        if extra["peak_hour"]:
            out.append(T["peak"][L].format(h=extra["peak_hour"][0], q=extra["peak_hour"][1]))
        out.append(T["avg"][L].format(n=extra["avg_qty"]))
    return "\n".join(out)


def period(days):
    return day_start() - timedelta(days=days - 1), day_start() + timedelta(days=1)


# ---------- старт и язык

def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=v, callback_data=f"lg:{k}")] for k, v in LANGS.items()])


async def enter(msg: Message, user, payload=None):
    uid = user.id
    if payload and payload.startswith("w_") and not is_admin(uid):
        if store.redeem(payload[2:], uid, uname(user)):
            await msg.answer(tr(uid, "joined"), reply_markup=menu(uid))
            for a in ADMINS:
                try:
                    await msg.bot.send_message(a, tr(a, "new_worker", name=uname(user), id=uid))
                except Exception:
                    pass
        else:
            await msg.answer(tr(uid, "invite_bad"))
        return
    if is_admin(uid) or store.is_worker(uid):
        key = "hi_admin" if is_admin(uid) else "hi_worker"
        await msg.answer(tr(uid, key, name=user.first_name or uname(user)), reply_markup=menu(uid))
    else:
        await msg.answer(tr(uid, "closed"))


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    parts = (m.text or "").split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else None
    if store.get_lang(m.from_user.id) is None:
        await state.update_data(payload=payload)
        return await m.answer(T["pick_lang"]["ru"], reply_markup=lang_kb())
    await enter(m, m.from_user, payload)


@router.message(F.text.in_(variants("b_lang")))
async def change_lang(m: Message):
    await m.answer(T["pick_lang"]["ru"], reply_markup=lang_kb())


@router.callback_query(F.data.startswith("lg:"))
async def set_lang(c: CallbackQuery, state: FSMContext):
    code = c.data[3:]
    if code not in LANGS:
        return await c.answer()
    store.set_lang(c.from_user.id, code)
    _lang[c.from_user.id] = code
    payload = (await state.get_data()).get("payload")
    await state.clear()
    await c.message.delete()
    await enter(c.message, c.from_user, payload)
    await c.answer()


# ---------- продажа / произведено (только работники)

def cart_kb(uid, cart):
    """Одна широкая кнопка на товар: название видно целиком, нажатие = +1."""
    rows = []
    for p in store.products():
        n = cart.get(str(p["id"]), 0)
        rows.append([InlineKeyboardButton(text=f"✅ {pn(uid, p)} × {n}" if n else f"➕ {pn(uid, p)}", callback_data=f"c:{p['id']}")])
    total = sum(cart.values())
    rows.append([InlineKeyboardButton(text=tr(uid, "cart_undo"), callback_data="cu")])
    rows.append([InlineKeyboardButton(text=tr(uid, "save_btn", n=total) if total else tr(uid, "pick_first"), callback_data="cdone"),
                 InlineKeyboardButton(text=tr(uid, "cancel_btn"), callback_data="ccancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cart_text(uid, kind, cart):
    head = tr(uid, "cart_sale" if kind == "sale" else "cart_made")
    if not cart:
        return head
    lines = [f"• {pn(uid, store.product(int(pid)))} × {q}" for pid, q in cart.items()]
    return head + "\n\n" + "\n".join(lines) + f"\n\n{sum(cart.values())} {T['pcs'][lang(uid)]}"


async def open_cart(m: Message, state: FSMContext, kind):
    uid = m.from_user.id
    if not store.products():
        return await m.answer(tr(uid, "no_products"))
    await state.clear()
    await state.update_data(kind=kind, cart={}, hist=[])
    await m.answer(cart_text(uid, kind, {}), reply_markup=cart_kb(uid, {}))


@router.message(F.text.in_(variants("b_sale")))
async def sale(m: Message, state: FSMContext):
    if is_worker(m.from_user.id):
        await open_cart(m, state, "sale")
    elif is_admin(m.from_user.id):
        await m.answer(tr(m.from_user.id, "admin_no_work"), reply_markup=menu(m.from_user.id))


@router.message(F.text.in_(variants("b_made")))
async def made(m: Message, state: FSMContext):
    if can_record(m.from_user.id):
        await open_cart(m, state, "made")


async def refresh_cart(c: CallbackQuery, state: FSMContext, kind, cart, hist):
    await state.update_data(cart=cart, hist=hist)
    try:
        await c.message.edit_text(cart_text(c.from_user.id, kind, cart), reply_markup=cart_kb(c.from_user.id, cart))
    except Exception:
        pass


@router.callback_query(F.data.startswith("c:"))
async def cart_tap(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    if not can_record(uid):
        return await c.answer()
    d = await state.get_data()
    if "cart" not in d:
        await c.message.edit_text(tr(uid, "stale"))
        return await c.answer()
    pid = c.data[2:]
    cart, hist = d["cart"], d.get("hist", [])
    cart[pid] = min(cart.get(pid, 0) + 1, 1000)
    hist.append(pid)
    await refresh_cart(c, state, d["kind"], cart, hist[-200:])
    await c.answer()


@router.callback_query(F.data == "cu")
async def cart_undo(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    if not can_record(uid):
        return await c.answer()
    d = await state.get_data()
    cart, hist = d.get("cart"), d.get("hist", [])
    if cart is None or not hist:
        return await c.answer()
    pid = hist.pop()
    if cart.get(pid, 0) > 1:
        cart[pid] -= 1
    else:
        cart.pop(pid, None)
    await refresh_cart(c, state, d["kind"], cart, hist)
    await c.answer()


@router.callback_query(F.data == "cdone")
async def cart_done(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    if not can_record(uid):
        return await c.answer()
    d = await state.get_data()
    cart = d.get("cart") or {}
    if not cart:
        return await c.answer(tr(uid, "pick_first"), show_alert=True)
    if d["kind"] == "sale" and not is_worker(uid):
        return await c.answer()
    await state.clear()
    lines, total = [], 0
    for pid, qty in cart.items():
        if store.record(uid, uname(c.from_user), d["kind"], int(pid), qty):
            p = store.product(int(pid))
            lines.append(f"• {pn(uid, p)} × {qty}")
            total += p["price"] * qty
    head = tr(uid, "sale_saved" if d["kind"] == "sale" else "made_saved")
    tail = f"\n{tr(uid, 'sum')}: {money(total, lang(uid))}" if d["kind"] == "sale" and total else ""
    await c.message.edit_text(head + "\n" + "\n".join(lines) + tail)
    await c.answer()


@router.callback_query(F.data == "ccancel")
async def cart_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text(tr(c.from_user.id, "cancelled"))
    await c.answer()


@router.message(F.text.in_(variants("b_undo")))
async def undo(m: Message):
    uid = m.from_user.id
    if not can_record(uid):
        return
    r = store.undo_last(uid)
    if r:
        await m.answer(tr(uid, "undone", p=pn(uid, store.product(r["product_id"])) or r["product"], q=r["qty"]))
    else:
        await m.answer(tr(uid, "nothing_undo"))


@router.message(F.text.in_(variants("b_mine")))
async def mine(m: Message):
    uid = m.from_user.id
    if is_worker(uid):
        s = store.summary(*period(1), uid)
        await m.answer(fmt_summary(uid, tr(uid, "t_mine"), s))


# ---------- статистика (только админы)

async def report(m: Message, days, title_key):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    a, b = period(days)
    text = fmt_summary(uid, tr(uid, title_key), store.summary(a, b), store.stats_extra(a, b))
    if title_key == "t_today" and uid in TODAY_NOTE:
        text += "\n\n" + TODAY_NOTE[uid]
    await m.answer(text)


@router.message(F.text.in_(variants("b_today")))
async def today(m: Message):
    await report(m, 1, "t_today")


@router.message(F.text.in_(variants("b_week")))
async def week(m: Message):
    await report(m, 7, "t_week")


@router.message(F.text.in_(variants("b_month")))
async def month(m: Message):
    await report(m, 30, "t_month")


@router.message(F.text.in_(variants("b_stock")))
async def stock(m: Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    st = store.stock(*period(1))
    names = {p["name"]: pn(uid, p) for p in store.db.execute("SELECT * FROM products").fetchall()}
    body = "\n".join(f"• {names.get(n, n)}: {q} {T['pcs'][lang(uid)]}" for n, q in sorted(st.items())) or tr(uid, "no_data")
    await m.answer(tr(uid, "stock_h") + "\n" + body)


# ---------- работники

@router.message(F.text.in_(variants("b_workers")))
async def workers(m: Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    ws = store.workers()
    if not ws:
        return await m.answer(tr(uid, "no_workers"))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"❌ {w['name']}", callback_data=f"rw:{w['id']}")] for w in ws])
    await m.answer(tr(uid, "workers_h"), reply_markup=kb)


@router.callback_query(F.data.startswith("rw:"))
async def rm_worker(c: CallbackQuery):
    if is_admin(c.from_user.id):
        store.remove_worker(int(c.data[3:]))
        await c.message.edit_text(tr(c.from_user.id, "worker_removed"))
    await c.answer()


@router.message(F.text.in_(variants("b_invite")))
async def invite(m: Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    me = await m.bot.get_me()
    await m.answer(tr(uid, "invite_text", link=f"https://t.me/{me.username}?start=w_{store.new_invite()}"))


@router.message(Command("addworker"))
async def addworker(m: Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    p = (m.text or "").split(maxsplit=2)
    if len(p) < 2 or not p[1].isdigit():
        return await m.answer(tr(uid, "addworker_fmt"))
    store.add_worker(int(p[1]), p[2] if len(p) > 2 else p[1])
    await m.answer(tr(uid, "worker_added"))


# ---------- товары

@router.message(F.text.in_(variants("b_products")))
async def products(m: Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    L = lang(uid)
    rows = [[InlineKeyboardButton(text=f"❌ {pn(uid, p)} — {money(p['price'], L) if p['price'] else T['price_unset'][L]}",
                                  callback_data=f"rp:{p['id']}")] for p in store.products()]
    rows.append([InlineKeyboardButton(text=tr(uid, "add_product"), callback_data="addp")])
    await m.answer(tr(uid, "products_h"), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("rp:"))
async def rm_product(c: CallbackQuery):
    if is_admin(c.from_user.id):
        store.remove_product(int(c.data[3:]))
        await c.message.edit_text(tr(c.from_user.id, "prod_removed"))
    await c.answer()


@router.callback_query(F.data == "addp")
async def addp(c: CallbackQuery, state: FSMContext):
    if is_admin(c.from_user.id):
        await state.set_state(Add.product)
        await c.message.answer(tr(c.from_user.id, "addp_prompt"))
    await c.answer()


@router.message(Add.product)
async def got_product(m: Message, state: FSMContext):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    parts = (m.text or "").rsplit(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
        return await m.answer(tr(uid, "addp_fmt"))
    store.add_product(parts[0], int(parts[1]))
    await state.clear()
    await m.answer(tr(uid, "product_added", n=parts[0], p=money(int(parts[1]), lang(uid))), reply_markup=menu(uid))


@router.message(F.text.in_(variants("b_export")))
async def export(m: Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    L = lang(uid)
    a, b = period(30)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(T["csv_head"][L])
    for r in store.between(a, b):
        w.writerow([r["ts"][:16], r["user_name"], T["csv_sale" if r["kind"] == "sale" else "csv_made"][L],
                    store.pname(store.product(r["product_id"]), L) or r["product"], r["qty"], r["amount"]])
    await m.answer_document(BufferedInputFile(buf.getvalue().encode("utf-8-sig"), "uchet.csv"), caption=tr(uid, "export_cap"))


@router.message()
async def fallback(m: Message):
    uid = m.from_user.id
    if is_admin(uid) or store.is_worker(uid):
        await m.answer(tr(uid, "fallback"), reply_markup=menu(uid))
    else:
        await m.answer(tr(uid, "closed"))


@router.error()
async def on_error(event):
    log.exception("handler error: %s", event.exception)
    return True


async def daily_loop(bot):
    last = None
    while True:
        try:
            await asyncio.sleep(120)
            t = now()
            if t.hour == 21 and last != t.date():
                last = t.date()
                a, b = period(1)
                s, ex = store.summary(a, b), store.stats_extra(a, b)
                for adm in ADMINS:
                    try:
                        await bot.send_message(adm, fmt_summary(adm, tr(adm, "t_daily"), s, ex))
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("daily loop")


async def main():
    logging.basicConfig(level=logging.INFO)
    proxy = os.getenv("HTTPS_PROXY") if os.getenv("USE_PROXY") else None
    bot = Bot(os.environ["BOT_TOKEN"], session=AiohttpSession(proxy=proxy) if proxy else None,
              default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    asyncio.create_task(daily_loop(bot))
    while True:  # 24/7: сеть/Telegram моргнули — перезапускаем опрос, а не падаем
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("polling crashed, restart in 5s")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
