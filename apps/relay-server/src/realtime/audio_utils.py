"""G.711 mu-law 오디오 유틸리티.

pipeline과 audio_router가 공유하는 mu-law 디코딩/에너지 계산 함수.
"""

import numpy as np

# G.711 mu-law -> linear PCM 디코딩 테이블 (256 entries)
_ULAW_TO_LINEAR: list[int] = []
for _byte in range(256):
    _b = ~_byte & 0xFF
    _sign = (_b >> 7) & 1
    _exp = (_b >> 4) & 0x07
    _man = _b & 0x0F
    _mag = ((2 * _man + 33) << _exp) - 33
    _ULAW_TO_LINEAR.append(-_mag if _sign else _mag)

# Silero VAD용 float32 변환 테이블 (정규화: -1.0 ~ 1.0)
_max_abs = max(abs(v) for v in _ULAW_TO_LINEAR)
_ULAW_TO_FLOAT32 = np.array(_ULAW_TO_LINEAR, dtype=np.float32) / _max_abs

# ulaw_rms용 원본 선형 스케일 테이블 (float64: 제곱 합산 시 오버플로·정밀도 안전)
_ULAW_TO_LINEAR_NP = np.array(_ULAW_TO_LINEAR, dtype=np.float64)


def pcm16_rms(audio: bytes) -> float:
    """PCM16 (16-bit signed LE) 오디오의 RMS 에너지를 계산한다.

    Returns:
        RMS 값 (0=무음, ~500=조용한 발화, ~2000=보통 발화, ~8000=매우 큰 소리)
    """
    if len(audio) < 2:
        return 0.0
    n = len(audio) // 2
    samples = np.frombuffer(audio[: n * 2], dtype="<i2").astype(np.float64)
    return float(np.sqrt(np.mean(samples * samples)))


def ulaw_rms(audio: bytes) -> float:
    """g711 mu-law 오디오의 RMS 에너지를 계산한다.

    Returns:
        RMS 값 (0=무음, ~500=조용한 발화, ~2000=보통 발화, ~8000=매우 큰 소리)
    """
    if not audio:
        return 0.0
    linear = _ULAW_TO_LINEAR_NP[np.frombuffer(audio, dtype=np.uint8)]
    return float(np.sqrt(np.mean(linear * linear)))


def ulaw_to_float32(audio: bytes) -> np.ndarray:
    """g711 mu-law 오디오를 float32 배열로 변환한다 (Silero VAD 입력용).

    Returns:
        numpy float32 배열 (정규화: -1.0 ~ 1.0)
    """
    indices = np.frombuffer(audio, dtype=np.uint8)
    return _ULAW_TO_FLOAT32[indices]
