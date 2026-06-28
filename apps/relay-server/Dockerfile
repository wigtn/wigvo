FROM python:3.12-slim

# silero-vad-lite (ONNX Runtime) 런타임 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    cmake \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 의존성 먼저 복사 (캐싱)
COPY pyproject.toml uv.lock ./

# 의존성 설치 (venv 없이 시스템에 직접)
RUN uv sync --frozen --no-dev --no-editable

# silero-vad-lite .so의 executable stack 플래그 제거 (gVisor 호환)
# ELF PT_GNU_STACK 헤더에서 PF_X(실행) 비트만 제거 — 추가 패키지 불필요
RUN python3 -c "\
import struct, pathlib; \
so = pathlib.Path('/app/.venv/lib/python3.12/site-packages/silero_vad_lite/data/silero_vad_lite.so'); \
data = bytearray(so.read_bytes()); \
assert data[:4] == b'\x7fELF'; \
phoff = struct.unpack_from('<Q', data, 32)[0]; \
phsz = struct.unpack_from('<H', data, 54)[0]; \
phnum = struct.unpack_from('<H', data, 56)[0]; \
[struct.pack_into('<I', data, phoff+i*phsz+4, struct.unpack_from('<I', data, phoff+i*phsz+4)[0] & ~1) for i in range(phnum) if struct.unpack_from('<I', data, phoff+i*phsz)[0] == 0x6474e551]; \
so.write_bytes(data); \
print('Cleared execstack flag from silero_vad_lite.so')"

# 소스 복사
COPY src/ src/
COPY static/ static/

# Python stdout/stderr 버퍼링 비활성화 (Cloud Run 로그 실시간 출력)
ENV PYTHONUNBUFFERED=1

# Cloud Run은 PORT 환경변수를 주입함
ENV PORT=8080

EXPOSE 8080

# uvicorn 실행 — venv 직접 호출 (uv run은 런타임에 dev deps를 재설치하므로 사용 금지)
CMD .venv/bin/uvicorn src.main:app \
  --host 0.0.0.0 \
  --port ${PORT} \
  --workers 1 \
  --timeout-keep-alive 300
