# 🤖 CallBot AutoAudit

> Ground Truth 없이도 신뢰할 수 있는 품질 지표를 산출하는 **RAG 품질 자동 감사 파이프라인**.  
> 콜봇 대화 로그를 입력받아 CP1~CP6 단계를 거쳐 환각(Hallucination)을 탐지하고 근거 기반 평가 리포트를 생성합니다.

---

## 아키텍처 (CP1 ~ CP6)

| CP | 단계 | 핵심 |
|----|------|------|
| CP1 | 전처리 | txt/json/csv 로그 → `CallLog` 표준 스키마 |
| CP2 | 지식베이스 | Parent-Child 청킹 + ChromaDB 인덱싱 (cosine HNSW) |
| CP3 | 검색 | HyDE + Multi-Query + **BM25/Dense Hybrid(RRF)** + Cross-Encoder Rerank |
| CP3.5 | QA 추출 | 대화 → (질문, 봇답변) 쌍 + 컨텍스트 부착 |
| CP4 | 평가 | LLM-as-a-Judge **다중 샘플링 + 신뢰도** (Faithfulness 등 4지표) |
| CP5 | 집계 | SLA 판정 + 통계 (mean/median/p10/p90) + 부트스트랩 CI |
| CP6 | 리포트 | HTML/JSON + **회귀 감지** + Slack 알림 |

---

## 🖥️ Admin Console

멀티테넌트 SaaS 콜봇의 답변 품질을 **자동 평가 → 휴먼 재평가 → 확정**하는 웹 콘솔.  
(Vite + React + TypeScript `frontend/` · FastAPI 백엔드)

| 화면 | 기능 |
|------|------|
| **Overview** | KPI·메트릭·추이·SLA미달 + 카드 클릭 **drill-in/out** |
| **Conversations** | 대화이력 목록(싱글/멀티턴) + 세션 타임라인 + 근거 하이라이트 |
| **Run Evaluation** | 6스텝 마법사 — Judge모델(Claude/GPT/Gemini, 앙상블)·레벨·메트릭·방법론 |
| **Review** | 3-pane 휴먼 재평가 워크스페이스 (자동→휴먼 점수 수정/승인) |
| **Evaluations** | 필터 탐색 + Evidence 드로어 |
| **Trends** | 일자별/배치별 추이 그래프 + 휴먼·자동 일치도 |
| **Knowledge Base** | **고객사 지식 구축**(문서 추가·청킹·삭제) + KB 현황 + 검색 커버리지 갭 |
| **Settings** | SLA 임계값·평가 프로필·Judge 자격증명·알림 |

---

## 🧪 로컬 개발 (API 키·무거운 의존성 불필요)

`AUTOAUDIT_MOCK=1`이면 **MockProvider + In-Memory 벡터스토어**로 동작합니다.  
openai / chromadb / sentence-transformers / torch / rank-bm25 / numpy 없이, 비용 $0으로  
CP1~CP6 전체 파이프라인 · API · 대시보드를 로컬에서 실행할 수 있습니다.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt          # 경량 의존성만

# 백엔드 (mock)
AUTOAUDIT_MOCK=1 uvicorn AutoAudit.app.api.server:app --port 8000 --reload

# 데모 데이터 시드 (최초 1회) — 포탈 전 화면 검증용 종합 데이터(8주 배치·전 메트릭·검수 4종)
python scripts/seed_mock_data.py --reset
# (간단 시드: python scripts/seed_sessions.py --reset)

# 프론트엔드
cd frontend && npm install && npm run dev    # http://localhost:5173

# 파이프라인 전체 실행
AUTOAUDIT_MOCK=1 python run_pipeline.py --data data/raw/

# mock E2E 테스트
pytest AutoAudit/tests/test_mock_e2e.py -q
```

> MockProvider는 해시 기반 **결정적** 응답을 생성하므로 평가 점수가 재현 가능합니다.  
> 실제 품질 수치가 아닌 파이프라인 동작 검증용입니다.

---

## 빠른 시작 (실제 LLM)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...

# 전체 파이프라인 (CP1~CP6)
python run_pipeline.py --reindex --data data/raw/

# CP3까지만 실행 후 중단
python run_pipeline.py --until cp3

# 중단된 실행 재개 (체크포인트 기반)
python run_pipeline.py --resume run_abc123

# 고급 기법 활성화
python run_pipeline.py --enable calibration,ensemble,nugget

# API 서버
uvicorn AutoAudit.app.api.server:app --reload --port 8000
```

### 프론트엔드

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173 (/api → :8000 프록시)
```

### Docker

```bash
docker compose up --build   # backend :8000, frontend :8080
```

---

## 🔬 고급 평가 기법 (옵션 토글)

모든 기법은 `config/settings.yaml`의 `evaluation:` 블록 또는 CLI `--enable/--disable`로 켜고 끕니다.

| 옵션 | 기법 | 효과 | 기본값 |
|------|------|------|--------|
| `cot` | **Chain-of-Thought 강제** | '근거 먼저, 점수 나중' 단계 추론 → 점수 선결정 편향 차단 (추가 비용 없음) | **on** |
| `reverse` | **역방향 검증** | 순방향+역방향 양방향 평가 → 불일치 시 저신뢰 플래그 → 환각 누락 탐지↑ | off |
| `correctness` | **정답성 + 참조 채점** ①② | 정답 대비 claim F1+유사도 → "충실하지만 틀린 답변" 포착, answer_relevance에 정답 주입 | off |
| `context_injection` | **대화 맥락 주입** ③ | 직전 N턴 이력 주입 → 후속 턴(지시대명사·생략) 오채점 제거 (비용 0) | off |
| `abstention` | **적정 거절 면제** ④ | 정당한 "정보 없음/거절"의 감점 면제 → 거짓 실패 제거 | off |
| `numeric_guard` | **결정적 수치 가드** ⑤ | 숫자·금액·날짜를 컨텍스트와 대조 → 수치 환각 포착 (LLM 0) | off |
| `auto_calibration` | **휴먼 정합 자동 보정** ⑦ | 골든셋으로 점수를 사람 척도로 교정(isotonic/platt/linear) + SLA 자동 튜닝 | off |
| `calibration` | 편향 보정 + G-Eval | 앵커/길이정규화로 leniency·verbosity bias 완화 | off |
| `ensemble` | 다중 Judge 앙상블 | OpenAI+Anthropic 교차 평가 → 불일치 시 메타 판정 에스컬레이션 | off |
| `meta_eval` | 골든셋 메타평가 | 인간 라벨 대비 Spearman ρ / Cohen κ / MAE | off |
| `nugget` | Nugget recall | 질문→정보조각 추출 후 컨텍스트 매칭으로 GT-free recall | off |
| `diagnosis` | 검색 vs 생성 진단 | faithfulness×recall 2×2로 고칠 레이어 자동 분류 | **on** |
| `statistics` | 부트스트랩 CI + 유의성 회귀 | 신뢰구간 + 순열검정 기반 회귀 감지(거짓알람↓) | **on** |
| `routing` | 불확실성 라우팅 | 저신뢰 항목 추가샘플 + active sampling 인간검수 큐 | off |
| `ppi` | Prediction-Powered Inference | 저비용 분류기 전체예측 + 소량 LLM 보정 → 대량 비용↓ | off |
| `domain` | 도메인 메트릭 | 멀티턴 일관성 + PII/컴플라이언스 안전성 | off |

```bash
# 정확도 극대화 프로필 (CoT 기본 ON + 역방향 검증)
python run_pipeline.py --enable reverse

# 신뢰성 극대화 프로필
python run_pipeline.py --enable reverse,calibration,ensemble,meta_eval

# 대규모 저비용 프로필 (CoT 끄고 비용 절감)
python run_pipeline.py --enable ppi,routing --disable cot

# 콜봇 안전성 감사 프로필
python run_pipeline.py --enable domain,nugget

# 정답 데이터 기반 정밀 감사 (정답성 + 참조 채점 + 수치 가드)
python run_pipeline.py --enable correctness,numeric_guard

# 멀티턴 콜봇 + 거절 면제 (비용 증가 거의 없음)
python run_pipeline.py --enable context_injection,abstention,numeric_guard
```

### CoT + 역방향 검증 동작 원리

```
[CoT 강제] — answer_relevance / context_precision / context_recall
  기존: "점수를 매겨라" → LLM이 점수 먼저 정하고 근거 역생성 (편향)
  개선: Step1 의도분석 → Step2 항목나열 → Step3 누락확인 → Step4 점수
        → cot_steps 필드에 단계별 추론 기록 (Evidence View 연동)

[역방향 검증] — faithfulness / answer_relevance
  순방향: 답변 → 컨텍스트 지지 여부 (forward_score)
  역방향: 컨텍스트 → 답변 도출 가능성 (reverse_score)
  최종점수 = forward × 0.6 + reverse × 0.4
  |forward - reverse| > 0.25 → is_low_confidence=True → 사람 검수 우선
```

### 답변 품질 정확도 강화 동작 원리 (①~⑤,⑦)

```
[① 정답성 + ② 참조 채점] — answer_correctness (ground_truth 필요)
  정답 vs 답변 → TP/FP/FN 분류 → F1 = 2·TP/(2·TP+FP+FN)
  최종 = F1 × 0.75 + 의미유사도 × 0.25
  → faithfulness가 못 잡는 '컨텍스트엔 충실하나 정답과 다른' 답변 포착
  + answer_relevance 판정 시 [모범답안]을 주입해 사람 채점과 정렬

[③ 대화 맥락 주입] — answer_relevance / faithfulness
  "그건 얼마예요?" + 직전 맥락("요금제 안내")  → 지시대명사 해석 후 채점
  → 후속 턴 고립 평가로 인한 체계적 오채점 제거 (LLM 호출 0)

[④ 적정 거절] — 거절 정규식 탐지 → 컨텍스트 근거 부재면 정당
  정당 → faithfulness/answer_relevance 감점 면제(abstention=True)

[⑤ 수치 가드] — 답변 수치 ∉ 컨텍스트 → 충돌 (결정적, LLM 0)
  faithfulness -= 0.3 × 충돌수, numeric_flags 기록

[⑦ 자동 보정] — 골든셋(judge→human)으로 보정맵 학습 → 점수 교정
  judge 0.8 → (사람 척도) 0.6 으로 끌어당김, SLA 임계 F1 최대점 자동 탐색
```

---

## 상용화 핵심 기능

| 기능 | 설명 |
|------|------|
| **Provider 추상화** | OpenAI/Azure/Anthropic/Gemini를 설정 한 줄로 전환 (`core/llm_client.py`) |
| **비용 가드** | 실행당 USD 예산 상한(circuit breaker) + 토큰/비용 추적 |
| **선택적 재시도** | 429/5xx만 재시도, 400은 즉시 실패 (tenacity) |
| **Resume/체크포인트** | CP4 항목 단위 멱등 재개 (`--resume run_id`) |
| **평가 신뢰성** | N회 샘플링 → 중앙값 + 분산 기반 `low_confidence` 플래그 |
| **Evidence View** | 답변 문장 ↔ 근거 컨텍스트 매핑으로 환각 시각화 |
| **멀티테넌시** | tenant_id 격리 — 가입자별 KB·평가·설정 분리 |
| **Human Review** | 자동 점수 검수 확정 → Final Score (휴먼 우선) |

---

## 테스트

```bash
pytest AutoAudit/tests/ -q    # 205개 테스트 (mock 모드, API 키 불필요)
ruff check AutoAudit/         # 린트
```

---

## 설정

모든 동작은 `config/settings.yaml`에서 제어합니다 (provider/모델/SLA 임계값/샘플링 횟수/예산 등).

```yaml
llm:
  provider: "openai"       # openai | azure | anthropic | gemini | mock
  model: "gpt-4o"
  budget_usd: 50.0         # 실행당 비용 상한

cp5:
  sla_thresholds:
    faithfulness: 0.8
    answer_relevance: 0.75
    context_precision: 0.7
    context_recall: 0.7
```

---

## 디렉토리

```
AutoAudit/app/
  core/          # 설정·로거·Provider·비용·체크포인트·Tracer
  cp1~cp6/       # 파이프라인 단계
  api/           # FastAPI 서버 + SQLite 데이터 접근 (25개 엔드포인트)
  tests/         # 205개 pytest 테스트
frontend/        # Vite + React 대시보드 (8개 화면)
config/          # settings.yaml
scripts/         # dev_local.sh, seed_mock_data.py, seed_sessions.py, export_openapi.py
docs/            # 산출물 문서
```

---

## 📄 산출물 문서

| 문서 | 설명 |
|------|------|
| [`docs/1_기획서.md`](docs/1_기획서.md) | 제품 목적·기능 요구사항·SLA 기준·마일스톤 |
| [`docs/2_아키텍처_설계서.md`](docs/2_아키텍처_설계서.md) | 기술 스택·파이프라인 설계·데이터 흐름 |
| [`docs/3_API_정의서.md`](docs/3_API_정의서.md) | REST API 전체 엔드포인트 명세 |
| [`docs/4_프로그램_상세_설명서.md`](docs/4_프로그램_상세_설명서.md) | 모듈별 구현 상세·설계 패턴 |
| [`docs/5_사용자_메뉴얼.md`](docs/5_사용자_메뉴얼.md) | Admin Console 화면별 사용법 (스크린샷 포함) |

---

## GitHub

```
https://github.com/kchul199/Callbot_AutoAudit
```
