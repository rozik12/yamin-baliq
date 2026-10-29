import asyncio
import logging
import os

from pytgcalls import PyTgCalls, filters, idle
from pytgcalls.types import (
    AudioQuality, ChatUpdate, Device, Direction, ExternalMedia,
    Frame, MediaStream, RecordStream, StreamFrames,
)
from aiohttp import web
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from brain import Brain
from vad import Utterance
from voice import speak

logging.basicConfig(level=logging.INFO)
# py-tgcalls remembers the loop it was created in, so create the loop first and run main() on it.
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
log = logging.getLogger("aicall")

RATE = 24000  # AudioQuality.LOW = 24 kHz mono
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()}

SESSION_FILE = os.getenv("SESSION_FILE") or os.path.join(os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "/data", "aicall.session")


def saved_session() -> str:
    if os.getenv("TG_SESSION"):
        return os.environ["TG_SESSION"]
    try:
        with open(SESSION_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


tg = TelegramClient(StringSession(saved_session()), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
calls = PyTgCalls(tg)
sessions: dict[int, dict] = {}


async def say(chat_id: int, text: str):
    s = sessions[chat_id]
    s["speaking"] = True
    try:
        async for pcm in speak(text):
            for i in range(0, len(pcm), 960):  # 20 ms
                if chat_id not in sessions:
                    return
                await calls.send_frame(chat_id, Device.MICROPHONE, pcm[i : i + 960])
                await asyncio.sleep(0.019)
    finally:
        s["speaking"] = False
        s["utt"].reset()


async def answer(chat_id: int, pcm: bytes):
    s = sessions.get(chat_id)
    if not s:
        return
    try:
        text = await s["brain"].reply(pcm)
        log.info("reply: %s", text)
        if text:
            await say(chat_id, text)
    except Exception:
        log.exception("answer failed")


async def start_session(chat_id: int):
    sessions[chat_id] = {"brain": Brain(RATE), "utt": Utterance(RATE), "speaking": False}
    await calls.play(chat_id, MediaStream(ExternalMedia.AUDIO, audio_parameters=AudioQuality.LOW))
    await calls.record(chat_id, RecordStream(True, AudioQuality.LOW))
    log.info("call started with %s", chat_id)


@calls.on_update(filters.chat_update(ChatUpdate.Status.INCOMING_CALL))
async def incoming(_, update):
    if ALLOWED and update.chat_id not in ALLOWED:
        log.warning("rejected call from %s", update.chat_id)
        return
    await start_session(update.chat_id)


@calls.on_update(filters.chat_update(ChatUpdate.Status.DISCARDED_CALL))
async def ended(_, update):
    sessions.pop(update.chat_id, None)
    log.info("call ended %s", update.chat_id)


@calls.on_update(filters.stream_frame(Direction.INCOMING, Device.SPEAKER))
async def frames(_, update: StreamFrames):
    s = sessions.get(update.chat_id)
    if not s or s["speaking"]:
        return
    for f in update.frames:
        done = s["utt"].feed(f.frame)
        if done:
            asyncio.create_task(answer(update.chat_id, done))


async def login():
    """First sign-in without a console: Telegram sends a code to PHONE, and the code
    (plus the 2FA password, if any) is delivered over HTTPS to /login/<LOGIN_SECRET>?code=...&password=..."""
    phone, secret = os.environ["PHONE"], os.environ["LOGIN_SECRET"]
    loop = asyncio.get_running_loop()
    code_f, pw_f = loop.create_future(), loop.create_future()

    async def handle(request):
        if request.match_info["secret"] != secret:
            return web.Response(status=404)
        q = request.query
        if "code" in q and not code_f.done():
            code_f.set_result(q["code"].strip())
        if "password" in q and not pw_f.done():
            pw_f.set_result(q["password"])
        return web.Response(text="ok")

    app = web.Application()
    app.add_routes([web.get("/login/{secret}", handle)])
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080"))).start()
    try:
        sent = await tg.send_code_request(phone)
        log.info("login code sent to %s; waiting for it on /login/<secret>", phone)
        code = await asyncio.wait_for(code_f, 600)
        try:
            await tg.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            log.info("2FA password required; waiting for it")
            await tg.sign_in(password=await asyncio.wait_for(pw_f, 600))
    finally:
        await runner.cleanup()
    try:
        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
        with open(SESSION_FILE, "w") as f:
            f.write(tg.session.save())
        os.chmod(SESSION_FILE, 0o600)
        log.info("login ok, session saved")
    except OSError:
        log.warning("login ok, but the session could not be saved: re-login needed after restart")


async def main():
    await tg.connect()
    if not await tg.is_user_authorized():
        await login()
    await calls.start()
    me = await tg.get_me()
    log.info("logged in as %s (id %s); allowed: %s", me.first_name, me.id, ALLOWED or "anyone")
    target = os.getenv("CALL_USER_ID") or os.getenv("CALL_USERNAME")  # optional: call this user on startup
    marker = SESSION_FILE + ".called." + str(target).lstrip("@").lower()
    if target and not os.path.exists(marker):  # call once, not on every restart
        peer = int(target) if target.lstrip("-").isdigit() else (await tg.get_entity(target.lstrip("@"))).id
        try:
            open(marker, "w").close()
        except OSError:
            pass
        try:
            await start_session(peer)
            if os.getenv("GREETING"):
                await say(peer, os.environ["GREETING"])
        except Exception as e:  # declined, unanswered, busy
            sessions.pop(peer, None)
            log.warning("outgoing call to %s did not connect: %s", target, e)
    await idle()


if __name__ == "__main__":
    loop.run_until_complete(main())
