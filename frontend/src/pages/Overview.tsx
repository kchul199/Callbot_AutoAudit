import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import { useTenant } from "../app/TenantContext";
import { EmptyState } from "../components/EmptyState";
import { ScoreBadge } from "../components/ScoreBadge";
import { TrendChart } from "../components/TrendChart";
import type { AuditSummary, EvaluationRecord, RunInfo, TrendData } from "../types";

// 드릴 대상 정의
type Drill =
  | { kind: "calls" }
  | { kind: "evals" }
  | { kind: "flagged" }
  | { kind: "pending" }
  | { kind: "metric"; metric: string };

function StatCard({
  label, value, tone, active, onClick,
}: {
  label: string; value: number | string;
  tone?: "bad" | "warn" | "ok"; active?: boolean; onClick?: () => void;
}) {
  const color =
    tone === "bad" ? "var(--bad)" : tone === "warn" ? "var(--warn)" : tone === "ok" ? "var(--ok)" : "var(--text)";
  return (
    <div
      className="card card-pad"
      onClick={onClick}
      style={{
        flex: 1, minWidth: 150, cursor: onClick ? "pointer" : "default",
        border: active ? "1px solid var(--accent)" : undefined,
        boxShadow: active ? "0 0 0 3px var(--accent-soft)" : undefined,
        transition: "box-shadow .12s, border-color .12s",
      }}
    >
      <div style={{ fontSize: 28, fontWeight: 700, color }}>{value}</div>
      <div className="muted" style={{ fontSize: 12.5, display: "flex", alignItems: "center", gap: 5 }}>
        {label}
        {onClick && <span className="faint" style={{ fontSize: 11 }}>{active ? "▲" : "▾"}</span>}
      </div>
    </div>
  );
}

function metricTone(rate: number) {
  return rate >= 0.9 ? "ok" : rate >= 0.7 ? "warn" : "bad";
}

export default function Overview() {
  const { current } = useTenant();
  const { tenant } = useParams();
  const navigate = useNavigate();
  const { data: runs } = useFetch<RunInfo[]>(() => api.runs(), [current?.tenant_id]);
  const latestId = runs && runs.length ? runs[0].run_id : null;

  const { data: summary, loading } = useFetch<AuditSummary | null>(
    () => (latestId ? api.summary(latestId) : Promise.resolve(null)),
    [latestId]
  );
  const { data: trends } = useFetch<TrendData | null>(
    () => (latestId ? api.trends(latestId) : Promise.resolve(null)),
    [latestId]
  );

  const [drill, setDrill] = useState<Drill | null>(null);
  const flagged = useMemo(() => summary?.flagged_call_ids ?? [], [summary]);

  // 같은 카드 다시 누르면 닫기(out), 다른 카드 누르면 전환
  const toggle = (d: Drill) =>
    setDrill((cur) => (cur && drillKey(cur) === drillKey(d) ? null : d));

  if (loading) return <div className="content"><div className="muted" style={{ padding: 40 }}>로딩 중…</div></div>;
  if (!summary)
    return (
      <div className="content">
        <EmptyState icon="🏠" title="아직 평가 데이터가 없습니다"
          desc="평가배치를 실행하면 이 가입자의 품질 요약이 여기에 표시됩니다." />
      </div>
    );

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <div className="sub">
            {current?.name} · 최근 배치 <span className="mono">{summary.run_id}</span>
          </div>
        </div>
      </div>

      {/* KPI */}
      <div className="row" style={{ marginBottom: 18, flexWrap: "wrap" }}>
        <StatCard label="총 콜(세션)" value={summary.total_calls}
          active={drill?.kind === "calls"} onClick={() => toggle({ kind: "calls" })} />
        <StatCard label="총 평가(턴)" value={summary.total_evaluations}
          active={drill?.kind === "evals"} onClick={() => toggle({ kind: "evals" })} />
        <StatCard label="SLA 미달 콜" value={flagged.length} tone="bad"
          active={drill?.kind === "flagged"} onClick={() => toggle({ kind: "flagged" })} />
        <StatCard label="검수 대기" value={current?.pending_review_count ?? 0} tone="warn"
          active={drill?.kind === "pending"} onClick={() => toggle({ kind: "pending" })} />
      </div>

      {/* KPI 드릴 패널 */}
      {drill && drill.kind !== "metric" && (
        <DrillPanel
          drill={drill} runId={latestId!} tenant={tenant!}
          onClose={() => setDrill(null)} navigate={navigate}
        />
      )}

      {/* 메트릭 카드 */}
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <h3>메트릭 현황</h3>
        <div className="card-sub">SLA 통과율 (카드 클릭 시 미달 평가 드릴인)</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 12 }}>
          {(summary.metrics ?? []).map((m) => {
            const tone = metricTone(m.sla_pass_rate);
            const c = tone === "ok" ? "var(--ok)" : tone === "warn" ? "var(--warn)" : "var(--bad)";
            const active = drill?.kind === "metric" && drill.metric === m.metric;
            return (
              <div key={m.metric} className="card card-pad"
                onClick={() => toggle({ kind: "metric", metric: m.metric })}
                style={{
                  boxShadow: active ? "0 0 0 3px var(--accent-soft)" : "none",
                  border: active ? "1px solid var(--accent)" : undefined,
                  cursor: "pointer",
                }}>
                <div className="muted" style={{ fontSize: 12.5 }}>
                  {tone === "ok" ? "✅" : tone === "warn" ? "⚠️" : "❌"} {m.metric}
                </div>
                <div style={{ fontSize: 24, fontWeight: 700, color: c }}>
                  {(m.sla_pass_rate * 100).toFixed(1)}%
                </div>
                <div className="faint" style={{ fontSize: 11.5 }}>
                  평균 {m.mean.toFixed(3)} · P10 {m.p10.toFixed(2)} / P90 {m.p90.toFixed(2)}
                  {m.below_sla_count > 0 && <> · 미달 {m.below_sla_count}건 ▾</>}
                </div>
              </div>
            );
          })}
        </div>

        {/* 메트릭 드릴 패널 */}
        {drill?.kind === "metric" && (
          <div style={{ marginTop: 14 }}>
            <DrillPanel
              drill={drill} runId={latestId!} tenant={tenant!}
              onClose={() => setDrill(null)} navigate={navigate} embedded
            />
          </div>
        )}
      </div>

      {/* 추이 */}
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <h3>품질 추이</h3>
        <div className="card-sub">배치 누적 메트릭 평균</div>
        {trends && <TrendChart data={trends} />}
      </div>

      {/* SLA 미달 */}
      <div className="card card-pad">
        <h3>🚨 SLA 미달 콜</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
          {flagged.length === 0 && <span className="muted">없음</span>}
          {flagged.map((id) => (
            <span key={id} className="badge bad" style={{ cursor: "pointer" }}
              onClick={() => navigate(`/t/${tenant}/conversations/${id}`)}>{id}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function drillKey(d: Drill): string {
  return d.kind === "metric" ? `metric:${d.metric}` : d.kind;
}

const DRILL_TITLE: Record<string, string> = {
  calls: "📞 전체 콜(세션)",
  evals: "📋 전체 평가(턴)",
  flagged: "🚨 SLA 미달 평가",
  pending: "✅ 검수 대기 평가",
};

// ---- 드릴 패널 ----
function DrillPanel({
  drill, runId, tenant, onClose, navigate, embedded,
}: {
  drill: Drill; runId: string; tenant: string;
  onClose: () => void; navigate: (to: string) => void; embedded?: boolean;
}) {
  // 평가 데이터 로드 (필터는 클라이언트에서 적용)
  const { data, loading } = useFetch<EvaluationRecord[]>(
    () => api.evaluations(runId, drill.kind === "flagged" ? { flagged_only: true } : {}),
    [runId, drill.kind, drill.kind === "metric" ? drill.metric : ""]
  );

  const rows = useMemo(() => {
    let r = (data ?? []) as EvaluationRecord[];
    if (drill.kind === "metric") {
      r = r.filter((e) => (e.scores ?? []).some((s) => s.metric === drill.metric && s.score < 0.8));
    } else if (drill.kind === "pending") {
      r = r.filter((e) => e.review_status === "pending");
    } else if (drill.kind === "calls") {
      // 콜(세션) 단위 — conversation_id 중복 제거
      const seen = new Set<string>();
      r = r.filter((e) => {
        const cid = e.conversation_id ?? e.call_id;
        if (seen.has(cid)) return false;
        seen.add(cid);
        return true;
      });
    }
    return r;
  }, [data, drill]);

  const title =
    drill.kind === "metric" ? `📊 ${drill.metric} 미달 평가` : DRILL_TITLE[drill.kind];

  const fullPageLink =
    drill.kind === "calls"
      ? `/t/${tenant}/conversations`
      : drill.kind === "pending"
        ? `/t/${tenant}/review`
        : `/t/${tenant}/evaluations`;

  const body = (
    <>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>{title}</h3>
        <span className="faint" style={{ fontSize: 12, marginLeft: 8 }}>
          {data ? `${rows.length}건` : ""}
        </span>
        <div className="spacer" style={{ flex: 1 }} />
        <button className="btn ghost sm" onClick={() => navigate(fullPageLink)}>전체 보기 →</button>
        <button className="btn ghost sm" onClick={onClose}>✕ 닫기</button>
      </div>

      {loading && <div className="muted" style={{ padding: 16 }}>로딩 중…</div>}
      {data && rows.length === 0 && <div className="muted" style={{ padding: 16 }}>해당 항목이 없습니다.</div>}

      {data && rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>세션/콜</th>
              <th>질문</th>
              <th>점수</th>
              <th>검수</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 12).map((e) => {
              const cid = e.conversation_id ?? e.call_id;
              const focus =
                drill.kind === "metric"
                  ? (e.scores ?? []).find((s) => s.metric === drill.metric)
                  : null;
              return (
                <tr key={e.eval_id} className="clickable"
                  onClick={() => navigate(`/t/${tenant}/conversations/${cid}`)}>
                  <td className="mono">{cid}</td>
                  <td style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {e.query}
                  </td>
                  <td>
                    {focus ? (
                      <ScoreBadge score={focus.score} label={drill.kind === "metric" ? drill.metric.slice(0, 8) : undefined} />
                    ) : (
                      <span style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                        {(e.scores ?? []).slice(0, 3).map((s) => (
                          <ScoreBadge key={s.metric} score={s.score} />
                        ))}
                      </span>
                    )}
                  </td>
                  <td>
                    <span className={`badge ${e.review_status === "pending" ? "warn" : "ok"}`}>
                      {e.review_status === "pending" ? "대기" : "완료"}
                    </span>
                  </td>
                  <td className="faint">›</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {data && rows.length > 12 && (
        <div className="faint" style={{ fontSize: 12, marginTop: 8, textAlign: "center" }}>
          상위 12건 표시 · 전체 {rows.length}건은 “전체 보기”
        </div>
      )}
    </>
  );

  if (embedded) {
    return (
      <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14 }}>{body}</div>
    );
  }
  return <div className="card card-pad" style={{ marginBottom: 18, borderLeft: "3px solid var(--accent)" }}>{body}</div>;
}
