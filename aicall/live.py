"""Gemini Live: speech in, speech out, over one streaming session per call.

Telegram side: 48 kHz mono 16-bit, 10 ms frames. Gemini side: 16 kHz in, 24 kHz out.
Barge-in, turn-taking and voice activity detection are done by the model.
"""
import array
import asyncio
import logging
import os
import time

from google import genai
from google.genai import types

log = logging.getLogger("aicall.live")

MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
VOICE = os.getenv("GEMINI_VOICE", "Kore")
SYSTEM = os.getenv(
    "SYSTEM_PROMPT",
    "Ты голосовой ассистент и говоришь по телефону. Говори как живой человек: просто, тепло, "
    "разговорно, короткими фразами (1-2 предложения), иногда с короткими вставками вроде "
    "«ага», «понял», «хм». Отвечай на языке собеседника (русский или узбекский). "
    "Никогда не говори о себе в третьем лице и не озвучивай служебные пометки. Если плохо "
    "расслышал, переспроси одной фразой. Ты ИИ и не скрываешь этого, если спросят.",
)
FRAME = 960  # 10 ms of 48 kHz mono 16-bit
DEBUG_AUDIO = os.getenv("DEBUG_AUDIO") == "1"
LAST_IN = b""  # debug: what the caller's audio looked like on the last call (48 kHz mono)


def down3(pcm: bytes) -> bytes:
    """48 kHz -> 16 kHz mono 16-bit (average of each 3 samples)."""
    a = array.array("h", pcm[: len(pcm) // 6 * 6])
    return array.array("h", [(a[i] + a[i + 1] + a[i + 2]) // 3 for i in range(0, len(a), 3)]).tobytes()


def up2(pcm: bytes) -> bytes:
    """24 kHz -> 48 kHz mono 16-bit (linear interpolation)."""
    a = array.array("h", pcm[: len(pcm) // 2 * 2])
    out = array.array("h", bytes(len(a) * 4))
    for i, v in enumerate(a):
        out[2 * i] = v
        out[2 * i + 1] = (v + (a[i + 1] if i + 1 < len(a) else v)) // 2
    return out.tobytes()


def make_config(system: str) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=system,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE))
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                silence_duration_ms=int(os.getenv("LIVE_SILENCE_MS", "600")),
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
    )


class LiveCall:
    def __init__(self, send_frame, system: str | None = None):
        self.send_frame = send_frame  # async (bytes) -> None
        self.system = system or SYSTEM
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.session = None
        self._cm = None
        self.out = bytearray()  # 48 kHz audio waiting to be played
        self.inq: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.inbuf = bytearray()
        self.closed = False
        self.tasks: list[asyncio.Task] = []
        self._heard = self._said = ""
        self.dbg = bytearray()

    async def start(self, opening: str | None = None):
        await self._connect()
        self.tasks = [asyncio.create_task(self._recv()), asyncio.create_task(self._send()), asyncio.create_task(self._pace())]
        if opening:
            await self.session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=opening)]), turn_complete=True
            )

    async def _connect(self):
        last = None
        for attempt in range(3):
            try:
                self._cm = self.client.aio.live.connect(model=MODEL, config=make_config(self.system))
                self.session = await self._cm.__aenter__()
                return
            except Exception as e:
                last = e
                log.warning("live connect failed (try %d): %s", attempt + 1, str(e)[:160])
                await asyncio.sleep(1)
        raise RuntimeError(f"Gemini Live unavailable: {last}")

    def feed(self, pcm48: bytes):
        """Called for every incoming 10 ms frame from the caller."""
        if self.closed:
            return
        if DEBUG_AUDIO and len(self.dbg) < 48000 * 2 * 90:
            self.dbg += pcm48
        self.inbuf += down3(pcm48)
        if len(self.inbuf) >= 1600:  # 50 ms
            chunk, self.inbuf = bytes(self.inbuf), bytearray()
            try:
                self.inq.put_nowait(chunk)
            except asyncio.QueueFull:
                pass  # drop rather than lag behind real time

    async def send_text(self, text: str):
        if self.session and not self.closed:
            await self.session.send_realtime_input(text=text)

    async def _send(self):
        while not self.closed:
            chunk = await self.inq.get()
            try:
                await self.session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
            except Exception as e:
                if not self.closed:
                    log.warning("send to Gemini failed: %s", str(e)[:120])
                    await self._reconnect()

    async def _recv(self):
        while not self.closed:
            try:
                async for m in self.session.receive():
                    if m.data:
                        self.out += up2(m.data)
                    sc = m.server_content
                    if not sc:
                        continue
                    if sc.interrupted:
                        self.out.clear()  # the caller started talking: stop at once
                    if sc.input_transcription and sc.input_transcription.text:
                        self._heard += sc.input_transcription.text
                    if sc.output_transcription and sc.output_transcription.text:
                        if self._heard.strip():
                            log.info("caller: %s", self._heard.strip()[:200])
                            self._heard = ""
                        self._said += sc.output_transcription.text
                    if (sc.turn_complete or sc.interrupted) and self._said.strip():
                        log.info("ai%s: %s", " (interrupted)" if sc.interrupted else "", self._said.strip()[:200])
                        self._said = ""
                    if sc.turn_complete and len(self.out) % FRAME:
                        self.out += bytes(FRAME - len(self.out) % FRAME)
            except Exception as e:
                if self.closed:
                    return
                log.warning("receive from Gemini failed: %s", str(e)[:120])
                await self._reconnect()

    async def _reconnect(self):
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception:
            pass
        if not self.closed:
            await self._connect()

    async def _pace(self):
        """Plays queued audio at exactly real time, 10 ms at a time."""
        t = time.monotonic()
        while not self.closed:
            if len(self.out) >= FRAME:
                frame = bytes(self.out[:FRAME])
                del self.out[:FRAME]
                try:
                    await self.send_frame(frame)
                except Exception:
                    log.exception("send_frame failed")
                t += 0.01
                delay = t - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -0.2:
                    t = time.monotonic()  # fell behind: do not burst
            else:
                await asyncio.sleep(0.005)
                t = time.monotonic()

    async def close(self):
        global LAST_IN
        if DEBUG_AUDIO:
            LAST_IN = bytes(self.dbg)
        self.closed = True
        for task in self.tasks:
            task.cancel()
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception:
            pass
