import asyncio
import logging
import os

from pytgcalls import PyTgCalls, filters, idle
from pytgcalls.types.raw import AudioParameters
from pytgcalls.types import (
    ChatUpdate, Device, Direction, ExternalMedia,
    Frame, MediaStream, RecordStream, StreamFrames,
)
from aiohttp import web
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

import array
import collections
import time

from brain import Brain, SYSTEM_TEXT
from vad import Utterance, rms
from voice import speak

logging.basicConfig(level=logging.INFO)
# py-tgcalls remembers the loop it was created in, so create the loop first and run main() on it.
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
log = logging.getLogger("aicall")

RATE = 48000  # Telegram calls run at 48 kHz; mono
TTS_RATE = 24000  # both TTS engines return 24 kHz
PARAMS = AudioParameters(RATE, 1)
TEXT_CHAT = os.getenv("TEXT_CHAT", "1") != "0"
BARGE_RMS = int(os.getenv("BARGE_RMS", "500"))
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


def upsample2(pcm: bytes) -> bytes:
    """24 kHz -> 48 kHz mono 16-bit, linear interpolation."""
    a = array.array("h", pcm[: len(pcm) // 2 * 2])
    out = array.array("h", bytes(len(a) * 4))
    for i, v in enumerate(a):
        nxt = a[i + 1] if i + 1 < len(a) else v
        out[2 * i] = v
        out[2 * i + 1] = (v + nxt) // 2
    return out.tobytes()


async def synth(text: str) -> bytes:
    """Text -> 48 kHz mono PCM, ready to send."""
    pcm = b"".join([c async for c in speak(text)])
    return await asyncio.to_thread(upsample2, pcm)


async def play(chat_id: int, pcm: bytes) -> bool:
    """Sends audio in paced 10 ms frames. False if the caller interrupted or hung up."""
    s = sessions.get(chat_id)
    step = RATE // 100 * 2  # 10 ms of 16-bit mono
    t = time.monotonic()
    for i in range(0, len(pcm), step):
        if not s or s["interrupt"] or chat_id not in sessions:
            return False
        await calls.send_frame(chat_id, Device.MICROPHONE, pcm[i : i + step])
        t += 0.01
        await asyncio.sleep(max(0.0, t - time.monotonic()))
    return True


async def turn(chat_id: int, sentences):
    """One spoken turn: synthesizes sentence k+1 while sentence k is playing; the caller can interrupt."""
    s = sessions.get(chat_id)
    if not s:
        return
    s["speaking"], s["interrupt"], s["loud"] = True, False, 0
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)

    async def produce():
        try:
            async for sentence in sentences:
                t0 = time.monotonic()
                try:
                    audio = await synth(sentence)
                except Exception:
                    log.exception("synthesis failed, skipping this sentence")
                    continue
                log.info("says (%.1fs audio, synthesized in %.1fs): %s", len(audio) / 2 / RATE, time.monotonic() - t0, sentence[:80])
                await queue.put(audio)
        except Exception:
            log.exception("synthesis failed")
        finally:
            await queue.put(None)

    producer = asyncio.create_task(produce())
    try:
        while (pcm := await queue.get()) is not None:
            if not await play(chat_id, pcm):
                break
    finally:
        producer.cancel()
        s["speaking"] = False
        if s["interrupt"]:
            log.info("interrupted by the caller")
        else:
            s["utt"].reset()


async def one(text: str):
    yield text


async def say(chat_id: int, text: str):
    await turn(chat_id, one(text))


async def answer(chat_id: int, pcm):
    s = sessions.get(chat_id)
    if not s:
        return
    try:
        await turn(chat_id, s["brain"].reply_stream(pcm))
    except Exception:
        log.exception("answer failed")


async def start_session(chat_id: int):
    sessions[chat_id] = {"brain": Brain(RATE), "utt": Utterance(RATE), "speaking": False, "interrupt": False, "loud": 0, "ring": collections.deque(maxlen=25)}
    await calls.play(chat_id, MediaStream(ExternalMedia.AUDIO, audio_parameters=PARAMS))
    await calls.record(chat_id, RecordStream(True, PARAMS))
    log.info("call started with %s", chat_id)


chats: dict[int, Brain] = {}
hits: dict[int, collections.deque] = {}


def under_limit(uid: int, per_hour: int = 30) -> bool:
    q = hits.setdefault(uid, collections.deque())
    now = time.monotonic()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= per_hour:
        return False
    q.append(now)
    return True


@tg.on(events.NewMessage(incoming=True))
async def on_text(event):
    """Typing: during a call the text is answered aloud; otherwise it is a text chat."""
    try:
        text = (event.raw_text or "").strip()
        if not text or not event.is_private:
            return
        uid = event.sender_id
        if ALLOWED and uid not in ALLOWED:
            return
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return
        if uid in sessions:
            log.info("typed text during a call: %s", text[:60])
            asyncio.create_task(answer(uid, text))
            return
        if not TEXT_CHAT or not under_limit(uid):
            return
        reply = await chats.setdefault(uid, Brain(RATE, SYSTEM_TEXT)).reply_text(text)
        if reply:
            await event.respond(reply)
    except Exception:
        log.exception("text handler failed")


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


@calls.on_update(filters.stream_frame())
async def frames(_, update: StreamFrames):
    s = sessions.get(update.chat_id)
    if not s:
        return
    s["seen"] = s.get("seen", 0) + 1
    if s["seen"] <= 5:
        f0 = update.frames[0].frame if update.frames else b""
        log.info("frames update: dir=%s dev=%s n=%d first=%d bytes rms=%.0f speaking=%s",
                 update.direction, update.device, len(update.frames), len(f0), rms(f0), s["speaking"])
    if not (Direction.INCOMING & update.direction):
        return
    for f in update.frames:
        if s["speaking"]:
            # barge-in: 150 ms of loud speech while the assistant talks stops it
            level = rms(f.frame)
            s["ring"].append(f.frame)
            s["maxr"] = max(s.get("maxr", 0), level)
            s["cnt"] = s.get("cnt", 0) + 1
            if s["cnt"] % 200 == 0:
                log.info("while speaking: caller level max %.0f (barge-in at %d)", s["maxr"], BARGE_RMS)
                s["maxr"] = 0
            s["loud"] = s["loud"] + 1 if level >= BARGE_RMS else 0
            if s["loud"] >= 15 and not s["interrupt"]:
                s["interrupt"] = True
                for old in s["ring"]:
                    s["utt"].feed(old)
                s["ring"].clear()
            continue
        s["n"] = s.get("n", 0) + 1
        if s["n"] in (1, 200, 1000) or s["n"] % 1500 == 0:
            log.info("incoming frame #%d: %d bytes, rms %.0f", s["n"], len(f.frame), rms(f.frame))
        done = s["utt"].feed(f.frame)
        if done:
            log.info("heard an utterance: %.1f s", len(done) / 2 / RATE)
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
    target = os.getenv("CALL_USERNAME") or os.getenv("CALL_USER_ID")  # optional: call this user on startup
    marker = SESSION_FILE + ".called." + str(target).lstrip("@").lower() + os.getenv("CALL_TAG", "")  # new CALL_TAG = call again
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
