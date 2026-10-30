"""ElevenLabs text-to-speech, streamed as 24 kHz mono 16-bit PCM."""
import logging
import os

import httpx
from google import genai
from google.genai import types

VOICE_ID = os.environ["ELEVENLABS_VOICE_ID"]
MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"


log = logging.getLogger("aicall.voice")
TTS_MODELS = [os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts"), "gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts"]
_eleven_ok = True  # flips to False after a 401/403 (e.g. key without text_to_speech permission)


async def _gemini(text: str):
    """Fallback voice (same 24 kHz mono PCM): tries several Gemini TTS models, twice each."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=os.getenv("GEMINI_VOICE", "Kore")))),
    )
    pcm = b""
    for model in dict.fromkeys(TTS_MODELS):
        for attempt in range(2):
            try:
                resp = await client.aio.models.generate_content(
                    model=model,
                    contents="Say naturally, in a warm conversational tone: " + text,
                    config=config,
                )
                parts = resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts
                if parts and parts[0].inline_data and parts[0].inline_data.data:
                    pcm = parts[0].inline_data.data
                    break
                log.warning("tts %s returned no audio (try %d)", model, attempt + 1)
            except Exception as e:
                log.warning("tts %s failed (try %d): %s", model, attempt + 1, str(e)[:160])
        if pcm:
            break
    if not pcm:
        raise RuntimeError("Gemini TTS returned no audio")
    for i in range(0, len(pcm), 9600):
        yield pcm[i : i + 9600]


async def speak(text: str):
    """Yields PCM chunks with an even byte length. ElevenLabs when it works, otherwise Gemini."""
    global _eleven_ok
    if _eleven_ok:
        started = False
        try:
            async for chunk in _eleven(text):
                started = True
                yield chunk
            return
        except httpx.HTTPStatusError as e:
            if started:
                raise
            if e.response.status_code in (401, 403):
                _eleven_ok = False  # do not retry a refused key on every sentence
        except httpx.HTTPError:
            if started:
                raise
    async for chunk in _gemini(text):
        yield chunk


async def _eleven(text: str):
    headers = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
    params = {"output_format": "pcm_24000"}
    body = {"text": text, "model_id": MODEL}
    async with httpx.AsyncClient(timeout=30) as http:
        async with http.stream("POST", URL, headers=headers, params=params, json=body) as r:
            r.raise_for_status()
            carry = b""
            async for chunk in r.aiter_bytes():
                data = carry + chunk
                cut = len(data) // 2 * 2
                carry = data[cut:]
                if cut:
                    yield data[:cut]
