# 콜봇 답변 품질 평가 관리도구 — 기획서 (PRD + IA + 화면 정의)

> CallBot AutoAudit — SaaS 생성형 콜봇의 답변 품질 평가 & 휴먼 재평가 콘솔
> 상태: 기획 (구현 전 합의용) · v2 · 작성 2026-06

---

## 1. 제품 배경

우리는 **SaaS형 생성형 콜봇**을 운영한다.
- 가입자(고객사)들이 자사 지식 데이터(문서·웹사이트 등)를 올리면 가입자별 **RAG 시스템**이 구축된다.
- 최종 사용자(가입자의 고객)가 문의하면 → RAG 검색 → LLM이 답변 생성.
- 답변은 **싱글턴**(1문1답)일 수도, **멀티턴**(맥락 누적 대화)일 수도 있다.

**해결하려는 문제:**
운영 중인 콜봇이 "좋은 답변을 하고 있는가?"를 가입자별로 측정하고, 자동 평가(LLM-as-a-Judge)
결과를 **사람이 검토·수정·확정**하여 신뢰할 수 있는 품질 지표를 만드는 것.

> 핵심: 이 도구는 단순 대시보드가 아니라 **"자동 평가 → 휴먼 재평가 → 확정"** 워크플로우 도구다.

---

## 2. 핵심 도메인 모델 (용어 정의)

| 용어 | 의미 |
|------|------|
| **Tenant(가입자)** | 콜봇을 운영하는 고객사. KB·대화이력·평가가 tenant 단위로 격리 |
| **Knowledge Base** | tenant가 올린 지식 데이터로 만든 RAG 인덱스 |
| **Conversation(세션)** | 한 최종사용자와의 대화 단위. 싱글턴 또는 멀티턴 |
| **Turn(턴)** | 세션 내 1개 (사용자질문 → 콜봇답변) 쌍. 평가의 최소 단위 |
| **Auto Evaluation** | LLM-as-a-Judge가 산출한 턴/세션 평가 (faithfulness 등) |
| **Human Review** | 사람이 자동 평가를 검토하여 점수를 **재평가/확정**한 결과 |
| **Final Score** | 휴먼 확정값이 있으면 그것, 없으면 자동 평가값 (= 신뢰 가능한 최종 지표) |

**평가의 3단 구조 (싱글턴/멀티턴 + 검색 대응):**
- **Retrieval 레벨** ★신규: RAG가 검색해 온 컨텍스트 자체의 품질 — context_precision(검색 정확도),
  context_recall(누락 없는지, nugget 기반), 순위 적절성(MRR/관련성). 답변 생성과 무관하게
  "검색이 좋은 문서를 가져왔는가"를 단독 평가
- **턴(답변) 레벨**: faithfulness(환각), answer_relevance — 생성된 답변 1건의 품질
- **세션 레벨**: 멀티턴 일관성, 목표 달성(해결 여부), 대화 흐름 — 대화 전체의 품질
- 싱글턴 = 턴 1개짜리 세션으로 동일하게 취급 (세션 레벨은 일부 메트릭 생략)

> 검색 평가를 분리하는 이유: "검색이 문제냐 / 생성이 문제냐"를 진단(2×2)하려면
> Retrieval 품질과 답변 품질을 독립적으로 측정해야 한다.

---

## 2.5 평가 구성 (Evaluation Configuration) ★핵심 요구

평가를 실행할 때마다 **무엇으로·어떻게 평가할지**를 구성한다. 이 구성은 평가배치 단위로 저장·재현된다.

### A) Judge 모델 선택 (다중 LLM)
평가자(LLM-as-a-Judge)를 자유롭게 고른다. provider 추상화(`core/llm_client.py`)로 이미 교체 가능.
- **Anthropic Claude** (claude-sonnet/opus 등)
- **OpenAI** (gpt-4o, o-series 등)
- **Google Gemini** (gemini-2.x) ★신규 provider 추가 필요
- **Azure OpenAI**, 로컬/오픈모델(선택), **Mock**(개발용)

선택 방식:
- **단일 Judge**: 한 모델로 평가 (기본, 저비용)
- **앙상블 Judge**: 2개 이상 모델로 평가 → 불일치 시 메타 판정 에스컬레이션 (정확도↑)
- 모델별 temperature·max_tokens·예산 상한 설정

### B) 평가 대상 레벨 선택
Retrieval / 턴(답변) / 세션 중 무엇을 평가할지 체크. (검색만 평가 / 답변만 / 전체 등)

### C) 메트릭 선택
레벨별 메트릭 on/off. 예) Retrieval=precision+recall, 턴=faithfulness+relevance, 세션=일관성+해결여부.

### D) 정확도 향상 방법론 옵션 (9종 토글) ★핵심 요구
검증 품질의 정확도를 높이는 기법을 평가 시 선택. (이미 구현된 `EvaluationOptions`)
| 옵션 | 효과 |
|------|------|
| **calibration** | 편향보정(앵커/길이정규화) + G-Eval logprob 기대점수 |
| **ensemble** | 다중 LLM 교차 평가 + 불일치 에스컬레이션 |
| **claim 분해(RAGAS)** | 답변을 claim 단위로 쪼개 NLI 판정 → 환각 정밀 탐지 |
| **nugget recall** | 질문→핵심정보 추출 후 검색 컨텍스트 매칭 (GT-free recall) |
| **multi-sample** | N회 샘플링 → 중앙값 + 분산 기반 신뢰도 |
| **diagnosis** | 검색 vs 생성 책임 2×2 자동 진단 |
| **statistics** | 부트스트랩 신뢰구간 + 유의성 회귀 |
| **routing** | 저신뢰 항목 추가샘플 + 인간검수 큐 |
| **domain** | 멀티턴 일관성 + PII/컴플라이언스 안전성 |

### E) 프리셋 프로필
자주 쓰는 조합을 저장. 예: "빠른 점검"(단일 Judge·기본 메트릭), "고신뢰"(앙상블+calibration+claim+statistics),
"검색 진단"(Retrieval 레벨+nugget+diagnosis), "안전성 감사"(domain+claim).

---

## 3. 페르소나 & Job-to-be-done

| 페르소나 | 목표 | 매일 하는 일 |
|---------|------|------------|
| **QA 운영자 (검수자)** | 자동 평가 검수·재평가하여 품질 확정 | 검수 큐 → 대화 열람 → 점수 확정/수정 |
| **RAG 엔지니어** | 가입자별 검색/생성 병목 진단·개선 검증 | 실패분석 → 진단 매트릭스 → 개선 전후 비교 |
| **팀 리드 / CSM** | 가입자별 품질 추이·회귀 파악, 고객 보고 | tenant별 요약 대시보드 → 리포트 |

3개 페르소나 모두 **tenant 컨텍스트** 위에서 작동한다 (어느 가입자의 콜봇을 보는지 항상 선택).

---

## 4. 정보 구조 (IA)

```
관리도구
├── (전역) Tenant 선택기 ──────────── 모든 화면이 선택된 가입자 컨텍스트로 동작
│
├── 🏠 Overview
│     선택 tenant의 품질 요약: KPI · 메트릭 추이 · 회귀 · 진단 분포 · 검수 진척
│
├── 💬 Conversations (대화이력)        ← 평가 대상의 원천
│     ├── 세션 목록 (싱글턴/멀티턴 뱃지 · 최종점수 · 검수상태 · 일시)
│     └── 세션 상세 (멀티턴 타임라인: 턴별 질문/답변 + 턴별 점수 + 세션 점수)
│
├── ✅ Review (휴먼 재평가)             ← 이 도구의 핵심
│     ├── 검수 큐 (우선순위: 저신뢰·SLA경계·미검수)
│     └── 재평가 워크스페이스
│           좌: 대화(턴/세션) · 검색컨텍스트 · 근거
│           우: 자동 평가 점수 → 사람이 수정/승인 → 확정 → 다음 건
│
├── 🔍 Evaluations (탐색/분석)         ← 실패 케이스 분석
│     필터 테이블(레벨·메트릭·SLA·신뢰도·진단·검수상태) → Evidence 상세
│
├── ▶ Run Evaluation (평가 실행)        ← 평가 구성 & 실행
│     ├── 평가배치 목록 (구성/Judge모델/옵션/상태)
│     └── New Run: 대화 선택 → Judge모델 선택 → 레벨/메트릭 → 방법론 옵션 → 실행/진행
│
├── 📈 Trends (추이/회귀)              ← 가입자 품질 리포트
│     tenant별 시계열 · 평가배치 A/B 비교 · 유의성 회귀 · 휴먼vs자동 일치도
│
├── 📚 Knowledge Base (참고)
│     tenant KB 현황(문서 수·인덱스 갱신일) — 검색 실패 원인 추적용
│
└── ⚙️ Settings
      tenant별 SLA 임계값 · 평가 옵션 프로필 · 알림 · 검수자 배정
```

상단바: 로고 · **Tenant 선택기** · 평가배치 선택기 · 환경(mock/prod) 뱃지
좌측 사이드바: 위 섹션들 (접힘 지원)

---

## 5. 화면 정의서

### 5.1 Overview (`/t/:tenant`)
선택한 가입자 콜봇의 품질을 30초에 파악.
- **KPI 카드**: 총 세션 · 총 턴(평가) · SLA 미달 · **검수 대기** · **휴먼 확정률** (전기간 대비 증감)
- **회귀 배너**: 직전 평가배치 대비 유의 하락 시 경고
- **메트릭 카드**: 턴/세션 레벨 분리 탭. SLA 통과율 + 신뢰구간 → 클릭 시 드릴다운
- **진단 도넛**: healthy / 검색실패 / 환각 / 복합
- **검수 진척 바**: "이번 배치 자동평가 320건 중 검수 완료 45건"
- 품질 추이 미니 차트

### 5.2 Conversations (`/t/:tenant/conversations`, `/conversations/:id`)
평가 대상인 실제 고객 대화이력.
- **목록**: 세션ID · 최종사용자 · **싱글턴/멀티턴 뱃지** · 턴 수 · 세션 최종점수 · 검수상태 · 일시. 필터(기간·점수·검수상태)
- **세션 상세 (멀티턴 타임라인)**:
  - 위→아래 대화 흐름. 각 **턴**: 사용자질문 → 콜봇답변 + 턴별 점수 뱃지 + 답변 근거 하이라이트
  - 턴 클릭 → 검색 컨텍스트(dense/bm25/rerank) + claim 분해
  - 상단: **세션 레벨 점수**(일관성·해결여부) + "이 세션 검수하기" 버튼
  - 싱글턴이면 턴 1개만, 세션 메트릭은 간소화

### 5.3 Review — 휴먼 재평가 (`/t/:tenant/review`) ★ 핵심
- **검수 큐**: active sampling 우선순위(저신뢰·SLA경계·미검수 우선) + 수동 필터. "내 담당" 배정
- **재평가 워크스페이스** (2-pane):
  - **좌측**: 대화 컨텍스트 (싱글턴=턴, 멀티턴=세션 타임라인) + 검색된 근거 + 자동 평가 근거(claim ✅/❌)
  - **우측**: 메트릭별 **자동 점수 표시 → 사람이 [동의] 또는 [수정]** + 코멘트 + 라벨(환각/검색실패/양호 등)
  - 액션: **확정(Approve)** / **수정 후 확정(Override)** / **보류(Skip)** → 자동으로 다음 건
  - 확정 결과는 ① Final Score 갱신 ② 골든셋(jsonl) 적재 ③ meta_eval(자동 vs 휴먼 일치도) 자동 갱신
  - 단축키 지원(승인 A / 수정 E / 다음 J) — 검수 속도 최적화
- **진척**: "45/320 검수 · 평균 23초/건 · 자동평가와 일치율 82%"

### 5.4 Evaluations (`/t/:tenant/evaluations`)
- 필터: 레벨(턴/세션) · 메트릭 · SLA미달 · 낮은신뢰도 · 진단 · 검수상태(미검수/확정/수정됨)
- 테이블: 세션·턴 · 질문 · 메트릭 점수(자동/휴먼 병기) · 진단 · 검수상태
- 행 클릭 → Evidence 상세 (= 세션 상세의 해당 턴으로 이동)

### 5.5 Run Evaluation — 평가 구성 & 실행 (`/t/:tenant/runs`, `/runs/new`)
평가배치 목록 + 새 평가 실행. §2.5 평가 구성을 UI로 노출.
- **배치 목록**: 배치ID · Judge 모델(뱃지) · 활성 방법론 옵션 · 대상 레벨 · 상태 · 일시
- **New Run 마법사** (스텝):
  1. **대상 선택**: 평가할 대화(세션) 범위 — 기간/미평가만/특정 세션
  2. **Judge 모델**: provider 드롭다운(Claude/OpenAI/Gemini/Azure/Mock) + 모델·temperature.
     "앙상블" 토글 시 2개+ 모델 선택 + 불일치 임계값
  3. **평가 레벨**: ☑ Retrieval ☑ 턴(답변) ☑ 세션
  4. **메트릭**: 레벨별 체크박스
  5. **정확도 방법론**: 9종 토글(§2.5 D) — 또는 프리셋 선택
  6. **검토 & 실행**: mock이면 "비용 $0", prod면 예상 비용·소요 추정 → 실행
  - 실행 중 **진행 스트리밍**(CP1~CP6 단계 + 처리 건수) → 완료 시 배치 상세로
- **배치 상세**: 구성 요약(어떤 모델·옵션으로 평가했는지 재현 가능) + 결과 메트릭 + 다운로드

### 5.6 Trends (`/t/:tenant/trends`)
- tenant 메트릭 시계열(평가배치 누적, CI 밴드)
- **배치 A/B 비교**: 두 평가배치 선택 → 메트릭 diff + 순열검정 p-value + 유의 회귀
- **Judge 모델 비교**: 같은 대화를 Claude vs OpenAI vs Gemini로 평가한 점수 차이 (모델 편향 파악)
- **휴먼 vs 자동 일치도** 추이 (Spearman/κ) — Judge별 신뢰도 모니터링
- 회귀 이력 타임라인

### 5.7 Knowledge Base (`/t/:tenant/kb`)
- tenant KB 현황: 문서 수, 청크 수, 인덱스 갱신일, 소스 유형(문서/웹)
- 검색 실패가 잦은 질의 → KB 커버리지 갭 힌트 (엔지니어용)

### 5.8 Settings (`/t/:tenant/settings`)
- tenant별 SLA 임계값 · 평가 옵션 프로필(신뢰성/저비용/안전성) · 알림(Slack)
- **Judge 모델 자격증명**: provider별 API 키 등록(Claude/OpenAI/Gemini/Azure) · 기본 Judge 지정
- 검수자 배정

---

## 6. 핵심 워크플로우: 자동 평가 → 휴먼 재평가 → 확정

```
1. 대화이력 수집      운영 콜봇 로그가 tenant별로 적재 (배치 또는 실시간)
2. 자동 평가(배치)    CP1~CP5 파이프라인 → 턴/세션 점수 + 진단 + 검수 우선순위
3. 검수 큐 생성       저신뢰·SLA경계·미검수 항목을 우선순위로 큐잉
4. 휴먼 재평가        검수자가 워크스페이스에서 동의/수정/보류 → Final Score 확정
5. 환류              확정값 → 골든셋 → meta_eval(자동 vs 휴먼 일치도) → Judge 개선
6. 리포트            tenant별 확정 기준 품질 지표를 CSM이 고객에 보고
```

Final Score = `human_score if reviewed else auto_score` — 모든 집계·리포트는 Final 기준.

---

## 7. 디자인 시스템 (모던 SaaS)

- **레이아웃**: 좌측 사이드바(240px/접힘64) + 상단바(56px, tenant 선택기) + 컨텐츠
- **컬러**: 중립 slate + 액센트 indigo + 시맨틱(ok/warn/bad) · 다크모드(CSS 변수)
- **핵심 컴포넌트**: TenantSwitcher, StatCard, DataTable(정렬/필터/페이지), ConversationTimeline(멀티턴), ReviewWorkspace(2-pane), ScoreEditor(자동→휴먼), Badge, Drawer, Toast, ProgressSteps, EmptyState, Skeleton
- **접근성**: 색+아이콘 병행, 키보드(검수 단축키), WCAG AA
- **반응형**: 데스크톱 우선(검수는 와이드), 태블릿 대응

---

## 8. 백엔드 API 갭

기존: `/runs`, `/runs/:id/summary`, `/evaluations`, `/evaluations/:id`, `/trends`

신규 필요 (tenant 컨텍스트 + 다중 LLM 평가 + 휴먼 재평가 중심):
| 엔드포인트 | 용도 |
|-----------|------|
| `GET /api/tenants` | 가입자 목록/전환 |
| `GET /api/judges` | 사용 가능한 Judge 모델/provider 목록(자격증명 등록 여부 포함) |
| `GET /api/t/:tenant/conversations` | 세션 목록(싱글/멀티턴) |
| `GET /api/conversations/:id` | 세션 상세(턴 타임라인 + Retrieval/턴/세션 점수) |
| `POST /api/t/:tenant/runs` | 평가 실행(구성: Judge모델·레벨·메트릭·방법론옵션) |
| `GET /api/runs/:id/progress` (SSE) | 실행 진행률 스트리밍 |
| `GET /api/t/:tenant/review-queue` | 검수 큐(우선순위) |
| `POST /api/review/:eval_id` | 휴먼 재평가 확정(동의/수정/보류) → Final + 골든셋 |
| `GET /api/t/:tenant/kb` | KB 현황 |
| `GET /api/t/:tenant/agreement` | 휴먼 vs 자동 일치도(Judge별) |
| `GET /api/runs/compare?a=&b=` | 배치/모델 A/B 유의성 비교 |
| `GET/PUT /api/t/:tenant/settings` | tenant 설정 + Judge 자격증명 |

**백엔드 코드 갭:**
- **Gemini provider 추가** (`core/llm_client.py`에 `GoogleProvider`) — 팩토리 `provider == "gemini"`
- **Retrieval 레벨 평가 분리** — context 평가를 답변과 독립 실행하는 경로 (nugget recall + precision)
- **세션 레벨 평가기** — 멀티턴 일관성(있음) + "해결 여부/목표 달성" Judge 신규
- **평가배치(EvalRun) 엔티티** — 구성(Judge·옵션·레벨)을 저장해 재현 가능하게

**데이터 모델 확장** (기존 EvaluationRecord에 추가):
- `tenant_id`, `conversation_id`, `turn_index`, `level`(retrieval/turn/session)
- `judge_provider`, `judge_model` (어떤 LLM이 평가했는지), `eval_run_id`(배치)
- `human_score`, `human_label`, `review_status`(pending/approved/overridden/skipped), `reviewer`, `reviewed_at`
- `final_score` (파생: human 우선)
- **EvalRun**: `run_id`, `tenant_id`, `config`(judge·레벨·메트릭·옵션 JSON), `status`, `created_at`

---

## 9. 구현 로드맵

| 단계 | 내용 |
|------|------|
| **M0 백엔드 확장** | Gemini provider 추가 · Retrieval/세션 레벨 평가기 · EvalRun 엔티티 · 데이터모델(tenant/level/judge/human) |
| **M1 셸+테넌시** | 사이드바/상단바, 라우팅, 디자인토큰, 다크모드, Tenant 선택기 |
| **M2 대화이력** | Conversations 목록 + 세션 상세(멀티턴 타임라인) + Retrieval/턴/세션 점수 |
| **M3 평가 실행** | Run Evaluation 마법사(Judge모델·레벨·메트릭·방법론 옵션) + 실행/진행 API ★ |
| **M4 휴먼 재평가** | Review 큐 + 2-pane 워크스페이스 + 확정 API + 골든셋 환류 ★ |
| **M5 분석/추이** | Evaluations 탐색 + Trends(배치/모델 A/B + 휴먼·자동 일치도) |
| **M6 KB/설정/마감** | KB 현황 + Settings(Judge 자격증명) + 반응형·접근성·테스트 |

---

## 10. 결정 필요 사항 (구현 전 확인)

1. **멀티테넌시 데이터 격리 수준**: 단일 DB에 tenant_id 컬럼 분리 vs tenant별 DB/디렉토리?
2. **대화이력 유입 경로**: 운영 콜봇 로그를 배치 업로드? API 연동? 현재는 `data/raw` 파일 기반
3. **인증/권한**: 검수자 계정·역할(RBAC)이 이번 범위인가? (검수 "담당 배정"이 있으려면 사용자 개념 필요)
4. **세션 레벨 메트릭 정의**: 멀티턴 "목표 달성/해결 여부"를 어떻게 판정할지 (LLM Judge 프롬프트 추가 필요)
5. **실시간성**: 자동 평가는 배치 주기? 대화 종료 즉시?

---

## 11. 비범위 (이번 제외)

- 콜봇 자체(생성)·RAG 구축 파이프라인 — 이 도구는 **평가/검수** 전용
- 가입자 셀프서비스 포털 (운영자 내부 도구로 한정)
- 청구/과금
