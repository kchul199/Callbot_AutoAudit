// 경량 API 클라이언트 + useFetch 훅
// (운영 확장 시 TanStack Query로 교체 — 캐싱/리트라이/무효화 표준화)
import { useEffect, useState } from "react";
import type {
  AgreementResult,
  AgreementSample,
  AuditConversationSubmit,
  AuditRunResult,
  AuditSummary,
  ConversationDetail,
  ConversationInfo,
  CredentialSubmit,
  EvaluationRecord,
  JudgeModel,
  KbDocumentSubmit,
  KbStatus,
  ReviewResult,
  ReviewSubmit,
  RunEvalConfig,
  RunEvalResult,
  RunInfo,
  Tenant,
  TenantSettings,
  TrendData,
} from "../types";

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return (await res.json()) as T;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return (await res.json()) as T;
}

async function delJSON<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return (await res.json()) as T;
}

const BASE = "/api";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return (await res.json()) as T;
}

export const api = {
  tenants: () => getJSON<Tenant[]>("/tenants"),
  judges: () => getJSON<JudgeModel[]>("/judges"),
  createRun: (tenantId: string, config: RunEvalConfig) =>
    postJSON<RunEvalResult>(`/t/${tenantId}/runs`, config),
  conversations: (tenantId: string) =>
    getJSON<ConversationInfo[]>(`/t/${tenantId}/conversations`),
  conversation: (conversationId: string) =>
    getJSON<ConversationDetail>(`/conversations/${conversationId}`),
  reviewQueue: (tenantId: string) =>
    getJSON<EvaluationRecord[]>(`/t/${tenantId}/review-queue`),
  submitReview: (evalId: string, body: ReviewSubmit) =>
    postJSON<ReviewResult>(`/review/${evalId}`, body),
  exploreEvaluations: (tenantId: string, params: Record<string, string | boolean | number | undefined> = {}) => {
    const qs = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== "" && v !== false)
      .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
      .join("&");
    return getJSON<EvaluationRecord[]>(`/t/${tenantId}/evaluations${qs ? `?${qs}` : ""}`);
  },
  tenantTrends: (tenantId: string, params: Record<string, string | undefined> = {}) => {
    const qs = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
      .join("&");
    return getJSON<TrendData>(`/t/${tenantId}/trends${qs ? `?${qs}` : ""}`);
  },
  agreement: (tenantId: string) => getJSON<AgreementResult>(`/t/${tenantId}/agreement`),
  agreementSamples: (tenantId: string, metric: string) =>
    getJSON<AgreementSample[]>(`/t/${tenantId}/agreement/${metric}`),
  kb: (tenantId: string) => getJSON<KbStatus>(`/t/${tenantId}/kb`),
  kbAddDocument: (tenantId: string, body: KbDocumentSubmit) =>
    postJSON<KbStatus>(`/t/${tenantId}/kb/documents`, body),
  kbDeleteDocument: (tenantId: string, docId: string) =>
    delJSON<KbStatus>(`/t/${tenantId}/kb/documents/${docId}`),
  kbUploadDocuments: async (tenantId: string, files: File[], sourceType = ""): Promise<KbStatus> => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    if (sourceType) fd.append("source_type", sourceType);
    const res = await fetch(`/api/t/${tenantId}/kb/documents/upload`, { method: "POST", body: fd });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try { detail = (await res.json()).detail ?? detail; } catch { /* noop */ }
      throw new Error(detail);
    }
    return (await res.json()) as KbStatus;
  },
  auditConversation: (tenantId: string, body: AuditConversationSubmit) =>
    postJSON<AuditRunResult>(`/t/${tenantId}/audit-conversation`, body),
  auditConversationUpload: async (tenantId: string, file: File, conversationId = ""): Promise<AuditRunResult> => {
    const fd = new FormData();
    fd.append("file", file);
    if (conversationId) fd.append("conversation_id", conversationId);
    const res = await fetch(`/api/t/${tenantId}/audit-conversation/upload`, { method: "POST", body: fd });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try { detail = (await res.json()).detail ?? detail; } catch { /* noop */ }
      throw new Error(detail);
    }
    return (await res.json()) as AuditRunResult;
  },
  settings: (tenantId: string) => getJSON<TenantSettings>(`/t/${tenantId}/settings`),
  saveSettings: (tenantId: string, s: TenantSettings) =>
    putJSON<TenantSettings>(`/t/${tenantId}/settings`, s),
  saveCredential: (tenantId: string, provider: string, body: CredentialSubmit) =>
    putJSON<TenantSettings>(`/t/${tenantId}/credentials/${provider}`, body),
  deleteCredential: (tenantId: string, provider: string) =>
    delJSON<TenantSettings>(`/t/${tenantId}/credentials/${provider}`),
  runs: () => getJSON<RunInfo[]>("/runs"),
  summary: (runId: string) => getJSON<AuditSummary>(`/runs/${runId}/summary`),
  trends: (runId: string) => getJSON<TrendData>(`/runs/${runId}/trends`),
  evaluations: (runId: string, params: Record<string, string | boolean | number | undefined> = {}) => {
    const qs = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== "" && v !== false)
      .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
      .join("&");
    return getJSON<EvaluationRecord[]>(`/runs/${runId}/evaluations${qs ? `?${qs}` : ""}`);
  },
  evaluation: (runId: string, evalId: string) =>
    getJSON<EvaluationRecord>(`/runs/${runId}/evaluations/${evalId}`),
  evaluationById: (evalId: string) =>
    getJSON<EvaluationRecord>(`/evaluations/${evalId}`),
};

export function useFetch<T>(fn: () => Promise<T>, deps: unknown[]): {
  data: T | null;
  loading: boolean;
  error: string | null;
} {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error };
}
