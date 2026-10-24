import asyncio
import csv
import yandex
import io
import json
import logging
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")
TOKEN = os.environ["BOT_TOKEN"]
ADMINS = {int(x) for x in os.environ.get("ADMIN_CHAT_ID", "0").replace(";", ",").split(",") if x.strip().lstrip("-").isdigit() and int(x) != 0}
PAY_TOKEN = os.environ.get("PAYMENT_PROVIDER_TOKEN", "")
AUTO_ACCEPT = os.environ.get("AUTO_ACCEPT", "") == "1"
COURIER = int(os.environ.get("COURIER_CHAT_ID", "0") or 0)
CLIENT = os.environ.get("CLIENT", "yamin-baliq")
CDIR = BASE / "clients" / CLIENT

DATA = json.loads((CDIR / "menu.json").read_text(encoding="utf-8"))
INFO = DATA["restaurant"]
if os.environ.get("CARD_NUMBER"):
    INFO["card"] = {"number": os.environ["CARD_NUMBER"], "holder": os.environ.get("CARD_HOLDER", "")}
CATS = {c["id"]: c for c in DATA["categories"]}
ITEMS = {i["id"]: i for c in DATA["categories"] for i in c["items"]}
LANGS = {"uz": "🇺🇿 O'zbekcha", "ru": "🇷🇺 Русский", "en": "🇬🇧 English"}

TXT = {
    "welcome": {
        "ru": "Добро пожаловать в «{name}»! 🐟\nВыберите действие:",
        "uz": "«{name}»ga xush kelibsiz! 🐟\nAmalni tanlang:",
        "en": "Welcome to {name}! 🐟\nChoose an action:"},
    "pick_lang": {"ru": "Выберите язык", "uz": "Tilni tanlang", "en": "Choose your language"},
    "b_menu": {"ru": "🍽 Меню", "uz": "🍽 Menyu", "en": "🍽 Menu"},
    "b_cart": {"ru": "🛒 Корзина", "uz": "🛒 Savat", "en": "🛒 Cart"},
    "b_book": {"ru": "📅 Забронировать стол", "uz": "📅 Stol band qilish", "en": "📅 Book a table"},
    "b_info": {"ru": "📍 Контакты", "uz": "📍 Aloqa", "en": "📍 Contacts"},
    "b_lang": {"ru": "🌐 Язык", "uz": "🌐 Til", "en": "🌐 Language"},
    "pick_cat": {"ru": "Выберите категорию:", "uz": "Bo'limni tanlang:", "en": "Choose a category:"},
    "add": {"ru": "➕ В корзину", "uz": "➕ Savatga", "en": "➕ Add to cart"},
    "added": {"ru": "Добавлено", "uz": "Qo'shildi", "en": "Added"},
    "in_cart": {"ru": "в корзине", "uz": "savatda", "en": "in cart"},
    "back": {"ru": "⬅️ К категориям", "uz": "⬅️ Bo'limlarga", "en": "⬅️ Categories"},
    "more": {"ru": "Выберите ещё или откройте 🛒 Корзину", "uz": "Yana tanlang yoki 🛒 Savatni oching",
             "en": "Pick more or open 🛒 Cart"},
    "empty": {"ru": "Корзина пуста", "uz": "Savat bo'sh", "en": "Your cart is empty"},
    "your_order": {"ru": "🛒 Ваш заказ:", "uz": "🛒 Buyurtmangiz:", "en": "🛒 Your order:"},
    "total": {"ru": "Итого", "uz": "Jami", "en": "Total"},
    "checkout": {"ru": "✅ Оформить заказ", "uz": "✅ Buyurtma berish", "en": "✅ Place order"},
    "clear": {"ru": "🗑 Очистить", "uz": "🗑 Tozalash", "en": "🗑 Clear"},
    "cleared": {"ru": "Корзина очищена", "uz": "Savat tozalandi", "en": "Cart cleared"},
    "how": {"ru": "Как получите заказ?", "uz": "Buyurtmani qanday olasiz?", "en": "How would you like to receive it?"},
    "delivery": {"ru": "🚚 Доставка", "uz": "🚚 Yetkazib berish", "en": "🚚 Delivery"},
    "pickup": {"ru": "🏃 Самовывоз", "uz": "🏃 Olib ketish", "en": "🏃 Pickup"},
    "ask_addr": {"ru": "Напишите адрес доставки или отправьте геолокацию:",
                 "uz": "Yetkazib berish manzilini yozing yoki joylashuvni yuboring:",
                 "en": "Write the delivery address or send your location:"},
    "ask_phone": {"ru": "Отправьте номер телефона:", "uz": "Telefon raqamingizni yuboring:",
                  "en": "Send your phone number:"},
    "send_phone": {"ru": "📱 Отправить номер", "uz": "📱 Raqamni yuborish", "en": "📱 Send number"},
    "fee": {"ru": "Доставка", "uz": "Yetkazib berish", "en": "Delivery"},
    "to_pay": {"ru": "К оплате", "uz": "To'lov", "en": "To pay"},
    "order_ok": {"ru": "Заказ №{n} принят! Мы скоро свяжемся с вами.",
                 "uz": "№{n} buyurtma qabul qilindi! Tez orada siz bilan bog'lanamiz.",
                 "en": "Order #{n} received! We will contact you shortly."},
    "ask_when": {"ru": "На какую дату и время? Например: 25.10 19:00",
                 "uz": "Qaysi sana va vaqtga? Masalan: 25.10 19:00",
                 "en": "What date and time? For example: 25.10 19:00"},
    "ask_guests": {"ru": "Сколько гостей?", "uz": "Necha kishi?", "en": "How many guests?"},
    "num_err": {"ru": "Введите число, например 4", "uz": "Raqam kiriting, masalan 4",
                "en": "Enter a number, e.g. 4"},
    "book_ok": {"ru": "Заявка на бронь №{n} отправлена. Подтвердим в ближайшее время.",
                "uz": "№{n} band qilish so'rovi yuborildi. Tez orada tasdiqlaymiz.",
                "en": "Booking request #{n} sent. We will confirm shortly."},
    "guests": {"ru": "Гостей", "uz": "Mehmonlar", "en": "Guests"},
    "ok": {"ru": "подтверждён ✅", "uz": "tasdiqlandi ✅", "en": "confirmed ✅"},
    "no": {"ru": "отклонён ❌. Позвоните нам:", "uz": "rad etildi ❌. Qo'ng'iroq qiling:",
           "en": "declined ❌. Please call us:"},
    "order_w": {"ru": "Заказ", "uz": "Buyurtma", "en": "Order"},
    "book_w": {"ru": "Бронь", "uz": "Band qilish", "en": "Booking"},
    "comment_ask": {"ru": "Комментарий к заказу? (например: без лука). Напишите или нажмите «Пропустить»",
                    "uz": "Buyurtmaga izoh? (masalan: piyozsiz). Yozing yoki «O'tkazib yuborish» tugmasini bosing",
                    "en": "Any comment for the order? (e.g. no onions). Write it or tap “Skip”"},
    "skip": {"ru": "⏭ Пропустить", "uz": "⏭ O'tkazib yuborish", "en": "⏭ Skip"},
    "comment_lbl": {"ru": "Комментарий", "uz": "Izoh", "en": "Comment"},
    "st_cook": {"ru": "готовится 👨‍🍳", "uz": "tayyorlanmoqda 👨‍🍳", "en": "is being prepared 👨‍🍳"},
    "st_go": {"ru": "в пути 🚚", "uz": "yo'lda 🚚", "en": "is on the way 🚚"},
    "st_done": {"ru": "готов, приятного аппетита! ✅", "uz": "tayyor, yoqimli ishtaha! ✅", "en": "is ready, enjoy! ✅"},
    "rate_ask": {"ru": "Оцените заказ:", "uz": "Buyurtmani baholang:", "en": "Please rate your order:"},
    "rate_thx": {"ru": "Спасибо за оценку! ⭐", "uz": "Bahoyingiz uchun rahmat! ⭐", "en": "Thanks for your rating! ⭐"},
    "s_dispatched": {"ru": "ищем курьера", "uz": "kuryer qidirilmoqda", "en": "looking for a courier"},
    "courier_lbl": {"ru": "Курьер", "uz": "Kuryer", "en": "Courier"},
    "pay_online": {"ru": "💳 Click / Payme (онлайн)", "uz": "💳 Click / Payme (onlayn)", "en": "💳 Click / Payme (online)"},
    "invoice_desc": {"ru": "Оплата заказа", "uz": "Buyurtma uchun to'lov", "en": "Order payment"},
    "paid_ok": {"ru": "Оплата получена ✅", "uz": "To'lov qabul qilindi ✅", "en": "Payment received ✅"},
    "cart_gone": {"ru": "Корзина пуста или устарела, начните заказ заново", "uz": "Savat bo'sh yoki eskirgan, buyurtmani qaytadan boshlang", "en": "Your cart is empty or expired, please start again"},
    "pay_transfer": {"ru": "💳 Перевод на карту", "uz": "💳 Kartaga o'tkazma", "en": "💳 Card transfer"},
    "transfer_info": {
        "ru": "Переведите {sum} на карту:\n\n💳 {card}{holder}\n\nПосле оплаты отправьте сюда скриншот чека 👇",
        "uz": "{sum} miqdorini kartaga o'tkazing:\n\n💳 {card}{holder}\n\nTo'lovdan keyin chek skrinshotini shu yerga yuboring 👇",
        "en": "Please transfer {sum} to the card:\n\n💳 {card}{holder}\n\nAfter paying, send the receipt screenshot here 👇"},
    "need_proof": {"ru": "Отправьте скриншот чека (фото) или /cancel", "uz": "Chek skrinshotini (rasm) yuboring yoki /cancel",
                   "en": "Please send the receipt screenshot (photo) or /cancel"},
    "verifying": {"ru": "Скриншот получен ⏳ Проверяем оплату, это займёт несколько минут.",
                  "uz": "Skrinshot qabul qilindi ⏳ To'lovni tekshiryapmiz, bir necha daqiqa kuting.",
                  "en": "Screenshot received ⏳ We are checking your payment, it takes a few minutes."},
    "pay_confirmed": {"ru": "оплата подтверждена, заказ принят ✅", "uz": "to'lov tasdiqlandi, buyurtma qabul qilindi ✅",
                      "en": "payment confirmed, order accepted ✅"},
    "pay_notfound": {"ru": "оплата не найдена ❌ Позвоните нам:", "uz": "to'lov topilmadi ❌ Qo'ng'iroq qiling:",
                     "en": "payment not found ❌ Please call us:"},
    "s_verify": {"ru": "проверяем оплату", "uz": "to'lov tekshirilmoqda", "en": "checking payment"},
    "s_awaiting": {"ru": "ждём оплату", "uz": "to'lov kutilmoqda", "en": "awaiting payment"},
    "closed_msg": {"ru": "Приём заказов временно приостановлен. Попробуйте позже 🙏",
                   "uz": "Buyurtma qabul qilish vaqtincha to'xtatilgan. Keyinroq urinib ko'ring 🙏",
                   "en": "Order intake is temporarily paused. Please try again later 🙏"},
    "expired": {"ru": "отменён: оплата не получена вовремя", "uz": "bekor qilindi: to'lov o'z vaqtida kelmadi",
                "en": "cancelled: payment was not received in time"},
    "from_rest": {"ru": "✉️ Сообщение от ресторана:", "uz": "✉️ Restorandan xabar:", "en": "✉️ Message from the restaurant:"},
    "b_hist": {"ru": "📜 Мои заказы", "uz": "📜 Buyurtmalarim", "en": "📜 My orders"},
    "h_empty": {"ru": "У вас пока нет заказов", "uz": "Hozircha buyurtmalaringiz yo'q", "en": "You have no orders yet"},
    "h_title": {"ru": "📜 Ваши последние заказы:", "uz": "📜 So'nggi buyurtmalaringiz:", "en": "📜 Your recent orders:"},
    "repeat": {"ru": "🔁 Повторить", "uz": "🔁 Takrorlash", "en": "🔁 Repeat"},
    "cancel_btn": {"ru": "❌ Отменить", "uz": "❌ Bekor qilish", "en": "❌ Cancel"},
    "cancel_ok": {"ru": "Заказ №{n} отменён", "uz": "№{n} buyurtma bekor qilindi", "en": "Order #{n} cancelled"},
    "cancel_late": {"ru": "Заказ уже в работе, отменить нельзя. Позвоните нам: ",
                    "uz": "Buyurtma allaqachon ishda, bekor qilib bo'lmaydi. Qo'ng'iroq qiling: ",
                    "en": "The order is already in progress and can't be cancelled. Please call us: "},
    "s_new": {"ru": "ожидает", "uz": "kutilmoqda", "en": "pending"},
    "s_accepted": {"ru": "принят", "uz": "qabul qilindi", "en": "accepted"},
    "s_cooking": {"ru": "готовится", "uz": "tayyorlanmoqda", "en": "preparing"},
    "s_delivering": {"ru": "в пути", "uz": "yo'lda", "en": "on the way"},
    "s_done": {"ru": "выполнен", "uz": "bajarildi", "en": "completed"},
    "s_rejected": {"ru": "отклонён", "uz": "rad etildi", "en": "declined"},
    "s_cancelled": {"ru": "отменён", "uz": "bekor qilindi", "en": "cancelled"},
    "promo_btn": {"ru": "🎟 Промокод", "uz": "🎟 Promokod", "en": "🎟 Promo code"},
    "promo_ask": {"ru": "Введите промокод:", "uz": "Promokodni kiriting:", "en": "Enter your promo code:"},
    "promo_ok": {"ru": "Промокод применён: −{p}% ✅", "uz": "Promokod qo'llandi: −{p}% ✅", "en": "Promo applied: −{p}% ✅"},
    "promo_bad": {"ru": "Такого промокода нет", "uz": "Bunday promokod yo'q", "en": "Promo code not found"},
    "pay_ask": {"ru": "Как будете платить?", "uz": "Qanday to'laysiz?", "en": "How would you like to pay?"},
    "pay_cash": {"ru": "💵 Наличными", "uz": "💵 Naqd pul", "en": "💵 Cash"},
    "pay_card": {"ru": "💳 Картой при получении", "uz": "💳 Qabul qilganda karta bilan", "en": "💳 Card on receipt"},
    "pay_lbl": {"ru": "Оплата", "uz": "To'lov", "en": "Payment"},
    "min_err": {"ru": "Минимальная сумма заказа: {sum}", "uz": "Minimal buyurtma summasi: {sum}", "en": "Minimum order amount: {sum}"},
    "send_loc": {"ru": "📍 Отправить геолокацию", "uz": "📍 Joylashuvni yuborish", "en": "📍 Send location"},
    "cancelled": {"ru": "Отменено", "uz": "Bekor qilindi", "en": "Cancelled"},
    "self": {"ru": "Самовывоз", "uz": "Olib ketish", "en": "Pickup"},
}
SOM = {"ru": "сум", "uz": "so'm", "en": "UZS"}

db = sqlite3.connect(os.environ.get("DB_PATH") or str(CDIR / "bot.db"))
db.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, user_id INT, text TEXT, total INT, status TEXT DEFAULT 'new', created TEXT DEFAULT CURRENT_TIMESTAMP)")
db.execute("CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, user_id INT, text TEXT, status TEXT DEFAULT 'new', created TEXT DEFAULT CURRENT_TIMESTAMP)")
db.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, lang TEXT)")
db.execute("CREATE TABLE IF NOT EXISTS reviews(order_id INTEGER PRIMARY KEY, user_id INT, rating INT)")
db.execute("CREATE TABLE IF NOT EXISTS stoplist(item_id TEXT PRIMARY KEY)")
db.execute("CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY)")
db.execute("CREATE TABLE IF NOT EXISTS banned(id INTEGER PRIMARY KEY)")
db.execute("CREATE TABLE IF NOT EXISTS overrides(key TEXT PRIMARY KEY, value TEXT)")
db.execute("CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY, percent INT)")
for col in ("items TEXT", "courier_id INT", "courier_name TEXT", "dest TEXT", "yandex_id TEXT", "proof TEXT"):
    try:
        db.execute(f"ALTER TABLE orders ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass
db.commit()

SETTINGS = {  # key: (подпись, тип)
    "card_number": ("💳 Номер карты", "card"), "card_holder": ("👤 Имя владельца карты", "text"),
    "phone": ("📞 Телефон ресторана", "text"), "address": ("📍 Адрес", "text"), "hours": ("🕒 Часы работы", "text"),
    "delivery_fee": ("🚚 Стоимость доставки (сум)", "int"), "free_delivery_from": ("🆓 Бесплатная доставка от (сум)", "int"),
    "min_order": ("🛒 Минимальная сумма заказа (сум)", "int")}


def set_override(key, value):
    db.execute("INSERT OR REPLACE INTO overrides(key,value) VALUES(?,?)", (key, str(value)))
    db.commit()
    apply_override(key, str(value))


def apply_override(key, v):
    kind, _, name = key.partition(":")
    if kind == "price" and name in ITEMS:
        ITEMS[name]["price"] = int(v)
    elif kind == "photo" and name in ITEMS:
        ITEMS[name]["photo_override"] = v
    elif key in ("card_number", "card_holder"):
        card = dict(INFO.get("card") or {"number": "", "holder": ""})
        card["number" if key == "card_number" else "holder"] = v
        INFO["card"] = card
    elif key in ("address", "hours"):
        INFO[key] = {"ru": v, "uz": v, "en": v}
    elif key == "phone":
        INFO["phone"] = v
    elif key == "paused":
        INFO["paused"] = v == "1"
    elif key in ("delivery_fee", "free_delivery_from", "min_order"):
        INFO[key] = int(v)


for _k, _v in db.execute("SELECT key, value FROM overrides").fetchall():
    try:
        apply_override(_k, _v)
    except Exception:
        logging.exception("bad override %s", _k)

promos_applied: dict[int, tuple[str, int]] = {}
carts: dict[int, dict[str, int]] = {}
router = Router()


SETUP_CODE = os.environ.get("ADMIN_SETUP_CODE", "")
ADMIN_BTN = "📋 Заказы (админ)"
SETTINGS_BTN = "⚙️ Настройки (админ)"
STATS_BTN = "📊 Статистика"
STOP_BTN = "🚫 Стоп-лист"
EXPORT_BTN = "📥 Экспорт заказов"
CLIENT_MODE_BTN = "👤 Режим клиента"
ADMIN_MODE_BTN = "🛠 Режим админа"
REPORT_BTN = "📈 Отчёт"
PAUSE_BTN = "⏯ Приём заказов"
AWAIT_MINUTES = 30
client_mode: set[int] = set()  # админы, временно смотрящие бота глазами клиента


def all_admins():
    return ADMINS | {r[0] for r in db.execute("SELECT id FROM admins")}


def is_admin(uid):
    return uid in all_admins()


async def notify_admins(bot, text, reply_markup=None):
    for a in all_admins():
        try:
            await bot.send_message(a, text, reply_markup=reply_markup)
        except Exception:
            logging.exception("admin notify failed")


def lang_of(uid):
    row = db.execute("SELECT lang FROM users WHERE id=?", (uid,)).fetchone()
    return row[0] if row else "uz"


def t(uid, key, **kw):
    s = TXT[key][lang_of(uid)]
    return s.format(**kw) if kw else s


def money(n, lang):
    return f"{n:,}".replace(",", " ") + " " + SOM[lang]


def is_btn(key):
    return F.text.in_(set(TXT[key].values()))


class Order(StatesGroup):
    kind = State()
    address = State()
    comment = State()
    contact = State()
    pay = State()
    proof = State()


class AdminEdit(StatesGroup):
    value = State()


class PromoSt(StatesGroup):
    code = State()


class Booking(StatesGroup):
    when = State()
    guests = State()
    contact = State()


def main_kb(uid):
    b = ReplyKeyboardBuilder()
    if is_admin(uid) and uid not in client_mode:
        for text in (ADMIN_BTN, SETTINGS_BTN, STATS_BTN, REPORT_BTN, STOP_BTN, PAUSE_BTN, EXPORT_BTN, CLIENT_MODE_BTN):
            b.button(text=text)
        return b.adjust(2).as_markup(resize_keyboard=True)
    for k in ("b_menu", "b_cart", "b_hist", "b_book", "b_info", "b_lang"):
        b.button(text=t(uid, k))
    if is_admin(uid):
        b.button(text=ADMIN_MODE_BTN)
    return b.adjust(2).as_markup(resize_keyboard=True)


def contact_kb(uid):
    b = ReplyKeyboardBuilder()
    b.button(text=t(uid, "send_phone"), request_contact=True)
    return b.as_markup(resize_keyboard=True, one_time_keyboard=True)


def lang_kb():
    b = InlineKeyboardBuilder()
    for code, name in LANGS.items():
        b.button(text=name, callback_data=f"lang:{code}")
    return b.adjust(1).as_markup()


def cart_text(uid):
    lang = lang_of(uid)
    cart = carts.get(uid)
    if not cart:
        return t(uid, "empty"), 0
    lines = [f"• {ITEMS[i]['name'][lang]} × {q} = {money(ITEMS[i]['price'] * q, lang)}" for i, q in cart.items()]
    total = sum(ITEMS[i]["price"] * q for i, q in cart.items())
    promo = promos_applied.get(uid)
    if promo:
        disc = total * promo[1] // 100
        lines.append(f"🎟 {promo[0]} −{promo[1]}%: −{money(disc, lang)}")
        total -= disc
    return "\n".join(lines) + f"\n\n{t(uid, 'total')}: {money(total, lang)}", total


def menu_kb(uid):
    lang = lang_of(uid)
    b = InlineKeyboardBuilder()
    for c in CATS.values():
        b.button(text=c["name"][lang], callback_data=f"cat:{c['id']}")
    return b.adjust(1).as_markup()


async def send_welcome(m: Message, uid):
    if is_admin(uid) and uid not in client_mode:
        return await m.answer("🛠 Панель администратора. Новые заказы и чеки приходят сюда сообщениями с кнопками.\n"
                              "Команды: /orders /stats /stop /export /settings /broadcast /promo /admins",
                              reply_markup=main_kb(uid))
    caption = t(uid, "welcome", name=INFO["name"])
    logo = CDIR / "logo.jpg"
    if logo.exists():
        await m.answer_photo(FSInputFile(logo), caption=caption, reply_markup=main_kb(uid))
    else:
        await m.answer(caption, reply_markup=main_kb(uid))


def is_banned(uid):
    return bool(db.execute("SELECT 1 FROM banned WHERE id=?", (uid,)).fetchone()) and not is_admin(uid)


async def ban_guard(handler, event, data):
    user = getattr(event, "from_user", None)
    if user and is_banned(user.id):
        return None
    return await handler(event, data)


router.message.outer_middleware(ban_guard)
router.callback_query.outer_middleware(ban_guard)


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    first = db.execute("SELECT 1 FROM users WHERE id=?", (m.from_user.id,)).fetchone() is None
    if first:
        await m.answer("Tilni tanlang / Выберите язык / Choose your language", reply_markup=lang_kb())
    else:
        await send_welcome(m, m.from_user.id)


@router.message(is_btn("b_lang"))
async def change_lang(m: Message):
    await m.answer("Tilni tanlang / Выберите язык / Choose your language", reply_markup=lang_kb())


@router.callback_query(F.data.startswith("lang:"))
async def set_lang(c: CallbackQuery):
    code = c.data.split(":")[1]
    db.execute("INSERT INTO users(id,lang) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET lang=excluded.lang",
               (c.from_user.id, code))
    db.commit()
    await c.message.delete()
    await send_welcome(c.message, c.from_user.id)
    await c.answer()


@router.message(is_btn("b_menu"))
async def show_menu(m: Message):
    await m.answer(t(m.from_user.id, "pick_cat"), reply_markup=menu_kb(m.from_user.id))


@router.callback_query(F.data == "menu")
async def back_menu(c: CallbackQuery):
    await c.message.answer(t(c.from_user.id, "pick_cat"), reply_markup=menu_kb(c.from_user.id))
    await c.answer()


def photo_of(item):
    if item.get("photo_override"):
        return item["photo_override"]
    for ext in ("jpg", "jpeg", "png"):
        p = CDIR / "photos" / f"{item['id']}.{ext}"
        if p.exists():
            return FSInputFile(p)
    return item.get("photo")


@router.callback_query(F.data.startswith("cat:"))
async def show_cat(c: CallbackQuery):
    uid = c.from_user.id
    lang = lang_of(uid)
    cat = CATS.get(c.data.split(":")[1])
    if not cat:
        await c.message.answer(t(uid, "pick_cat"), reply_markup=menu_kb(uid))
        return await c.answer()
    await c.message.delete()
    await c.message.answer(f"<b>{cat['name'][lang]}</b>", parse_mode="HTML")
    stopped = {r[0] for r in db.execute("SELECT item_id FROM stoplist")}
    for i in [x for x in cat["items"] if x["id"] not in stopped]:
        caption = f"<b>{i['name'][lang]}</b> — {money(i['price'], lang)}\n{i['desc'][lang]}"
        kb = InlineKeyboardBuilder()
        kb.button(text=t(uid, "add"), callback_data=f"add:{i['id']}")
        kb = kb.as_markup()
        photo = photo_of(i) or (FSInputFile(CDIR / "logo.jpg") if (CDIR / "logo.jpg").exists() else None)
        try:
            if not photo:
                raise ValueError("no photo")
            await c.message.answer_photo(photo, caption=caption, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await c.message.answer(caption, reply_markup=kb, parse_mode="HTML")
    back = InlineKeyboardBuilder()
    back.button(text=t(uid, "back"), callback_data="menu")
    await c.message.answer(t(uid, "more"), reply_markup=back.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("add:"))
async def add(c: CallbackQuery):
    uid = c.from_user.id
    iid = c.data.split(":")[1]
    if iid not in ITEMS:
        return await c.answer(t(uid, "pick_cat"))
    cart = carts.setdefault(uid, {})
    cart[iid] = cart.get(iid, 0) + 1
    await c.answer(f"{t(uid, 'added')}: {ITEMS[iid]['name'][lang_of(uid)]} ({t(uid, 'in_cart')}: {cart[iid]})")


def cart_view(uid):
    text, total = cart_text(uid)
    if not total:
        return text, None
    lang = lang_of(uid)
    b = InlineKeyboardBuilder()
    for iid, q in carts[uid].items():
        b.button(text="➖", callback_data=f"dec:{iid}")
        b.button(text=f"{ITEMS[iid]['name'][lang]} × {q}", callback_data="noop")
        b.button(text="➕", callback_data=f"inc:{iid}")
    b.adjust(3)
    b.row(InlineKeyboardButton(text=t(uid, "promo_btn"), callback_data="promo"))
    b.row(InlineKeyboardButton(text=t(uid, "checkout"), callback_data="checkout"),
          InlineKeyboardButton(text=t(uid, "clear"), callback_data="clear"))
    return f"{t(uid, 'your_order')}\n\n{text}", b.as_markup()


@router.message(is_btn("b_cart"))
async def show_cart(m: Message):
    text, kb = cart_view(m.from_user.id)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^(inc|dec):"))
async def change_qty(c: CallbackQuery):
    uid = c.from_user.id
    op, iid = c.data.split(":")
    if iid not in ITEMS:
        return await c.answer()
    cart = carts.setdefault(uid, {})
    cart[iid] = cart.get(iid, 0) + (1 if op == "inc" else -1)
    if cart[iid] <= 0:
        cart.pop(iid, None)
    text, kb = cart_view(uid)
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await c.answer()


@router.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data == "clear")
async def clear(c: CallbackQuery):
    carts.pop(c.from_user.id, None)
    promos_applied.pop(c.from_user.id, None)
    await c.message.edit_text(t(c.from_user.id, "cleared"))
    await c.answer()


@router.callback_query(F.data == "checkout")
async def checkout(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    if not carts.get(uid):
        return await c.answer(t(uid, "empty"), show_alert=True)
    if INFO.get("paused"):
        return await c.answer(t(uid, "closed_msg"), show_alert=True)
    minimum = INFO.get("min_order", 0)
    if cart_text(uid)[1] < minimum:
        return await c.answer(t(uid, "min_err", sum=money(minimum, lang_of(uid))), show_alert=True)
    b = InlineKeyboardBuilder()
    b.button(text=t(uid, "delivery"), callback_data="kind:delivery")
    b.button(text=t(uid, "pickup"), callback_data="kind:pickup")
    await state.set_state(Order.kind)
    await c.message.answer(t(uid, "how"), reply_markup=b.as_markup())
    await c.answer()


@router.callback_query(Order.kind, F.data.startswith("kind:"))
async def order_kind(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    kind = c.data.split(":")[1]
    await state.update_data(kind=kind)
    if kind == "delivery":
        await state.set_state(Order.address)
        lk = ReplyKeyboardBuilder()
        lk.button(text=t(uid, "send_loc"), request_location=True)
        await c.message.answer(t(uid, "ask_addr"), reply_markup=lk.as_markup(resize_keyboard=True, one_time_keyboard=True))
    else:
        await state.update_data(address=t(uid, "self"))
        await ask_comment(c.message, uid, state)
    await c.answer()


@router.message(Order.address, F.location)
async def order_location(m: Message, state: FSMContext):
    loc = m.location
    await state.update_data(address=f"📍 {map_link(loc.latitude, loc.longitude)}",
                            lat=loc.latitude, lon=loc.longitude, loc_msg=[m.chat.id, m.message_id])
    await ask_comment(m, m.from_user.id, state)


@router.message(Order.address)
async def order_address(m: Message, state: FSMContext):
    await state.update_data(address=m.text)
    await ask_comment(m, m.from_user.id, state)


async def ask_comment(target, uid, state):
    await state.set_state(Order.comment)
    b = InlineKeyboardBuilder()
    b.button(text=t(uid, "skip"), callback_data="skipc")
    await target.answer(t(uid, "comment_ask"), reply_markup=b.as_markup())


async def ask_phone(target, uid, state):
    await state.set_state(Order.contact)
    await target.answer(t(uid, "ask_phone"), reply_markup=contact_kb(uid))


@router.message(Order.comment)
async def got_comment(m: Message, state: FSMContext):
    await state.update_data(comment=m.text)
    await ask_phone(m, m.from_user.id, state)


@router.callback_query(Order.comment, F.data == "skipc")
async def skip_comment(c: CallbackQuery, state: FSMContext):
    await ask_phone(c.message, c.from_user.id, state)
    await c.answer()


def map_link(lat, lon):
    """Ссылка на точку в Яндекс Картах (в pt сначала долгота, потом широта)."""
    return f"https://yandex.com/maps/?pt={lon},{lat}&z=17&l=map"


async def send_client_location(bot, lat, lon, oid, src=None):
    """Пересылает админам геолокацию клиента: исходное сообщение с точкой (src=[chat_id, message_id]),
    а если пересылка не удалась — новую точку по координатам."""
    if lat is None or lon is None:
        return
    for a in all_admins():
        try:
            if src:
                try:
                    await bot.forward_message(a, src[0], src[1])
                    continue
                except Exception:
                    logging.warning("forward of location failed, sending a new point")
            await bot.send_location(a, float(lat), float(lon))
        except Exception:
            logging.exception("location send failed")


def phone_of(m: Message):
    return m.contact.phone_number if m.contact else (m.text or "").strip()


@router.message(Order.contact)
async def got_contact(m: Message, state: FSMContext):
    uid = m.from_user.id
    await state.update_data(phone=phone_of(m))
    await state.set_state(Order.pay)
    methods = pay_methods()
    if methods == ["pay_transfer"]:
        return await begin_transfer(m, m.from_user, state)
    b = InlineKeyboardBuilder()
    for key in methods:
        b.button(text=t(uid, key), callback_data=f"pay:{key}")
    await m.answer(t(uid, "pay_ask"), reply_markup=b.adjust(1).as_markup())


def pay_methods():
    """Способы оплаты клиента: menu.json → restaurant.payment_methods (по умолчанию все доступные)."""
    wanted = INFO.get("payment_methods") or ["pay_cash", "pay_card", "pay_transfer", "pay_online"]
    ok = [k for k in wanted if (k != "pay_transfer" or INFO.get("card")) and (k != "pay_online" or PAY_TOKEN)]
    return ok or ["pay_cash"]


async def begin_transfer(target, user, state):
    """Показывает сумму и карту, ждёт скриншот чека."""
    uid = user.id
    card = INFO["card"]
    d = await state.get_data()
    db.execute("UPDATE orders SET status='cancelled' WHERE user_id=? AND status='awaiting'", (uid,))
    oid, full, total = create_order_row(uid, user, d, "pay_transfer", False, status="awaiting")
    await state.update_data(oid=oid)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отменить заказ", callback_data=f"ord:{oid}:no")
    await notify_admins(target.bot, f"🛒 Заказ №{oid} сформирован, ждём оплату переводом ({AWAIT_MINUTES} мин)\n\n{full}",
                        reply_markup=b.as_markup())
    await send_client_location(target.bot, d.get("lat"), d.get("lon"), oid, d.get("loc_msg"))
    await state.set_state(Order.proof)
    holder = f"\n👤 {card['holder']}" if card.get("holder") else ""
    await target.answer(t(uid, "transfer_info", sum=money(total, lang_of(uid)), card=card["number"], holder=holder))


def build_order(uid, user, d, method_key, paid):
    lang = lang_of(uid)
    text, total = cart_text(uid)
    fee = INFO["delivery_fee"] if d.get("kind") == "delivery" and total < INFO["free_delivery_from"] else 0
    total += fee
    place = ("🚚 " + d.get("address", "")) if d.get("kind") == "delivery" else "🏃 " + t(uid, "self")
    full = (f"{text}\n" + (f"{t(uid, 'fee')}: {money(fee, lang)}\n{t(uid, 'to_pay')}: {money(total, lang)}\n" if fee else "")
            + (f"\n💬 {t(uid, 'comment_lbl')}: {d['comment']}" if d.get("comment") else "")
            + f"\n{place}\n💰 {t(uid, 'pay_lbl')}: {t(uid, method_key)}" + (" ✅ ОПЛАЧЕНО" if paid else "")
            + f"\n📞 {d.get('phone', '')}\n👤 {user.full_name}")
    return full, total


YCFG = INFO.get("yandex")


async def try_yandex(bot, oid):
    """Вызывает Яндекс Доставку для заказа с доставкой. True, если заявка создана."""
    row = db.execute("SELECT user_id, total, dest FROM orders WHERE id=?", (oid,)).fetchone()
    if not (row and row[2] and yandex.enabled(YCFG)):
        return False
    uid, total, dest = row
    try:
        claim_id, status, pricing = await yandex.create_claim(YCFG, oid, total, json.loads(dest))
    except Exception as e:
        logging.exception("yandex create failed")
        await notify_admins(bot, f"⚠️ Яндекс Доставка не создана для заказа №{oid}: {e}\nПередайте курьерам вручную.")
        return False
    db.execute("UPDATE orders SET status='dispatched', yandex_id=? WHERE id=?", (claim_id, oid))
    db.commit()
    price = ""
    try:
        price = f" Стоимость: {pricing['offer']['price']}"
    except Exception:
        pass
    await notify_admins(bot, f"🚕 Яндекс Доставка вызвана для заказа №{oid} (заявка {claim_id}, статус {status}).{price}")
    await bot.send_message(uid, f"{t(uid, 'order_w')} №{oid}: {t(uid, 's_dispatched')} 🔎")
    return True


async def yandex_poller(bot):
    while True:
        await asyncio.sleep(30)
        if not yandex.enabled(YCFG):
            continue
        for oid, uid, cid, status in db.execute(
                "SELECT id, user_id, yandex_id, status FROM orders WHERE yandex_id IS NOT NULL AND status IN ('dispatched','delivering')").fetchall():
            try:
                info = await yandex.claim_info(cid)
                st = info.get("status")
                if st == "performer_found" and status == "dispatched":
                    name = (info.get("performer_info") or {}).get("courier_name", "")
                    db.execute("UPDATE orders SET status='delivering', courier_name=? WHERE id=?", (name, oid))
                    db.commit()
                    await bot.send_message(uid, f"{t(uid, 'order_w')} №{oid} {t(uid, 'st_go')}\n{t(uid, 'courier_lbl')}: {name}")
                    await notify_admins(bot, f"🚕 Курьер Яндекса найден для заказа №{oid}: {name}")
                elif st in yandex.FINAL_OK:
                    db.execute("UPDATE orders SET status='done' WHERE id=?", (oid,))
                    db.commit()
                    await bot.send_message(uid, f"{t(uid, 'order_w')} №{oid} {t(uid, 'st_done')}")
                    await send_rating(bot, uid, oid)
                    await notify_admins(bot, f"✅ Яндекс доставил заказ №{oid}")
                elif st in yandex.FINAL_BAD:
                    db.execute("UPDATE orders SET status='accepted', yandex_id=NULL WHERE id=?", (oid,))
                    db.commit()
                    b = InlineKeyboardBuilder()
                    b.button(text="📦 Передать курьерам", callback_data=f"ord:{oid}:disp")
                    await notify_admins(bot, f"⚠️ Яндекс Доставка по заказу №{oid}: {st}. Передайте заказ курьерам вручную.",
                                        reply_markup=b.as_markup())
            except Exception:
                logging.exception("yandex poll failed")


async def dispatch_to_couriers(bot, oid, uid, order_text):
    b = InlineKeyboardBuilder()
    b.button(text="🙋 Беру заказ", callback_data=f"crt:{oid}:take")
    await bot.send_message(COURIER, f"📦 Заказ №{oid} — нужен курьер\n\n{order_text}", reply_markup=b.as_markup())
    await bot.send_message(uid, f"{t(uid, 'order_w')} №{oid}: {t(uid, 's_dispatched')} 🔎")


def create_order_row(uid, user, d, method_key, paid, status="new"):
    full, total = build_order(uid, user, d, method_key, paid)
    cur = db.execute("INSERT INTO orders(user_id,text,total,items,status) VALUES(?,?,?,?,?)",
                     (uid, full, total, json.dumps(carts.get(uid, {})), status))
    oid = cur.lastrowid
    db.execute("UPDATE orders SET dest=? WHERE id=?", (json.dumps({
        "fullname": d.get("address", ""), "lat": d.get("lat"), "lon": d.get("lon"), "phone": d.get("phone", ""),
        "name": user.full_name, "comment": d.get("comment")}), oid))
    db.commit()
    return oid, full, total


async def finalize(bot, user, state, method_key, paid, edit=None, proof=None):
    d = await state.get_data()
    uid = user.id
    lang = lang_of(uid)
    existing = d.get("oid") if method_key == "pay_transfer" else None
    if existing:
        row = db.execute("SELECT status, text, total FROM orders WHERE id=?", (existing,)).fetchone()
        if not row or row[0] != "awaiting":
            await state.clear()
            return await bot.send_message(uid, t(uid, "cart_gone"), reply_markup=main_kb(uid))
        oid, full, total = existing, row[1], row[2]
    else:
        oid, full, total = create_order_row(uid, user, d, method_key, paid)
    carts.pop(uid, None)
    promos_applied.pop(uid, None)
    await state.clear()
    transfer = method_key == "pay_transfer"
    if transfer:
        db.execute("UPDATE orders SET status='verify', proof=? WHERE id=?",
                   (f"{proof[0]}:{proof[1]}" if proof else None, oid))
        db.commit()
    msg = (f"{t(uid, 'paid_ok') + chr(10) if paid else ''}{t(uid, 'verifying') + chr(10) if transfer else ''}"
           f"{t(uid, 'order_ok', n=oid)}\n\n{full}")
    if edit:
        await edit.edit_text(msg)
        await edit.answer("👇", reply_markup=main_kb(uid))
    else:
        await bot.send_message(uid, msg, reply_markup=main_kb(uid))
    delivery = d.get("kind") == "delivery"
    if not existing and delivery:
        await send_client_location(bot, d.get("lat"), d.get("lon"), oid, d.get("loc_msg"))
    if transfer:
        b = InlineKeyboardBuilder()
        b.button(text="✅ Оплата пришла", callback_data=f"ord:{oid}:paid")
        b.button(text="❌ Оплаты нет", callback_data=f"ord:{oid}:nopay")
        caption = f"🆕 Заказ №{oid} [{lang}] — ПРОВЕРЬТЕ ОПЛАТУ НА КАРТЕ\n\n{full}"
        for a in all_admins():
            try:
                if proof and proof[0] == "doc":
                    await bot.send_document(a, proof[1], caption=caption[:1024], reply_markup=b.adjust(1).as_markup())
                elif proof:
                    await bot.send_photo(a, proof[1], caption=caption[:1024], reply_markup=b.adjust(1).as_markup())
                else:
                    await bot.send_message(a, caption, reply_markup=b.adjust(1).as_markup())
            except Exception:
                logging.exception("proof send failed")
        return
    auto = paid and AUTO_ACCEPT
    b = InlineKeyboardBuilder()
    if auto:
        db.execute("UPDATE orders SET status='accepted' WHERE id=?", (oid,))
        db.commit()
        for nxt in next_steps("ok", delivery):
            b.button(text=STEP_BTN[nxt], callback_data=f"ord:{oid}:{nxt}")
        b.adjust(1)
        await bot.send_message(uid, f"{t(uid, 'order_w')} №{oid} {t(uid, 'ok')}")
    else:
        b.button(text="✅ Принять", callback_data=f"ord:{oid}:ok")
        b.button(text="❌ Отклонить", callback_data=f"ord:{oid}:no")
    await notify_admins(bot, f"🆕 Заказ №{oid} [{lang}]\n\n{full}" + ("\n\n⚙️ принят автоматически" if auto else ""),
                        reply_markup=b.as_markup())
    if auto and delivery and await try_yandex(bot, oid):
        return
    if auto and delivery and COURIER:
        db.execute("UPDATE orders SET status='dispatched' WHERE id=?", (oid,))
        db.commit()
        await dispatch_to_couriers(bot, oid, uid, full)


@router.callback_query(Order.pay, F.data.startswith("pay:"))
async def order_pay(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    method = c.data.split(":")[1]
    if not carts.get(uid):
        await state.clear()
        return await c.answer(t(uid, "cart_gone"), show_alert=True)
    if method not in pay_methods():
        return await c.answer()
    if method == "pay_online":
        if not PAY_TOKEN:
            return await c.answer()
        d = await state.get_data()
        _, total = build_order(uid, c.from_user, d, method, False)
        await c.bot.send_invoice(chat_id=uid, title=INFO["name"], description=t(uid, "invoice_desc"),
                                 payload=f"ord:{uid}", provider_token=PAY_TOKEN, currency="UZS",
                                 prices=[LabeledPrice(label=t(uid, "order_w"), amount=total * 100)])
        return await c.answer()
    if method == "pay_transfer":
        await begin_transfer(c.message, c.from_user, state)
        return await c.answer()
    await finalize(c.bot, c.from_user, state, method, False, edit=c.message)
    await c.answer()


@router.message(Order.proof, F.photo)
async def got_proof_photo(m: Message, state: FSMContext):
    if not carts.get(m.from_user.id):
        await state.clear()
        return await m.answer(t(m.from_user.id, "cart_gone"))
    await finalize(m.bot, m.from_user, state, "pay_transfer", False, proof=("photo", m.photo[-1].file_id))


@router.message(Order.proof, F.document)
async def got_proof_doc(m: Message, state: FSMContext):
    if not carts.get(m.from_user.id):
        await state.clear()
        return await m.answer(t(m.from_user.id, "cart_gone"))
    await finalize(m.bot, m.from_user, state, "pay_transfer", False, proof=("doc", m.document.file_id))


@router.message(Order.proof)
async def need_proof(m: Message, state: FSMContext):
    if (m.text or "").startswith("/"):
        return await cancel(m, state)
    await m.answer(t(m.from_user.id, "need_proof"))


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    if carts.get(q.from_user.id):
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message=t(q.from_user.id, "cart_gone"))


@router.message(F.successful_payment)
async def got_payment(m: Message, state: FSMContext):
    await finalize(m.bot, m.from_user, state, "pay_online", True)


@router.callback_query(F.data == "promo")
async def promo_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(PromoSt.code)
    await c.message.answer(t(c.from_user.id, "promo_ask"))
    await c.answer()


@router.message(PromoSt.code)
async def promo_apply(m: Message, state: FSMContext):
    uid = m.from_user.id
    row = db.execute("SELECT code, percent FROM promos WHERE code=?", ((m.text or "").strip().upper(),)).fetchone()
    await state.clear()
    if not row:
        return await m.answer(t(uid, "promo_bad"))
    promos_applied[uid] = (row[0], row[1])
    await m.answer(t(uid, "promo_ok", p=row[1]))
    text, kb = cart_view(uid)
    await m.answer(text, reply_markup=kb)


@router.message(is_btn("b_hist"))
async def history(m: Message):
    uid = m.from_user.id
    rows = db.execute("SELECT id, total, status, items FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,)).fetchall()
    if not rows:
        return await m.answer(t(uid, "h_empty"))
    lang = lang_of(uid)
    await m.answer(t(uid, "h_title"))
    for oid, total, status, items in rows:
        b = InlineKeyboardBuilder()
        if items:
            b.button(text=t(uid, "repeat"), callback_data=f"rep:{oid}")
        if status in ("new", "awaiting"):
            b.button(text=t(uid, "cancel_btn"), callback_data=f"cx:{oid}")
        await m.answer(f"№{oid} · {money(total, lang)} · {t(uid, 's_' + status)}", reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("rep:"))
async def repeat_order(c: CallbackQuery):
    uid = c.from_user.id
    row = db.execute("SELECT items FROM orders WHERE id=? AND user_id=?", (c.data.split(":")[1], uid)).fetchone()
    if not row or not row[0]:
        return await c.answer()
    stopped = {r[0] for r in db.execute("SELECT item_id FROM stoplist")}
    carts[uid] = {i: q for i, q in json.loads(row[0]).items() if i in ITEMS and i not in stopped}
    text, kb = cart_view(uid)
    await c.message.answer(text, reply_markup=kb)
    await c.answer()


@router.callback_query(F.data.startswith("cx:"))
async def cancel_order(c: CallbackQuery):
    uid = c.from_user.id
    oid = c.data.split(":")[1]
    row = db.execute("SELECT status FROM orders WHERE id=? AND user_id=?", (oid, uid)).fetchone()
    if not row:
        return await c.answer()
    if row[0] not in ("new", "awaiting"):
        return await c.answer(t(uid, "cancel_late") + INFO["phone"], show_alert=True)
    db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    db.commit()
    await c.message.edit_text(t(uid, "cancel_ok", n=oid))
    await notify_admins(c.bot, f"❌ Клиент отменил заказ №{oid} ({c.from_user.full_name})")
    await c.answer()


@router.message(is_btn("b_book"))
async def book(m: Message, state: FSMContext):
    await state.set_state(Booking.when)
    await m.answer(t(m.from_user.id, "ask_when"), reply_markup=ReplyKeyboardRemove())


@router.message(Booking.when)
async def book_when(m: Message, state: FSMContext):
    await state.update_data(when=m.text)
    await state.set_state(Booking.guests)
    await m.answer(t(m.from_user.id, "ask_guests"))


@router.message(Booking.guests)
async def book_guests(m: Message, state: FSMContext):
    uid = m.from_user.id
    if not (m.text or "").isdigit():
        return await m.answer(t(uid, "num_err"))
    await state.update_data(guests=m.text)
    await state.set_state(Booking.contact)
    await m.answer(t(uid, "ask_phone"), reply_markup=contact_kb(uid))


@router.message(Booking.contact)
async def book_done(m: Message, state: FSMContext):
    d = await state.get_data()
    uid = m.from_user.id
    full = (f"📅 {d['when']}\n👥 {t(uid, 'guests')}: {d['guests']}\n📞 {phone_of(m)}\n👤 {m.from_user.full_name}")
    cur = db.execute("INSERT INTO bookings(user_id,text) VALUES(?,?)", (uid, full))
    db.commit()
    bid = cur.lastrowid
    await state.clear()
    await m.answer(f"{t(uid, 'book_ok', n=bid)}\n\n{full}", reply_markup=main_kb(uid))
    if all_admins():
        b = InlineKeyboardBuilder()
        b.button(text="✅ Подтвердить", callback_data=f"book:{bid}:ok")
        b.button(text="❌ Отказать", callback_data=f"book:{bid}:no")
        await notify_admins(m.bot, f"🆕 Бронь №{bid}\n\n{full}", reply_markup=b.as_markup())


STATUS = {"ok": "accepted", "no": "rejected", "cook": "cooking", "go": "delivering", "done": "done", "disp": "dispatched", "paid": "accepted", "nopay": "rejected"}
LABEL = {"ok": "✅ принят", "no": "❌ отклонён", "cook": "👨‍🍳 готовится", "go": "🚚 в пути", "done": "✅ выдан",
         "disp": "📦 передан курьерам", "paid": "✅ оплата подтверждена", "nopay": "❌ оплаты нет"}
STEP_BTN = {"cook": "👨‍🍳 Готовится", "go": "🚚 В пути", "done": "✅ Выдан", "disp": "📦 Передать курьеру"}


def next_steps(res, delivery):
    res = "ok" if res == "paid" else res
    if COURIER and delivery:
        return {"ok": ("cook", "disp"), "cook": ("disp",)}.get(res, ())
    return {"ok": ("cook", "go", "done"), "cook": ("go", "done"), "go": ("done",)}.get(res, ())


async def send_rating(bot, uid, rid):
    b = InlineKeyboardBuilder()
    for n in range(1, 6):
        b.button(text="⭐" * n, callback_data=f"rate:{rid}:{n}")
    await bot.send_message(uid, t(uid, "rate_ask"), reply_markup=b.adjust(3).as_markup())


async def _edit_admin(c, suffix, kb):
    if c.message.photo or c.message.document:
        await c.message.edit_caption(caption=((c.message.caption or "") + suffix)[:1024], reply_markup=kb)
    else:
        await c.message.edit_text(c.message.text + suffix, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^(ord|book):\d+:(ok|no|cook|go|done|disp|paid|nopay)$"))
async def admin_decision(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    kind, rid, res = c.data.split(":")
    table = "orders" if kind == "ord" else "bookings"
    row = db.execute(f"SELECT user_id, text FROM {table} WHERE id=?", (rid,)).fetchone()
    if not row or (kind == "book" and res in ("cook", "go", "done", "disp", "paid", "nopay")):
        return await c.answer("Не найдено")
    uid, order_text = row
    delivery = "\n🚚 " in order_text
    if res == "disp" and not (COURIER and delivery):
        return await c.answer("Курьерская группа не настроена", show_alert=True)
    db.execute(f"UPDATE {table} SET status=? WHERE id=?", (STATUS[res], rid))
    db.commit()
    if res == "disp":
        await dispatch_to_couriers(c.bot, rid, uid, order_text)
    else:
        what = t(uid, "order_w" if kind == "ord" else "book_w")
        tail = {"ok": t(uid, "ok"), "no": f"{t(uid, 'no')} {INFO['phone']}", "cook": t(uid, "st_cook"),
                "go": t(uid, "st_go"), "done": t(uid, "st_done"),
                "paid": t(uid, "pay_confirmed"), "nopay": f"{t(uid, 'pay_notfound')} {INFO['phone']}"}[res]
        await c.bot.send_message(uid, f"{what} №{rid} {tail}")
        if kind == "ord" and res == "done":
            await send_rating(c.bot, uid, rid)
    kb = None
    if kind == "ord" and res in ("ok", "paid") and delivery and yandex.enabled(YCFG) and await try_yandex(c.bot, rid):
        res_label_extra = " · 🚕 Яндекс вызван"
        await _edit_admin(c, f"\n→ {LABEL[res]}{res_label_extra}", None)
        return await c.answer()
    steps = next_steps(res, delivery) if kind == "ord" else ()
    if steps:
        b = InlineKeyboardBuilder()
        for nxt in steps:
            b.button(text=STEP_BTN[nxt], callback_data=f"ord:{rid}:{nxt}")
        kb = b.adjust(1).as_markup()
    if c.message.photo or c.message.document:
        await c.message.edit_caption(caption=((c.message.caption or "") + f"\n→ {LABEL[res]}")[:1024], reply_markup=kb)
    else:
        await c.message.edit_text(c.message.text + f"\n→ {LABEL[res]}", reply_markup=kb)
    await c.answer()


@router.callback_query(F.data.regexp(r"^crt:\d+:(take|done)$"))
async def courier_action(c: CallbackQuery):
    if not COURIER or c.message.chat.id != COURIER:
        return await c.answer("Нет доступа", show_alert=True)
    _, rid, act = c.data.split(":")
    row = db.execute("SELECT user_id, status, courier_id FROM orders WHERE id=?", (rid,)).fetchone()
    if not row:
        return await c.answer("Заказ не найден")
    uid, status, courier_id = row
    name = c.from_user.full_name
    if act == "take":
        if status != "dispatched":
            return await c.answer("Заказ уже взят или отменён", show_alert=True)
        db.execute("UPDATE orders SET status='delivering', courier_id=?, courier_name=? WHERE id=?", (c.from_user.id, name, rid))
        db.commit()
        b = InlineKeyboardBuilder()
        b.button(text="✅ Доставлено", callback_data=f"crt:{rid}:done")
        await c.message.edit_text(c.message.text + f"\n\n🚚 Взял: {name}", reply_markup=b.as_markup())
        await c.bot.send_message(uid, f"{t(uid, 'order_w')} №{rid} {t(uid, 'st_go')}\n{t(uid, 'courier_lbl')}: {name}")
        await notify_admins(c.bot, f"🚚 Курьер {name} взял заказ №{rid}")
    else:
        if c.from_user.id != courier_id:
            return await c.answer("Это не ваш заказ", show_alert=True)
        db.execute("UPDATE orders SET status='done' WHERE id=?", (rid,))
        db.commit()
        await c.message.edit_text(c.message.text + "\n✅ Доставлено")
        await c.bot.send_message(uid, f"{t(uid, 'order_w')} №{rid} {t(uid, 'st_done')}")
        await send_rating(c.bot, uid, rid)
        await notify_admins(c.bot, f"✅ Заказ №{rid} доставлен ({name})")
    await c.answer()


@router.callback_query(F.data.startswith("rate:"))
async def rate(c: CallbackQuery):
    uid = c.from_user.id
    _, oid, n = c.data.split(":")
    db.execute("INSERT OR REPLACE INTO reviews(order_id,user_id,rating) VALUES(?,?,?)", (oid, uid, int(n)))
    db.commit()
    await c.message.edit_text(t(uid, "rate_thx"))
    if all_admins():
        await notify_admins(c.bot, f"⭐ Оценка заказа №{oid}: {n}/5 ({c.from_user.full_name})")
    await c.answer()


@router.message(Command("stats"))
@router.message(F.text == STATS_BTN)
async def stats(m: Message):
    if not is_admin(m.from_user.id):
        return
    o = db.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM orders WHERE status!='rejected' AND date(created)=date('now')").fetchone()
    bk = db.execute("SELECT COUNT(*) FROM bookings WHERE date(created)=date('now')").fetchone()[0]
    r = db.execute("SELECT ROUND(AVG(rating),1), COUNT(*) FROM reviews").fetchone()
    users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    await m.answer(f"📊 Сегодня (UTC)\nЗаказов: {o[0]}\nВыручка: {money(o[1], 'ru')}\nБроней: {bk}\n\n"
                   f"⭐ Средняя оценка: {r[0] or '—'} ({r[1]} оценок)\n👥 Клиентов в боте: {users}")


@router.message(Command("admin"))
async def claim_admin(m: Message, command: CommandObject):
    if not SETUP_CODE or (command.args or "").strip() != SETUP_CODE:
        return
    db.execute("INSERT OR IGNORE INTO admins(id) VALUES(?)", (m.from_user.id,))
    db.commit()
    await m.answer("✅ Вы теперь администратор. Команды: /orders /stats /stop /export /broadcast /promo /admins /addadmin /removeadmin")


@router.message(Command("addadmin"))
async def add_admin(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        return await m.answer("Использование: /addadmin ID (число из @userinfobot). Или человек сам отправит боту /admin КОД")
    db.execute("INSERT OR IGNORE INTO admins(id) VALUES(?)", (int(arg),))
    db.commit()
    await m.answer(f"Администратор {arg} добавлен. Ему нужно один раз написать боту /start.")


@router.message(Command("removeadmin"))
async def remove_admin(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        return await m.answer("Использование: /removeadmin ID")
    if int(arg) in ADMINS:
        return await m.answer("Этот администратор задан в настройках сервера, его нельзя удалить из бота")
    db.execute("DELETE FROM admins WHERE id=?", (int(arg),))
    db.commit()
    await m.answer(f"Администратор {arg} удалён")


@router.message(Command("admins"))
async def list_admins(m: Message):
    if is_admin(m.from_user.id):
        await m.answer("Администраторы: " + ", ".join(str(a) for a in sorted(all_admins())))


ACTIVE = ("awaiting", "new", "verify", "accepted", "cooking", "delivering", "dispatched")


async def show_order(m: Message, oid):
    row = db.execute("SELECT id, total, status, text, proof FROM orders WHERE id=?", (oid,)).fetchone()
    if not row:
        return await m.answer("Заказ не найден")
    oid, total, status, text, proof = row
    delivery = "\n🚚 " in text
    b = InlineKeyboardBuilder()
    if status == "awaiting":
        b.button(text="❌ Отменить заказ", callback_data=f"ord:{oid}:no")
    elif status == "new":
        b.button(text="✅ Принять", callback_data=f"ord:{oid}:ok")
        b.button(text="❌ Отклонить", callback_data=f"ord:{oid}:no")
    elif status == "verify":
        b.button(text="✅ Оплата пришла", callback_data=f"ord:{oid}:paid")
        b.button(text="❌ Оплаты нет", callback_data=f"ord:{oid}:nopay")
    elif status in ("accepted", "cooking"):
        for nxt in next_steps("cook" if status == "cooking" else "ok", delivery):
            b.button(text=STEP_BTN[nxt], callback_data=f"ord:{oid}:{nxt}")
    elif status in ("delivering", "dispatched"):
        b.button(text="✅ Выдан", callback_data=f"ord:{oid}:done")
    kb = b.adjust(1).as_markup()
    caption = f"📋 Заказ №{oid} · статус: {status}\n\n{text}"
    dest = db.execute("SELECT dest FROM orders WHERE id=?", (oid,)).fetchone()
    try:
        point = json.loads(dest[0]) if dest and dest[0] else {}
    except ValueError:
        point = {}
    if point.get("lat") is not None and point.get("lon") is not None and delivery:
        try:
            await m.answer_location(float(point["lat"]), float(point["lon"]))
        except Exception:
            logging.exception("card location failed")
    if proof:
        kind, _, file_id = proof.partition(":")
        send = m.answer_document if kind == "doc" else m.answer_photo
        try:
            if len(caption) <= 1000:
                return await send(file_id, caption=caption, reply_markup=kb)
            await send(file_id, caption=f"🧾 Чек к заказу №{oid}")
        except Exception:
            logging.exception("proof show failed")
    await m.answer(caption, reply_markup=kb)


@router.message(Command("orders"))
@router.message(F.text == ADMIN_BTN)
async def active_orders(m: Message):
    if not is_admin(m.from_user.id):
        return
    ids = [r[0] for r in db.execute(
        f"SELECT id FROM orders WHERE status IN ({','.join('?' * len(ACTIVE))}) ORDER BY id", ACTIVE)]
    if not ids:
        return await m.answer("Активных заказов нет. Новые заказы придут сюда сообщением с кнопками.")
    for oid in ids:
        await show_order(m, oid)


@router.message(Command("order"))
async def one_order(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    arg = (command.args or "").strip().lstrip("№#")
    if not arg.isdigit():
        return await m.answer("Использование: /order НОМЕР, например /order 12")
    await show_order(m, int(arg))


@router.message(Command("msg"))
async def msg_client(m: Message, command: CommandObject):
    """/msg НОМЕР_ЗАКАЗА текст — написать клиенту этого заказа от имени ресторана."""
    if not is_admin(m.from_user.id):
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[0].lstrip("№#").isdigit():
        return await m.answer("Использование: /msg НОМЕР_ЗАКАЗА текст сообщения")
    row = db.execute("SELECT user_id FROM orders WHERE id=?", (int(parts[0].lstrip("№#")),)).fetchone()
    if not row:
        return await m.answer("Заказ не найден")
    try:
        await m.bot.send_message(row[0], f"{t(row[0], 'from_rest')}\n{parts[1]}")
        await m.answer("Отправлено клиенту ✅")
    except Exception:
        await m.answer("Не удалось отправить: клиент заблокировал бота")


PERIODS = {"today": ("Сегодня", "date(created)=date('now')"), "week": ("7 дней", "date(created)>=date('now','-6 days')"),
           "month": ("30 дней", "date(created)>=date('now','-29 days')"), "all": ("Всё время", "1=1")}
PAID_STATUSES = "('accepted','cooking','delivering','dispatched','done')"


def report_text(period):
    label, cond = PERIODS[period]
    n, total = db.execute(f"SELECT COUNT(*), COALESCE(SUM(total),0) FROM orders WHERE status IN {PAID_STATUSES} AND {cond}").fetchone()
    done = db.execute(f"SELECT COUNT(*) FROM orders WHERE status='done' AND {cond}").fetchone()[0]
    lost = db.execute(f"SELECT COUNT(*) FROM orders WHERE status IN ('rejected','cancelled') AND {cond}").fetchone()[0]
    wait = db.execute("SELECT COUNT(*) FROM orders WHERE status IN ('awaiting','new','verify')").fetchone()[0]
    counts: dict[str, int] = {}
    for (items,) in db.execute(f"SELECT items FROM orders WHERE status IN {PAID_STATUSES} AND {cond}"):
        for iid, q in json.loads(items or "{}").items():
            counts[iid] = counts.get(iid, 0) + q
    top = sorted(counts.items(), key=lambda x: -x[1])[:5]
    top_txt = "\n".join(f"{n_}. {ITEMS[i]['name']['ru']} — {q}" for n_, (i, q) in enumerate(top, 1) if i in ITEMS) or "—"
    bk = db.execute(f"SELECT COUNT(*) FROM bookings WHERE {cond}").fetchone()[0]
    r = db.execute("SELECT ROUND(AVG(rating),1), COUNT(*) FROM reviews").fetchone()
    users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    avg = total // n if n else 0
    return (f"📈 Отчёт: {label}\n\nПодтверждённых заказов: {n}\nВыручка: {money(total, 'ru')}\nСредний чек: {money(avg, 'ru')}\n"
            f"Выдано: {done}\nОтклонено/отменено: {lost}\nОжидают решения сейчас: {wait}\nБроней: {bk}\n\n"
            f"🔥 Популярные блюда:\n{top_txt}\n\n⭐ Оценка: {r[0] or '—'} ({r[1]})\n👥 Клиентов в боте: {users}")


@router.message(Command("report"))
@router.message(F.text == REPORT_BTN)
async def report(m: Message, command: CommandObject = None):
    if not is_admin(m.from_user.id):
        return
    arg = ((command.args if command else "") or "").strip().lower()
    b = InlineKeyboardBuilder()
    for key, (label, _) in PERIODS.items():
        b.button(text=label, callback_data=f"rpt:{key}")
    await m.answer(report_text(arg if arg in PERIODS else "today"), reply_markup=b.adjust(2).as_markup())


@router.callback_query(F.data.startswith("rpt:"))
async def report_period(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    key = c.data.split(":")[1]
    if key in PERIODS:
        b = InlineKeyboardBuilder()
        for k, (label, _) in PERIODS.items():
            b.button(text=label, callback_data=f"rpt:{k}")
        try:
            await c.message.edit_text(report_text(key), reply_markup=b.adjust(2).as_markup())
        except Exception:
            pass
    await c.answer()


def toggle_pause(paused=None):
    new = (not INFO.get("paused")) if paused is None else paused
    set_override("paused", "1" if new else "0")
    return new


@router.message(Command("pause"))
async def pause_cmd(m: Message):
    if is_admin(m.from_user.id):
        toggle_pause(True)
        await m.answer("⏸ Приём заказов приостановлен. Клиенты увидят сообщение. Возобновить: /resume")


@router.message(Command("resume"))
async def resume_cmd(m: Message):
    if is_admin(m.from_user.id):
        toggle_pause(False)
        await m.answer("▶️ Приём заказов включён")


@router.message(F.text == PAUSE_BTN)
async def pause_btn(m: Message):
    if is_admin(m.from_user.id):
        paused = toggle_pause()
        await m.answer("⏸ Приём заказов ПРИОСТАНОВЛЕН. Нажмите ту же кнопку, чтобы включить." if paused
                       else "▶️ Приём заказов ВКЛЮЧЁН")


@router.message(Command("ban"))
async def ban_cmd(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    arg = (command.args or "").strip()
    if not arg.isdigit():
        return await m.answer("Использование: /ban ID_пользователя (число). Снять: /unban ID")
    db.execute("INSERT OR IGNORE INTO banned(id) VALUES(?)", (int(arg),))
    db.commit()
    await m.answer(f"🚫 {arg} заблокирован: бот не отвечает ему")


@router.message(Command("unban"))
async def unban_cmd(m: Message, command: CommandObject):
    if is_admin(m.from_user.id) and (command.args or "").strip().isdigit():
        db.execute("DELETE FROM banned WHERE id=?", (int(command.args.strip()),))
        db.commit()
        await m.answer("✅ Разблокирован")


async def expire_awaiting(bot):
    """Отменяет заказы, за которые не прислали чек за AWAIT_MINUTES минут."""
    rows = db.execute("SELECT id, user_id FROM orders WHERE status='awaiting' "
                      f"AND created <= datetime('now','-{AWAIT_MINUTES} minutes')").fetchall()
    for oid, uid in rows:
        db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
        db.commit()
        try:
            await bot.send_message(uid, f"{t(uid, 'order_w')} №{oid} {t(uid, 'expired')}")
        except Exception:
            pass
        await notify_admins(bot, f"⌛ Заказ №{oid} отменён автоматически: чек не пришёл за {AWAIT_MINUTES} минут")
    return len(rows)


async def expire_loop(bot):
    while True:
        await asyncio.sleep(60)
        try:
            await expire_awaiting(bot)
        except Exception:
            logging.exception("expire loop failed")


def adm_home_kb():
    b = InlineKeyboardBuilder()
    b.button(text="💰 Цены блюд", callback_data="adm:prices")
    b.button(text="🖼 Фото блюд", callback_data="adm:photos")
    b.button(text="💳 Карта для оплаты", callback_data="adm:card")
    b.button(text="ℹ️ Контакты и доставка", callback_data="adm:info")
    return b.adjust(1).as_markup()


@router.message(Command("settings"))
@router.message(F.text == SETTINGS_BTN)
async def admin_settings(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    await m.answer("⚙️ Что изменить? Изменения вступают в силу сразу.", reply_markup=adm_home_kb())


@router.callback_query(F.data.startswith("adm:"))
async def admin_nav(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    parts = c.data.split(":")
    act = parts[1]
    b = InlineKeyboardBuilder()
    text = "Что изменить?"
    if act == "home":
        await state.clear()
        return await c.message.edit_text("⚙️ Что изменить? Изменения вступают в силу сразу.", reply_markup=adm_home_kb())
    if act in ("prices", "photos"):
        pre = "pc" if act == "prices" else "fc"
        for cat in CATS.values():
            b.button(text=cat["name"]["ru"], callback_data=f"adm:{pre}:{cat['id']}")
        text = "Выберите раздел:"
    elif act in ("pc", "fc") and parts[2] in CATS:
        for i in CATS[parts[2]]["items"]:
            label = f"{i['name']['ru']} — {money(i['price'], 'ru')}" if act == "pc" else f"🖼 {i['name']['ru']}"
            b.button(text=label, callback_data=f"adm:{'p' if act == 'pc' else 'f'}:{i['id']}")
        text = "Выберите блюдо:"
    elif act in ("p", "f") and parts[2] in ITEMS:
        item = ITEMS[parts[2]]
        await state.set_state(AdminEdit.value)
        await state.update_data(kind="price" if act == "p" else "photo", key=parts[2])
        ask = (f"Введите новую цену для «{item['name']['ru']}» числом в сумах (сейчас {money(item['price'], 'ru')}):"
               if act == "p" else f"Отправьте новое фото для «{item['name']['ru']}» (как фото, не файлом):")
        await c.message.answer(ask + "\n\n/cancel — отмена")
        return await c.answer()
    elif act == "card":
        for k in ("card_number", "card_holder"):
            b.button(text=SETTINGS[k][0], callback_data=f"adm:s:{k}")
        card = INFO.get("card") or {}
        text = f"Сейчас: {card.get('number', 'не задана')} {card.get('holder', '')}".strip()
    elif act == "info":
        for k in ("phone", "address", "hours", "delivery_fee", "free_delivery_from", "min_order"):
            b.button(text=SETTINGS[k][0], callback_data=f"adm:s:{k}")
        text = (f"Телефон: {INFO['phone']}\nАдрес: {INFO['address']['ru']}\nЧасы: {INFO['hours']['ru']}\n"
                f"Доставка: {money(INFO['delivery_fee'], 'ru')}, бесплатно от {money(INFO['free_delivery_from'], 'ru')}\n"
                f"Минимальный заказ: {money(INFO.get('min_order', 0), 'ru')}")
    elif act == "s" and parts[2] in SETTINGS:
        await state.set_state(AdminEdit.value)
        await state.update_data(kind="setting", key=parts[2])
        await c.message.answer(f"Введите новое значение: {SETTINGS[parts[2]][0]}\n\n/cancel — отмена")
        return await c.answer()
    else:
        return await c.answer()
    b.button(text="⬅️ Назад", callback_data="adm:home")
    await c.message.edit_text(text, reply_markup=b.adjust(1).as_markup())
    await c.answer()


@router.message(AdminEdit.value)
async def admin_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return await state.clear()
    d = await state.get_data()
    kind, key = d.get("kind"), d.get("key")
    text = (m.text or "").strip()
    if text == "/cancel":
        await state.clear()
        return await m.answer("Отменено", reply_markup=main_kb(m.from_user.id))
    if kind == "photo":
        if not m.photo:
            return await m.answer("Отправьте именно фото (не файл), или /cancel")
        set_override(f"photo:{key}", m.photo[-1].file_id)
        done = f"Фото для «{ITEMS[key]['name']['ru']}» обновлено ✅"
    elif kind == "price":
        digits = text.replace(" ", "").replace(",", "")
        if not digits.isdigit() or not 100 <= int(digits) <= 100_000_000:
            return await m.answer("Введите цену числом, например 125000, или /cancel")
        set_override(f"price:{key}", int(digits))
        done = f"Цена «{ITEMS[key]['name']['ru']}»: {money(int(digits), 'ru')} ✅"
    else:
        label, typ = SETTINGS[key]
        value = text
        if typ == "int":
            value = text.replace(" ", "")
            if not value.isdigit():
                return await m.answer("Введите число, или /cancel")
        elif typ == "card":
            value = text.replace(" ", "")
            if not (value.isdigit() and 12 <= len(value) <= 19):
                return await m.answer("Номер карты — только цифры (16 цифр), или /cancel")
            value = " ".join(value[i:i + 4] for i in range(0, len(value), 4))
        elif not text:
            return await m.answer("Введите текст, или /cancel")
        set_override(key, value)
        done = f"{label} обновлено ✅"
    await state.clear()
    await m.answer(done, reply_markup=main_kb(m.from_user.id))
    await m.answer("⚙️ Что ещё изменить?", reply_markup=adm_home_kb())


@router.message(F.text == CLIENT_MODE_BTN)
async def to_client_mode(m: Message):
    if is_admin(m.from_user.id):
        client_mode.add(m.from_user.id)
        await m.answer("👤 Режим клиента: так бот выглядит для гостей. Вернуться: «🛠 Режим админа».",
                       reply_markup=main_kb(m.from_user.id))


@router.message(F.text == ADMIN_MODE_BTN)
async def to_admin_mode(m: Message):
    if is_admin(m.from_user.id):
        client_mode.discard(m.from_user.id)
        await m.answer("🛠 Режим админа", reply_markup=main_kb(m.from_user.id))


@router.message(Command("export"))
@router.message(F.text == EXPORT_BTN)
async def export_orders(m: Message):
    if not is_admin(m.from_user.id):
        return
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "created", "status", "total", "details"])
    for r in db.execute("SELECT id, created, status, total, replace(text, char(10), ' | ') FROM orders ORDER BY id"):
        w.writerow(r)
    await m.answer_document(BufferedInputFile(out.getvalue().encode("utf-8-sig"), filename="orders.csv"))


@router.message(Command("broadcast"))
async def broadcast(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    if not command.args:
        return await m.answer("Использование: /broadcast текст сообщения")
    sent = 0
    for (uid,) in db.execute("SELECT id FROM users").fetchall():
        try:
            await m.bot.send_message(uid, command.args)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await m.answer(f"Отправлено: {sent}")


@router.message(Command("promo"))
async def promo_admin(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    a = (command.args or "").split()
    if len(a) == 2 and a[0].lower() == "off":
        db.execute("DELETE FROM promos WHERE code=?", (a[1].upper(),))
        db.commit()
        return await m.answer(f"Промокод {a[1].upper()} удалён")
    if len(a) == 2 and a[1].isdigit() and 0 < int(a[1]) <= 90:
        db.execute("INSERT OR REPLACE INTO promos(code,percent) VALUES(?,?)", (a[0].upper(), int(a[1])))
        db.commit()
        return await m.answer(f"Промокод {a[0].upper()} на {a[1]}% создан")
    await m.answer("Использование:\n/promo КОД ПРОЦЕНТ  (например /promo FISH10 10)\n/promo off КОД")


def stop_kb():
    stopped = {r[0] for r in db.execute("SELECT item_id FROM stoplist")}
    b = InlineKeyboardBuilder()
    for i in ITEMS.values():
        b.button(text=("🚫 " if i["id"] in stopped else "✅ ") + i["name"]["ru"], callback_data=f"sl:{i['id']}")
    return b.adjust(1).as_markup()


@router.message(Command("stop"))
@router.message(F.text == STOP_BTN)
async def stoplist(m: Message):
    if is_admin(m.from_user.id):
        await m.answer("Стоп-лист: нажмите на блюдо, чтобы убрать из меню (🚫) или вернуть (✅)", reply_markup=stop_kb())


@router.callback_query(F.data.startswith("sl:"))
async def toggle_stop(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    iid = c.data.split(":")[1]
    if db.execute("SELECT 1 FROM stoplist WHERE item_id=?", (iid,)).fetchone():
        db.execute("DELETE FROM stoplist WHERE item_id=?", (iid,))
    else:
        db.execute("INSERT INTO stoplist(item_id) VALUES(?)", (iid,))
    db.commit()
    await c.message.edit_reply_markup(reply_markup=stop_kb())
    await c.answer()


@router.message(is_btn("b_info"))
async def contacts(m: Message):
    lang = lang_of(m.from_user.id)
    await m.answer(f"{INFO['name']}\n📍 {INFO['address'][lang]}\n📞 {INFO['phone']}\n🕒 {INFO['hours'][lang]}")


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    d = await state.get_data()
    oid = d.get("oid")
    if oid and db.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone() == ("awaiting",):
        db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
        db.commit()
        await notify_admins(m.bot, f"❌ Клиент отменил неоплаченный заказ №{oid}")
    await state.clear()
    await m.answer(t(m.from_user.id, "cancelled"), reply_markup=main_kb(m.from_user.id))


async def main():
    logging.basicConfig(level=logging.INFO)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    bot = Bot(TOKEN, session=AiohttpSession(proxy=proxy) if proxy else None)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    poller = asyncio.create_task(yandex_poller(bot))
    expirer = asyncio.create_task(expire_loop(bot))
    for a in all_admins():
        try:
            await bot.send_message(a, "🔄 Бот запущен. Кнопка «📋 Заказы (админ)» показывает активные заказы.", reply_markup=main_kb(a))
        except Exception:
            logging.warning("admin %s has not started the bot yet", a)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
