# CallBot AutoAudit — API 정의서

> **버전** v1.1 | **작성일** 2026-06-03  
> **Base URL**: `http://localhost:8000`  
> **OpenAPI 문서**: `http://localhost:8000/docs` (FastAPI Swagger UI 자동 생성)

---

## 공통 규칙

| 항목 | 규칙 |
|------|------|
| 프로토콜 | HTTP/1.1 |
| 데이터 포맷 | JSON (`Content-Type: application/json`) |
| 인증 | 현재 없음 (운영 시 Bearer Token 또는 API Key 추가 예정) |
| 오류 응답 | `{"detail": "메시지"}` (FastAPI 표준 HTTPException) |
| 날짜 포맷 | ISO 8601 (`2026-06-03T10:00:00Z`) |
| 점수 범위 | `0.0 ~ 1.0` |
| run_id 특수값 | `"latest"` → 최신 배치 ID로 자동 resolve (`_resolve()`) |

---

## API 라우터 구조

```
GET  /api/health                                  # 헬스체크
GET  /api/tenants                                 # 테넌트 목록
GET  /api/judges                                  # Judge 모델 목록

# 테넌트 범위 API
POST /api/t/{tenant}/runs                         # 평가 실행
GET  /api/t/{tenant}/conversations                # 대화 목록
GET  /api/t/{tenant}/evaluations                  # 평가 탐색 (필터)
GET  /api/t/{tenant}/trends                       # 추이 (group_by=run|day)
GET  /api/t/{tenant}/agreement                    # 휴먼 vs 자동 일치도
GET  /api/t/{tenant}/agreement/{metric}           # 메트릭 일치도 표본
GET  /api/t/{tenant}/kb                           # KB 현황
GET  /api/t/{tenant}/settings                     # 설정 조회
PUT  /api/t/{tenant}/settings                     # 설정 저장
GET  /api/t/{tenant}/review-queue                 # 검수 큐

# 전역 범위 API (run_id 또는 eval_id 기반)
GET  /api/evaluations/{eval_id}                   # 단일 평가 상세
POST /api/review/{eval_id}                        # 휴먼 재평가 확정
GET  /api/conversations/{conversation_id}         # 세션 상세
GET  /api/runs                                    # 배치 목록
GET  /api/runs/{run_id}/summary                   # 배치 요약
GET  /api/runs/{run_id}/evaluations               # 배치 평가 목록
GET  /api/runs/{run_id}/evaluations/{eval_id}     # 배치 단일 평가
GET  /api/runs/{run_id}/trends                    # 전체 배치 추이
```

---

## 1. 헬스체크

### GET /api/health

서버 상태 확인.

**응답 200** — `HealthResponse`
```json
{ "status": "ok" }
```

---

## 2. 테넌트

### GET /api/tenants

가입자 목록 조회. `conversations` 테이블에서 tenant 집계. 데이터 없을 시 단일 데모 tenant 폴백.

**응답 200** — `TenantInfo[]`
```json
[
  {
    "tenant_id": "acme",
    "name": "Acme Telecom (데모)",
    "conversation_count": 142,
    "pending_review_count": 7
  }
]
```

---

## 3. Judge 모델

### GET /api/judges

사용 가능한 LLM Judge 목록. API 키 환경변수 존재 여부로 `available` 결정.  
Mock 모드(`AUTOAUDIT_MOCK=1`)이면 전체 available=true.

**응답 200** — `JudgeModel[]`
```json
[
  {
    "provider": "anthropic",
    "model": "claude-sonnet-4-5",
    "label": "Anthropic Claude",
    "available": true,
    "note": ""
  },
  {
    "provider": "openai",
    "model": "gpt-4o",
    "label": "OpenAI GPT-4o",
    "available": false,
    "note": "키 미등록"
  },
  {
    "provider": "gemini",
    "model": "gemini-2.5-pro",
    "label": "Google Gemini",
    "available": false,
    "note": "키 미등록"
  },
  {
    "provider": "azure",
    "model": "gpt-4o",
    "label": "Azure OpenAI",
    "available": false,
    "note": "키 미등록"
  },
  {
    "provider": "mock",
    "model": "mock",
    "label": "Mock (개발용)",
    "available": true,
    "note": "비용 $0"
  }
]
```

**API 키 환경변수 매핑:**

| Provider | 환경변수 |
|---------|---------|
| anthropic | `ANTHROPIC_API_KEY` |
| openai | `OPENAI_API_KEY` |
| gemini | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| azure | `AZURE_OPENAI_API_KEY` |

---

## 4. 평가 실행

### POST /api/t/{tenant}/runs

평가 배치 실행 요청. 현재 Mock 모드에서만 완전 지원(실 LLM은 501 반환).

**Path Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `tenant` | string | 가입자 ID |

**요청 본문** — `RunEvalConfig`
```json
{
  "judges": ["anthropic"],
  "ensemble": false,
  "levels": ["retrieval", "turn", "session"],
  "metrics": ["faithfulness", "answer_relevance", "context_precision", "context_recall"],
  "methods": ["calibration", "diagnosis", "statistics"],
  "target": "all",
  "temperature": 0.0
}
```

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `judges` | string[] | `["mock"]` | Judge provider 목록 (openai/anthropic/gemini/azure/mock) |
| `ensemble` | boolean | false | 앙상블 Judge 여부 |
| `levels` | string[] | `["turn"]` | 평가 레벨 (retrieval/turn/session) |
| `metrics` | string[] | `[]` | 평가 메트릭 목록 (빈 배열 = 전체) |
| `methods` | string[] | `[]` | 고급 기법 옵션 (calibration/ensemble/nugget/diagnosis/statistics/routing/ppi/domain) |
| `target` | string | "all" | 평가 대상 (all/unreviewed/기간 범위) |
| `temperature` | float | 0.0 | Judge LLM 온도 |

**응답 200** — `RunEvalResult`
```json
{
  "run_id": "run_a1b2c3d4",
  "tenant_id": "acme",
  "status": "completed",
  "judges": ["anthropic"],
  "levels": ["turn"],
  "methods": ["diagnosis"],
  "total_evaluations": 84,
  "message": "[mock] 구성 검증 완료 — 7개 대화 대상, Judge anthropic"
}
```

**응답 501** (실 LLM 실행)
```json
{ "detail": "실 LLM 실행은 아직 미연결 (mock 모드만 지원)" }
```

---

## 5. 대화 (Conversations)

### GET /api/t/{tenant}/conversations

테넌트의 대화 세션 목록. `conversation_id · tenant_id` 기준 조회.

**응답 200** — `ConversationInfo[]`
```json
[
  {
    "conversation_id": "ACME-1000",
    "tenant_id": "acme",
    "subscriber_id": "SUB_0042",
    "is_multiturn": true,
    "turn_count": 6,
    "pending_review": 1,
    "started_at": "2026-06-02T06:08:00Z",
    "session_scores": {
      "efficiency": 0.87,
      "escalation": 1.00,
      "consistency": 0.60,
      "resolution": 0.40
    }
  }
]
```

---

### GET /api/conversations/{conversation_id}

대화 세션 상세. 대화 원문 타임라인 + 턴별 평가 + 세션 평가.

**응답 200** — `ConversationDetail`
```json
{
  "conversation_id": "ACME-1000",
  "tenant_id": "acme",
  "subscriber_id": "SUB_0042",
  "is_multiturn": true,
  "started_at": "2026-06-02T06:08:00Z",
  "turns": [
    { "role": "user", "content": "요금제를 변경하고 싶은데요." },
    { "role": "bot",  "content": "안녕하세요! 어떤 요금제로 변경을 원하시나요?" }
  ],
  "turn_evaluations": [
    {
      "eval_id": "eval_abc",
      "query": "본인 확인은 어떻게 하나요?",
      "level": "turn",
      "turn_index": 5,
      "scores": [
        {
          "metric": "faithfulness",
          "score": 0.30,
          "is_low_confidence": false,
          "reasoning": "주민번호 확인 가능 주장이 컨텍스트에 없음",
          "claims": [
            { "claim": "주민번호로 확인 가능", "supported": false, "verdict": "unsupported", "reasoning": "..." }
          ]
        }
      ]
    }
  ],
  "session_evaluation": {
    "scores": [
      { "metric": "resolution", "score": 0.40 },
      { "metric": "consistency", "score": 0.60 }
    ]
  }
}
```

**응답 404**
```json
{ "detail": "conversation not found: ACME-1000" }
```

---

## 6. 평가 (Evaluations)

### GET /api/t/{tenant}/evaluations

테넌트 평가 목록. 다양한 필터 조합 지원.

**Query Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `level` | string | retrieval / turn / session |
| `metric` | string | 특정 메트릭 필터 |
| `review_status` | string | pending / approved / overridden / skipped |
| `low_confidence_only` | boolean | is_low_confidence=True 항목만 |
| `below_metric` | string | 특정 메트릭 임계값 미달 필터 |
| `below_threshold` | float | 임계값 (0.0~1.0) |

**응답 200** — `EvaluationResponse[]`
```json
[
  {
    "eval_id": "eval_abc123",
    "qa_id": "qa_xyz",
    "call_id": "ACME-1000",
    "tenant_id": "acme",
    "conversation_id": "ACME-1000",
    "subscriber_id": "SUB_0042",
    "level": "turn",
    "turn_index": 5,
    "query": "본인 확인은 어떻게 하나요?",
    "generated_answer": "주민번호 또는 비밀번호 4자리로 확인 가능합니다. 또한 대리점에서 지문 인증도 됩니다.",
    "judge_provider": "anthropic",
    "judge_model": "claude-sonnet-4-5",
    "evaluated_at": "2026-06-02T06:09:00Z",
    "retrieval_result": {
      "query": "본인 확인은 어떻게 하나요?",
      "hyde_query": "본인 확인은 주민등록번호 또는 비밀번호로 가능합니다.",
      "sub_queries": ["본인인증 방법", "신원확인 절차", "본인 확인 어떻게"],
      "contexts": [
        {
          "chunk_id": "chunk_72074113",
          "content": "본인 확인은 주민등록번호 또는 고객 비밀번호 4자리로 가능합니다.",
          "score": 0.95,
          "bm25_score": 0.78,
          "dense_score": 0.96,
          "source_call_id": "ACME-1000"
        }
      ]
    },
    "scores": [
      {
        "metric": "faithfulness",
        "score": 0.30,
        "reasoning": "claim 3개 중 1개 지지 (모순 1, 미지지 1)",
        "grounding_chunks": ["chunk_72074113"],
        "confidence": 1.0,
        "sample_scores": [0.33],
        "is_low_confidence": false,
        "claims": [
          {
            "claim": "주민번호 또는 비밀번호 4자리로 확인 가능",
            "supported": true,
            "verdict": "supported",
            "reasoning": "컨텍스트에 명시됨"
          },
          {
            "claim": "대리점에서 지문 인증도 됨",
            "supported": false,
            "verdict": "unsupported",
            "reasoning": "컨텍스트에 지문 인증 관련 내용 없음"
          }
        ],
        "method": "claim_nli",
        "human_score": null,
        "final_score": 0.30
      },
      {
        "metric": "answer_relevance",
        "score": 1.0,
        "reasoning": "질문의 본인확인 방법에 대해 직접 답변",
        "confidence": 0.95,
        "sample_scores": [1.0, 1.0, 0.9],
        "is_low_confidence": false,
        "method": "multi_sample"
      }
    ],
    "review_status": "pending",
    "human_scores": {},
    "human_label": [],
    "human_comment": "",
    "reviewer": null
  }
]
```

**`scores[].method` 값 설명:**

| method | 설명 |
|--------|------|
| `single` | 단일 LLM 호출 |
| `multi_sample` | N회 샘플링 → 중앙값 |
| `claim_nli` | RAGAS claim 분해 + NLI |
| `g_eval` | G-Eval (앵커보정 + 기댓값) |
| `ensemble` | 다중 Judge 앙상블 |
| `nugget` | Nugget recall |
| `ppi_classifier` | Heuristic 분류기 추정 |
| `domain` | 도메인 메트릭 |

---

### GET /api/evaluations/{eval_id}

단일 평가 상세 (run 무관 조회 — 검수 워크스페이스용).

**응답 200** — `EvaluationResponse` (위와 동일 구조)

**응답 404**
```json
{ "detail": "evaluation not found: eval_abc123" }
```

---

### GET /api/runs/{run_id}/evaluations

배치 run의 평가 목록.

**Path Parameters**: `run_id` — Run ID 또는 `"latest"`

**Query Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `call_id` | string | 특정 콜 필터 |
| `subscriber_id` | string | 특정 가입자 필터 |
| `flagged_only` | boolean | SLA 미달 콜만 (`flagged_call_ids` 기준) |
| `low_confidence_only` | boolean | is_low_confidence=True 항목만 |
| `below_metric` | string | 메트릭 이름 |
| `below_threshold` | float | 0.0~1.0 (Query 파라미터, 유효성 검증 포함) |

---

### GET /api/runs/{run_id}/evaluations/{eval_id}

배치 run의 단일 평가 상세.

---

## 7. 검수 (Human Review)

### POST /api/review/{eval_id}

휴먼 재평가 확정. `human_scores`로 수정된 메트릭만 Final Score를 덮어씀. 골든셋 적재.

**요청 본문** — `ReviewSubmit`
```json
{
  "status": "overridden",
  "human_scores": {
    "faithfulness": 0.30,
    "answer_relevance": 1.0
  },
  "labels": ["hallucination", "unsupported_claim"],
  "comment": "대리점 지문 인증 관련 내용이 KB에 없어 환각으로 판단",
  "reviewer": "qa_team@company.com"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `status` | string | **approved** (자동점수 승인) / **overridden** (점수 수정) / **skipped** (보류) |
| `human_scores` | dict | 수정할 메트릭: 점수. 미포함 메트릭은 자동 점수 유지 |
| `labels` | string[] | 오류 유형 라벨 (hallucination/missing_context/off_topic/unsupported_claim 등) |
| `comment` | string | 검수 의견 (자유 텍스트) |
| `reviewer` | string? | 검수자 식별자 (이메일 등) |

**응답 200** — `ReviewResult`
```json
{
  "eval_id": "eval_abc123",
  "ok": true,
  "review_status": "overridden"
}
```

**응답 404**
```json
{ "detail": "evaluation not found: eval_abc123" }
```

---

### GET /api/t/{tenant}/review-queue

검수 대기 평가 목록. 저신뢰(is_low_confidence=True) + 미검수(pending) 우선순위 정렬.

**응답 200** — `EvaluationResponse[]`

---

### GET /api/t/{tenant}/agreement

휴먼 vs 자동 평가 일치도 통계. 검수 완료 항목(approved/overridden)이 없으면 `available=false`.

**응답 200** — `AgreementResult`
```json
{
  "available": true,
  "overall_agreement": 0.82,
  "n": 47,
  "per_metric": {
    "faithfulness": {
      "agreement": 0.87,
      "spearman": 0.91,
      "mae": 0.08
    },
    "answer_relevance": {
      "agreement": 0.76,
      "spearman": 0.84,
      "mae": 0.12
    }
  }
}
```

---

### GET /api/t/{tenant}/agreement/{metric}

특정 메트릭의 휴먼·자동 표본 상세 (드릴다운용).

**Path Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `metric` | string | faithfulness / answer_relevance / context_precision / context_recall |

**응답 200** — `AgreementSample[]`
```json
[
  {
    "eval_id": "eval_abc123",
    "conversation_id": "ACME-1000",
    "query": "본인 확인은 어떻게 하나요?",
    "review_status": "overridden",
    "reviewer": "qa_team@company.com",
    "auto_score": 0.92,
    "human_score": 0.30,
    "delta": -0.62,
    "agree": false
  }
]
```

---

## 8. 배치 요약 (Runs / Summary)

### GET /api/runs

배치 목록 조회 (최신순 정렬).

**응답 200** — `RunInfo[]`
```json
[
  {
    "run_id": "seed_20260602",
    "generated_at": "2026-06-02T21:08:26Z",
    "total_calls": 7,
    "total_evaluations": 7,
    "flagged_count": 2,
    "has_summary": true
  }
]
```

---

### GET /api/runs/{run_id}/summary

배치 요약 통계. `"latest"` 사용 가능.

**응답 200** — `AuditSummaryResponse`
```json
{
  "run_id": "seed_20260602",
  "generated_at": "2026-06-02T21:08:26Z",
  "total_calls": 7,
  "total_evaluations": 7,
  "flagged_call_ids": ["ACME-1000", "ACME-1002"],
  "metrics": [
    {
      "metric": "faithfulness",
      "mean": 0.793,
      "median": 0.920,
      "p10": 0.300,
      "p90": 1.000,
      "below_sla_count": 2,
      "total_count": 7,
      "sla_pass_rate": 0.714
    },
    {
      "metric": "answer_relevance",
      "mean": 0.921,
      "median": 1.000,
      "p10": 0.600,
      "p90": 1.000,
      "below_sla_count": 1,
      "total_count": 7,
      "sla_pass_rate": 0.857
    }
  ]
}
```

**응답 404**
```json
{ "detail": "summary not found: run_xyz" }
```

---

## 9. 추이 (Trends)

### GET /api/t/{tenant}/trends

테넌트 메트릭 추이. 배치별 또는 일자별 집계.

**Query Parameters**
| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `group_by` | string | "run" | run / day |
| `date_from` | string | null | 시작일 (YYYY-MM-DD) |
| `date_to` | string | null | 종료일 |

**응답 200** — `TrendsResponse`
```json
{
  "points": [
    {
      "run_id": "run_130c7006",
      "label": "run_130c7006",
      "generated_at": "2026-06-01T21:38:06Z",
      "n": 20,
      "faithfulness": 0.831,
      "answer_relevance": 0.774,
      "context_precision": 0.712,
      "context_recall": 0.698
    },
    {
      "run_id": "run_0d2b5ec1",
      "label": "run_0d2b5ec1",
      "generated_at": "2026-06-01T21:39:05Z",
      "n": 20,
      "faithfulness": 0.856,
      "answer_relevance": 0.801
    }
  ],
  "metrics": ["faithfulness", "answer_relevance", "context_precision", "context_recall"]
}
```

> `TrendPoint.model_config = {"extra": "allow"}` — 메트릭 키는 동적 추가.

---

### GET /api/runs/{run_id}/trends

전체 배치 누적 추이 (회귀 감지용). `run_id`는 현재 조회 중인 배치.

---

## 10. 지식 베이스

### GET /api/t/{tenant}/kb

테넌트 KB 현황 + 검색 커버리지 갭.

**응답 200** — `KbStatus`
```json
{
  "tenant_id": "acme",
  "document_count": 42,
  "chunk_count": 380,
  "source_types": ["json", "txt"],
  "last_indexed_at": "2026-06-01T09:00:00Z",
  "avg_context_recall": 0.734,
  "coverage_gaps": [
    {
      "query": "위약금 계산 방법은?",
      "conversation_id": "ACME-1007",
      "context_recall": 0.32
    }
  ]
}
```

---

## 11. 설정

### GET /api/t/{tenant}/settings

테넌트 설정 조회.

**응답 200** — `TenantSettings`
```json
{
  "tenant_id": "acme",
  "sla_thresholds": {
    "faithfulness": 0.80,
    "answer_relevance": 0.75,
    "context_precision": 0.70,
    "context_recall": 0.70
  },
  "eval_profile": "기본",
  "default_judge": "anthropic",
  "judge_credentials": {
    "anthropic": true,
    "openai": false,
    "gemini": false,
    "azure": false
  },
  "slack_webhook": "",
  "notify_on_regression": true,
  "reviewers": ["qa@company.com"]
}
```

**`eval_profile` 선택값**: 기본 / 빠른 점검 / 고신뢰 / 검색 진단 / 안전성 감사

---

### PUT /api/t/{tenant}/settings

테넌트 설정 저장. `tenant_id` 필드는 path parameter로 override됨 (body의 tenant_id 무시).

**요청 본문** — `TenantSettings` (위와 동일 구조)  
**응답 200** — `TenantSettings` (저장 후 현재 값)

---

## 12. 오류 코드

| HTTP 상태 | 설명 | 발생 조건 |
|---------|------|-----------|
| 200 | 성공 | — |
| 404 | 리소스 없음 | conversation/evaluation/summary not found |
| 422 | 유효성 검사 오류 | Pydantic 유효성 오류 (below_threshold 범위 초과 등) |
| 501 | 미구현 | 실 LLM 평가 실행 (`AUTOAUDIT_MOCK=1` 아닐 때 POST /runs) |
| 500 | 서버 내부 오류 | 예상치 못한 예외 |

---

## 13. 주요 Pydantic 스키마 (schemas.py)

| 스키마 | 용도 |
|--------|------|
| `HealthResponse` | GET /health |
| `JudgeModel` | GET /judges |
| `RunEvalConfig` | POST /runs 요청 |
| `RunEvalResult` | POST /runs 응답 |
| `TenantInfo` | GET /tenants |
| `ConversationInfo` | GET /conversations |
| `ConversationDetail` | GET /conversations/{id} |
| `ConversationTurnView` | ConversationDetail.turns 항목 |
| `EvaluationResponse` | 평가 목록/상세 응답 |
| `MetricScoreResponse` | EvaluationResponse.scores 항목 |
| `ClaimVerdict` | MetricScoreResponse.claims 항목 (faithfulness NLI) |
| `RetrievalResultResponse` | EvaluationResponse.retrieval_result |
| `RetrievedContextResponse` | 검색 컨텍스트 항목 |
| `ReviewSubmit` | POST /review 요청 |
| `ReviewResult` | POST /review 응답 |
| `AgreementResult` | GET /agreement |
| `AgreementSample` | GET /agreement/{metric} |
| `RunInfo` | GET /runs |
| `AuditSummaryResponse` | GET /runs/{id}/summary |
| `MetricSummary` | AuditSummaryResponse.metrics 항목 |
| `TrendPoint` | TrendsResponse.points 항목 (extra=allow) |
| `TrendsResponse` | GET /trends |
| `KbGap` | KbStatus.coverage_gaps 항목 |
| `KbStatus` | GET /kb |
| `TenantSettings` | GET/PUT /settings |

---

## 14. OpenAPI / TypeScript 타입 자동 생성

```bash
# OpenAPI JSON 추출 (FastAPI 자동 생성 스키마)
python scripts/export_openapi.py    # → frontend/openapi.json

# TypeScript 타입 자동 생성
cd frontend && npx openapi-typescript openapi.json -o src/types.gen.ts
```

`schemas.py` → FastAPI `openapi()` → `frontend/openapi.json` → `types.gen.ts` 체인으로  
백엔드·프론트엔드 타입 계약이 자동 동기화. `.gitignore`에 `frontend/openapi.json` 포함되어 있으나 `scripts/export_openapi.py` 실행으로 재생성.
