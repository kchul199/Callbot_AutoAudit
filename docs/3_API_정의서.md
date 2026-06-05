# CallBot AutoAudit — API 정의서

> **버전** v1.3 | **작성일** 2026-06-05  
> **Base URL**: `http://localhost:8000` (로컬 프론트 개발 시 Vite가 `/api` → `:8000` 프록시)  
> **OpenAPI 문서**: `http://localhost:8000/docs` (FastAPI Swagger UI 자동 생성)  
> **엔드포인트**: 25개 오퍼레이션(23 path) · **스키마**: 29종 (schemas.py 정의 기준)

---

## 공통 규칙

| 항목 | 규칙 |
|------|------|
| 프로토콜 | HTTP/1.1 |
| 데이터 포맷 | JSON (`Content-Type: application/json`) |
| 인증 | 현재 없음 (운영 시 Bearer Token 또는 API Key 추가 예정) |
| 오류 응답 | `{"detail": "메시지"}` (FastAPI HTTPException) |
| 날짜 포맷 | ISO 8601 (`2026-06-03T10:00:00Z`) |
| 점수 범위 | `0.0 ~ 1.0` |
| run_id 특수값 | `"latest"` → `data.latest_run_id()` 자동 resolve |
| 설정 저장 | `DataAccess._settings` 인메모리 (서버 재시작 시 초기화) |

---

## API 라우터 전체 목록

```
GET  /api/health
GET  /api/tenants
GET  /api/judges

# 테넌트 범위 (tenant_id 필터)
POST /api/t/{tenant}/runs
GET  /api/t/{tenant}/conversations
GET  /api/t/{tenant}/evaluations
GET  /api/t/{tenant}/trends
GET  /api/t/{tenant}/agreement
GET  /api/t/{tenant}/agreement/{metric}
GET  /api/t/{tenant}/kb
POST   /api/t/{tenant}/kb/documents          # 고객사 지식 문서 추가 (청킹 후 저장)
DELETE /api/t/{tenant}/kb/documents/{doc_id} # 구축 문서 삭제
GET  /api/t/{tenant}/settings
PUT  /api/t/{tenant}/settings
PUT    /api/t/{tenant}/credentials/{provider}   # provider API 키 등록 (마스킹 저장)
DELETE /api/t/{tenant}/credentials/{provider}   # 수동 등록 자격증명 삭제
GET  /api/t/{tenant}/review-queue

# 전역 범위 (run_id 또는 eval_id 기반)
GET  /api/evaluations/{eval_id}          # run 무관 (store.get_evaluation_by_id)
POST /api/review/{eval_id}
GET  /api/conversations/{conversation_id}
GET  /api/runs
GET  /api/runs/{run_id}/summary
GET  /api/runs/{run_id}/evaluations
GET  /api/runs/{run_id}/evaluations/{eval_id}
GET  /api/runs/{run_id}/trends
```

---

## 1. 헬스체크

### GET /api/health

**응답 200**
```json
{ "status": "ok" }
```

---

## 2. 테넌트

### GET /api/tenants

가입자 목록. `conversations` 테이블 tenant_id 집계. 데이터 없으면 단일 데모 테넌트 폴백.

**응답 200** — `TenantInfo[]`
```json
[
  {
    "tenant_id": "acme",
    "name": "Acme Telecom",
    "conversation_count": 3,
    "pending_review_count": 1
  },
  {
    "tenant_id": "globex",
    "name": "Globex 보험",
    "conversation_count": 2,
    "pending_review_count": 0
  }
]
```

**테넌트 이름 매핑 (`_TENANT_NAMES`):**

| tenant_id | name |
|-----------|------|
| acme | Acme Telecom |
| globex | Globex 보험 |
| initech | Initech 커머스 |
| 그 외 | tenant_id 그대로 |

---

## 3. Judge 모델

### GET /api/judges

사용 가능한 LLM Judge 목록. API 키 환경변수 존재 여부로 `available` 결정.  
`AUTOAUDIT_MOCK=1` 환경이면 전체 `available=true`.

**응답 200** — `JudgeModel[]`
```json
[
  { "provider": "anthropic", "model": "claude-sonnet-4-5", "label": "Anthropic Claude", "available": true, "note": "" },
  { "provider": "openai",    "model": "gpt-4o",           "label": "OpenAI GPT-4o",   "available": false, "note": "키 미등록" },
  { "provider": "gemini",    "model": "gemini-2.5-pro",   "label": "Google Gemini",   "available": false, "note": "키 미등록" },
  { "provider": "azure",     "model": "gpt-4o",           "label": "Azure OpenAI",    "available": false, "note": "키 미등록" },
  { "provider": "mock",      "model": "mock",             "label": "Mock (개발용)",    "available": true, "note": "비용 $0" }
]
```

**API 키 환경변수:**

| Provider | 환경변수 |
|---------|---------|
| anthropic | `ANTHROPIC_API_KEY` |
| openai | `OPENAI_API_KEY` |
| gemini | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| azure | `AZURE_OPENAI_API_KEY` |

> **임베딩 주의**: Anthropic/Gemini Provider는 임베딩을 지원하지 않아 내부적으로 OpenAI API를 사용합니다. 이 Provider를 Judge로 사용하면 `OPENAI_API_KEY`도 함께 필요합니다.

---

## 4. 평가 실행

### POST /api/t/{tenant}/runs

평가 배치 실행. 현재 `AUTOAUDIT_MOCK=1` 환경에서만 완전 지원.

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
| `judges` | string[] | `["mock"]` | provider 목록 |
| `ensemble` | boolean | false | 앙상블 Judge (2개 이상 judges 필요) |
| `levels` | string[] | `["turn"]` | retrieval / turn / session |
| `metrics` | string[] | `[]` | 빈 배열 = 전체 |
| `methods` | string[] | `[]` | 16종: cot/reverse/correctness/context_injection/abstention/numeric_guard/auto_calibration/calibration/ensemble/meta_eval/nugget/diagnosis/statistics/routing/ppi/domain |
| `target` | string | "all" | all / unreviewed / 기간 범위 |
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

**응답 501** (실 LLM 환경)
```json
{ "detail": "실 LLM 실행은 아직 미연결 (mock 모드만 지원)" }
```

---

## 5. 대화 (Conversations)

### GET /api/t/{tenant}/conversations

테넌트 대화 목록. `conversations` 테이블에서 pending 카운트 포함 조회.

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
      "resolution": 0.40,
      "efficiency": 0.87,
      "escalation_handling": 1.00,
      "multiturn_consistency": 0.60
    }
  }
]
```

---

### GET /api/conversations/{conversation_id}

세션 상세. 대화 원문 (`turns_json`) + 턴별 평가 + 세션 평가.

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
      "level": "turn",
      "turn_index": 5,
      "query": "본인 확인은 어떻게 하나요?",
      "scores": [
        {
          "metric": "faithfulness",
          "score": 0.30,
          "reasoning": "claim 3개 중 1개 지지",
          "is_low_confidence": false,
          "human_score": null,
          "final_score": 0.30,
          "claims": [
            { "claim": "주민번호로 확인 가능", "supported": true, "verdict": "supported" },
            { "claim": "대리점 지문 인증 가능", "supported": false, "verdict": "unsupported" }
          ]
        }
      ]
    }
  ],
  "session_evaluation": {
    "level": "session",
    "scores": [
      { "metric": "resolution", "score": 0.40 },
      { "metric": "efficiency", "score": 0.87 },
      { "metric": "escalation_handling", "score": 1.00 },
      { "metric": "multiturn_consistency", "score": 0.60 }
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

테넌트 평가 목록 탐색 (`store.query_evaluations_by_tenant()` 위임).

**Query Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `level` | string | retrieval / turn / session |
| `metric` | string | 특정 메트릭 필터 |
| `review_status` | string | pending / approved / overridden / skipped |
| `low_confidence_only` | boolean | is_low_confidence=True 항목만 |
| `below_metric` | string | 특정 메트릭 임계값 미달 |
| `below_threshold` | float | 0.0~1.0 |

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
      "hyde_query": "본인 확인은 주민등록번호로 가능합니다.",
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
        "reasoning": "claim 3개 중 1개 지지 (모순 0, 미지지 2)",
        "grounding_chunks": ["chunk_72074113"],
        "confidence": 1.0,
        "sample_scores": [0.33],
        "is_low_confidence": false,
        "claims": [
          { "claim": "주민번호 또는 비밀번호 4자리로 확인 가능", "supported": true, "verdict": "supported", "reasoning": "컨텍스트에 명시" },
          { "claim": "대리점에서 지문 인증도 됨", "supported": false, "verdict": "unsupported", "reasoning": "컨텍스트에 지문 인증 없음" }
        ],
        "method": "claim_nli",
        "human_score": null,
        "final_score": 0.30
      },
      {
        "metric": "answer_relevance",
        "score": 1.0,
        "confidence": 0.95,
        "sample_scores": [1.0, 1.0, 0.9],
        "is_low_confidence": false,
        "method": "multi_sample",
        "human_score": null,
        "final_score": 1.0
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

**`scores[].method` 값:**

| method | 설명 |
|--------|------|
| `single` | 단일 LLM 호출 |
| `multi_sample` | N회 샘플링 → 중앙값 |
| `claim_nli` | RAGAS claim 분해 + NLI (faithfulness 기본) |
| `cot` | CoT 단계별 추론 강제 (answer_relevance/context_precision/context_recall) |
| `cot_reverse` | CoT + 역방향 검증 결합 |
| `reverse` | 역방향 검증 (순방향+역방향 가중 결합) |
| `g_eval` | G-Eval (앵커+보정, 기댓값) |
| `ensemble` | 다중 Judge 앙상블 |
| `nugget` | Nugget recall (context_recall 대체) |
| `answer_correctness` | 정답 대비 claim F1 + 의미 유사도 (ground_truth 필요) |
| `abstention_exempt` | 적정 거절로 감점 면제 처리됨 |
| `ppi_classifier` | HeuristicClassifier 추정 |
| `domain` | 도메인 메트릭 (PII/일관성) |
| `session` | SessionEvaluator (세션 전체) |

---

### GET /api/evaluations/{eval_id}

단일 평가 상세 (run 무관, `store.get_evaluation_by_id()` 사용).  
검수 워크스페이스에서 run_id 없이 직접 조회할 때 사용.

**응답 200** — `EvaluationResponse`
**응답 404** — `{"detail": "evaluation not found: eval_abc123"}`

---

### GET /api/runs/{run_id}/evaluations

배치 run의 평가 목록. `run_id="latest"` 지원.

**Query Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `call_id` | string | 특정 콜 필터 |
| `subscriber_id` | string | 특정 가입자 필터 |
| `flagged_only` | boolean | flagged_call_ids 기준 필터 |
| `low_confidence_only` | boolean | is_low_confidence=True 항목만 |
| `below_metric` | string | 메트릭 이름 |
| `below_threshold` | float | 0.0~1.0 (Query 파라미터, 유효성 검증) |

**데이터 접근 우선순위:**
1. SQLite에 해당 run이 있으면 → `store.query_evaluations(run_id, **filters)` (SQL 필터)
2. 없으면 → `repo.get_evaluations(run_id, ...)` (인메모리 필터)

---

### GET /api/runs/{run_id}/evaluations/{eval_id}

배치 run 내 단일 평가 상세.

---

## 7. 검수 (Human Review)

### POST /api/review/{eval_id}

휴먼 재평가 확정. `human_scores`에 있는 메트릭만 Final Score를 덮어씀.

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
  "reviewer": "qa@company.com"
}
```

| `status` | 의미 |
|---------|------|
| `approved` | 자동 점수에 동의, 수정 없음 |
| `overridden` | human_scores로 점수 수정 |
| `skipped` | 나중에 다시 검수 |

**저장:** `store.record_human_review(eval_id, status, human_scores, labels, comment, reviewer)`

**응답 200** — `ReviewResult`
```json
{ "eval_id": "eval_abc123", "ok": true, "review_status": "overridden" }
```

**응답 404**
```json
{ "detail": "evaluation not found: eval_abc123" }
```

---

### GET /api/t/{tenant}/review-queue

검수 대기 평가 목록. `review_status='pending'` + `min_confidence ASC` 정렬.

```sql
SELECT e.*, (SELECT MIN(s.confidence) FROM scores s WHERE s.eval_id=e.eval_id) AS min_conf
FROM evaluations e WHERE tenant_id=? AND review_status='pending'
ORDER BY min_conf ASC LIMIT 50
```

**응답 200** — `EvaluationResponse[]`

---

### GET /api/t/{tenant}/agreement

휴먼 vs 자동 평가 일치도. 검수 완료 항목 없으면 `available=false`.

**응답 200** — `AgreementResult`
```json
{
  "available": true,
  "overall_agreement": 0.82,
  "n": 47,
  "per_metric": {
    "faithfulness": { "agreement": 0.87, "spearman": 0.91, "mae": 0.08 },
    "answer_relevance": { "agreement": 0.76, "spearman": 0.84, "mae": 0.12 }
  }
}
```

---

### GET /api/t/{tenant}/agreement/{metric}

특정 메트릭 일치도 표본 상세.

**응답 200** — `AgreementSample[]`
```json
[
  {
    "eval_id": "eval_abc123",
    "conversation_id": "ACME-1000",
    "query": "본인 확인은 어떻게 하나요?",
    "review_status": "overridden",
    "reviewer": "qa@company.com",
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

배치 목록. SQLite + JSON 병합, `generated_at DESC` 정렬.

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

배치 집계 통계. `"latest"` 지원.

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
    }
  ]
}
```

**응답 404** — `{"detail": "summary not found: run_xyz"}`

---

## 9. 추이 (Trends)

### GET /api/t/{tenant}/trends

테넌트 메트릭 추이.

**Query Parameters**
| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `group_by` | "run" | run / day |
| `date_from` | null | YYYY-MM-DD |
| `date_to` | null | YYYY-MM-DD |

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
      "answer_relevance": 0.774
    }
  ],
  "metrics": ["answer_relevance", "context_precision", "context_recall", "faithfulness"]
}
```

> `TrendPoint.model_config = {"extra": "allow"}` — 메트릭 키는 동적 추가됨.

---

### GET /api/runs/{run_id}/trends

전체 배치 누적 추이 (회귀 감지용). SQL: `runs JOIN metric_summary ORDER BY generated_at ASC`.

---

## 10. 지식 베이스

### GET /api/t/{tenant}/kb

```sql
-- 커버리지 갭: context_recall < 0.7인 질의 (상위 10건)
SELECT query, conversation_id, recall FROM evaluations
JOIN scores ON ... WHERE metric='context_recall' AND tenant_id=?
ORDER BY recall ASC LIMIT 10
```

**응답 200** — `KbStatus`
```json
{
  "tenant_id": "acme",
  "document_count": 8,
  "chunk_count": 73,
  "source_types": ["DOC_요금제규정", "DOC_약관해지", "정책"],
  "last_indexed_at": "2026-06-05T09:00:00Z",
  "avg_context_recall": 0.734,
  "coverage_gaps": [
    { "query": "위약금 계산 방법은?", "conversation_id": "ACME-1007", "context_recall": 0.32 }
  ],
  "built_documents": [
    {
      "doc_id": "kbdoc_a1b2c3d4e5", "tenant_id": "acme",
      "title": "5G 프리미엄 요금제 안내", "source_type": "정책",
      "char_count": 320, "chunk_count": 3,
      "created_at": "2026-06-05T09:00:00Z", "content_preview": "5G 프리미엄 요금제는..."
    }
  ],
  "built_document_count": 1,
  "built_chunk_count": 3
}
```

> **document_count / chunk_count** = 검색에서 파생된 소스·청크 + 수동 구축 문서·청크의 합.
> `built_documents`는 "고객사 지식 구축"으로 추가한 문서만 별도 노출.
> `source_types`: 파생 소스 call_id + 구축 문서 유형 (최대 12개).

---

### POST /api/t/{tenant}/kb/documents

고객사 지식 문서 추가. 내용을 CP2 child 청크(기본 200자, overlap 50) 기준으로
청킹해 청크 수를 산정하고 저장합니다.

**요청 본문** — `KbDocumentSubmit`
```json
{ "title": "5G 프리미엄 요금제 안내", "content": "5G 프리미엄 요금제는 월 69,000원...", "source_type": "정책" }
```

| 필드 | 필수 | 설명 |
|------|------|------|
| `title` | ✅ | 문서 제목 (빈 값 400) |
| `content` | ✅ | 문서 본문 (빈 값 400) — 청킹 대상 |
| `source_type` | — | 정책 / FAQ / 약관 / 매뉴얼 / 상품정보 / 기타 (기본 "수동") |

**응답 200** — `KbStatus` (구축 문서가 반영된 최신 KB 현황)  
**응답 400** — `{"detail": "문서 제목이 필요합니다."}` / `{"detail": "문서 내용이 필요합니다."}`

---

### DELETE /api/t/{tenant}/kb/documents/{doc_id}

구축 KB 문서 삭제.

**응답 200** — `KbStatus` (삭제 반영된 최신 현황)

---

## 11. 설정

### GET /api/t/{tenant}/settings

테넌트 설정 조회. `DataAccess._settings[tenant_id]`에 있으면 그 값, 없으면 기본값 생성.

**응답 200** — `TenantSettings`
```json
{
  "tenant_id": "acme",
  "sla_thresholds": {
    "faithfulness": 0.80,
    "answer_relevance": 0.75,
    "context_precision": 0.70,
    "context_recall": 0.70,
    "answer_correctness": 0.75
  },
  "eval_profile": "기본",
  "default_judge": "anthropic",
  "judge_credentials": {
    "anthropic": true,
    "openai": false,
    "gemini": false,
    "azure": false
  },
  "credential_details": {
    "anthropic": {
      "provider": "anthropic", "registered": true, "source": "manual",
      "masked_key": "••••••••1234", "base_url": "",
      "endpoint": "", "api_version": "", "deployment": "",
      "updated_at": "2026-06-05T09:00:00Z"
    },
    "azure": {
      "provider": "azure", "registered": false, "source": "none",
      "masked_key": "", "base_url": "", "endpoint": "",
      "api_version": "", "deployment": "", "updated_at": null
    }
  },
  "slack_webhook": "",
  "notify_on_regression": true,
  "reviewers": ["qa_kim", "qa_lee"]
}
```

> **judge_credentials / credential_details** 는 매 조회 시 **실시간 산출**됩니다:
> 환경변수(예: `ANTHROPIC_API_KEY`) 존재 시 `source="env"`, 자격증명 엔드포인트로
> 수동 등록 시 `source="manual"`(마스킹 키 노출), 둘 다 없으면 `registered=false`.
> (이전 버전의 "mock=1 → 전부 true" 동작은 제거됨. 평문 키는 어떤 응답에도 포함되지 않음.)

> **기본 검수자**: `["qa_kim", "qa_lee"]` (데모 기본값).  
> **설정 저장**: `DataAccess._settings[tenant_id]` 인메모리 — 서버 재시작 시 초기화됨.

---

### PUT /api/t/{tenant}/settings

테넌트 설정 저장. 기본값 + 기존 + 신규를 병합하되 **자격증명 필드(`judge_credentials`,
`credential_details`)는 제외**하고 저장 (자격증명은 전용 엔드포인트로만 갱신).

**요청 본문** — `TenantSettings`  
**응답 200** — `TenantSettings` (저장 후 현재 값, 자격증명은 실시간 재산출)

---

### PUT /api/t/{tenant}/credentials/{provider}

provider(anthropic/openai/gemini/azure) API 키 등록. **평문 키는 저장하지 않고
마스킹(마지막 4자리)만 보관**하며, 응답에 평문을 반환하지 않습니다.

**요청 본문** — `CredentialSubmit`
```json
{
  "api_key": "sk-ant-xxxxxxxx1234",
  "base_url": "",
  "endpoint": "https://acme.openai.azure.com",
  "api_version": "2024-06-01",
  "deployment": "gpt-4o"
}
```

| 필드 | 적용 provider | 설명 |
|------|---------------|------|
| `api_key` | 전체 (필수) | 빈 값이면 400 |
| `base_url` | openai/anthropic | 호환 게이트웨이 URL (선택) |
| `endpoint` | azure | Azure 리소스 엔드포인트 |
| `api_version` | azure | API 버전 (예: 2024-06-01) |
| `deployment` | azure | 배포명 (예: gpt-4o) |

**응답 200** — `TenantSettings` (해당 provider가 `registered=true, source="manual"`로 갱신)  
**응답 400** — `{"detail": "api_key가 필요합니다."}` (빈 키)

---

### DELETE /api/t/{tenant}/credentials/{provider}

수동 등록 자격증명 삭제. 환경변수 기반 등록(`source="env"`)에는 영향 없음.

**응답 200** — `TenantSettings` (해당 provider가 env 유무에 따라 재산출)

---

## 12. 오류 코드

| HTTP | 설명 | 발생 조건 |
|------|------|-----------|
| 200 | 성공 | — |
| 404 | 리소스 없음 | conversation/evaluation/summary not found |
| 422 | 유효성 오류 | Pydantic 검증 실패 (below_threshold 범위 등) |
| 501 | 미구현 | 실 LLM 평가 실행 (Mock 아닐 때 POST /runs) |
| 500 | 서버 오류 | 예상치 못한 예외 |

---

## 13. Pydantic 스키마 전체 목록 (schemas.py)

| 스키마 | 용도 |
|--------|------|
| `HealthResponse` | GET /health |
| `JudgeModel` | GET /judges |
| `RunEvalConfig` | POST /runs 요청 |
| `RunEvalResult` | POST /runs 응답 |
| `ReviewSubmit` | POST /review 요청 |
| `ReviewResult` | POST /review 응답 |
| `TenantInfo` | GET /tenants |
| `ConversationInfo` | GET /t/{tenant}/conversations |
| `ConversationDetail` | GET /conversations/{id} |
| `ConversationTurnView` | ConversationDetail.turns 항목 |
| `EvaluationResponse` | 평가 목록/상세 |
| `MetricScoreResponse` | EvaluationResponse.scores 항목 |
| `ClaimVerdict` | MetricScoreResponse.claims 항목 |
| `RetrievalResultResponse` | EvaluationResponse.retrieval_result |
| `RetrievedContextResponse` | 검색 컨텍스트 항목 |
| `AgreementResult` | GET /agreement |
| `AgreementSample` | GET /agreement/{metric} |
| `RunInfo` | GET /runs |
| `AuditSummaryResponse` | GET /runs/{id}/summary |
| `MetricSummary` | AuditSummaryResponse.metrics 항목 |
| `TrendPoint` | model_config extra=allow (동적 메트릭 키) |
| `TrendsResponse` | GET /trends |
| `KbGap` | KbStatus.coverage_gaps 항목 |
| `KbStatus` | GET /kb (built_documents 포함) |
| `KbDocument` | KbStatus.built_documents 항목 (구축 지식 문서) |
| `KbDocumentSubmit` | POST /kb/documents 요청 본문 |
| `TenantSettings` | GET/PUT /settings (credential_details 포함) |
| `CredentialInfo` | TenantSettings.credential_details 항목 (provider별 등록 상태·마스킹 키) |
| `CredentialSubmit` | PUT /credentials/{provider} 요청 본문 |

---

## 14. 데이터 접근 우선순위

```
DataAccess.get_evaluations(run_id, **filters)
    │
    ├─ store.get_summary(run_id) is not None
    │   → store.query_evaluations(run_id, **filters)  ← SQL 필터 (빠름)
    │
    └─ else (DB 미적재 run)
        → repo.get_evaluations(run_id, ...)  ← 인메모리 필터 (느림)

DataAccess.list_runs()
    → db_runs + json_only (DB에 없는 run만 JSON에서 보충)
    → sorted by generated_at DESC
```

---

## 15. OpenAPI / TypeScript 타입 자동 생성

```bash
# OpenAPI JSON 추출
python scripts/export_openapi.py    # → frontend/openapi.json

# TypeScript 타입 자동 생성
cd frontend && npx openapi-typescript openapi.json -o src/types.gen.ts
```

`schemas.py` → FastAPI `openapi()` → `frontend/openapi.json` → `types.gen.ts`.  
`frontend/openapi.json`은 `.gitignore`에 포함되어 있으나 `export_openapi.py` 실행으로 재생성.
