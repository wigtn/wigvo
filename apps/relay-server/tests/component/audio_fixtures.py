"""오디오 테스트 픽스처 생성기.

실제 통화에서 사용되는 각 포맷의 현실적 오디오 데이터를 생성한다.
numpy 없이 struct만으로 구현하여 의존성을 최소화한다.

오디오 포맷:
  - PCM16 16kHz: User → Relay (App → Session A)
  - PCM16 24kHz: Session B TTS → App 재생
  - g711_ulaw 8kHz: Twilio ↔ Session A/B
"""

import math
import struct


def gen_pcm16_sine(
    freq_hz: float = 440,
    duration_s: float = 1.0,
    sample_rate: int = 16000,
    amplitude: int = 3000,
) -> bytes:
    """User 음성 시뮬레이션 — PCM16 사인파.

    amplitude=3000 → RMS ~2121 (실제 발화 수준, echo threshold 500 훨씬 초과).
    """
    num_samples = int(sample_rate * duration_s)
    samples = []
    for i in range(num_samples):
        value = int(amplitude * math.sin(2 * math.pi * freq_hz * i / sample_rate))
        value = max(-32768, min(32767, value))
        samples.append(value)
    return struct.pack(f"<{num_samples}h", *samples)


def gen_ulaw_speech(duration_s: float = 1.0) -> bytes:
    """수신자 발화 시뮬레이션 — g711_ulaw (8kHz).

    0x10 패턴 → RMS ~8000+ (echo threshold 500 훨씬 초과).
    실제 PSTN 발화 에너지를 시뮬레이션한다.
    """
    num_bytes = int(8000 * duration_s)
    return bytes([0x10] * num_bytes)


def gen_ulaw_echo(duration_s: float = 1.0) -> bytes:
    """에코 시뮬레이션 — g711_ulaw (8kHz).

    0xF0 패턴 → 낮은 RMS (~200, echo threshold 500 미만).
    TTS가 스피커 → 마이크로 되돌아온 약한 에코를 시뮬레이션한다.
    """
    num_bytes = int(8000 * duration_s)
    return bytes([0xF0] * num_bytes)


def gen_ulaw_silence(duration_s: float = 1.0) -> bytes:
    """무음 — g711_ulaw 0xFF (디지털 무음)."""
    num_bytes = int(8000 * duration_s)
    return bytes([0xFF] * num_bytes)


def gen_ulaw_tts(duration_s: float = 0.5) -> bytes:
    """Session A TTS 출력 — g711_ulaw (Twilio 전달용).

    0x20 패턴 → 중간 에너지 (실제 TTS 출력 시뮬레이션).
    """
    num_bytes = int(8000 * duration_s)
    return bytes([0x20] * num_bytes)


def gen_pcm16_tts(duration_s: float = 0.3, sample_rate: int = 24000) -> bytes:
    """Session B TTS 출력 — PCM16 24kHz (App 재생용).

    300Hz 사인파로 번역된 음성 TTS를 시뮬레이션한다.
    """
    return gen_pcm16_sine(
        freq_hz=300,
        duration_s=duration_s,
        sample_rate=sample_rate,
        amplitude=4000,
    )


def chunk_audio(audio: bytes, chunk_size: int) -> list[bytes]:
    """오디오를 고정 크기 청크로 분할한다.

    일반적인 20ms 청크 크기:
      - g711_ulaw 8kHz: 160 bytes
      - PCM16 16kHz: 640 bytes (320 samples × 2 bytes)
      - PCM16 24kHz: 960 bytes (480 samples × 2 bytes)
    """
    return [audio[i : i + chunk_size] for i in range(0, len(audio), chunk_size)]
