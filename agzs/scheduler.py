"""Автоматические сообщения: итоги дня и недели, напоминания о налогах, о нехватке газа, о несданных отчётах, резервная копия базы."""
import asyncio
import logging
import os
import tempfile
from datetime import timedelta

import calc
import core
import texts
from db import now

log = logging.getLogger("agzs.scheduler")
JOBS = ("daily_report", "weekly_report", "tax_reminder", "stock_alert", "shift_reminder", "backup")


def _minutes(hhmm):
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def due_jobs(dt, settings, sent):
    """Какие задания пора выполнить сейчас. dt — время в Ташкенте, settings — настройки, sent — {задание: ключ последнего запуска}."""
    cfg = calc.cfg_of(settings)
    hm = dt.hour * 60 + dt.minute
    day = dt.date().isoformat()
    out = []

    def once(job):
        return sent.get(job) != day

    if cfg["auto_daily"] and hm >= 21 * 60 + 30 and once("daily_report"):
        out.append("daily_report")
    if cfg["auto_daily"] and dt.weekday() == 0 and hm >= 9 * 60 and once("weekly_report"):
        out.append("weekly_report")
    due = int(cfg["tax_due_day"])
    if hm >= 9 * 60 and dt.day in (due - 3, due) and once("tax_reminder"):
        out.append("tax_reminder")
    if hm >= 9 * 60 and once("stock_alert"):
        out.append("stock_alert")
    dl = _minutes(cfg["report_deadline"]) if str(cfg["report_deadline"]) != "0" else None
    if dl is not None and hm >= dl and once("shift_reminder"):
        out.append("shift_reminder")
    if hm >= 3 * 60 and once("backup"):
        out.append("backup")
    return out


async def _send_admins(bot, text, perm="finance", **kw):
    for a in core.ids_with(perm):
        try:
            await bot.send_message(a, text, **kw)
        except Exception:
            log.warning("cannot send to %s", a)


async def run_job(bot, job):
    import extras
    import reports
    t = core.today()
    if job == "daily_report":
        rows = core.rows_for(t.isoformat(), t.isoformat())
        await _send_admins(bot, "📅 Итоги дня\n\n" + reports.finance_text(rows, "сегодня", t.isoformat(), t.isoformat()))
    elif job == "weekly_report":
        s, e, label = core.period_range("week")
        await _send_admins(bot, "🗓 Итоги недели\n\n" + reports.finance_text(core.rows_for(s, e), label, s, e))
        data, name, _ = await reports.build_excel("month")
        from aiogram.types import BufferedInputFile
        for a in core.ids_with("finance"):
            try:
                await bot.send_document(a, BufferedInputFile(data, name), caption="📤 Сводная таблица за 30 дней (автоотчёт)")
            except Exception:
                pass
    elif job == "tax_reminder":
        first = t.replace(day=1)
        prev = (first - timedelta(days=1)).replace(day=1)
        s, e = calc.month_bounds(prev.year, prev.month)
        due = int(core.cfg()["tax_due_day"])
        await _send_admins(bot, perm="accounting", text=f"⏰ Напоминание: до {due} числа нужно сдать НДС и акциз за {extras.MONTHS[prev.month - 1]} {prev.year}.\n\n" + extras.tax_text(s, e, f"{extras.MONTHS[prev.month - 1]} {prev.year}"))
    elif job == "stock_alert":
        st, fc = core.stock_forecast()
        await reports.alert_stock(bot, st, fc)
    elif job == "shift_reminder":
        if core.db.shifts(t.isoformat(), t.isoformat()):
            return
        ops = core.db.staff_ids("operator")
        for uid in ops:
            try:
                await bot.send_message(uid, texts.tx(core.lang(uid), "remind"))
            except Exception:
                pass
        if ops:
            await _send_admins(bot, f"⏰ Отчёт за {t.strftime('%d.%m')} ещё не сдан. Напоминание операторам отправлено ({len(ops)}).", perm="approve")
    elif job == "backup":
        from aiogram.types import FSInputFile
        with tempfile.TemporaryDirectory() as wd:
            path = os.path.join(wd, f"agzs_backup_{t.isoformat()}.db")
            core.db.backup_to(path)
            for a in core.ids_with("staff"):
                try:
                    await bot.send_document(a, FSInputFile(path), caption="💾 Резервная копия базы АГЗС (храните в надёжном месте)")
                except Exception:
                    pass


async def tick(bot):
    """Один проход планировщика (вызывается раз в минуту)."""
    s = core.db.settings()
    sent = {j: s.get(f"sched_{j}") for j in JOBS}
    for job in due_jobs(now(), s, sent):
        core.db.set_setting(f"sched_{job}", now().date().isoformat())  # сначала отметка, чтобы сбой не вызвал повторную рассылку каждую минуту
        try:
            await run_job(bot, job)
        except Exception:
            log.exception("job %s failed", job)


async def run(bot):
    while True:
        try:
            await asyncio.sleep(60)
            await tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler loop")
