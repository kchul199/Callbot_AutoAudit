import { useState } from "react";
import { api, useFetch } from "../api/client";
import { MetricCard } from "../components/MetricCard";
import { TrendChart } from "../components/TrendChart";
import { EvaluationList } from "../components/EvaluationList";
import { EvidenceView } from "../components/EvidenceView";
import type { AuditSummary, EvaluationRecord, RunInfo, TrendData } from "../types";

type View =
  | { kind: "overview" }
  | { kind: "list"; metric?: string }
  | { kind: "evidence"; evalId: string };

export default function Dashboard() {
  const { data: runs } = useFetch<RunInfo[]>(() => api.runs(), []);
  const [runId, setRunId] = useState<string>("latest");
  const [view, setView] = useState<View>({ kind: "overview" });

  const effectiveRunId =
    runId === "latest" && runs && runs.length ? runs[0].run_id : runId;

  return (
    <>
      <header className="app-header">
        <h1>🤖 CallBot AutoAudit</h1>
        <span style={{ fontSize: 12, opacity: 0.8 }}>RAG 품질 자동 감사</span>
        <select
          value={runId}
          onChange={(e) => {
            setRunId(e.target.value);
            setView({ kind: "overview" });
          }}
        >
          <option value="latest">최신 실행</option>
          {runs?.map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {r.run_id} ({r.generated_at?.slice(0, 16) ?? "?"})
            </option>
          ))}
        </select>
      </header>

      <div className="container">
        {/* 브레드크럼 */}
        {view.kind !== "overview" && (
          <button className="link" onClick={() => setView({ kind: "overview" })}>
            ← 개요로
          </button>
        )}

        {view.kind === "overview" && (
          <Overview
            runId={effectiveRunId}
            onMetricClick={(m) => setView({ kind: "list", metric: m })}
            onSeeAll={() => setView({ kind: "list" })}
          />
        )}

        {view.kind === "list" && (
          <EvaluationList
            runId={effectiveRunId}
            initialMetric={view.metric}
            onSelect={(evalId) => setView({ kind: "evidence", evalId })}
          />
        )}

        {view.kind === "evidence" && (
          <EvidenceLoader
            runId={effectiveRunId}
            evalId={view.evalId}
            onBack={() => setView({ kind: "list" })}
          />
        )}
      </div>
    </>
  );
}

function Overview({
  runId,
  onMetricClick,
  onSeeAll,
}: {
  runId: string;
  onMetricClick: (metric: string) => void;
  onSeeAll: () => void;
}) {
  const { data: summary, loading, error } = useFetch<AuditSummary>(
    () => api.summary(runId),
    [runId]
  );
  const { data: trends } = useFetch<TrendData>(() => api.trends(runId), [runId]);

  if (loading) return <div className="loading">로딩 중…</div>;
  if (error) return <div className="error">{error}</div>;
  if (!summary) return <div className="loading">데이터 없음</div>;

  return (
    <>
      <div className="card">
        <div className="stat-row">
          <div className="stat">
            <div className="num">{summary.total_calls}</div>
            <div className="label">총 콜 수</div>
          </div>
          <div className="stat">
            <div className="num">{summary.total_evaluations}</div>
            <div className="label">총 평가 수</div>
          </div>
          <div className="stat" style={{ background: "var(--bad)" }}>
            <div className="num">{(summary.flagged_call_ids ?? []).length}</div>
            <div className="label">SLA 미달 콜</div>
          </div>
        </div>
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
          Run {summary.run_id} · {summary.generated_at?.slice(0, 19).replace("T", " ")}
        </p>
      </div>

      <div className="card">
        <h4 style={{ marginTop: 0 }}>
          📈 메트릭 현황 <span className="muted" style={{ fontSize: 12 }}>(클릭 → 드릴다운)</span>
        </h4>
        <div className="metric-grid">
          {(summary.metrics ?? []).map((m) => (
            <MetricCard key={m.metric} metric={m} onClick={() => onMetricClick(m.metric)} />
          ))}
        </div>
      </div>

      <div className="card">
        <h4 style={{ marginTop: 0 }}>📉 품질 추이 (회귀 감지)</h4>
        {trends && <TrendChart data={trends} />}
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h4 style={{ margin: 0 }}>🚨 SLA 미달 콜</h4>
          <button className="btn" onClick={onSeeAll}>
            전체 평가 보기
          </button>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
          {(summary.flagged_call_ids ?? []).length === 0 && <span className="muted">없음</span>}
          {(summary.flagged_call_ids ?? []).map((id) => (
            <span key={id} className="badge bad">
              {id}
            </span>
          ))}
        </div>
      </div>
    </>
  );
}

function EvidenceLoader({
  runId,
  evalId,
  onBack,
}: {
  runId: string;
  evalId: string;
  onBack: () => void;
}) {
  const { data, loading, error } = useFetch<EvaluationRecord>(
    () => api.evaluation(runId, evalId),
    [runId, evalId]
  );
  if (loading) return <div className="loading">로딩 중…</div>;
  if (error) return <div className="error">{error}</div>;
  if (!data) return <div className="loading">데이터 없음</div>;
  return <EvidenceView record={data} onBack={onBack} />;
}
