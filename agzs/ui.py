"""Общее для всех обработчиков: названия кнопок, меню по должности, короткие помощники для текстов и клавиатур."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

import core
import texts
from core import can, role

B_STOCK, B_FIN, B_GRAPH, B_ACC = "⛽ Остаток газа", "📊 Финансы", "📈 Графики", "💼 Бухгалтерия"
B_XLS, B_SHIFTS, B_STAFF, B_SET = "📤 Excel", "🧾 Смены", "👥 Персонал", "⚙️ Настройки"
B_SHIFT, B_MINE, B_LANG = "➕ Сменный отчёт", "📋 Мои смены", "🌐 Язык"  # русские названия; у операторов есть и узбекские
B_HOME, B_QUICK = "🏠 Главная", "⚡ Быстрый отчёт"
V_SHIFT, V_MINE, V_LANG = texts.variants("btn_shift"), texts.variants("btn_mine"), texts.variants("btn_lang")
V_HOME, V_QUICK = texts.variants("btn_home"), texts.variants("btn_quick")
MENU = {B_STOCK, B_FIN, B_GRAPH, B_ACC, B_XLS, B_SHIFTS, B_STAFF, B_SET} | V_SHIFT | V_MINE | V_LANG | V_HOME | V_QUICK
MONEY = ["cash", "terminal", "click", "paynet"]


def L(uid):
    return core.lang(uid)


def t(uid, key, **kw):
    return texts.tx(L(uid), key, **kw)


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=x, callback_data=c) for x, c in r] for r in rows])


def menu(uid):
    """Кнопки по правам должности: оператор видит своё, остальные — по своим правам."""
    if role(uid) == "operator":
        rows = [[t(uid, "btn_shift"), t(uid, "btn_quick")], [t(uid, "btn_mine"), t(uid, "btn_home")], [t(uid, "btn_lang")]]
    else:
        items = [t(uid, "btn_home")] + [t(uid, "btn_shift")] * can(uid, "shift") + [t(uid, "btn_quick")] * can(uid, "shift") + [B_STOCK] * can(uid, "stock_view") \
            + [B_FIN, B_GRAPH, B_XLS] * can(uid, "finance") + [B_SHIFTS] * can(uid, "shifts_view") + [B_ACC] * can(uid, "accounting") \
            + [B_SET] * can(uid, "settings") + [B_STAFF] * can(uid, "staff")
        rows = [items[k:k + 2] for k in range(0, len(items), 2)]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x) for x in r] for r in rows], resize_keyboard=True)
