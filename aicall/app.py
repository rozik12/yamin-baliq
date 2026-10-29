import asyncio
import logging
import os

from pytgcalls import PyTgCalls, filters, idle
from pytgcalls.types import (
    AudioQuality, ChatUpdate, Device, Direction, ExternalMedia,
    Frame, MediaStream, RecordStream, StreamFrames,
)
from telethon import TelegramClient
from telethon.sessions import StringSession

from brain import Brain
from vad import Utterance
from voice import speak

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aicall")

RATE = 24000  # AudioQuality.LOW = 24 kHz mono
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()}

tg = TelegramClient(StringSession(os.environ["TG_SESSION"]), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
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


async def main():
    await calls.start()
    me = await tg.get_me()
    log.info("logged in as %s (id %s); allowed: %s", me.first_name, me.id, ALLOWED or "anyone")
    target = os.getenv("CALL_USER_ID") or os.getenv("CALL_USERNAME")  # optional: call this user on startup
    if target:
        peer = int(target) if target.lstrip("-").isdigit() else (await tg.get_entity(target.lstrip("@"))).id
        await start_session(peer)
    await idle()


if __name__ == "__main__":
    asyncio.run(main())
