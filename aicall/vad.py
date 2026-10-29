"""Energy-based end-of-utterance detector for 16-bit mono PCM."""
import array
import math


def rms(pcm: bytes) -> float:
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class Utterance:
    def __init__(self, rate=24000, threshold=300, silence_s=0.7, min_s=0.4):
        self.rate, self.threshold = rate, threshold
        self.silence_s, self.min_s = silence_s, min_s
        self.buf = bytearray()
        self.speech_s = 0.0
        self.quiet_s = 0.0

    def feed(self, pcm: bytes) -> bytes | None:
        """Returns a finished utterance (PCM) once the speaker pauses, else None."""
        dur = len(pcm) / 2 / self.rate
        if rms(pcm) >= self.threshold:
            self.buf += pcm
            self.speech_s += dur
            self.quiet_s = 0.0
            return None
        if not self.buf:
            return None
        self.buf += pcm
        self.quiet_s += dur
        if self.quiet_s < self.silence_s:
            return None
        out = bytes(self.buf) if self.speech_s >= self.min_s else None
        self.reset()
        return out

    def reset(self):
        self.buf.clear()
        self.speech_s = self.quiet_s = 0.0
