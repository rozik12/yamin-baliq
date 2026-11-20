"""Запускает бота ресторана и, если задан SHABBODA_BOT_TOKEN, бота второго клиента в одном сервисе.
Если задан CONVERTER_BOT_TOKEN, ещё и бот-конвертер документов (converter/); AGZS_BOT_TOKEN — бот-бухгалтер АГЗС (agzs/).
Если задан AICALL_TG_SESSION или AICALL_PHONE (первый вход), ещё и голосового ИИ для звонков в Telegram (aicall/, переменные AICALL_*).

У второго бота свои база, токен, админы и карта: берутся из переменных SHABBODA_*;
всё остальное из окружения первого (карта, курьеры, Яндекс, онлайн-оплата) ему не передаётся.
Переменная RUN_ONLY (имена через запятую: main, shabboda, converter, agzs, aicall) оставляет работать только перечисленных.
Упавший бот перезапускается, второй от этого не страдает.
"""
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PER_CLIENT = ("CARD_NUMBER", "CARD_HOLDER", "COURIER_CHAT_ID", "ADMIN_SETUP_CODE", "PAYMENT_PROVIDER_TOKEN",
              "YANDEX_DELIVERY_TOKEN", "AUTO_ACCEPT")


def specs(env=None):
    env = dict(os.environ if env is None else env)
    out = [("main", dict(env))]
    token = env.get("SHABBODA_BOT_TOKEN")
    if token:
        e = {k: v for k, v in env.items() if k not in PER_CLIENT}
        e.update(BOT_TOKEN=token, CLIENT=env.get("SHABBODA_CLIENT", "shabboda"),
                 DB_PATH=env.get("SHABBODA_DB_PATH") or os.path.join(env.get("RAILWAY_VOLUME_MOUNT_PATH") or "/data", "shabboda.db"),
                 ADMIN_CHAT_ID=env.get("SHABBODA_ADMIN_CHAT_ID", "0"))
        for k in PER_CLIENT:
            if env.get("SHABBODA_" + k):
                e[k] = env["SHABBODA_" + k]
        out.append(("shabboda", e))
    if env.get("AICALL_TG_SESSION") or env.get("AICALL_PHONE"):
        # Голосовой ИИ-аккаунт: ему не передаём ничего от ресторанных ботов, берём только AICALL_*.
        e = {k: v for k, v in env.items() if k not in PER_CLIENT and not k.startswith(("SHABBODA_", "BOT_", "DB_", "ADMIN_"))}
        e.update({k[len("AICALL_"):]: v for k, v in env.items() if k.startswith("AICALL_")})
        out.append(("aicall", e))
    if env.get("CONVERTER_BOT_TOKEN"):
        # Бот-конвертер документов (converter/): токен, админы и база свои, остальное из окружения не передаём.
        e = {k: v for k, v in env.items() if k not in PER_CLIENT and not k.startswith(("SHABBODA_", "BOT_", "DB_", "ADMIN_", "AICALL_", "CONVERTER_"))}
        e.update(BOT_TOKEN=env["CONVERTER_BOT_TOKEN"], ADMIN_IDS=env.get("CONVERTER_ADMIN_IDS", "8219891075"),
                 DB_PATH=env.get("CONVERTER_DB_PATH") or os.path.join(env.get("RAILWAY_VOLUME_MOUNT_PATH") or "/data", "converter.db"))
        for k in ("CONVERTER_MAX_MB", "CONVERTER_DAILY_LIMIT"):
            if env.get(k):
                e[k] = env[k]
        out.append(("converter", e))
    if env.get("AGZS_BOT_TOKEN"):
        # Бот-бухгалтер АГЗС (agzs/): токен, владельцы и база свои, остальное из окружения не передаём.
        e = {k: v for k, v in env.items() if k not in PER_CLIENT and not k.startswith(("SHABBODA_", "BOT_", "DB_", "ADMIN_", "AICALL_", "CONVERTER_", "AGZS_"))}
        e.update(BOT_TOKEN=env["AGZS_BOT_TOKEN"], ADMIN_IDS=env.get("AGZS_ADMIN_IDS", "8219891075"),
                 DB_PATH=env.get("AGZS_DB_PATH") or os.path.join(env.get("RAILWAY_VOLUME_MOUNT_PATH") or "/data", "agzs.db"),
                 OWNER_USERNAMES=env.get("AGZS_OWNER_USERNAMES", ""))
        for k in ("AGZS_TG_API_ID", "AGZS_TG_API_HASH", "AGZS_TG_SESSION"):  # свой Telegram-аккаунт владельца для поиска номеров
            if env.get(k):
                e[k] = env[k]
        out.append(("agzs", e))
    only = {x.strip() for x in env.get("RUN_ONLY", "").split(",") if x.strip()}
    if only:  # например RUN_ONLY=converter: остальные боты не запускаются (данные и код остаются)
        out = [(n, e) for n, e in out if n in only]
    return out


def main():
    procs, stop = {}, False

    def bye(*_):
        nonlocal stop
        stop = True
        for p in procs.values():
            p.terminate()

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    jobs = specs()
    last = {}
    while not stop:
        for name, env in jobs:
            p = procs.get(name)
            if p is None or p.poll() is not None:
                if p is not None:
                    print(f"[run_clients] {name} exited with {p.returncode}, restarting", flush=True)
                    if time.time() - last.get(name, 0) < 10:
                        time.sleep(5)
                last[name] = time.time()
                script = {"aicall": os.path.join("aicall", "app.py"), "converter": os.path.join("converter", "bot.py"), "agzs": os.path.join("agzs", "bot.py")}.get(name, "bot.py")
                procs[name] = subprocess.Popen([sys.executable, script], cwd=HERE, env=env)
        time.sleep(3)
    for p in procs.values():
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()


if __name__ == "__main__":
    main()
