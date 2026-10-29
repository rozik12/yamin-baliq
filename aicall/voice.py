"""ElevenLabs text-to-speech, streamed as 24 kHz mono 16-bit PCM."""
import os

import httpx

VOICE_ID = os.environ["ELEVENLABS_VOICE_ID"]
MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"


async def speak(text: str):
    """Yields PCM chunks with an even byte length."""
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
