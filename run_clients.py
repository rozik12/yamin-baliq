"""Запускает бота ресторана и, если задан SHABBODA_BOT_TOKEN, бота второго клиента в одном сервисе.
Если задан AICALL_TG_SESSION или AICALL_PHONE (первый вход), ещё и голосового ИИ для звонков в Telegram (aicall/, переменные AICALL_*).

У второго бота свои база, токен, админы и карта: берутся из переменных SHABBODA_*;
всё остальное из окружения первого (карта, курьеры, Яндекс, онлайн-оплата) ему не передаётся.
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
                script = os.path.join("aicall", "app.py") if name == "aicall" else "bot.py"
                procs[name] = subprocess.Popen([sys.executable, script], cwd=HERE, env=env)
        time.sleep(3)
    for p in procs.values():
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()


if __name__ == "__main__":
    main()
