#!/usr/bin/env python3
"""Generate the inbound AI-interpretation notice asset (8 kHz µ-law, base64).

Twilio Media Streams expect 8 kHz mono G.711 µ-law in 20 ms (160-byte) frames.
OpenAI TTS emits 24 kHz signed-16 PCM, so we decimate 24k->8k and µ-law encode
in numpy (Python 3.13 removed the stdlib `audioop`).

Run whenever the notice wording (legal-approved) changes:

    OPENAI_API_KEY=... uv run python -m scripts.gen_inbound_notice

Output: static/audio/inbound-notice-en.ulaw.b64 (committed asset).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import numpy as np
from openai import OpenAI

# Draft wording — LEGAL APPROVAL PENDING (see poc-refactor/inbound-ai-notice-design.md §8).
NOTICE_TEXT = (
    "This call will be assisted by AI-powered interpretation. "
    "Your voice may be processed and transferred abroad for translation. "
    "Please stay on the line to be connected to an agent, or hang up now to decline."
)

TTS_MODEL = "gpt-4o-mini-tts"
TTS_VOICE = "alloy"
SRC_RATE = 24_000  # OpenAI pcm output rate
DST_RATE = 8_000   # Twilio telephony rate
FRAME_BYTES = 160  # 20 ms @ 8 kHz µ-law
OUT_PATH = Path(__file__).resolve().parents[1] / "static/audio/inbound-notice-en.ulaw.b64"


def _tts_pcm24() -> np.ndarray:
    """Return the notice as 24 kHz signed-16 mono PCM samples."""
    client = OpenAI()
    resp = client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=NOTICE_TEXT,
        response_format="pcm",
    )
    raw = resp.read()
    return np.frombuffer(raw, dtype="<i2").astype(np.int32)


def _decimate_24k_to_8k(pcm: np.ndarray) -> np.ndarray:
    """3:1 decimation with a 3-tap moving-average anti-alias (24k -> 8k)."""
    pad = (-len(pcm)) % 3
    if pad:
        pcm = np.concatenate([pcm, np.zeros(pad, dtype=pcm.dtype)])
    return pcm.reshape(-1, 3).mean(axis=1).astype(np.int32)


def _pcm16_to_ulaw(pcm: np.ndarray) -> bytes:
    """Vectorized G.711 µ-law encode of signed-16 PCM."""
    BIAS = 0x84
    CLIP = 32635
    sign = np.where(pcm < 0, 0x80, 0x00).astype(np.int32)
    mag = np.minimum(np.abs(pcm), CLIP).astype(np.int32) + BIAS
    exponent = np.clip(np.floor(np.log2(mag)).astype(np.int32) - 7, 0, 7)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    ulaw = (~(sign | (exponent << 4) | mantissa)) & 0xFF
    return ulaw.astype(np.uint8).tobytes()


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required")
    pcm8 = _decimate_24k_to_8k(_tts_pcm24())
    ulaw = _pcm16_to_ulaw(pcm8)
    # Pad to a whole number of 20 ms frames with µ-law silence (0xFF).
    pad = (-len(ulaw)) % FRAME_BYTES
    if pad:
        ulaw = ulaw + b"\xff" * pad
    assert len(ulaw) % FRAME_BYTES == 0
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(base64.b64encode(ulaw).decode("ascii"), encoding="ascii")
    print(
        f"wrote {OUT_PATH} bytes={len(ulaw)} frames={len(ulaw)//FRAME_BYTES} "
        f"dur={len(ulaw)/DST_RATE:.2f}s"
    )


if __name__ == "__main__":
    main()
