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
_gate = asyncio.Semaphore(2)

LANGS = {"ru": "🇷🇺 Русский", "uz": "🇺🇿 O'zbekcha", "en": "🇬🇧 English"}
T = {
    "pick": {"ru": "🌐 Выберите язык\nTilni tanlang\nChoose language"},
    "hi": {
        "ru": "👋 Я конвертирую документы.\n\nПришлите файл (PDF, Word, Excel, презентацию, книгу, картинку и др.), выберите нужный формат и получите результат.\n\nПримеры:\n• PDF → Word или Excel\n• Word / Excel → PDF\n• старые .doc, .xls, .ppt → новые\n• фото, SVG, HEIC → PDF или JPG\n• EPUB, FB2, DJVU и многое другое\n\nФайлы нигде не сохраняются: после конвертации они удаляются.",
        "uz": "👋 Men hujjatlarni konvertatsiya qilaman.\n\nFayl yuboring (PDF, Word, Excel, taqdimot, kitob, rasm va boshqalar), kerakli formatni tanlang va natijani oling.\n\nMisollar:\n• PDF → Word yoki Excel\n• Word / Excel → PDF\n• eski .doc, .xls, .ppt → yangi\n• rasm, SVG, HEIC → PDF yoki JPG\n• EPUB, FB2, DJVU va boshqalar\n\nFayllar saqlanmaydi: konvertatsiyadan keyin o'chiriladi.",
        "en": "👋 I convert documents.\n\nSend a file (PDF, Word, Excel, a presentation, an ebook, an image and more), pick the format and get the result.\n\nExamples:\n• PDF → Word or Excel\n• Word / Excel → PDF\n• old .doc, .xls, .ppt → new\n• photos, SVG, HEIC → PDF or JPG\n• EPUB, FB2, DJVU and more\n\nFiles are not stored: they are deleted right after conversion."},
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


async def run_convert(src, ext, dst, outdir):
    proc = await asyncio.create_subprocess_exec(sys.executable, str(CLI), str(src), ext, dst, str(outdir),
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


# ---------- обработчики

def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=v, callback_data=f"lg:{k}")] for k, v in LANGS.items()])


@router.message(CommandStart())
async def start(m: Message):
    uid = m.from_user.id
    if db.execute("SELECT 1 FROM langs WHERE id=?", (uid,)).fetchone() is None:
        return await m.answer(T["pick"]["ru"], reply_markup=lang_kb())
    await m.answer(tr(uid, "hi"))


@router.message(Command("lang"))
async def lang_cmd(m: Message):
    await m.answer(T["pick"]["ru"], reply_markup=lang_kb())


@router.callback_query(F.data.startswith("lg:"))
async def set_lang(c: CallbackQuery):
    code = c.data[3:]
    if code in LANGS:
        db.execute("INSERT INTO langs(id,lang) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET lang=excluded.lang", (c.from_user.id, code))
        db.commit()
        _lang[c.from_user.id] = code
        await c.message.edit_text(tr(c.from_user.id, "hi"))
    await c.answer()


@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(tr(m.from_user.id, "hi"))


@router.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id not in ADMINS:
        return
    d = today()
    week = (datetime.now(TZ) - timedelta(days=6)).strftime("%Y-%m-%d")
    r1 = db.execute("SELECT COALESCE(SUM(n),0), COUNT(*) FROM usage WHERE day=?", (d,)).fetchone()
    r7 = db.execute("SELECT COALESCE(SUM(n),0), COUNT(DISTINCT uid) FROM usage WHERE day>=?", (week,)).fetchone()
    await m.answer(f"📊 Сегодня: {r1[0]} конвертаций, {r1[1]} чел.\nЗа 7 дней: {r7[0]} конвертаций, {r7[1]} чел.\nLibreOffice: {'есть' if converters.soffice() else 'нет'}")


async def offer(m: Message, file_id, name, ext, size):
    uid = m.from_user.id
    if size and size > MAX_MB * 1024 * 1024:
        return await m.answer(tr(uid, "too_big", mb=MAX_MB))
    opts = converters.targets(ext)
    if not opts:
        return await m.answer(tr(uid, "unsupported", ext=ext or "?", formats=", ".join(e.upper() for e in converters.supported_inputs())))
    token = remember(uid, file_id, name, ext)
    btns = [InlineKeyboardButton(text=f"➡️ {o.upper()}", callback_data=f"cv:{token}:{o}") for o in opts]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    await m.answer(tr(uid, "choose", name=name), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(F.document)
async def got_document(m: Message):
    d = m.document
    name = d.file_name or "file"
    await offer(m, d.file_id, name, file_ext(name, d.mime_type), d.file_size)


@router.message(F.photo)
async def got_photo(m: Message):
    p = m.photo[-1]
    await offer(m, p.file_id, "photo.jpg", "jpg", p.file_size)


@router.callback_query(F.data.startswith("cv:"))
async def convert_cb(c: CallbackQuery):
    uid = c.from_user.id
    _, token, dst = c.data.split(":")
    job = _pending.get(token)
    if not job or job["uid"] != uid:
        await c.answer()
        return await c.message.edit_text(tr(uid, "expired"))
    if uid not in ADMINS and used_today(uid) >= DAILY_LIMIT:
        await c.answer()
        return await c.message.edit_text(tr(uid, "limit", n=DAILY_LIMIT))
    await c.answer()
    await c.message.edit_text(tr(uid, "busy"))
    tmp = Path(tempfile.mkdtemp(prefix="cv_"))
    try:
        src = tmp / f"in.{job['ext']}"
        await c.bot.download(job["file_id"], destination=src)
        async with _gate:
            res = await run_convert(src, job["ext"], dst, tmp)
        stem = Path(job["name"]).stem or "file"
        await c.message.answer_document(FSInputFile(res, filename=f"{stem}.{res.suffix.lstrip('.')}"), caption=tr(uid, "done"))
        await c.message.delete()
        count_use(uid)
    except converters.ConvertError as e:
        await c.message.edit_text(tr(uid, e.code if e.code in T else "fail"))
    except Exception:
        log.exception("conversion crashed")
        await c.message.edit_text(tr(uid, "fail"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
