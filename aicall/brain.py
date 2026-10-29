"""Gemini: audio in, a short spoken reply out, streamed sentence by sentence."""
import io
import os
import re
import wave

from google import genai
from google.genai import types

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
SYSTEM = os.getenv(
    "SYSTEM_PROMPT",
    "Ты голосовой ассистент и говоришь по телефону. Говори как живой человек: просто, тепло, "
    "разговорно, короткими фразами (1-2 предложения), иногда с короткими вставками вроде "
    "«ага», «понял», «хм». Отвечай на языке собеседника. Никакого markdown, списков, эмодзи и "
    "сложных конструкций. Если не расслышал, переспроси одной фразой.",
)
MAX_TURNS = 12
SENTENCE = re.compile(r"(.+?[.!?…]+)(?:\s|$)", re.S)


def to_wav(pcm: bytes, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class Brain:
    def __init__(self, rate=48000):
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.rate = rate
        self.history: list[types.Content] = []

    async def reply_stream(self, pcm: bytes):
        """Yields the reply one sentence at a time, as soon as each is complete."""
        audio = types.Part.from_bytes(data=to_wav(pcm, self.rate), mime_type="audio/wav")
        self.history.append(types.Content(role="user", parts=[audio]))
        said, buf = "", ""
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=MODEL,
                contents=self.history,
                config=types.GenerateContentConfig(system_instruction=SYSTEM),
            )
            async for chunk in stream:
                buf += chunk.text or ""
                while (m := SENTENCE.match(buf)):
                    sentence, buf = m.group(1).strip(), buf[m.end():]
                    if sentence:
                        said += sentence + " "
                        yield sentence
            if buf.strip():
                said += buf.strip()
                yield buf.strip()
        finally:
            self.history.append(types.Content(role="model", parts=[types.Part(text=said.strip() or "...")]))
            del self.history[: -MAX_TURNS * 2]
