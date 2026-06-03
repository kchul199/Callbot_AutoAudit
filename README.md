# 🤖 CallBot AutoAudit

Ground Truth 없이도 신뢰할 수 있는 품질 지표를 산출하는 **RAG 품질 자동 감사 파이프라인**.
콜봇 대화 로그를 입력받아 CP1~CP6 단계를 거쳐 환각(Hallucination)을 탐지하고 근거 기반(Evidence-based) 평가 리포트를 생성합니다.

## 아키텍처 (CP1 ~ CP6)

| CP | 단계 | 핵심 |
|----|------|------|
| CP1 | 전처리 | txt/json/csv 로그 → `CallLog` 표준 스키마 |
| CP2 | 지식베이스 | Parent-Child 청킹 + ChromaDB 인덱싱 |
| CP3 | 검색 | HyDE + Multi-Query + **BM25/Dense Hybrid(RRF)** + Cross-Encoder Rerank |
| CP3.5 | QA 추출 | 대화 → (질문, 봇답변) 쌍 + 컨텍스트 부착 |
| CP4 | 평가 | LLM-as-a-Judge **다중 샘플링 + 신뢰도** (Faithfulness 등 4지표) |
| CP5 | 집계 | SLA 판정 + 통계 (mean/median/p10/p90) |
| CP6 | 리포트 | HTML/JSON + **회귀 감지** + Slack 알림 |

## 🖥️ 관리 포털 (Admin Console)

멀티테넌트 SaaS 콜봇의 답변 품질을 **자동 평가 → 휴먼 재평가 → 확정**하는 웹 콘솔.
(Vite + React, `frontend/` · FastAPI 백엔드 · 기획서 `docs/ADMIN_PORTAL_PRD.md`)

| 화면 | 기능 |
|------|------|
| **Overview** | KPI·메트릭·추이·SLA미달 + 카드 클릭 **drill-in/out** |
| **Conversations** | 대화이력 목록(싱글/멀티턴) + 세션 타임라인 + 근거 하이라이트 |
| **Run Evaluation** | 6스텝 마법사 — Judge모델(Claude/GPT/Gemini, 앙상블)·레벨·메트릭·방법론 |
| **Review** | 3-pane 휴먼 재평가 워크스페이스 (자동→휴먼 점수 수정/승인) |
| **Evaluations** | 필터 탐색 + Evidence 드로어 |
| **Trends** | 일자별/배치별 추이 그래프 + 휴먼·자동 일치도(표본 drill-in) |
| **Knowledge Base** | KB 현황 + 검색 커버리지 갭 |
| **Settings** | SLA 임계값·평가 프로필·Judge 자격증명·알림 |

핵심: 멀티테넌시(tenant 격리) · 평가 3레벨(검색/턴/세션) · 다중 LLM Judge · Final Score(휴먼 우선)

```bash
# 백엔드 (mock)
AUTOAUDIT_MOCK=1 uvicorn AutoAudit.app.api.server:app --port 8010
python scripts/seed_sessions.py --reset   # 데모 데이터 시드
# 프론트
cd frontend && npm run dev                 # http://localhost:5173
```

## 상용화 핵심 기능

- **Provider 추상화** (`core/llm_client.py`) — OpenAI/Azure/Anthropic을 설정 한 줄로 전환
- **비용 가드** — 실행당 예산 상한(circuit breaker) + 토큰/비용 추적
- **선택적 재시도** — 429/5xx만 재시도, 400은 즉시 실패
- **Resume/체크포인트** — CP4 항목 단위 멱등 재개 (`--resume <run_id>`)
- **평가 신뢰성** — N회 샘플링 → 중앙값 + 분산 기반 `low_confidence` 플래그
- **Evidence View** — 답변 문장 ↔ 근거 컨텍스트 매핑으로 환각 시각화

## 🔬 고급 평가 기법 (옵션 토글)

모든 기법은 `config/settings.yaml`의 `evaluation:` 블록 또는 CLI `--enable/--disable`로 켜고 끕니다.
(`EvaluationOptions` — `AutoAudit/app/cp4_evaluator/options.py`)

| 옵션 | 기법 | 효과 | 기본값 |
|------|------|------|--------|
| `calibration` | 편향 보정 + G-Eval | 앵커/길이정규화로 leniency·verbosity bias 완화, logprob 기대점수 | off |
| `ensemble` | 다중 Judge 앙상블 | OpenAI+Anthropic 평가 → 불일치 시 메타 판정 에스컬레이션 | off |
| `meta_eval` | 골든셋 메타평가 | 인간 라벨 대비 Spearman ρ / Cohen κ / MAE로 "평가자를 평가" | off |
| `nugget` | Nugget recall | 질문→정보조각 추출 후 컨텍스트 매칭으로 GT-free recall | off |
| `diagnosis` | 검색 vs 생성 진단 | faithfulness×recall 2×2로 고칠 레이어 자동 분류 | **on** |
| `statistics` | 부트스트랩 CI + 유의성 회귀 | 신뢰구간 + 순열검정 기반 회귀 감지(거짓알람↓) | **on** |
| `routing` | 불확실성 라우팅 | 저신뢰 항목 추가샘플 + active sampling 인간검수 큐 | off |
| `ppi` | Prediction-Powered Inference | 저비용 분류기 전체예측 + 소량 LLM 보정 → 대량 비용↓ | off |
| `domain` | 도메인 메트릭 | 멀티턴 일관성 + PII/컴플라이언스 안전성 | off |

```bash
# 신뢰성 극대화 프로필
python run_pipeline.py --enable calibration,ensemble,meta_eval

# 대규모 저비용 프로필
python run_pipeline.py --enable ppi,routing

# 콜봇 안전성 감사 프로필
python run_pipeline.py --enable domain,nugget
```

세부 필드도 점 표기로 토글: `--enable calibration.g_eval_logprobs`

## 🧪 로컬 개발 (API 키·무거운 의존성 불필요)

`AUTOAUDIT_MOCK=1` 이면 **MockProvider + In-Memory 벡터스토어**로 동작합니다.
openai/chromadb/sentence-transformers/torch/rank-bm25/numpy 없이, 비용 $0으로
CP1~CP6 전체 파이프라인·API·대시보드를 돌려볼 수 있습니다.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt          # 경량 의존성만

# 원클릭 로컬 실행 (mock)
./scripts/dev_local.sh                        # 전체 파이프라인
./scripts/dev_local.sh --enable nugget,domain,routing
./scripts/dev_local.sh --until cp3

# 또는 환경변수로 직접
AUTOAUDIT_MOCK=1 python run_pipeline.py --reindex --data data/raw/

# mock E2E 테스트
pytest AutoAudit/tests/test_mock_e2e.py -q
```

> MockProvider는 토큰 중첩·해시 기반의 **결정적** 응답을 생성하므로 평가 점수가
> 재현 가능합니다. 실제 품질 수치가 아닌 파이프라인 동작 검증용입니다.

## 빠른 시작 (실제 LLM)

### 백엔드
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...

# 전체 파이프라인 (CP1~CP6)
python run_pipeline.py --reindex --data data/raw/

# CP3까지만 검증
python run_pipeline.py --until cp3

# 중단된 실행 재개
python run_pipeline.py --resume run_abc123

# 고급 평가 기법 토글 (예: 앙상블 + nugget recall 켜고, 진단 끄기)
python run_pipeline.py --enable ensemble,nugget --disable diagnosis

# API 서버
uvicorn AutoAudit.app.api.server:app --reload --port 8000
```

### 프론트엔드
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (/api → :8000 프록시)
```

### Docker
```bash
docker compose up --build   # backend :8000, frontend :8080
```

## 테스트
```bash
pytest -q          # 126 tests
ruff check AutoAudit
```

## 설정
모든 동작은 `config/settings.yaml`에서 제어 (provider/모델/SLA 임계값/샘플링 횟수/예산 등).

## 디렉토리
```
AutoAudit/app/
  core/        # 설정·로거·Provider·비용·체크포인트·Tracer
  cp1~cp6/     # 파이프라인 단계
  api/         # FastAPI 서버 + 결과 저장소
  tests/       # 126 tests
frontend/      # Vite + React 대시보드 (Overview→Drilldown→Evidence)
```
