"""Telegram-бот «универсальный конвертер»: присылаешь файл, выбираешь формат, получаешь результат.

Переменные: BOT_TOKEN, ADMIN_IDS (без лимитов, /stats), DB_PATH, CONVERTER_MAX_MB (по умолчанию 15),
CONVERTER_DAILY_LIMIT (конвертаций на человека в сутки, по умолчанию 30).
Файлы лежат только во временной папке на время конвертации и сразу удаляются.
"""
import asyncio
import logging
import mimetypes
import os
import resource
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message)

import converters

HERE = Path(__file__).resolve().parent
CLI = HERE / "cli.py"
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "8219891075").replace(" ", "").split(",") if x.isdigit()}
MAX_MB = int(os.getenv("CONVERTER_MAX_MB", "15"))
MAX_MERGE = 20
DAILY_LIMIT = int(os.getenv("CONVERTER_DAILY_LIMIT", "30"))
TIMEOUT = 150
TZ = timezone(timedelta(hours=5))
log = logging.getLogger("converter")
router = Router()
db = sqlite3.connect(os.getenv("DB_PATH", str(HERE / "converter.db")), check_same_thread=False)
db.executescript("""CREATE TABLE IF NOT EXISTS langs(id INTEGER PRIMARY KEY, lang TEXT);
CREATE TABLE IF NOT EXISTS usage(day TEXT, uid INTEGER, n INTEGER, PRIMARY KEY(day, uid));""")
_lang = {}
_pending = {}
_gate = asyncio.Semaphore(1)  # по одному файлу: LibreOffice и OCR тяжёлые

LANGS = {"ru": "🇷🇺 Русский", "uz": "🇺🇿 O'zbekcha", "en": "🇬🇧 English"}
T = {
    "pick": {"ru": "🌐 Выберите язык\nTilni tanlang\nChoose language"},
    "hi": {
        "ru": "👋 Я конвертирую документы.\n\nПришлите файл (PDF, Word, Excel, презентацию, книгу, картинку и др.), выберите нужный формат и получите результат.\n\nПримеры:\n• PDF → Word или Excel\n• Word / Excel → PDF\n• старые .doc, .xls, .ppt → новые\n• фото, SVG, HEIC → PDF или JPG\n• скан или фото документа → текст, Word, PDF с поиском (OCR)\n• объединить PDF и фото, сжать PDF, разбить на страницы\n• EPUB, FB2, DJVU и многое другое\n\nФайлы нигде не сохраняются: после конвертации они удаляются.",
        "uz": "👋 Men hujjatlarni konvertatsiya qilaman.\n\nFayl yuboring (PDF, Word, Excel, taqdimot, kitob, rasm va boshqalar), kerakli formatni tanlang va natijani oling.\n\nMisollar:\n• PDF → Word yoki Excel\n• Word / Excel → PDF\n• eski .doc, .xls, .ppt → yangi\n• rasm, SVG, HEIC → PDF yoki JPG\n• skan yoki hujjat rasmi → matn, Word, qidiruvli PDF (OCR)\n• PDF va rasmlarni birlashtirish, PDFni siqish, sahifalarga bo'lish\n• EPUB, FB2, DJVU va boshqalar\n\nFayllar saqlanmaydi: konvertatsiyadan keyin o'chiriladi.",
        "en": "👋 I convert documents.\n\nSend a file (PDF, Word, Excel, a presentation, an ebook, an image and more), pick the format and get the result.\n\nExamples:\n• PDF → Word or Excel\n• Word / Excel → PDF\n• old .doc, .xls, .ppt → new\n• photos, SVG, HEIC → PDF or JPG\n• a scan or photo of a document → text, Word, searchable PDF (OCR)\n• merge PDFs and photos, compress PDF, split into pages\n• EPUB, FB2, DJVU and more\n\nFiles are not stored: they are deleted right after conversion."},
    "choose": {"ru": "📄 {name}\nВ какой формат конвертировать?", "uz": "📄 {name}\nQaysi formatga o'tkazamiz?", "en": "📄 {name}\nWhich format do you need?"},
    "unsupported": {
        "ru": "Формат ({ext}) пока не поддерживается.\n\nЯ принимаю: {formats}.",
        "uz": "Format ({ext}) hozircha qo'llab-quvvatlanmaydi.\n\nMen qabul qilaman: {formats}.",
        "en": "The format ({ext}) isn't supported yet.\n\nI accept: {formats}."},
    "too_big": {"ru": "Файл слишком большой. Максимум {mb} МБ.", "uz": "Fayl juda katta. Maksimum {mb} MB.", "en": "The file is too big. Maximum is {mb} MB."},
    "busy": {"ru": "⏳ Конвертирую…", "uz": "⏳ Konvertatsiya qilinmoqda…", "en": "⏳ Converting…"},
    "done": {"ru": "✅ Готово", "uz": "✅ Tayyor", "en": "✅ Done"},
    "expired": {"ru": "Запрос устарел. Пришлите файл ещё раз.", "uz": "So'rov eskirgan. Faylni qayta yuboring.", "en": "This request expired. Please send the file again."},
    "limit": {"ru": "Лимит на сегодня исчерпан ({n} конвертаций). Попробуйте завтра.", "uz": "Bugungi limit tugadi ({n} ta konvertatsiya). Ertaga urinib ko'ring.", "en": "Today's limit is used up ({n} conversions). Try again tomorrow."},
    "bad_file": {"ru": "Не удалось прочитать файл. Он повреждён, защищён паролем или имеет другой формат.", "uz": "Faylni o'qib bo'lmadi. U buzilgan, parol bilan himoyalangan yoki boshqa formatda.", "en": "I couldn't read the file. It may be damaged, password-protected or in another format."},
    "empty": {"ru": "В файле нет данных для этого формата (возможно, это скан без текста).", "uz": "Faylda bu format uchun ma'lumot yo'q (ehtimol, matnsiz skan).", "en": "The file has no data for this format (maybe it is a scan without text)."},
    "fail": {"ru": "Что-то пошло не так. Попробуйте другой формат или пришлите файл ещё раз.", "uz": "Nimadir xato ketdi. Boshqa formatni sinab ko'ring yoki faylni qayta yuboring.", "en": "Something went wrong. Try another format or send the file again."},
    "cancel_btn": {"ru": "✖️ Отмена", "uz": "✖️ Bekor", "en": "✖️ Cancel"},
    "cancelled": {"ru": "Отменено.", "uz": "Bekor qilindi.", "en": "Cancelled."},
    "lbl_compress": {"ru": "🗜 Сжать PDF", "uz": "🗜 PDFni siqish", "en": "🗜 Compress PDF"},
    "lbl_split": {"ru": "✂️ Разбить на страницы", "uz": "✂️ Sahifalarga bo'lish", "en": "✂️ Split into pages"},
    "lbl_ocrpdf": {"ru": "🔍 PDF с текстом (OCR)", "uz": "🔍 Matnli PDF (OCR)", "en": "🔍 Searchable PDF (OCR)"},
    "btn_merge": {"ru": "📎 Объединить в один PDF", "uz": "📎 Bitta PDFga birlashtirish", "en": "📎 Merge into one PDF"},
    "btn_formats": {"ru": "ℹ️ Форматы", "uz": "ℹ️ Formatlar", "en": "ℹ️ Formats"},
    "btn_lang": {"ru": "🌐 Язык", "uz": "🌐 Til", "en": "🌐 Language"},
    "formats": {"ru": "Принимаю: {formats}.\n\nСкан или фото документа можно превратить в текст, Word или PDF с поиском (OCR).", "uz": "Qabul qilaman: {formats}.\n\nSkan yoki hujjat rasmini matn, Word yoki qidiruvli PDFga aylantirish mumkin (OCR).", "en": "I accept: {formats}.\n\nA scan or a photo of a document can become text, Word or a searchable PDF (OCR)."},
    "merge_ask": {"ru": "📎 Присылайте PDF и фото по очереди (до {max} файлов). Когда всё готово, нажмите «Объединить».", "uz": "📎 PDF va rasmlarni ketma-ket yuboring (ko'pi bilan {max} ta). Tayyor bo'lgach «Birlashtirish» ni bosing.", "en": "📎 Send PDFs and photos one by one (up to {max} files). When ready, tap «Merge»."},
    "merge_count": {"ru": "📎 Добавлено файлов: {n}\nПорядок такой же, как вы присылали.", "uz": "📎 Qo'shilgan fayllar: {n}\nTartib siz yuborgandek.", "en": "📎 Files added: {n}\nThe order is the one you sent them in."},
    "merge_go": {"ru": "✅ Объединить ({n})", "uz": "✅ Birlashtirish ({n})", "en": "✅ Merge ({n})"},
    "merge_bad": {"ru": "В объединение можно добавлять только PDF и картинки.", "uz": "Birlashtirishga faqat PDF va rasmlarni qo'shish mumkin.", "en": "Only PDFs and images can be merged."},
    "merge_few": {"ru": "Нужно минимум 2 файла.", "uz": "Kamida 2 ta fayl kerak.", "en": "You need at least 2 files."},
    "merge_full": {"ru": "Максимум {max} файлов за раз.", "uz": "Bir martada ko'pi bilan {max} ta fayl.", "en": "At most {max} files at a time."},
    "merge_done": {"ru": "✅ Готово: {n} файл(ов) в одном PDF · {size}", "uz": "✅ Tayyor: {n} ta fayl bitta PDFda · {size}", "en": "✅ Done: {n} file(s) in one PDF · {size}"},
    "many_pages": {"ru": "Слишком много страниц для распознавания текста (максимум {n}). Разбейте файл на части.", "uz": "Matnni aniqlash uchun sahifalar juda ko'p (ko'pi bilan {n}). Faylni qismlarga bo'ling.", "en": "Too many pages for text recognition (maximum {n}). Please split the file."},
    "done2": {"ru": "✅ Готово · {size} · {sec} с", "uz": "✅ Tayyor · {size} · {sec} s", "en": "✅ Done · {size} · {sec} s"},
    "done_shrunk": {"ru": "✅ Готово · было {a} → стало {b} (−{pct}%)", "uz": "✅ Tayyor · {a} edi → {b} bo'ldi (−{pct}%)", "en": "✅ Done · was {a} → now {b} (−{pct}%)"},
    "busy_ocr": {"ru": "⏳ Конвертирую… Сканы и большие файлы могут занять до минуты.", "uz": "⏳ Konvertatsiya qilinmoqda… Skanlar va katta fayllar bir daqiqagacha olishi mumkin.", "en": "⏳ Converting… Scans and large files can take up to a minute."},
    "other": {"ru": "Другой формат — нажмите кнопку ниже 👇", "uz": "Boshqa format — pastdagi tugmani bosing 👇", "en": "Another format — tap a button below 👇"},
    "text_hint": {"ru": "Пришлите файл документом (скрепка → Файл) или фото.", "uz": "Faylni hujjat sifatida yuboring (skrepka → Fayl) yoki rasm.", "en": "Send a file as a document (clip → File) or a photo."},
}
T["pick"].update(uz=T["pick"]["ru"], en=T["pick"]["ru"])

MIME_EXT = {
    "application/pdf": "pdf", "application/msword": "doc", "application/vnd.ms-excel": "xls", "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt", "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp", "application/rtf": "rtf", "text/rtf": "rtf",
    "text/csv": "csv", "text/plain": "txt", "text/html": "html", "text/markdown": "md", "application/epub+zip": "epub",
    "application/x-fictionbook+xml": "fb2", "image/svg+xml": "svg", "image/heic": "heic", "image/heif": "heic",
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp", "image/tiff": "tiff",
    "image/vnd.djvu": "djvu", "image/x-djvu": "djvu", "application/vnd.ms-xpsdocument": "xps", "application/oxps": "xps",
}


def lang(uid):
    if uid not in _lang:
        r = db.execute("SELECT lang FROM langs WHERE id=?", (uid,)).fetchone()
        _lang[uid] = r[0] if r else None
    return _lang[uid] or "ru"


def tr(uid, key, **kw):
    s = T[key][lang(uid)]
    return s.format(**kw) if kw else s


def today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def used_today(uid):
    r = db.execute("SELECT n FROM usage WHERE day=? AND uid=?", (today(), uid)).fetchone()
    return r[0] if r else 0


def count_use(uid):
    db.execute("INSERT INTO usage(day,uid,n) VALUES(?,?,1) ON CONFLICT(day,uid) DO UPDATE SET n=n+1", (today(), uid))
    db.commit()


def file_ext(name, mime):
    ext = Path(name or "").suffix.lower().lstrip(".")
    if ext:
        return ext
    return MIME_EXT.get(mime or "") or (mimetypes.guess_extension(mime or "") or "").lstrip(".")


def remember(uid, file_id, name, ext):
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["ts"] > 3600]:
        _pending.pop(k, None)
    while len(_pending) > 500:
        _pending.pop(next(iter(_pending)))
    token = secrets.token_urlsafe(6)
    _pending[token] = {"uid": uid, "file_id": file_id, "name": name, "ext": ext, "ts": now}
    return token


def limits():
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT + 10))


async def run_cli(*args):
    """Запускает cli.py в отдельном процессе с ограничением по времени; возвращает путь результата."""
    proc = await asyncio.create_subprocess_exec(sys.executable, str(CLI), *map(str, args),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, preexec_fn=limits)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise converters.ConvertError("bad_file", "timeout")
    if proc.returncode != 0:
        code = err.decode("utf-8", "replace").split(":", 1)[0].strip()
        log.warning("convert failed: %s", err.decode("utf-8", "replace")[:300])
        raise converters.ConvertError(code if code in T else "fail")
    return Path(out.decode().strip().splitlines()[-1])


def err_text(uid, e):
    code = e.code if e.code in T else "fail"
    return tr(uid, code, mb=MAX_MB, n=converters.OCR_MAX_PAGES, max=MAX_MERGE, ext="?", formats=", ".join(x.upper() for x in converters.supported_inputs()))


def fmt_size(n):
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


SPECIAL = ("compress", "split", "ocrpdf")


def label(uid, dst, cat):
    if dst in SPECIAL:
        return tr(uid, "lbl_" + dst)
    if cat == "image" and dst in ("txt", "docx"):
        return f"📝 {dst.upper()} (OCR)"
    return f"➡️ {dst.upper()}"


def targets_kb(uid, token, opts, cat, exclude=None):
    opts = [o for o in opts if o != exclude]
    normal = [InlineKeyboardButton(text=label(uid, o, cat), callback_data=f"cv:{token}:{o}") for o in opts if o not in SPECIAL]
    rows = [normal[i:i + 3] for i in range(0, len(normal), 3)]
    rows += [[InlineKeyboardButton(text=label(uid, o, cat), callback_data=f"cv:{token}:{o}")] for o in opts if o in SPECIAL]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- обработчики

def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=v, callback_data=f"lg:{k}")] for k, v in LANGS.items()])


def home_kb(uid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(uid, "btn_merge"), callback_data="mg:start")],
        [InlineKeyboardButton(text=tr(uid, "btn_formats"), callback_data="fmts"), InlineKeyboardButton(text=tr(uid, "btn_lang"), callback_data="langpick")]])


@router.message(CommandStart())
async def start(m: Message):
    uid = m.from_user.id
    if db.execute("SELECT 1 FROM langs WHERE id=?", (uid,)).fetchone() is None:
        return await m.answer(T["pick"]["ru"], reply_markup=lang_kb())
    await m.answer(tr(uid, "hi"), reply_markup=home_kb(uid))


@router.message(Command("lang"))
async def lang_cmd(m: Message):
    await m.answer(T["pick"]["ru"], reply_markup=lang_kb())


@router.callback_query(F.data == "langpick")
async def lang_pick(c: CallbackQuery):
    await c.message.answer(T["pick"]["ru"], reply_markup=lang_kb())
    await c.answer()


@router.callback_query(F.data.startswith("lg:"))
async def set_lang(c: CallbackQuery):
    code = c.data[3:]
    if code in LANGS:
        db.execute("INSERT INTO langs(id,lang) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET lang=excluded.lang", (c.from_user.id, code))
        db.commit()
        _lang[c.from_user.id] = code
        await c.message.edit_text(tr(c.from_user.id, "hi"), reply_markup=home_kb(c.from_user.id))
    await c.answer()


@router.callback_query(F.data == "fmts")
async def formats_cb(c: CallbackQuery):
    uid = c.from_user.id
    await c.message.answer(tr(uid, "formats", formats=", ".join(x.upper() for x in converters.supported_inputs())))
    await c.answer()


@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(tr(m.from_user.id, "hi"), reply_markup=home_kb(m.from_user.id))


@router.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id not in ADMINS:
        return
    d = today()
    week = (datetime.now(TZ) - timedelta(days=6)).strftime("%Y-%m-%d")
    r1 = db.execute("SELECT COALESCE(SUM(n),0), COUNT(*) FROM usage WHERE day=?", (d,)).fetchone()
    r7 = db.execute("SELECT COALESCE(SUM(n),0), COUNT(DISTINCT uid) FROM usage WHERE day>=?", (week,)).fetchone()
    tools = [x for x, ok in (("LibreOffice", converters.soffice()), ("OCR", converters.tesseract_ok()), ("pandoc", shutil.which("pandoc"))) if ok]
    await m.answer(f"📊 Сегодня: {r1[0]} конвертаций, {r1[1]} чел.\nЗа 7 дней: {r7[0]} конвертаций, {r7[1]} чел.\nИнструменты: {', '.join(tools) or 'встроенные'}")


# ----- объединение PDF и фото (команда /merge, кнопка или альбом из нескольких фото)

_collect = {}


def mergeable(ext):
    e = converters.canon(ext)
    return e == "pdf" or converters.CATEGORY.get(e) == "image"


def merge_kb(uid, n):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(uid, "merge_go", n=n), callback_data="mg:go"),
        InlineKeyboardButton(text=tr(uid, "cancel_btn"), callback_data="mg:no")]])


async def collect_add(m: Message, file_id, name, ext, size):
    uid = m.from_user.id
    c = _collect.setdefault(uid, {"items": [], "msg": None, "task": None})
    if len(c["items"]) >= MAX_MERGE:
        return await m.answer(tr(uid, "merge_full", max=MAX_MERGE))
    if size and size > MAX_MB * 1024 * 1024:
        return await m.answer(tr(uid, "too_big", mb=MAX_MB))
    c["items"].append({"file_id": file_id, "name": name, "ext": converters.canon(ext)})
    if c["task"]:
        c["task"].cancel()
    c["task"] = asyncio.create_task(_collect_refresh(uid, m))


async def _collect_refresh(uid, m):
    try:
        await asyncio.sleep(0.8)  # альбом приходит пачкой сообщений: показываем один счётчик
    except asyncio.CancelledError:
        return
    c = _collect.get(uid)
    if not c:
        return
    text, kb = tr(uid, "merge_count", n=len(c["items"])), merge_kb(uid, len(c["items"]))
    if c["msg"]:
        try:
            await c["msg"].edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    c["msg"] = await m.answer(text, reply_markup=kb)


@router.message(Command("merge"))
async def merge_cmd(m: Message):
    uid = m.from_user.id
    _collect[uid] = {"items": [], "msg": None, "task": None}
    await m.answer(tr(uid, "merge_ask", max=MAX_MERGE))


@router.callback_query(F.data == "mg:start")
async def merge_start_cb(c: CallbackQuery):
    uid = c.from_user.id
    _collect[uid] = {"items": [], "msg": None, "task": None}
    await c.message.answer(tr(uid, "merge_ask", max=MAX_MERGE))
    await c.answer()


@router.callback_query(F.data == "mg:no")
async def merge_cancel(c: CallbackQuery):
    _collect.pop(c.from_user.id, None)
    await c.message.edit_text(tr(c.from_user.id, "cancelled"))
    await c.answer()


@router.callback_query(F.data == "mg:go")
async def merge_go(c: CallbackQuery):
    uid = c.from_user.id
    col = _collect.get(uid)
    if not col or len(col["items"]) < 2:
        return await c.answer(tr(uid, "merge_few"), show_alert=True)
    if uid not in ADMINS and used_today(uid) >= DAILY_LIMIT:
        await c.answer()
        return await c.message.edit_text(tr(uid, "limit", n=DAILY_LIMIT))
    items = col["items"]
    _collect.pop(uid, None)
    await c.answer()
    await c.message.edit_text(tr(uid, "busy"))
    tmp = Path(tempfile.mkdtemp(prefix="cv_"))
    try:
        files = []
        for i, it in enumerate(items):
            p = tmp / f"in{i:02d}.{it['ext']}"
            await c.bot.download(it["file_id"], destination=p)
            files.append(p)
        out = tmp / "merged.pdf"
        async with _gate:
            await run_cli("merge", out, *files)
        await c.message.answer_document(FSInputFile(out, filename="merged.pdf"),
                                        caption=tr(uid, "merge_done", n=len(items), size=fmt_size(out.stat().st_size)))
        await c.message.delete()
        count_use(uid)
    except converters.ConvertError as e:
        await c.message.edit_text(err_text(uid, e))
    except Exception:
        log.exception("merge crashed")
        await c.message.edit_text(tr(uid, "fail"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----- приём файлов

async def offer(m: Message, file_id, name, ext, size):
    uid = m.from_user.id
    if size and size > MAX_MB * 1024 * 1024:
        return await m.answer(tr(uid, "too_big", mb=MAX_MB))
    opts = converters.targets(ext)
    if not opts:
        return await m.answer(tr(uid, "unsupported", ext=ext or "?", formats=", ".join(e.upper() for e in converters.supported_inputs())))
    token = remember(uid, file_id, name, ext)
    cat = converters.CATEGORY.get(converters.canon(ext))
    await m.answer(tr(uid, "choose", name=name), reply_markup=targets_kb(uid, token, opts, cat))


async def incoming(m: Message, file_id, name, ext, size):
    uid = m.from_user.id
    if mergeable(ext) and (uid in _collect or m.media_group_id):
        return await collect_add(m, file_id, name, ext, size)
    if uid in _collect and not mergeable(ext):
        return await m.answer(tr(uid, "merge_bad"))
    await offer(m, file_id, name, ext, size)


@router.message(F.document)
async def got_document(m: Message):
    d = m.document
    name = d.file_name or "file"
    await incoming(m, d.file_id, name, file_ext(name, d.mime_type), d.file_size)


@router.message(F.photo)
async def got_photo(m: Message):
    p = m.photo[-1]
    await incoming(m, p.file_id, "photo.jpg", "jpg", p.file_size)


@router.callback_query(F.data.startswith("cv:"))
async def convert_cb(c: CallbackQuery):
    uid = c.from_user.id
    _, token, dst = c.data.split(":")
    job = _pending.get(token)
    if not job or job["uid"] != uid:
        await c.answer()
        return await c.message.answer(tr(uid, "expired"))
    if uid not in ADMINS and used_today(uid) >= DAILY_LIMIT:
        await c.answer()
        return await c.message.answer(tr(uid, "limit", n=DAILY_LIMIT))
    await c.answer()
    cat = converters.CATEGORY.get(converters.canon(job["ext"]))
    heavy = dst == "ocrpdf" or (dst in ("txt", "docx") and cat in ("image", "pdf"))
    status = await c.message.answer(tr(uid, "busy_ocr" if heavy else "busy"))
    tmp = Path(tempfile.mkdtemp(prefix="cv_"))
    t0 = time.time()
    try:
        src = tmp / f"in.{job['ext']}"
        await c.bot.download(job["file_id"], destination=src)
        before = src.stat().st_size
        async with _gate:
            res = await run_cli(src, job["ext"], dst, tmp)
        stem = Path(job["name"]).stem or "file"
        after = res.stat().st_size
        if dst == "compress":
            pct = max(0, round(100 * (before - after) / before)) if before else 0
            caption = tr(uid, "done_shrunk", a=fmt_size(before), b=fmt_size(after), pct=pct)
        else:
            caption = tr(uid, "done2", size=fmt_size(after), sec=round(time.time() - t0, 1))
        suffix = {"compress": ".pdf", "ocrpdf": ".pdf"}.get(dst, res.suffix)
        name = f"{stem}_compressed{suffix}" if dst == "compress" else f"{stem}{suffix}"
        again = targets_kb(uid, token, converters.targets(job["ext"]), cat, exclude=dst)
        await c.message.answer_document(FSInputFile(res, filename=name), caption=caption, reply_markup=again)
        await status.delete()
        count_use(uid)
    except converters.ConvertError as e:
        await status.edit_text(err_text(uid, e))
    except Exception as e:
        log.exception("conversion crashed")
        text = tr(uid, "too_big", mb=MAX_MB) if "too big" in str(e).lower() else tr(uid, "fail")
        await status.edit_text(text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.callback_query(F.data == "ccancel")
async def cancel_cb(c: CallbackQuery):
    await c.message.edit_text(tr(c.from_user.id, "cancelled"))
    await c.answer()


@router.message()
async def fallback(m: Message):
    await m.answer(tr(m.from_user.id, "text_hint"))


@router.error()
async def on_error(event):
    log.exception("handler error: %s", event.exception)
    return True


async def main():
    logging.basicConfig(level=logging.INFO)
    proxy = os.getenv("HTTPS_PROXY") if os.getenv("USE_PROXY") else None
    bot = Bot(os.environ["BOT_TOKEN"], session=AiohttpSession(proxy=proxy) if proxy else None,
              default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    while True:
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
