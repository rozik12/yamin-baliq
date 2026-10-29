"""ElevenLabs text-to-speech, streamed as 24 kHz mono 16-bit PCM."""
import os

import httpx
from google import genai
from google.genai import types

VOICE_ID = os.environ["ELEVENLABS_VOICE_ID"]
MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"


async def _gemini(text: str):
    """Fallback voice (same 24 kHz mono PCM) when ElevenLabs refuses."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=os.getenv("GEMINI_VOICE", "Kore")))),
    )
    pcm = b""
    for _ in range(3):  # the TTS model sometimes returns no audio
        resp = await client.aio.models.generate_content(
            model=os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts"),
            contents="Say naturally, in a warm conversational tone: " + text,
            config=config,
        )
        parts = resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts
        if parts and parts[0].inline_data and parts[0].inline_data.data:
            pcm = parts[0].inline_data.data
            break
    if not pcm:
        raise RuntimeError("Gemini TTS returned no audio")
    for i in range(0, len(pcm), 9600):
        yield pcm[i : i + 9600]


async def speak(text: str):
    """Yields PCM chunks with an even byte length. ElevenLabs first, Gemini if it fails before any audio."""
    started = False
    try:
        async for chunk in _eleven(text):
            started = True
            yield chunk
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
