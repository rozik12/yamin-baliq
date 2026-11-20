"""Подключение вашего Telegram-аккаунта к боту АГЗС (один раз). Запустите, введите данные, получите строку сессии.
Строка сессии = доступ к аккаунту: никому не показывайте, вставьте только в Railway (переменная AGZS_TG_SESSION)."""
import asyncio
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession


def ask(text):
    return input(text).strip()


async def main():
    print("1) Откройте https://my.telegram.org → войдите по номеру → API development tools → создайте приложение.")
    print("   Там будут api_id (число) и api_hash (длинная строка).\n")
    api_id = int(ask("api_id: "))
    api_hash = ask("api_hash: ")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start(phone=lambda: ask("Ваш номер телефона (+998...): "), code_callback=lambda: ask("Код из Telegram: "),
                       password=lambda: ask("Пароль двухэтапной защиты (если есть, иначе Enter): "))
    me = await client.get_me()
    session = client.session.save()
    print(f"\nГотово, вошли как {me.first_name} (id {me.id}).\n")
    print("AGZS_TG_API_ID =", api_id)
    print("AGZS_TG_API_HASH =", api_hash)
    print("AGZS_TG_SESSION =", session)
    try:
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(session)
        r.update()
        print("\nСтрока сессии скопирована в буфер обмена.")
    except Exception:
        pass
    await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        print("Ошибка:", e)
    input("\nНажмите Enter, чтобы закрыть окно…")
    sys.exit(0)
