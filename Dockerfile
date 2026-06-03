# ============================================================
# CallBot AutoAudit — 백엔드 이미지
# ============================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 시스템 의존성 (sentence-transformers/torch 빌드용 최소)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 설치 (레이어 캐시 최적화)
COPY requirements.txt .
RUN pip install -r requirements.txt

# 앱 소스
COPY AutoAudit/ ./AutoAudit/
COPY config/ ./config/
COPY run_pipeline.py .

# 결과/데이터 볼륨 마운트 지점
VOLUME ["/app/data"]

EXPOSE 8000

# 기본: API 서버 기동
CMD ["uvicorn", "AutoAudit.app.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
