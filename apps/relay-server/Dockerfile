FROM python:3.12-slim

# onnxruntime 런타임 의존성 (libgomp1).
# v6 채택으로 silero-vad-lite(C 빌드) 제거 → cmake/build-essential 불필요(onnxruntime은 prebuilt wheel).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*
# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 의존성 먼저 복사 (캐싱)
COPY pyproject.toml uv.lock ./

# 의존성 설치 (venv 없이 시스템에 직접)
RUN uv sync --frozen --no-dev --no-editable

# 네이티브 .so의 executable-stack 플래그 제거 (gVisor/Cloud Run 호환).
# onnxruntime 등 모든 확장 모듈에 일괄 적용 — 특정 패키지에 의존하지 않음(의존성 교체에도 안전).
COPY scripts/clear_execstack.py scripts/clear_execstack.py
RUN python3 scripts/clear_execstack.py /app/.venv

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
