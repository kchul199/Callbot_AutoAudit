// 백엔드 OpenAPI에서 자동 생성된 타입(types.gen.ts)을 앱 친화적 별칭으로 재노출.
//
// 재생성: `npm run gen:api` (FastAPI OpenAPI → openapi.json → types.gen.ts)
// 수동 편집 금지 대상은 types.gen.ts 이며, 이 파일은 별칭만 유지한다.
import type { components } from "./types.gen";

type S = components["schemas"];

export type MetricScore = S["MetricScoreResponse"];
export type ClaimVerdict = S["ClaimVerdict"];
export type RetrievedContext = S["RetrievedContextResponse"];
export type RetrievalResult = S["RetrievalResultResponse"];
export type EvaluationRecord = S["EvaluationResponse"];
export type AggregatedMetric = S["MetricSummary"];
export type AuditSummary = S["AuditSummaryResponse"];
export type RunInfo = S["RunInfo"];
export type TrendData = S["TrendsResponse"];
export type ConversationInfo = S["ConversationInfo"];
export type ConversationDetail = S["ConversationDetail"];
export type JudgeModel = S["JudgeModel"];
export type RunEvalConfig = S["RunEvalConfig"];
export type RunEvalResult = S["RunEvalResult"];
export type ReviewSubmit = S["ReviewSubmit"];
export type ReviewResult = S["ReviewResult"];
export type AgreementResult = S["AgreementResult"];
export type AgreementSample = S["AgreementSample"];
export type KbStatus = S["KbStatus"];
export type TenantSettings = S["TenantSettings"];

// 수동 타입 (백엔드 스키마 추가 후 gen:api로 자동화 예정)
export interface Tenant {
  tenant_id: string;
  name: string;
  conversation_count?: number;
  pending_review_count?: number;
}
