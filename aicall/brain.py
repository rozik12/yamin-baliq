"""Gemini: audio in, short text reply out."""
import io
import os
import wave

from google import genai
from google.genai import types

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SYSTEM = os.getenv(
    "SYSTEM_PROMPT",
    "Ты голосовой ассистент в телефонном звонке. Отвечай на языке собеседника, "
    "коротко (1-2 предложения), без markdown, списков и эмодзи.",
)
MAX_TURNS = 12


def to_wav(pcm: bytes, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class Brain:
    def __init__(self, rate=24000):
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.rate = rate
        self.history: list[types.Content] = []

    async def reply(self, pcm: bytes) -> str:
        audio = types.Part.from_bytes(data=to_wav(pcm, self.rate), mime_type="audio/wav")
        self.history.append(types.Content(role="user", parts=[audio]))
        resp = await self.client.aio.models.generate_content(
            model=MODEL,
            contents=self.history,
            config=types.GenerateContentConfig(system_instruction=SYSTEM),
        )
        text = (resp.text or "").strip()
        self.history.append(types.Content(role="model", parts=[types.Part(text=text)]))
        del self.history[: -MAX_TURNS * 2]
        return text
