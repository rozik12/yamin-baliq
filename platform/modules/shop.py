"""Модуль «Магазин»: каталог, корзина, заказ (доставка или самовывоз), оплата наличными или переводом на карту, статусы."""
import datetime as dt
import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import core
from core import db, get_setting, is_admin, is_btn, main_kb, money, notify_admins, t, tenant_of

router = Router()
ADM_ORDERS, ADM_ADDP, ADM_CATALOG, ADM_REPORT, ADM_PROMO, ADM_PAUSE = "📋 Заказы", "➕ Товар", "🗂 Каталог", "📈 Отчёт магазина", "🎟 Промокоды", "⏯ Приём заказов"
ACTIVE = ("new", "verify", "accepted", "ready")
STATUS_RU = {"new": "🆕 новый", "verify": "🔎 проверка оплаты", "accepted": "✅ принят", "ready": "📦 готов/в пути",
             "done": "🏁 выдан", "rejected": "❌ отклонён", "cancelled": "🚫 отменён"}

core.register_texts({
    "b_catalog": {"ru": "🛍 Каталог", "uz": "🛍 Katalog", "en": "🛍 Catalog"},
    "b_cart": {"ru": "🛒 Корзина", "uz": "🛒 Savat", "en": "🛒 Cart"},
    "b_orders": {"ru": "📜 Мои заказы", "uz": "📜 Buyurtmalarim", "en": "📜 My orders"},
    "shop_empty_catalog": {"ru": "Каталог пока пуст", "uz": "Katalog hozircha bo'sh", "en": "The catalog is empty for now"},
    "shop_pick_cat": {"ru": "Выберите раздел:", "uz": "Bo'limni tanlang:", "en": "Choose a category:"},
    "shop_add": {"ru": "➕ В корзину", "uz": "➕ Savatga", "en": "➕ Add to cart"},
    "shop_added": {"ru": "Добавлено", "uz": "Qo'shildi", "en": "Added"},
    "shop_cart_empty": {"ru": "Корзина пуста", "uz": "Savat bo'sh", "en": "Your cart is empty"},
    "shop_your_order": {"ru": "🛒 Ваш заказ:", "uz": "🛒 Buyurtmangiz:", "en": "🛒 Your order:"},
    "shop_total": {"ru": "Итого", "uz": "Jami", "en": "Total"},
    "shop_checkout": {"ru": "✅ Оформить", "uz": "✅ Rasmiylashtirish", "en": "✅ Checkout"},
    "shop_clear": {"ru": "🗑 Очистить", "uz": "🗑 Tozalash", "en": "🗑 Clear"},
    "shop_cleared": {"ru": "Корзина очищена", "uz": "Savat tozalandi", "en": "Cart cleared"},
    "shop_q_kind": {"ru": "Как получите заказ?", "uz": "Buyurtmani qanday olasiz?", "en": "How would you like to receive it?"},
    "shop_delivery": {"ru": "🚚 Доставка", "uz": "🚚 Yetkazib berish", "en": "🚚 Delivery"},
    "shop_pickup": {"ru": "🏃 Самовывоз", "uz": "🏃 Olib ketish", "en": "🏃 Pickup"},
    "shop_q_addr": {"ru": "Напишите адрес доставки или отправьте геолокацию:", "uz": "Manzilni yozing yoki joylashuvni yuboring:",
                    "en": "Write the delivery address or send your location:"},
    "shop_q_comment": {"ru": "Комментарий к заказу?", "uz": "Buyurtmaga izoh?", "en": "Any comment for the order?"},
    "shop_q_phone": {"ru": "Отправьте номер телефона:", "uz": "Telefon raqamingizni yuboring:", "en": "Send your phone number:"},
    "shop_q_pay": {"ru": "Как будете платить?", "uz": "Qanday to'laysiz?", "en": "How would you like to pay?"},
    "shop_pay_cash": {"ru": "💵 Наличными", "uz": "💵 Naqd pul", "en": "💵 Cash"},
    "shop_pay_transfer": {"ru": "💳 Перевод на карту", "uz": "💳 Kartaga o'tkazma", "en": "💳 Card transfer"},
    "shop_transfer_info": {"ru": "Переведите {sum} на карту:\n\n💳 {card}{holder}\n\nПосле оплаты отправьте скриншот чека 👇",
                           "uz": "{sum} miqdorini kartaga o'tkazing:\n\n💳 {card}{holder}\n\nTo'lovdan keyin chek skrinshotini yuboring 👇",
                           "en": "Please transfer {sum} to the card:\n\n💳 {card}{holder}\n\nAfter paying, send the receipt screenshot 👇"},
    "shop_order_ok": {"ru": "Заказ №{n} принят! Мы скоро свяжемся с вами.", "uz": "№{n} buyurtma qabul qilindi! Tez orada bog'lanamiz.",
                      "en": "Order #{n} received! We will contact you shortly."},
    "shop_verifying": {"ru": "Проверяем оплату ⏳", "uz": "To'lovni tekshiryapmiz ⏳", "en": "Checking your payment ⏳"},
    "shop_orders_empty": {"ru": "У вас пока нет заказов", "uz": "Hozircha buyurtmalaringiz yo'q", "en": "You have no orders yet"},
    "shop_cancel": {"ru": "❌ Отменить", "uz": "❌ Bekor qilish", "en": "❌ Cancel"},
    "shop_cancelled": {"ru": "Заказ №{n} отменён", "uz": "№{n} buyurtma bekor qilindi", "en": "Order #{n} cancelled"},
    "shop_cart_gone": {"ru": "Корзина пуста, начните заново", "uz": "Savat bo'sh, qaytadan boshlang", "en": "Your cart is empty, please start again"},
    "shop_q_promo": {"ru": "Есть промокод? Введите его.", "uz": "Promokod bormi? Kiriting.", "en": "Have a promo code? Enter it."},
    "shop_q_bonus": {"ru": "У вас {bal} баллов. Списать {use} на этот заказ?", "uz": "Sizda {bal} ball bor. Shu buyurtmaga {use} ni yechamizmi?",
                     "en": "You have {bal} points. Spend {use} on this order?"},
    "shop_bonus_spend": {"ru": "💎 Списать {use}", "uz": "💎 {use} ni yechish", "en": "💎 Spend {use}"},
    "shop_bonus_keep": {"ru": "Копить", "uz": "Jamg'arish", "en": "Keep saving"},
    "shop_earned": {"ru": "💎 Начислено {n} баллов. Баланс: {bal}", "uz": "💎 {n} ball qo'shildi. Balans: {bal}", "en": "💎 {n} points added. Balance: {bal}"},
    "shop_balance": {"ru": "💎 Ваши баллы: {bal}", "uz": "💎 Ballaringiz: {bal}", "en": "💎 Your points: {bal}"},
    "b_popular": {"ru": "🔥 Популярное", "uz": "🔥 Ommabop", "en": "🔥 Popular"},
    "b_search": {"ru": "🔍 Поиск", "uz": "🔍 Qidirish", "en": "🔍 Search"},
    "shop_q_search": {"ru": "Что ищете? Напишите название.", "uz": "Nima qidiryapsiz? Nomini yozing.", "en": "What are you looking for? Type a name."},
    "shop_nothing": {"ru": "Ничего не нашли. Попробуйте другое слово.", "uz": "Hech narsa topilmadi. Boshqa so'z yozing.", "en": "Nothing found. Try another word."},
    "shop_repeat": {"ru": "🔁 Повторить", "uz": "🔁 Takrorlash", "en": "🔁 Repeat"},
    "shop_ref_bonus": {"ru": "🎁 Ваш друг сделал первый заказ! Вам начислено {n} баллов.", "uz": "🎁 Do'stingiz birinchi buyurtma berdi! Sizga {n} ball qo'shildi.",
                       "en": "🎁 Your friend placed a first order! You received {n} points."},
    "shop_min_err": {"ru": "Минимальная сумма заказа: {sum}", "uz": "Minimal buyurtma summasi: {sum}", "en": "Minimum order amount: {sum}"},
    "shop_closed": {"ru": "Приём заказов временно приостановлен. Попробуйте позже 🙏", "uz": "Buyurtma qabul qilish vaqtincha to'xtatilgan 🙏",
                    "en": "Orders are temporarily paused. Please try later 🙏"},
    "shop_no_stock": {"ru": "К сожалению, «{name}» осталось только {n} шт.", "uz": "Afsuski, «{name}» faqat {n} dona qoldi.", "en": "Sorry, only {n} of “{name}” left."},
    "shop_q_phone_saved": {"ru": "Использовать ваш прошлый номер?", "uz": "Oldingi raqamingizdan foydalanamizmi?", "en": "Use your previous number?"},
    "shop_use_phone": {"ru": "📞 {phone}", "uz": "📞 {phone}", "en": "📞 {phone}"},
    "shop_other_phone": {"ru": "✏️ Другой номер", "uz": "✏️ Boshqa raqam", "en": "✏️ Another number"},
    "shop_winback": {"ru": "Давно вас не было! Мы скучаем и ждём вас снова 🙂", "uz": "Anchadan beri ko'rinmadingiz! Sizni kutamiz 🙂", "en": "We miss you! Come back and order again 🙂"},
    "shop_st_new": {"ru": "ожидает", "uz": "kutilmoqda", "en": "pending"},
    "shop_st_verify": {"ru": "проверяем оплату", "uz": "to'lov tekshirilmoqda", "en": "checking payment"},
    "shop_st_accepted": {"ru": "принят ✅", "uz": "qabul qilindi ✅", "en": "accepted ✅"},
    "shop_st_ready": {"ru": "готов / в пути 📦", "uz": "tayyor / yo'lda 📦", "en": "ready / on the way 📦"},
    "shop_st_done": {"ru": "выдан, спасибо! 🏁", "uz": "topshirildi, rahmat! 🏁", "en": "completed, thank you! 🏁"},
    "shop_st_rejected": {"ru": "отклонён ❌", "uz": "rad etildi ❌", "en": "declined ❌"},
    "shop_st_cancelled": {"ru": "отменён", "uz": "bekor qilindi", "en": "cancelled"},
})
core.register_settings("shop", [("card_number", "💳 Номер карты", "text"), ("card_holder", "👤 Владелец карты", "text"),
                                ("delivery_fee", "🚚 Стоимость доставки", "int"), ("free_delivery_from", "🆓 Бесплатная доставка от", "int"),
                                ("phone", "📞 Телефон", "text"), ("currency", "💱 Валюта (сум, $ ...)", "text"),
                                ("cashback_percent", "💎 Кешбэк баллами, %", "int"),
                                ("referral_bonus", "🎁 Баллов за приглашённого друга", "int"), ("min_order", "🛒 Минимальная сумма заказа", "int"),
                                ("winback_days", "💌 Напомнить о себе клиенту через N дней без заказов (0 — выкл)", "int"), ("winback_text", "💌 Текст такого напоминания", "text")])


def cleanup(slug):
    db.execute("DELETE FROM products WHERE tenant=?", (slug,))
    db.execute("DELETE FROM shop_orders WHERE tenant=?", (slug,))
    db.execute("DELETE FROM shop_promos WHERE tenant=?", (slug,))
    db.execute("DELETE FROM shop_bonuses WHERE tenant=?", (slug,))
    db.commit()


core.register_module("shop", ["b_catalog", "b_cart", "b_orders", "b_popular", "b_search"], [ADM_ORDERS, ADM_ADDP, ADM_CATALOG], cleanup=cleanup, more_labels=[ADM_REPORT, ADM_PROMO, ADM_PAUSE])
core.register_help("shop", "🛍 Магазин, кафе, доставка", [
    "Нажмите «➕ Товар» и добавьте первое блюдо или товар: раздел, название, цена, фото.",
    "Откройте «⚙️ Ещё» → «⚙️ Настройки»: укажите телефон, номер карты для оплаты и стоимость доставки.",
    "Нажмите «⚙️ Ещё» → «🔗 Ссылка и QR» и покажите клиентам.",
    "Новые заказы приходят сюда сообщением. Нажмите «Принять», потом «Готов» и «Выдан»: клиент получает уведомления сам.",
    "Цену или фото можно поменять в «🗂 Каталог». Скидки: «⚙️ Ещё» → «🎟 Промокоды».",
    "Чтобы клиенты возвращались: «⚙️ Ещё» → «⚙️ Настройки» → включите кешбэк баллами и бонус за приглашённого друга.",
    "Закончился товар или нужен остаток: «🗂 Каталог» → «📦 Остаток». Нужна пауза? «⚙️ Ещё» → «⏯ Приём заказов».",
    "Хотите сообщить об акции? «⚙️ Ещё» → «📣 Рассылка».",
])
db.execute("CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, tenant TEXT, category TEXT, name TEXT, price INT, descr TEXT, photo TEXT, active INTEGER DEFAULT 1)")
db.execute("CREATE TABLE IF NOT EXISTS shop_orders(id INTEGER PRIMARY KEY, tenant TEXT, user_id INT, items TEXT, total INT, status TEXT DEFAULT 'new', text TEXT, proof TEXT, dest TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)")
db.execute("CREATE TABLE IF NOT EXISTS shop_promos(tenant TEXT, code TEXT, percent INT, PRIMARY KEY(tenant, code))")
db.execute("CREATE TABLE IF NOT EXISTS shop_bonuses(tenant TEXT, user_id INT, balance INT DEFAULT 0, PRIMARY KEY(tenant, user_id))")
for _ddl in ("ALTER TABLE products ADD COLUMN stock INTEGER", "ALTER TABLE shop_orders ADD COLUMN stock_back INT DEFAULT 0"):
    try:
        db.execute(_ddl)
    except Exception:
        pass
db.execute("CREATE TABLE IF NOT EXISTS shop_winback(tenant TEXT, user_id INT, sent_at TEXT, PRIMARY KEY(tenant, user_id))")
for col in ("bonus_used INT DEFAULT 0", "promo TEXT", "earned INT DEFAULT 0"):
    try:
        db.execute(f"ALTER TABLE shop_orders ADD COLUMN {col}")
    except Exception:
        pass
db.commit()

carts: dict[tuple, dict[int, int]] = {}


def cart_of(uid):
    return carts.setdefault((tenant_of(uid), uid), {})


def product(pid, tenant):
    row = db.execute("SELECT id, name, price, descr, photo, category, active, stock FROM products WHERE id=? AND tenant=?", (pid, tenant)).fetchone()
    return dict(zip(("id", "name", "price", "descr", "photo", "category", "active", "stock"), row)) if row else None


def cart_lines(uid):
    tenant = tenant_of(uid)
    lines, total = [], 0
    for pid, q in list(cart_of(uid).items()):
        p = product(pid, tenant)
        if not p or not p["active"]:
            cart_of(uid).pop(pid, None)
            continue
        lines.append(f"• {p['name']} × {q} = {money(tenant, p['price'] * q)}")
        total += p["price"] * q
    return lines, total


def cart_view(uid):
    lines, total = cart_lines(uid)
    tenant = tenant_of(uid)
    if not lines:
        return t(uid, "shop_cart_empty"), None
    b = InlineKeyboardBuilder()
    for pid, q in cart_of(uid).items():
        p = product(pid, tenant)
        b.button(text="➖", callback_data=f"sd:{pid}")
        b.button(text=f"{p['name']} × {q}"[:30], callback_data="noop")
        b.button(text="➕", callback_data=f"si:{pid}")
    b.adjust(3)
    b.button(text=t(uid, "shop_checkout"), callback_data="scheckout")
    b.button(text=t(uid, "shop_clear"), callback_data="sclear")
    b.adjust(*([3] * len(lines)), 2)
    return f"{t(uid, 'shop_your_order')}\n\n" + "\n".join(lines) + f"\n\n{t(uid, 'shop_total')}: {money(tenant, total)}", b.as_markup()


# ---------------- клиент ----------------
def categories(tenant):
    return [r[0] for r in db.execute("SELECT DISTINCT category FROM products WHERE tenant=? AND active=1 AND (stock IS NULL OR stock>0) ORDER BY category", (tenant,))]


async def show_products(target, uid, category):
    tenant = tenant_of(uid)
    for p in db.execute("SELECT id, name, price, descr, photo FROM products WHERE tenant=? AND active=1 AND (stock IS NULL OR stock>0) AND category=? ORDER BY id", (tenant, category)).fetchall():
        pid, name, price, descr, photo = p
        b = InlineKeyboardBuilder()
        b.button(text=t(uid, "shop_add"), callback_data=f"sa:{pid}")
        caption = f"<b>{name}</b> — {money(tenant, price)}\n{descr or ''}"
        try:
            if photo:
                await target.answer_photo(photo, caption=caption, parse_mode="HTML", reply_markup=b.as_markup())
                continue
        except Exception:
            logging.warning("product photo failed")
        await target.answer(caption, parse_mode="HTML", reply_markup=b.as_markup())


@router.message(is_btn("b_catalog"))
async def catalog(m: Message):
    uid, tenant = m.from_user.id, tenant_of(m.from_user.id)
    cats = categories(tenant)
    if not cats:
        return await m.answer(t(uid, "shop_empty_catalog"))
    if len(cats) == 1:
        return await show_products(m, uid, cats[0])
    b = InlineKeyboardBuilder()
    for i, c in enumerate(cats):
        b.button(text=c, callback_data=f"sc:{i}")
    await m.answer(t(uid, "shop_pick_cat"), reply_markup=b.adjust(1).as_markup())


@router.callback_query(F.data.startswith("sc:"))
async def category_pick(c: CallbackQuery):
    uid = c.from_user.id
    cats = categories(tenant_of(uid))
    i = int(c.data.split(":")[1])
    if i < len(cats):
        await show_products(c.message, uid, cats[i])
    await c.answer()


@router.callback_query(F.data.startswith("sa:"))
async def add_to_cart(c: CallbackQuery):
    uid = c.from_user.id
    p = product(int(c.data.split(":")[1]), tenant_of(uid))
    if not p or not p["active"]:
        return await c.answer()
    cart = cart_of(uid)
    if p["stock"] is not None and cart.get(p["id"], 0) + 1 > p["stock"]:
        return await c.answer(t(uid, "shop_no_stock", name=p["name"], n=p["stock"]), show_alert=True)
    cart[p["id"]] = cart.get(p["id"], 0) + 1
    await c.answer(f"{t(uid, 'shop_added')}: {p['name']} ({cart[p['id']]})")


@router.callback_query(F.data.regexp(r"^(si|sd):\d+$"))
async def change_qty(c: CallbackQuery):
    uid = c.from_user.id
    op, pid = c.data.split(":")
    pid = int(pid)
    cart = cart_of(uid)
    p = product(pid, tenant_of(uid))
    if pid in cart and op == "si" and p and p["stock"] is not None and cart[pid] + 1 > p["stock"]:
        return await c.answer(t(uid, "shop_no_stock", name=p["name"], n=p["stock"]), show_alert=True)
    if pid in cart:
        cart[pid] += 1 if op == "si" else -1
        if cart[pid] <= 0:
            cart.pop(pid)
    text, kb = cart_view(uid)
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await c.answer()


@router.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data == "sclear")
async def clear_cart(c: CallbackQuery):
    cart_of(c.from_user.id).clear()
    await c.message.edit_text(t(c.from_user.id, "shop_cleared"))
    await c.answer()


@router.message(is_btn("b_cart"))
async def show_cart(m: Message):
    text, kb = cart_view(m.from_user.id)
    await m.answer(text, reply_markup=kb)


def balance(tenant, uid):
    row = db.execute("SELECT balance FROM shop_bonuses WHERE tenant=? AND user_id=?", (tenant, uid)).fetchone()
    return row[0] if row else 0


def add_balance(tenant, uid, delta):
    db.execute("INSERT INTO shop_bonuses(tenant,user_id,balance) VALUES(?,?,?) ON CONFLICT(tenant,user_id) DO UPDATE SET balance=balance+?",
               (tenant, uid, max(0, delta), delta))
    db.execute("UPDATE shop_bonuses SET balance=0 WHERE balance<0")
    db.commit()


def promo_percent(tenant, code):
    row = db.execute("SELECT percent FROM shop_promos WHERE tenant=? AND code=?", (tenant, (code or "").upper())).fetchone()
    return row[0] if row else 0


def pricing(uid, ans):
    tenant = tenant_of(uid)
    _, base = cart_lines(uid)
    pct = promo_percent(tenant, ans.get("promo"))
    discount = base * pct // 100
    spend = max(0, min(int(ans.get("spend") or 0), balance(tenant, uid), (base - discount) // 2))
    goods = base - discount - spend
    fee = 0
    if ans.get("kind") == "delivery":
        fee_base = int(get_setting(tenant, "delivery_fee", 0) or 0)
        free_from = int(get_setting(tenant, "free_delivery_from", 0) or 0)
        fee = fee_base if fee_base and (not free_from or goods < free_from) else 0
    return {"base": base, "pct": pct, "discount": discount, "spend": spend, "goods": goods, "fee": fee}


def order_total(uid, ans):
    p = pricing(uid, ans)
    return p["goods"], p["fee"]


def max_spend(uid, ans):
    p = pricing(uid, dict(ans, spend=0))
    return min(balance(tenant_of(uid), uid), p["goods"] // 2)


def max_spend_ctx(ans, ctx):
    uid = ctx["uid"]
    return max_spend(uid, ans)


def bonus_choices(uid, ans, ctx):
    use = max_spend(uid, ans)
    return [(t(uid, "shop_bonus_spend", use=money(tenant_of(uid), use)), use), (t(uid, "shop_bonus_keep"), 0)]


def bonus_prompt(uid, ans, ctx):
    tenant = tenant_of(uid)
    return t(uid, "shop_q_bonus", bal=money(tenant, balance(tenant, uid)), use=money(tenant, max_spend(uid, ans)))


def transfer_prompt(uid, ans, ctx):
    tenant = tenant_of(uid)
    total, fee = order_total(uid, ans)
    holder = get_setting(tenant, "card_holder")
    return t(uid, "shop_transfer_info", sum=money(tenant, total + fee), card=get_setting(tenant, "card_number", "—"),
             holder=(f"\n👤 {holder}" if holder else ""))


def pay_choices(uid, ans, ctx):
    opts = [(t(uid, "shop_pay_cash"), "cash")]
    if get_setting(tenant_of(uid), "card_number"):
        opts.append((t(uid, "shop_pay_transfer"), "transfer"))
    return opts


async def checkout_done(bot, uid, ans, ctx):
    tenant = ctx["tenant"]
    lines, _ = cart_lines(uid)
    if not lines:
        return await bot.send_message(uid, t(uid, "shop_cart_gone"), reply_markup=main_kb(uid))
    for pid, q in cart_of(uid).items():
        prod = product(pid, tenant)
        if prod and prod["stock"] is not None and q > prod["stock"]:
            return await bot.send_message(uid, t(uid, "shop_no_stock", name=prod["name"], n=prod["stock"]), reply_markup=main_kb(uid))
    p = pricing(uid, ans)
    addr = ans.get("addr") or {}
    place = f"🚚 {addr.get('text', '')}" if ans.get("kind") == "delivery" else "🏃 " + t(uid, "shop_pickup")
    pay = ans.get("pay", "cash")
    phone = ans.get("phone") or ans.get("phone_saved") or ""
    total = p["goods"] + p["fee"]
    extra = ""
    if p["discount"]:
        extra += f"\n🎟 {ans['promo'].upper()} −{p['pct']}%: −{money(tenant, p['discount'])}"
    if p["spend"]:
        extra += f"\n💎 −{money(tenant, p['spend'])}"
    text = ("\n".join(lines) + extra + (f"\n🚚 {money(tenant, p['fee'])}" if p["fee"] else "") + f"\n\n{t(uid, 'shop_total')}: {money(tenant, total)}\n"
            + (f"💬 {ans['comment']}\n" if ans.get("comment") else "") + f"{place}\n💰 {t(uid, 'shop_pay_' + pay)}\n📞 {phone}")
    status = "verify" if pay == "transfer" else "new"
    cur = db.execute("INSERT INTO shop_orders(tenant,user_id,items,total,status,text,proof,dest,bonus_used,promo) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (tenant, uid, json.dumps(cart_of(uid)), total, status, text, ans.get("proof"), json.dumps(addr), p["spend"],
                      ans.get("promo") if p["discount"] else None))
    db.commit()
    oid = cur.lastrowid
    if p["spend"]:
        add_balance(tenant, uid, -p["spend"])
    for pid, q in cart_of(uid).items():
        db.execute("UPDATE products SET stock=MAX(stock-?,0) WHERE id=? AND tenant=? AND stock IS NOT NULL", (q, pid, tenant))
    if phone:
        db.execute("INSERT INTO profiles(tenant,user_id,name,phone) VALUES(?,?,'',?) ON CONFLICT(tenant,user_id) DO UPDATE SET phone=excluded.phone", (tenant, uid, phone))
    db.commit()
    cart_of(uid).clear()
    await bot.send_message(uid, f"{t(uid, 'shop_verifying') + chr(10) if status == 'verify' else ''}{t(uid, 'shop_order_ok', n=oid)}\n\n{text}",
                           reply_markup=main_kb(uid))
    await send_order_card(bot, oid, notify=True)
    if addr.get("lat") is not None and addr.get("msg"):
        for a in core.admins_of(tenant):
            try:
                await bot.forward_message(a, addr["msg"][0], addr["msg"][1])
            except Exception:
                try:
                    await bot.send_location(a, float(addr["lat"]), float(addr["lon"]))
                except Exception:
                    logging.warning("location not delivered")


core.register_wizard("shop_checkout", [
    {"key": "kind", "prompt": "shop_q_kind", "type": "choice",
     "choices": lambda uid, a, c: [(t(uid, "shop_delivery"), "delivery"), (t(uid, "shop_pickup"), "pickup")]},
    {"key": "addr", "prompt": "shop_q_addr", "type": "address", "when": lambda a, c: a.get("kind") == "delivery"},
    {"key": "comment", "prompt": "shop_q_comment", "type": "text", "optional": True},
    {"key": "promo", "prompt": "shop_q_promo", "type": "text", "optional": True,
     "when": lambda a, c: bool(db.execute("SELECT 1 FROM shop_promos WHERE tenant=?", (c["tenant"],)).fetchone()),
     "check": lambda v, c: promo_percent(c["tenant"], v) > 0},
    {"key": "spend", "prompt": bonus_prompt, "type": "choice", "choices": bonus_choices,
     "when": lambda a, c: max_spend_ctx(a, c) > 0},
    {"key": "phone_saved", "prompt": "shop_q_phone_saved", "type": "choice", "when": lambda a, c: bool(c.get("saved_phone")),
     "choices": lambda uid, a, c: [(t(uid, "shop_use_phone", phone=c["saved_phone"]), c["saved_phone"]), (t(uid, "shop_other_phone"), "")]},
    {"key": "phone", "prompt": "shop_q_phone", "type": "phone", "when": lambda a, c: not c.get("saved_phone") or a.get("phone_saved") == ""},
    {"key": "pay", "prompt": "shop_q_pay", "type": "choice", "choices": pay_choices},
    {"key": "proof", "prompt": transfer_prompt, "type": "proof", "when": lambda a, c: a.get("pay") == "transfer"},
], checkout_done)


@router.callback_query(F.data == "scheckout")
async def checkout(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    lines, total = cart_lines(uid)
    tenant = tenant_of(uid)
    if not lines:
        return await c.answer(t(uid, "shop_cart_empty"), show_alert=True)
    if get_setting(tenant, "orders_paused") == "1":
        return await c.answer(t(uid, "shop_closed"), show_alert=True)
    minimum = int(get_setting(tenant, "min_order", 0) or 0)
    if total < minimum:
        return await c.answer(t(uid, "shop_min_err", sum=money(tenant, minimum)), show_alert=True)
    saved = db.execute("SELECT phone FROM profiles WHERE tenant=? AND user_id=?", (tenant, uid)).fetchone()
    await core.start_wizard(c.message, state, "shop_checkout", uid, {"tenant": tenant, "uid": uid, "saved_phone": (saved[0] if saved else None)})
    await c.answer()


@router.message(is_btn("b_orders"))
async def my_orders(m: Message):
    uid, tenant = m.from_user.id, tenant_of(m.from_user.id)
    rows = db.execute("SELECT id, total, status FROM shop_orders WHERE tenant=? AND user_id=? ORDER BY id DESC LIMIT 8", (tenant, uid)).fetchall()
    if balance(tenant, uid):
        await m.answer(t(uid, "shop_balance", bal=money(tenant, balance(tenant, uid))))
    if not rows:
        return await m.answer(t(uid, "shop_orders_empty"))
    for oid, total, status in rows:
        b = InlineKeyboardBuilder()
        if status in ("new", "verify"):
            b.button(text=t(uid, "shop_cancel"), callback_data=f"scx:{oid}")
        b.button(text=t(uid, "shop_repeat"), callback_data=f"srep:{oid}")
        await m.answer(f"№{oid} · {money(tenant, total)} · {t(uid, 'shop_st_' + status)}", reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("scx:"))
async def cancel_order(c: CallbackQuery):
    uid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    oid = int(c.data.split(":")[1])
    row = db.execute("SELECT status FROM shop_orders WHERE id=? AND tenant=? AND user_id=?", (oid, tenant, uid)).fetchone()
    if not row or row[0] not in ("new", "verify"):
        return await c.answer()
    db.execute("UPDATE shop_orders SET status='cancelled' WHERE id=?", (oid,))
    db.commit()
    refund_bonus(tenant, oid)
    restore_stock(tenant, oid)
    await c.message.edit_text(t(uid, "shop_cancelled", n=oid))
    await notify_admins(c.bot, tenant, f"❌ Клиент отменил заказ №{oid}")
    await c.answer()


def restore_stock(tenant, oid):
    row = db.execute("SELECT items, stock_back FROM shop_orders WHERE id=? AND tenant=?", (oid, tenant)).fetchone()
    if not row or row[1]:
        return
    for pid, q in json.loads(row[0] or "{}").items():
        db.execute("UPDATE products SET stock=stock+? WHERE id=? AND tenant=? AND stock IS NOT NULL", (q, int(pid), tenant))
    db.execute("UPDATE shop_orders SET stock_back=1 WHERE id=?", (oid,))
    db.commit()


def refund_bonus(tenant, oid):
    row = db.execute("SELECT user_id, bonus_used FROM shop_orders WHERE id=? AND tenant=?", (oid, tenant)).fetchone()
    if row and row[1]:
        add_balance(tenant, row[0], row[1])
        db.execute("UPDATE shop_orders SET bonus_used=0 WHERE id=?", (oid,))
        db.commit()


async def credit_cashback(bot, tenant, uid, oid):
    pct = int(get_setting(tenant, "cashback_percent", 0) or 0)
    row = db.execute("SELECT total, earned FROM shop_orders WHERE id=?", (oid,)).fetchone()
    if not pct or not row or row[1]:
        return
    earned = row[0] * pct // 100
    if earned <= 0:
        return
    add_balance(tenant, uid, earned)
    db.execute("UPDATE shop_orders SET earned=? WHERE id=?", (earned, oid))
    db.commit()
    try:
        await bot.send_message(uid, t(uid, "shop_earned", n=money(tenant, earned), bal=money(tenant, balance(tenant, uid))))
    except Exception:
        logging.warning("cashback notice not delivered")


# ---------------- админ ----------------
def order_kb(oid, status):
    b = InlineKeyboardBuilder()
    if status == "new":
        b.button(text="✅ Принять", callback_data=f"so:{oid}:ok")
        b.button(text="❌ Отклонить", callback_data=f"so:{oid}:no")
    elif status == "verify":
        b.button(text="✅ Оплата пришла", callback_data=f"so:{oid}:paid")
        b.button(text="❌ Оплаты нет", callback_data=f"so:{oid}:nopay")
    elif status == "accepted":
        b.button(text="📦 Готов / в пути", callback_data=f"so:{oid}:ready")
    elif status == "ready":
        b.button(text="🏁 Выдан", callback_data=f"so:{oid}:done")
    return b.adjust(1).as_markup()


async def send_order_card(bot, oid, notify=False, to=None):
    row = db.execute("SELECT tenant, text, status, proof FROM shop_orders WHERE id=?", (oid,)).fetchone()
    if not row:
        return
    tenant, text, status, proof = row
    caption = f"{'🆕 ' if notify else '📋 '}Заказ №{oid} · {STATUS_RU[status]}\n\n{text}"[:1000]
    kb = order_kb(oid, status)
    for a in ([to] if to else core.admins_of(tenant)):
        try:
            if proof:
                kind, _, fid = proof.partition(":")
                await (bot.send_document if kind == "doc" else bot.send_photo)(a, fid, caption=caption, reply_markup=kb)
            else:
                await bot.send_message(a, caption, reply_markup=kb)
        except Exception:
            logging.exception("order card failed")


@router.message(F.text == ADM_ORDERS)
async def admin_orders(m: Message):
    if not is_admin(m.from_user.id):
        return
    ids = [r[0] for r in db.execute(f"SELECT id FROM shop_orders WHERE tenant=? AND status IN ({','.join('?' * len(ACTIVE))}) ORDER BY id",
                                    (tenant_of(m.from_user.id), *ACTIVE))]
    if not ids:
        return await m.answer("Активных заказов нет")
    for oid in ids:
        await send_order_card(m.bot, oid, to=m.from_user.id)


TRANSITIONS = {"ok": "accepted", "no": "rejected", "paid": "accepted", "nopay": "rejected", "ready": "ready", "done": "done"}
ALLOWED = {"new": ("ok", "no"), "verify": ("paid", "nopay"), "accepted": ("ready",), "ready": ("done",)}


@router.callback_query(F.data.regexp(r"^so:\d+:(ok|no|paid|nopay|ready|done)$"))
async def order_action(c: CallbackQuery):
    aid = c.from_user.id
    if not is_admin(aid):
        return await c.answer("Нет доступа", show_alert=True)
    _, oid, act = c.data.split(":")
    row = db.execute("SELECT user_id, status FROM shop_orders WHERE id=? AND tenant=?", (oid, tenant_of(aid))).fetchone()
    if not row or act not in ALLOWED.get(row[1], ()):
        return await c.answer("Действие недоступно", show_alert=True)
    uid, _status = row
    new = TRANSITIONS[act]
    db.execute("UPDATE shop_orders SET status=? WHERE id=?", (new, oid))
    db.commit()
    tenant = tenant_of(aid)
    if new == "rejected":
        refund_bonus(tenant, oid)
        restore_stock(tenant, oid)
    try:
        await c.bot.send_message(uid, f"№{oid}: {t(uid, 'shop_st_' + new)}")
    except Exception:
        pass
    if new == "done":
        await reward_referrer(c.bot, tenant, uid)
        await credit_cashback(c.bot, tenant, uid, oid)
        await core.ask_rating(c.bot, uid, "shop", int(oid))
    suffix = f"\n→ {STATUS_RU[new]}"
    kb = order_kb(oid, new)
    if c.message.photo or c.message.document:
        await c.message.edit_caption(caption=((c.message.caption or "") + suffix)[:1024], reply_markup=kb)
    else:
        await c.message.edit_text(c.message.text + suffix, reply_markup=kb)
    await c.answer()


async def product_added(bot, uid, ans, ctx):
    tenant = ctx["tenant"]
    cur = db.execute("INSERT INTO products(tenant,category,name,price,descr,photo) VALUES(?,?,?,?,?,?)",
                     (tenant, ans["category"], ans["name"], ans["price"], ans.get("descr"), ans.get("photo")))
    db.commit()
    await bot.send_message(uid, f"✅ Товар №{cur.lastrowid} «{ans['name']}» добавлен", reply_markup=main_kb(uid))


core.register_wizard("shop_addproduct", [
    {"key": "category", "prompt": "Раздел каталога? (например: Одежда)", "type": "text"},
    {"key": "name", "prompt": "Название товара?", "type": "text"},
    {"key": "price", "prompt": "Цена числом (без валюты)?", "type": "int"},
    {"key": "descr", "prompt": "Описание?", "type": "text", "optional": True},
    {"key": "photo", "prompt": "Фото товара (как фото)?", "type": "photo", "optional": True},
], product_added)


@router.message(F.text == ADM_ADDP)
async def add_product(m: Message, state: FSMContext):
    if is_admin(m.from_user.id) and tenant_of(m.from_user.id):
        await core.start_wizard(m, state, "shop_addproduct", m.from_user.id, {"tenant": tenant_of(m.from_user.id)})


async def product_field_saved(bot, uid, ans, ctx):
    field = ctx["field"]
    db.execute(f"UPDATE products SET {field}=? WHERE id=? AND tenant=?", (ans["value"], ctx["pid"], ctx["tenant"]))
    db.commit()
    await bot.send_message(uid, "✅ Сохранено", reply_markup=main_kb(uid))


core.register_wizard("shop_setprice", [{"key": "value", "prompt": "Новая цена числом?", "type": "int"}], product_field_saved)
core.register_wizard("shop_setstock", [{"key": "value", "prompt": "Сколько штук в наличии? Число, или «-» если без ограничений.", "type": "int", "optional": True}], product_field_saved)
core.register_wizard("shop_setphoto", [{"key": "value", "prompt": "Отправьте новое фото (как фото)", "type": "photo"}], product_field_saved)


@router.message(F.text == ADM_CATALOG)
async def manage_catalog(m: Message):
    if not is_admin(m.from_user.id):
        return
    tenant = tenant_of(m.from_user.id)
    rows = db.execute("SELECT id, category, name, price, active, stock FROM products WHERE tenant=? ORDER BY category, id", (tenant,)).fetchall()
    if not rows:
        return await m.answer("Каталог пуст. Добавьте товар кнопкой «➕ Товар».")
    for pid, cat, name, price, active, stock in rows:
        b = InlineKeyboardBuilder()
        b.button(text="💰 Цена", callback_data=f"sp:{pid}:price")
        b.button(text="🖼 Фото", callback_data=f"sp:{pid}:photo")
        b.button(text="📦 Остаток", callback_data=f"sp:{pid}:stock")
        b.button(text="👁 Скрыть" if active else "👁 Показать", callback_data=f"sp:{pid}:toggle")
        b.button(text="🗑 Удалить", callback_data=f"sp:{pid}:del")
        await m.answer(f"№{pid} · {'🟢' if active else '⚪️'} {cat} / {name} · {money(tenant, price)}" + (f" · остаток {stock}" if stock is not None else ""),
                       reply_markup=b.adjust(2).as_markup())


@router.callback_query(F.data.regexp(r"^sp:\d+:(price|photo|toggle|del|stock)$"))
async def product_action(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    tenant = tenant_of(uid)
    if not (tenant and is_admin(uid)):
        return await c.answer("Нет доступа", show_alert=True)
    _, pid, act = c.data.split(":")
    p = product(int(pid), tenant)
    if not p:
        return await c.answer("Не найдено")
    if act in ("price", "photo", "stock"):
        await core.start_wizard(c.message, state, {"price": "shop_setprice", "photo": "shop_setphoto", "stock": "shop_setstock"}[act], uid,
                                {"tenant": tenant, "pid": p["id"], "field": act})
    elif act == "toggle":
        db.execute("UPDATE products SET active=? WHERE id=?", (0 if p["active"] else 1, p["id"]))
        db.commit()
        await c.message.edit_text(f"№{pid} · {'⚪️ скрыт' if p['active'] else '🟢 показан'}: {p['name']}")
    else:
        db.execute("DELETE FROM products WHERE id=?", (p["id"],))
        db.commit()
        await c.message.edit_text(f"🗑 «{p['name']}» удалён")
    await c.answer()


PERIODS = {"today": ("Сегодня", "date(created)=date('now')"), "week": ("7 дней", "date(created)>=date('now','-6 days')"),
           "all": ("Всё время", "1=1")}


def report_text(tenant, period):
    label, cond = PERIODS[period]
    n, total = db.execute(f"SELECT COUNT(*), COALESCE(SUM(total),0) FROM shop_orders WHERE tenant=? AND status IN ('accepted','ready','done') AND {cond}", (tenant,)).fetchone()
    lost = db.execute(f"SELECT COUNT(*) FROM shop_orders WHERE tenant=? AND status IN ('rejected','cancelled') AND {cond}", (tenant,)).fetchone()[0]
    counts: dict[int, int] = {}
    for (items,) in db.execute(f"SELECT items FROM shop_orders WHERE tenant=? AND status IN ('accepted','ready','done') AND {cond}", (tenant,)):
        for pid, q in json.loads(items or "{}").items():
            counts[int(pid)] = counts.get(int(pid), 0) + q
    top = []
    for pid, q in sorted(counts.items(), key=lambda x: -x[1])[:5]:
        p = product(pid, tenant)
        top.append(f"{p['name'] if p else pid} — {q}")
    return (f"📈 Магазин: {label}\nЗаказов: {n}\nВыручка: {money(tenant, total)}\nСредний чек: {money(tenant, total // n if n else 0)}\n"
            f"Отклонено/отменено: {lost}\n\n🔥 Популярные:\n" + ("\n".join(top) or "—"))


@router.message(F.text == ADM_REPORT)
async def report(m: Message):
    if not is_admin(m.from_user.id):
        return
    b = InlineKeyboardBuilder()
    for key, (label, _) in PERIODS.items():
        b.button(text=label, callback_data=f"srp:{key}")
    await m.answer(report_text(tenant_of(m.from_user.id), "today"), reply_markup=b.adjust(3).as_markup())


@router.callback_query(F.data.regexp(r"^srp:(today|week|all)$"))
async def report_period(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    b = InlineKeyboardBuilder()
    for key, (label, _) in PERIODS.items():
        b.button(text=label, callback_data=f"srp:{key}")
    try:
        await c.message.edit_text(report_text(tenant_of(c.from_user.id), c.data.split(":")[1]), reply_markup=b.adjust(3).as_markup())
    except Exception:
        pass
    await c.answer()


# ---------------- промокоды ----------------
async def promo_saved(bot, uid, ans, ctx):
    db.execute("INSERT OR REPLACE INTO shop_promos(tenant,code,percent) VALUES(?,?,?)", (ctx["tenant"], ans["code"].upper(), ans["percent"]))
    db.commit()
    await bot.send_message(uid, f"✅ Промокод {ans['code'].upper()} на {ans['percent']}% сохранён", reply_markup=main_kb(uid))


core.register_wizard("shop_addpromo", [
    {"key": "code", "prompt": "Промокод (латиница или цифры, например SALE10)?", "type": "text"},
    {"key": "percent", "prompt": "Скидка в процентах (1-90)?", "type": "int", "check": lambda v, c: 1 <= v <= 90},
], promo_saved)


@router.message(F.text == ADM_PROMO)
async def promos_menu(m: Message):
    uid, tenant = m.from_user.id, tenant_of(m.from_user.id)
    if not (tenant and is_admin(uid)):
        return
    b = InlineKeyboardBuilder()
    rows = db.execute("SELECT code, percent FROM shop_promos WHERE tenant=? ORDER BY code", (tenant,)).fetchall()
    for code, pct in rows:
        b.button(text=f"🗑 {code} (−{pct}%)", callback_data=f"spd:{code}")
    b.button(text="➕ Добавить промокод", callback_data="spadd")
    await m.answer("🎟 Промокоды. Нажмите на код, чтобы удалить." if rows else "Промокодов пока нет.", reply_markup=b.adjust(1).as_markup())


@router.callback_query(F.data == "spadd")
async def promo_add(c: CallbackQuery, state: FSMContext):
    tenant = tenant_of(c.from_user.id)
    if not (tenant and is_admin(c.from_user.id)):
        return await c.answer("Нет доступа", show_alert=True)
    await core.start_wizard(c.message, state, "shop_addpromo", c.from_user.id, {"tenant": tenant})
    await c.answer()


@router.callback_query(F.data.startswith("spd:"))
async def promo_delete(c: CallbackQuery):
    tenant = tenant_of(c.from_user.id)
    if not (tenant and is_admin(c.from_user.id)):
        return await c.answer("Нет доступа", show_alert=True)
    db.execute("DELETE FROM shop_promos WHERE tenant=? AND code=?", (tenant, c.data.split(":", 1)[1]))
    db.commit()
    await c.message.edit_text("🗑 Промокод удалён")
    await c.answer()


def export_orders(tenant):
    rows = db.execute("SELECT id, created, status, total, promo, bonus_used, replace(text, char(10), ' | ') FROM shop_orders WHERE tenant=? ORDER BY id", (tenant,)).fetchall()
    return ["№", "Создан", "Статус", "Сумма", "Промокод", "Списано баллов", "Состав"], rows


def stats_lines(tenant):
    n, total = db.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM shop_orders WHERE tenant=? AND status IN ('accepted','ready','done')", (tenant,)).fetchone()
    waiting = db.execute("SELECT COUNT(*) FROM shop_orders WHERE tenant=? AND status IN ('new','verify')", (tenant,)).fetchone()[0]
    avg, cnt = core.avg_rating(tenant, "shop")
    return [f"🛍 Магазин: заказов {n}, выручка {money(tenant, total)}, ждут решения {waiting}, товаров {db.execute('SELECT COUNT(*) FROM products WHERE tenant=?', (tenant,)).fetchone()[0]}",
            f"⭐ Оценка магазина: {avg or '—'} ({cnt})"]


core.register_export("shop", "orders.csv", export_orders)
core.register_stats("shop", stats_lines)


# ---------------- поиск, популярное, повтор заказа ----------------
async def send_found(target, uid, rows):
    tenant = tenant_of(uid)
    if not rows:
        return await target.answer(t(uid, "shop_nothing"))
    for pid, name, price, descr, photo in rows:
        b = InlineKeyboardBuilder()
        b.button(text=t(uid, "shop_add"), callback_data=f"sa:{pid}")
        caption = f"<b>{name}</b> — {money(tenant, price)}\n{descr or ''}"
        try:
            if photo:
                await target.answer_photo(photo, caption=caption, parse_mode="HTML", reply_markup=b.as_markup())
                continue
        except Exception:
            logging.warning("product photo failed")
        await target.answer(caption, parse_mode="HTML", reply_markup=b.as_markup())


async def search_done(bot, uid, ans, ctx):
    q = ans["q"].casefold().strip()  # регистр сравниваем в Python: SQLite lower() не понимает кириллицу
    rows = [r for r in db.execute("SELECT id, name, price, descr, photo, category FROM products WHERE tenant=? AND active=1 ORDER BY id", (ctx["tenant"],))
            if q in f"{r[1]} {r[5]} {r[3] or ''}".casefold()][:10]
    rows = [r[:5] for r in rows]
    class _T:  # отправка через бота, как через Message.answer
        async def answer(self, text, **kw):
            return await bot.send_message(uid, text, **{k: v for k, v in kw.items() if k in ("reply_markup", "parse_mode")})

        async def answer_photo(self, photo, **kw):
            return await bot.send_photo(uid, photo, **kw)
    await send_found(_T(), uid, rows)


core.register_wizard("shop_search", [{"key": "q", "prompt": "shop_q_search", "type": "text"}], search_done)


@router.message(is_btn("b_search"))
async def search_start(m: Message, state: FSMContext):
    await core.start_wizard(m, state, "shop_search", m.from_user.id, {"tenant": tenant_of(m.from_user.id)})


def popular_ids(tenant, limit=6):
    counts: dict[int, int] = {}
    for (items,) in db.execute("SELECT items FROM shop_orders WHERE tenant=? AND status IN ('accepted','ready','done')", (tenant,)):
        for pid, q in json.loads(items or "{}").items():
            counts[int(pid)] = counts.get(int(pid), 0) + q
    return [pid for pid, _ in sorted(counts.items(), key=lambda x: -x[1])[:limit]]


@router.message(is_btn("b_popular"))
async def popular(m: Message):
    uid, tenant = m.from_user.id, tenant_of(m.from_user.id)
    ids = popular_ids(tenant)
    rows = [db.execute("SELECT id, name, price, descr, photo FROM products WHERE id=? AND tenant=? AND active=1", (pid, tenant)).fetchone() for pid in ids]
    rows = [r for r in rows if r]
    if not rows:  # пока нет продаж: показываем новинки
        rows = db.execute("SELECT id, name, price, descr, photo FROM products WHERE tenant=? AND active=1 ORDER BY id DESC LIMIT 6", (tenant,)).fetchall()
    await send_found(m, uid, rows)


@router.callback_query(F.data.startswith("srep:"))
async def repeat_order(c: CallbackQuery):
    uid, tenant = c.from_user.id, tenant_of(c.from_user.id)
    row = db.execute("SELECT items FROM shop_orders WHERE id=? AND tenant=? AND user_id=?", (int(c.data.split(":")[1]), tenant, uid)).fetchone()
    if not row:
        return await c.answer()
    cart = cart_of(uid)
    for pid, q in json.loads(row[0] or "{}").items():
        p = product(int(pid), tenant)
        if p and p["active"]:
            cart[int(pid)] = cart.get(int(pid), 0) + q
    text, kb = cart_view(uid)
    await c.message.answer(text, reply_markup=kb)
    await c.answer()


# ---------------- приглашение друга ----------------
async def reward_referrer(bot, tenant, uid):
    bonus = int(get_setting(tenant, "referral_bonus", 0) or 0)
    row = db.execute("SELECT referrer FROM referrals WHERE tenant=? AND user_id=? AND rewarded=0", (tenant, uid)).fetchone()
    if not bonus or not row:
        return
    db.execute("UPDATE referrals SET rewarded=1 WHERE tenant=? AND user_id=?", (tenant, uid))
    db.commit()
    add_balance(tenant, row[0], bonus)
    try:
        await bot.send_message(row[0], t(row[0], "shop_ref_bonus", n=bonus))
    except Exception:
        logging.warning("referral notice not delivered")


# ---------------- сводка, напоминания, лучшие клиенты ----------------
def daily_lines(tenant, today):
    y = (today - dt.timedelta(days=1)).isoformat()
    shift = tz_shift(tenant)
    n, total = db.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM shop_orders WHERE tenant=? AND status IN ('accepted','ready','done') "
                          "AND date(datetime(created, ?))=?", (tenant, shift, y)).fetchone()
    waiting = db.execute("SELECT COUNT(*) FROM shop_orders WHERE tenant=? AND status IN ('new','verify')", (tenant,)).fetchone()[0]
    lines = [f"🛍 Вчера: заказов {n}, выручка {money(tenant, total)}"]
    if waiting:
        lines.append(f"⚠️ Ждут вашего решения: {waiting}")
    return lines


def tz_shift(tenant):
    return f"{core.tz_hours(tenant):+d} hours"


def top_clients_line(tenant):
    rows = db.execute("SELECT user_id, SUM(total), COUNT(*) FROM shop_orders WHERE tenant=? AND status IN ('accepted','ready','done') "
                      "GROUP BY user_id ORDER BY 2 DESC LIMIT 3", (tenant,)).fetchall()
    if not rows:
        return []
    names = []
    for uid, total, cnt in rows:
        p = db.execute("SELECT name FROM profiles WHERE tenant=? AND user_id=?", (tenant, uid)).fetchone()
        names.append(f"{p[0] if p else uid} ({cnt} зак., {money(tenant, total)})")
    return ["🏆 Лучшие клиенты: " + "; ".join(names)]


core.register_daily("shop", daily_lines)
core.register_stats("shop", lambda tenant: top_clients_line(tenant))
core.register_stale("shop_orders", ("new", "verify"), 10, "Заказ")


@router.message(F.text == ADM_PAUSE)
async def toggle_pause(m: Message):
    tenant = tenant_of(m.from_user.id)
    if not (tenant and is_admin(m.from_user.id)):
        return
    paused = get_setting(tenant, "orders_paused") != "1"
    core.set_setting(tenant, "orders_paused", "1" if paused else "0")
    await m.answer("⏸ Приём заказов ПРИОСТАНОВЛЕН. Клиенты увидят вежливое сообщение. Нажмите ту же кнопку, чтобы включить." if paused
                   else "▶️ Приём заказов ВКЛЮЧЁН")


# ---------------- «мы скучаем»: возврат клиентов ----------------
async def send_winbacks(bot, now_utc=None):
    sent = 0
    now = now_utc or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for (tenant,) in db.execute("SELECT slug FROM tenants WHERE active=1 AND (module='shop' OR module LIKE 'shop,%' OR module LIKE '%,shop' OR module LIKE '%,shop,%')").fetchall():
        days = int(get_setting(tenant, "winback_days", 0) or 0)
        if days <= 0:
            continue
        border = (now - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        for uid, last in db.execute("SELECT user_id, MAX(created) FROM shop_orders WHERE tenant=? AND status='done' GROUP BY user_id HAVING MAX(created) <= ?", (tenant, border)).fetchall():
            seen = db.execute("SELECT sent_at FROM shop_winback WHERE tenant=? AND user_id=?", (tenant, uid)).fetchone()
            if seen and seen[0] > last:
                continue
            text = get_setting(tenant, "winback_text") or t(uid, "shop_winback")
            db.execute("INSERT INTO shop_winback(tenant,user_id,sent_at) VALUES(?,?,?) ON CONFLICT(tenant,user_id) DO UPDATE SET sent_at=excluded.sent_at",
                       (tenant, uid, now.strftime("%Y-%m-%d %H:%M:%S")))
            db.commit()
            try:
                await bot.send_message(uid, text)
                sent += 1
            except Exception:
                logging.warning("winback not delivered")
    return sent


core.register_periodic(send_winbacks)


def weekly_lines(tenant, today):
    n, total = db.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM shop_orders WHERE tenant=? AND status IN ('accepted','ready','done') AND date(created)>=date('now','-6 days')", (tenant,)).fetchone()
    return [f"🛍 За неделю: заказов {n}, выручка {money(tenant, total)}, средний чек {money(tenant, total // n if n else 0)}"]


core.register_weekly("shop", weekly_lines)
