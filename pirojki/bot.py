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

from store import Store, day_start, money, now, timedelta

ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "1755207256,8219891075").replace(" ", "").split(",") if x}
store = Store(os.getenv("DB_PATH", "pirojki.db"))
store.seed_defaults()
router = Router()

B_SALE, B_MADE, B_MINE, B_UNDO = "💰 Продажа", "🥟 Произведено", "📋 Мои записи", "↩️ Отменить последнюю"
B_TODAY, B_WEEK, B_STOCK = "📊 Сегодня", "📅 Неделя", "📦 Остаток"
B_WORKERS, B_PRODUCTS, B_INVITE, B_EXPORT = "👥 Работники", "🧁 Товары", "➕ Позвать работника", "📤 Экспорт"


class Add(StatesGroup):
    product = State()
    worker = State()


def is_admin(uid):
    return uid in ADMINS


def allowed(uid):
    return is_admin(uid) or store.is_worker(uid)


def menu(uid):
    rows = [[KeyboardButton(text=B_SALE), KeyboardButton(text=B_MADE)],
            [KeyboardButton(text=B_MINE), KeyboardButton(text=B_UNDO)]]
    if is_admin(uid):
        rows += [[KeyboardButton(text=B_TODAY), KeyboardButton(text=B_WEEK), KeyboardButton(text=B_STOCK)],
                 [KeyboardButton(text=B_WORKERS), KeyboardButton(text=B_PRODUCTS)],
                 [KeyboardButton(text=B_INVITE), KeyboardButton(text=B_EXPORT)]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def uname(u):
    return u.full_name or u.username or str(u.id)


def fmt_summary(title, s):
    out = [f"<b>{title}</b>", ""]
    if s["sold"]:
        out.append("💰 Продано:")
        out += [f"  • {n}: {q} шт" for n, q in sorted(s["sold"].items())]
        if s["revenue"]:
            out.append(f"Выручка: <b>{money(s['revenue'])}</b>")
    else:
        out.append("Продаж пока нет.")
    if s["made"]:
        out += ["", "🥟 Произведено:"] + [f"  • {n}: {q} шт" for n, q in sorted(s["made"].items())]
    if s["by_worker"]:
        out += ["", "👥 По работникам:"] + [f"  • {n}: {v['qty']} шт, {money(v['amount'])}"
                                            for n, v in sorted(s["by_worker"].items())]
    return "\n".join(out)


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("w_") and not is_admin(uid):
        if store.redeem(parts[1][2:], uid, uname(m.from_user)):
            await m.answer("✅ Вы добавлены как работник. Нажимайте кнопки внизу.", reply_markup=menu(uid))
            for a in ADMINS:
                try:
                    await m.bot.send_message(a, f"👤 Новый работник: {uname(m.from_user)} ({uid})")
                except Exception:
                    pass
            return
        await m.answer("Эта ссылка уже использована или неверна. Попросите администратора новую.")
        return
    if not allowed(uid):
        await m.answer("Это закрытый бот учёта. Попросите администратора прислать вам ссылку-приглашение.")
        return
    role = "администратор" if is_admin(uid) else "работник"
    await m.answer(f"Привет, {m.from_user.first_name}! Вы — {role}.", reply_markup=menu(uid))


def cart_kb(kind, cart):
    rows = []
    for p in store.products():
        n = cart.get(str(p["id"]), 0)
        rows.append([InlineKeyboardButton(text="➖", callback_data=f"c:{p['id']}:-"),
                     InlineKeyboardButton(text=f"{p['name']}: {n}" if n else p["name"], callback_data="noop"),
                     InlineKeyboardButton(text="➕", callback_data=f"c:{p['id']}:+")])
    total = sum(cart.values())
    rows.append([InlineKeyboardButton(text=f"✅ Записать ({total} шт)" if total else "Выберите товары ➕", callback_data="cdone"),
                 InlineKeyboardButton(text="✖️ Отмена", callback_data="ccancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def open_cart(m: Message, state: FSMContext, kind):
    if not store.products():
        return await m.answer("Список товаров пуст. Нажмите 🧁 Товары и добавьте." if is_admin(m.from_user.id)
                              else "Список товаров пуст. Попросите администратора добавить товары.")
    await state.clear()
    await state.update_data(kind=kind, cart={})
    await m.answer(("💰 Что продано? Нажимайте ➕ у каждого товара" if kind == "sale" else
                    "🥟 Что произведено? Нажимайте ➕ у каждого товара") + ", потом «Записать».", reply_markup=cart_kb(kind, {}))


@router.message(F.text == B_SALE)
async def sale(m: Message, state: FSMContext):
    if allowed(m.from_user.id):
        await open_cart(m, state, "sale")


@router.message(F.text == B_MADE)
async def made(m: Message, state: FSMContext):
    if allowed(m.from_user.id):
        await open_cart(m, state, "made")


@router.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data.startswith("c:"))
async def cart_tap(c: CallbackQuery, state: FSMContext):
    if not allowed(c.from_user.id):
        return await c.answer()
    d = await state.get_data()
    if "cart" not in d:
        await c.message.edit_text("Список устарел. Нажмите кнопку «Продажа» или «Произведено» заново.")
        return await c.answer()
    _, pid, op = c.data.split(":")
    cart = d["cart"]
    n = cart.get(pid, 0) + (1 if op == "+" else -1)
    if n > 0:
        cart[pid] = min(n, 1000)
    else:
        cart.pop(pid, None)
    await state.update_data(cart=cart)
    await c.message.edit_reply_markup(reply_markup=cart_kb(d["kind"], cart))
    await c.answer()


@router.callback_query(F.data == "cdone")
async def cart_done(c: CallbackQuery, state: FSMContext):
    if not allowed(c.from_user.id):
        return await c.answer()
    d = await state.get_data()
    cart = d.get("cart") or {}
    if not cart:
        return await c.answer("Сначала выберите товары ➕", show_alert=True)
    await state.clear()
    lines, total = [], 0
    for pid, qty in cart.items():
        if store.record(c.from_user.id, uname(c.from_user), d["kind"], int(pid), qty):
            p = store.product(int(pid))
            lines.append(f"• {p['name']} × {qty}")
            total += p["price"] * qty
    head = "✅ Продажа записана:" if d["kind"] == "sale" else "✅ Произведено записано:"
    tail = f"\nСумма: {money(total)}" if d["kind"] == "sale" and total else ""
    await c.message.edit_text(head + "\n" + "\n".join(lines) + tail)
    await c.answer()


@router.callback_query(F.data == "ccancel")
async def cart_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Отменено.")
    await c.answer()


@router.message(F.text == B_UNDO)
async def undo(m: Message):
    if not allowed(m.from_user.id):
        return
    r = store.undo_last(m.from_user.id)
    await m.answer(f"↩️ Отменено: {r['product']} × {r['qty']}" if r else "Нечего отменять (можно отменить запись младше часа).")


@router.message(F.text == B_MINE)
async def mine(m: Message):
    if not allowed(m.from_user.id):
        return
    s = store.summary(day_start(), day_start() + timedelta(days=1), m.from_user.id)
    await m.answer(fmt_summary("Ваши записи сегодня", s))


@router.message(F.text == B_TODAY)
async def today(m: Message):
    if is_admin(m.from_user.id):
        await m.answer(fmt_summary("Сегодня", store.summary(day_start(), day_start() + timedelta(days=1))))


@router.message(F.text == B_WEEK)
async def week(m: Message):
    if is_admin(m.from_user.id):
        await m.answer(fmt_summary("Последние 7 дней", store.summary(day_start() - timedelta(days=6), day_start() + timedelta(days=1))))


@router.message(F.text == B_STOCK)
async def stock(m: Message):
    if not is_admin(m.from_user.id):
        return
    st = store.stock(day_start(), day_start() + timedelta(days=1))
    await m.answer("📦 Остаток за сегодня (произведено − продано):\n" + ("\n".join(f"• {n}: {q} шт" for n, q in sorted(st.items())) or "Пока нет данных."))


@router.message(F.text == B_WORKERS)
async def workers(m: Message):
    if not is_admin(m.from_user.id):
        return
    ws = store.workers()
    if not ws:
        return await m.answer("Работников пока нет. Нажмите «➕ Позвать работника».")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"❌ {w['name']}", callback_data=f"rw:{w['id']}")] for w in ws])
    await m.answer("Работники (нажмите, чтобы убрать):", reply_markup=kb)


@router.callback_query(F.data.startswith("rw:"))
async def rm_worker(c: CallbackQuery):
    if is_admin(c.from_user.id):
        store.remove_worker(int(c.data[3:]))
        await c.message.edit_text("Работник убран.")
    await c.answer()


@router.message(F.text == B_INVITE)
async def invite(m: Message):
    if not is_admin(m.from_user.id):
        return
    me = await m.bot.get_me()
    await m.answer(f"Отправьте работнику эту ссылку (одноразовая):\nhttps://t.me/{me.username}?start=w_{store.new_invite()}\n\n"
                   "Или добавьте по id: /addworker 123456789 Имя")


@router.message(Command("addworker"))
async def addworker(m: Message):
    if not is_admin(m.from_user.id):
        return
    p = (m.text or "").split(maxsplit=2)
    if len(p) < 2 or not p[1].isdigit():
        return await m.answer("Формат: /addworker 123456789 Имя")
    store.add_worker(int(p[1]), p[2] if len(p) > 2 else p[1])
    await m.answer("✅ Работник добавлен.")


@router.message(F.text == B_PRODUCTS)
async def products(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    ps = store.products()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"❌ {p['name']} — {money(p['price']) if p['price'] else 'цена не задана'}", callback_data=f"rp:{p['id']}")] for p in ps]
                              + [[InlineKeyboardButton(text="➕ Добавить товар", callback_data="addp")]])
    await m.answer("Товары (нажмите ❌, чтобы убрать).\nЧтобы задать или изменить цену — «➕ Добавить товар» и напишите название и цену (название то же — цена обновится):", reply_markup=kb)


@router.callback_query(F.data.startswith("rp:"))
async def rm_product(c: CallbackQuery):
    if is_admin(c.from_user.id):
        store.remove_product(int(c.data[3:]))
        await c.message.edit_text("Товар убран.")
    await c.answer()


@router.callback_query(F.data == "addp")
async def addp(c: CallbackQuery, state: FSMContext):
    if is_admin(c.from_user.id):
        await state.set_state(Add.product)
        await c.message.answer("Напишите название и цену одной строкой, например:\nПирожок с мясом 5000")
    await c.answer()


@router.message(Add.product)
async def got_product(m: Message, state: FSMContext):
    parts = (m.text or "").rsplit(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
        return await m.answer("Формат: название и цена, например: Пирожок с мясом 5000")
    store.add_product(parts[0], int(parts[1]))
    await state.clear()
    await m.answer(f"✅ Добавлено: {parts[0]} — {money(int(parts[1]))}", reply_markup=menu(m.from_user.id))


@router.message(F.text == B_EXPORT)
async def export(m: Message):
    if not is_admin(m.from_user.id):
        return
    rows = store.between(day_start() - timedelta(days=30), day_start() + timedelta(days=1))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["время", "работник", "тип", "товар", "кол-во", "сумма"])
    for r in rows:
        w.writerow([r["ts"][:16], r["user_name"], "продажа" if r["kind"] == "sale" else "произведено", r["product"], r["qty"], r["amount"]])
    await m.answer_document(BufferedInputFile(buf.getvalue().encode("utf-8-sig"), "uchet.csv"), caption="Записи за 30 дней")


@router.message()
async def fallback(m: Message):
    if allowed(m.from_user.id):
        await m.answer("Нажмите кнопку внизу 👇", reply_markup=menu(m.from_user.id))
    else:
        await m.answer("Это закрытый бот учёта. Попросите администратора прислать ссылку-приглашение.")


async def daily_loop(bot):
    last = None
    while True:
        await asyncio.sleep(120)
        t = now()
        if t.hour == 21 and last != t.date():
            last = t.date()
            text = fmt_summary("Итог дня", store.summary(day_start(), day_start() + timedelta(days=1)))
            for a in ADMINS:
                try:
                    await bot.send_message(a, text)
                except Exception:
                    pass


async def main():
    logging.basicConfig(level=logging.INFO)
    proxy = os.getenv("HTTPS_PROXY") if os.getenv("USE_PROXY") else None
    bot = Bot(os.environ["BOT_TOKEN"], session=AiohttpSession(proxy=proxy) if proxy else None,
              default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    asyncio.create_task(daily_loop(bot))
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
