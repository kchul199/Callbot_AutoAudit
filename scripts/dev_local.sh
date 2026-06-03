#!/usr/bin/env bash
# ============================================================
# scripts/dev_local.sh
# 로컬 개발 환경 — API 키·무거운 의존성 없이 전체 파이프라인 실행.
#
# AUTOAUDIT_MOCK=1 → MockProvider + In-Memory 벡터스토어 사용
# (openai/chromadb/sentence-transformers/rank-bm25 불필요)
#
# 사용:
#   ./scripts/dev_local.sh              # 기본 파이프라인
#   ./scripts/dev_local.sh --enable nugget,domain,routing
#   ./scripts/dev_local.sh --until cp3
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

# venv 자동 탐색
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

export AUTOAUDIT_MOCK=1

echo "🧪 로컬 mock 모드로 파이프라인 실행 (비용 \$0, API 키 불필요)"
echo "   provider=mock · vector=in-memory"
echo ""

"$PY" run_pipeline.py --reindex --data data/raw/ "$@"

echo ""
echo "✅ 완료. 결과: data/results/  ·  리포트: data/results/reports/*.html"
