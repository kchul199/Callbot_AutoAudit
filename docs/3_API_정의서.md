# CallBot AutoAudit — API 정의서

> **버전** v1.0 | **작성일** 2026-06-03  
> **Base URL**: `http://localhost:8000`  
> **OpenAPI 문서**: `http://localhost:8000/docs` (FastAPI 자동 생성)

---

## 공통 규칙

| 항목 | 규칙 |
|------|------|
| 프로토콜 | HTTP/1.1 |
| 데이터 포맷 | JSON (`Content-Type: application/json`) |
| 인증 | 현재 없음 (운영 시 Bearer Token 또는 API Key 추가 예정) |
| 오류 응답 | `{"detail": "메시지"}` (FastAPI 표준) |
| 날짜 포맷 | ISO 8601 (`2026-06-03T10:00:00Z`) |
| 점수 범위 | `0.0 ~ 1.0` |

---

## 1. 헬스체크

### GET /api/health

서버 상태 확인.

**응답 200**
```json
{
  "status": "ok"
}
```

---

## 2. 테넌트 (Tenant)

### GET /api/tenants

가입자 목록 조회.

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

사용 가능한 LLM Judge 목록 (API 키 등록 여부 포함).

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
    "provider": "mock",
    "model": "mock",
    "label": "Mock (개발용)",
    "available": true,
    "note": "비용 $0"
  }
]
```

---

## 4. 평가 실행

### POST /api/t/{tenant}/runs

평가 배치 실행 요청. Mock 모드에서 구성을 검증하고 run_id를 반환.

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
  "methods": ["calibration", "diagnosis"],
  "target": "all",
  "temperature": 0.0
}
```

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `judges` | string[] | `["mock"]` | Judge LLM provider 목록 |
| `ensemble` | boolean | false | 앙상블 Judge 여부 |
| `levels` | string[] | `["turn"]` | 평가 레벨: retrieval/turn/session |
| `metrics` | string[] | `[]` | 평가 메트릭 (빈 배열 = 전체) |
| `methods` | string[] | `[]` | 고급 기법 옵션 |
| `target` | string | "all" | 평가 대상 범위 |
| `temperature` | float | 0.0 | Judge LLM 온도 |

**응답 200** — `RunEvalResult`
```json
{
  "run_id": "run_a1b2c3d4",
  "tenant_id": "acme",
  "status": "completed",
  "judges": ["anthropic"],
  "levels": ["turn"],
  "methods": [],
  "total_evaluations": 84,
  "message": "[mock] 구성 검증 완료 — 7개 대화 대상, Judge anthropic"
}
```

---

## 5. 대화 (Conversations)

### GET /api/t/{tenant}/conversations

테넌트의 대화 세션 목록 조회.

**Path Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `tenant` | string | 가입자 ID |

**응답 200** — `ConversationInfo[]`
```json
[
  {
    "conversation_id": "conv_001",
    "tenant_id": "acme",
    "subscriber_id": "sub_042",
    "is_multiturn": true,
    "turn_count": 6,
    "pending_review": 2,
    "started_at": "2026-06-01T09:00:00Z",
    "session_scores": {
      "faithfulness": 0.85,
      "answer_relevance": 0.78
    }
  }
]
```

---

### GET /api/conversations/{conversation_id}

대화 세션 상세 — 대화 타임라인 + 평가 결과.

**Path Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `conversation_id` | string | 세션 ID |

**응답 200** — `ConversationDetail`
```json
{
  "conversation_id": "conv_001",
  "tenant_id": "acme",
  "subscriber_id": "sub_042",
  "is_multiturn": true,
  "started_at": "2026-06-01T09:00:00Z",
  "turns": [
    { "role": "user", "content": "요금제 변경하려면 어떻게 해야 하나요?" },
    { "role": "bot",  "content": "요금제 변경은 마이페이지 > 요금제 관리에서 가능합니다." }
  ],
  "turn_evaluations": [
    {
      "eval_id": "eval_abc",
      "query": "요금제 변경하려면 어떻게 해야 하나요?",
      "scores": [
        { "metric": "faithfulness", "score": 0.92, "is_low_confidence": false }
      ]
    }
  ],
  "session_evaluation": null
}
```

**응답 404**
```json
{ "detail": "conversation not found: conv_001" }
```

---

## 6. 평가 (Evaluations)

### GET /api/t/{tenant}/evaluations

테넌트 평가 목록 (필터 지원).

**Path Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `tenant` | string | 가입자 ID |

**Query Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `level` | string | 필터: retrieval/turn/session |
| `metric` | string | 특정 메트릭 필터 |
| `review_status` | string | pending/approved/overridden/skipped |
| `low_confidence_only` | boolean | 저신뢰 항목만 |
| `below_metric` | string | 특정 메트릭 임계값 미달 필터 |
| `below_threshold` | float | 임계값 (0.0~1.0) |

**응답 200** — `EvaluationResponse[]`
```json
[
  {
    "eval_id": "eval_abc123",
    "qa_id": "qa_xyz",
    "call_id": "C001",
    "tenant_id": "acme",
    "conversation_id": "conv_001",
    "subscriber_id": "sub_042",
    "level": "turn",
    "turn_index": 2,
    "query": "요금제 변경하려면 어떻게 해야 하나요?",
    "generated_answer": "마이페이지 > 요금제 관리에서 변경 가능합니다.",
    "judge_provider": "anthropic",
    "judge_model": "claude-sonnet-4-5",
    "evaluated_at": "2026-06-01T10:30:00Z",
    "retrieval_result": {
      "query": "요금제 변경하려면...",
      "hyde_query": "요금제 변경은 마이페이지...",
      "sub_queries": ["요금제 바꾸는 방법", "플랜 변경 절차"],
      "contexts": [
        {
          "chunk_id": "chunk_001",
          "content": "요금제 변경은 마이페이지 > 요금제 관리에서 가능합니다.",
          "score": 0.94,
          "bm25_score": 0.78,
          "dense_score": 0.96,
          "source_call_id": "C001"
        }
      ]
    },
    "scores": [
      {
        "metric": "faithfulness",
        "score": 0.92,
        "reasoning": "답변은 제공된 컨텍스트에서 직접 도출되었습니다.",
        "grounding_chunks": ["chunk_001"],
        "confidence": 0.95,
        "sample_scores": [0.9, 0.95, 0.9],
        "is_low_confidence": false,
        "claims": [
          {
            "claim": "요금제 변경은 마이페이지에서 가능하다",
            "supported": true,
            "verdict": "supported",
            "reasoning": "컨텍스트에 명시됨"
          }
        ],
        "human_score": null,
        "final_score": 0.92
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

---

### GET /api/evaluations/{eval_id}

단일 평가 상세 (run 무관 조회 — 검수 워크스페이스용).

**응답 200** — `EvaluationResponse` (위와 동일)

---

### GET /api/runs/{run_id}/evaluations

배치 run의 평가 목록.

**Path Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `run_id` | string | Run ID 또는 `"latest"` |

**Query Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `call_id` | string | 특정 콜 필터 |
| `subscriber_id` | string | 특정 가입자 필터 |
| `flagged_only` | boolean | SLA 미달 콜만 |
| `low_confidence_only` | boolean | 저신뢰 항목만 |
| `below_metric` | string | 메트릭 이름 |
| `below_threshold` | float | 0.0~1.0 |

---

### GET /api/runs/{run_id}/evaluations/{eval_id}

배치 run의 단일 평가 상세.

---

## 7. 검수 (Human Review)

### POST /api/review/{eval_id}

휴먼 재평가 확정. Final Score 갱신 + 골든셋 적재.

**Path Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `eval_id` | string | 평가 ID |

**요청 본문** — `ReviewSubmit`
```json
{
  "status": "overridden",
  "human_scores": {
    "faithfulness": 0.60,
    "answer_relevance": 0.80
  },
  "labels": ["hallucination", "missing_context"],
  "comment": "컨텍스트에 없는 정보를 답변에 포함함",
  "reviewer": "reviewer@company.com"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `status` | string | approved / overridden / skipped |
| `human_scores` | dict | 수정할 메트릭별 점수 |
| `labels` | string[] | 오류 라벨 (hallucination/missing_context 등) |
| `comment` | string | 검수 의견 |
| `reviewer` | string | 검수자 이메일 |

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

검수 대기 평가 목록 (저신뢰·미검수 우선순위 정렬).

**응답 200** — `EvaluationResponse[]`

---

### GET /api/t/{tenant}/agreement

휴먼 vs 자동 평가 일치도 통계.

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

특정 메트릭의 휴먼·자동 표본 상세 (드릴다운).

**응답 200** — `AgreementSample[]`
```json
[
  {
    "eval_id": "eval_abc123",
    "conversation_id": "conv_001",
    "query": "요금제 변경하려면...",
    "review_status": "overridden",
    "reviewer": "reviewer@company.com",
    "auto_score": 0.92,
    "human_score": 0.60,
    "delta": -0.32,
    "agree": false
  }
]
```

---

## 8. 배치 요약 (Runs / Summary)

### GET /api/runs

배치 목록 조회.

**응답 200** — `RunInfo[]`
```json
[
  {
    "run_id": "run_a1b2c3d4",
    "generated_at": "2026-06-01T10:30:00Z",
    "total_calls": 15,
    "total_evaluations": 84,
    "flagged_count": 3,
    "has_summary": true
  }
]
```

---

### GET /api/runs/{run_id}/summary

배치 요약 통계 (AuditSummary).

**Path Parameters**: `run_id` — Run ID 또는 `"latest"`

**응답 200** — `AuditSummaryResponse`
```json
{
  "run_id": "run_a1b2c3d4",
  "generated_at": "2026-06-01T10:30:00Z",
  "total_calls": 15,
  "total_evaluations": 84,
  "flagged_call_ids": ["C005", "C012", "C019"],
  "metrics": [
    {
      "metric": "faithfulness",
      "mean": 0.847,
      "median": 0.870,
      "p10": 0.640,
      "p90": 0.960,
      "below_sla_count": 8,
      "total_count": 84,
      "sla_pass_rate": 0.905
    },
    {
      "metric": "answer_relevance",
      "mean": 0.791,
      "median": 0.810,
      "p10": 0.590,
      "p90": 0.940,
      "below_sla_count": 14,
      "total_count": 84,
      "sla_pass_rate": 0.833
    }
  ]
}
```

**응답 404**
```json
{ "detail": "summary not found: run_a1b2c3d4" }
```

---

## 9. 추이 (Trends)

### GET /api/t/{tenant}/trends

테넌트 메트릭 추이 (배치별 또는 일자별).

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
      "answer_relevance": 0.801,
      "context_precision": 0.735,
      "context_recall": 0.721
    }
  ],
  "metrics": ["faithfulness", "answer_relevance", "context_precision", "context_recall"]
}
```

---

### GET /api/runs/{run_id}/trends

전체 배치 누적 추이 (회귀 감지용).

---

## 10. 지식 베이스 (Knowledge Base)

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
      "conversation_id": "conv_007",
      "context_recall": 0.32
    }
  ]
}
```

---

## 11. 설정 (Settings)

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
    "openai": false
  },
  "slack_webhook": "",
  "notify_on_regression": true,
  "reviewers": ["reviewer@company.com"]
}
```

---

### PUT /api/t/{tenant}/settings

테넌트 설정 저장.

**요청 본문** — `TenantSettings` (위와 동일 구조)

**응답 200** — `TenantSettings` (저장 후 현재 값)

---

## 12. 오류 코드

| HTTP 상태 | 설명 | 예시 |
|---------|------|------|
| 200 | 성공 | — |
| 404 | 리소스 없음 | `{"detail": "evaluation not found: eval_xyz"}` |
| 422 | 유효성 검사 오류 | Pydantic 유효성 오류 상세 |
| 501 | 미구현 (실 LLM 실행) | `{"detail": "실 LLM 실행은 아직 미연결 (mock 모드만 지원)"}` |
| 500 | 서버 내부 오류 | — |

---

## 13. OpenAPI / TypeScript 타입 자동 생성

```bash
# OpenAPI JSON 추출
python scripts/export_openapi.py    # → frontend/openapi.json

# TypeScript 타입 자동 생성
cd frontend && npx openapi-typescript openapi.json -o src/types.gen.ts
```

`schemas.py` → `openapi.json` → `types.gen.ts` 체인으로 백엔드·프론트엔드 타입 계약이 자동 동기화된다.
