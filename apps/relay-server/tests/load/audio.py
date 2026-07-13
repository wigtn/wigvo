"""합성 g711 µ-law 8kHz 오디오 생성 — 부하테스트용.

핵심: 서버의 CPU 병목은 수신자 오디오(Session B)에 대한 Local VAD(Silero) 추론이다.
RMS가 임계값 이하이면 서버가 Silero를 스킵하므로(local_vad.py), 실제 추론 부하를
재현하려면 발화 구간의 RMS가 게이트(local_vad_rms_threshold≈200, min_peak≈300)를
넘겨야 한다. 따라서 '발화(speech)' 구간은 충분한 진폭의 노이즈로, '무음(silence)'
구간은 0으로 생성해 VAD 상태 전환과 최악 추론 부하를 함께 만든다.
"""

import numpy as np

SAMPLE_RATE = 8000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 160 samples / 20ms
_BIAS = 0x84
_CLIP = 32635


def _linear16_to_ulaw(pcm: np.ndarray) -> bytes:
    """PCM int16 → G.711 µ-law (numpy 벡터화). audioop 비의존(3.13 대비)."""
    pcm = pcm.astype(np.int32)
    sign = np.where(pcm < 0, 0x80, 0x00).astype(np.int32)
    mag = np.minimum(np.abs(pcm), _CLIP) + _BIAS
    # exponent = floor(log2(mag >> 7)), [0,7]로 클램프 (mag>=BIAS라 항상 >=1)
    seg = np.clip((mag >> 7), 1, None)
    exponent = np.clip(np.floor(np.log2(seg)).astype(np.int32), 0, 7)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    ulaw = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return ulaw.astype(np.uint8).tobytes()


def make_frame_stream(
    duration_s: float,
    speech_ratio: float = 0.7,
    speech_amplitude: int = 8000,
    seed: int = 0,
) -> list[bytes]:
    """duration_s 동안의 20ms µ-law 프레임 리스트를 생성한다.

    speech(노이즈)/silence 구간을 ~1s 단위로 번갈아 배치해 VAD가 실제로
    speech_started/stopped 전환을 겪게 한다. speech_ratio로 발화 비중 조절
    (1.0 = 매 프레임 Silero 추론 = 최악 CPU 부하).
    """
    rng = np.random.default_rng(seed)
    n_frames = int(duration_s * 1000 / FRAME_MS)
    frames: list[bytes] = []
    # ~1초(50프레임) 단위로 speech/silence 블록을 정한다.
    block = 50
    for i in range(n_frames):
        is_speech = ((i // block) % 10) < int(speech_ratio * 10)
        if is_speech:
            pcm = rng.integers(
                -speech_amplitude, speech_amplitude, size=FRAME_SAMPLES, dtype=np.int16
            )
        else:
            pcm = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        frames.append(_linear16_to_ulaw(pcm))
    return frames
