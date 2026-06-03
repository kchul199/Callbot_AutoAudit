import { Fragment, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { ScoreBadge } from "../components/ScoreBadge";
import { TrendChart } from "../components/TrendChart";
import type { AgreementResult, AgreementSample, TrendData } from "../types";

function labelKo(m: string): string {
  const t: Record<string, string> = {
    faithfulness: "충실성", answer_relevance: "관련성", context_precision: "정밀도",
    context_recall: "재현율", resolution: "해결", multiturn_consistency: "일관성",
    efficiency: "효율", escalation_handling: "에스컬레이션", gt_similarity: "정답유사",
  };
  return t[m] ?? m;
}

export default function Trends() {
  const { tenant } = useParams();
  const [groupBy, setGroupBy] = useState<"day" | "run">("day");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [drillMetric, setDrillMetric] = useState<string | null>(null);

  const { data: trends } = useFetch<TrendData>(
    () => api.tenantTrends(tenant!, {
      group_by: groupBy,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    }),
    [tenant, groupBy, dateFrom, dateTo]
  );
  const { data: agree } = useFetch<AgreementResult>(() => api.agreement(tenant!), [tenant]);

  const pointCount = trends?.points?.length ?? 0;

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Trends</h1>
          <div className="sub">평가 추이와 휴먼·자동 일치도를 분석합니다.</div>
        </div>
      </div>

      {/* 품질 추이 */}
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>품질 추이</h3>
            <div className="card-sub" style={{ marginBottom: 0 }}>메트릭 평균 (turn 레벨)</div>
          </div>
          <div className="spacer" style={{ flex: 1 }} />
          {/* 집계 단위 토글 */}
          <div style={{ display: "inline-flex", border: "1px solid var(--border-strong)", borderRadius: 7, overflow: "hidden" }}>
            {([["day", "일자별"], ["run", "배치별"]] as [string, string][]).map(([v, l]) => (
              <button key={v} onClick={() => setGroupBy(v as "day" | "run")}
                style={{
                  border: "none", padding: "6px 13px", fontSize: 12.5, cursor: "pointer", fontFamily: "inherit",
                  background: groupBy === v ? "var(--accent)" : "var(--surface)",
                  color: groupBy === v ? "#fff" : "var(--text-muted)", fontWeight: groupBy === v ? 600 : 400,
                }}>{l}</button>
            ))}
          </div>
          {/* 일자 범위 필터 */}
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
            style={{ width: 150 }} title="시작일" />
          <span className="faint">~</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
            style={{ width: 150 }} title="종료일" />
          {(dateFrom || dateTo) && (
            <button className="btn ghost sm" onClick={() => { setDateFrom(""); setDateTo(""); }}>초기화</button>
          )}
        </div>

        {pointCount === 0 ? (
          <div className="muted" style={{ padding: 20 }}>해당 기간에 평가 데이터가 없습니다.</div>
        ) : (
          <>
            <TrendChart data={trends!} xKey="label" />
            <div className="faint" style={{ fontSize: 11.5, marginTop: 6 }}>
              {pointCount}개 {groupBy === "day" ? "일자" : "배치"} 포인트
              {pointCount === 1 && " · 추세선을 보려면 기간을 넓혀보세요"}
            </div>
          </>
        )}
      </div>

      {/* 휴먼 vs 자동 일치도 */}
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <h3>휴먼 vs 자동 일치도</h3>
        <div className="card-sub">검수자가 확정한 항목에서 자동 평가와의 일치 정도 (행 클릭 → 표본 상세)</div>
        {!agree?.available ? (
          <EmptyState icon="🤝" title="아직 검수 데이터가 부족합니다"
            desc="Review에서 평가를 확정하면 자동 평가와의 일치도가 집계됩니다." />
        ) : (
          <>
            <div style={{ display: "flex", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
              <div className="card card-pad" style={{ boxShadow: "none", minWidth: 160 }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: agree.overall_agreement >= 0.8 ? "var(--ok)" : "var(--warn)" }}>
                  {(agree.overall_agreement * 100).toFixed(0)}%
                </div>
                <div className="muted" style={{ fontSize: 12.5 }}>전체 동의율 (n={agree.n})</div>
              </div>
            </div>
            <table>
              <thead>
                <tr><th>메트릭</th><th>표본</th><th>동의율</th><th>MAE</th><th>해석</th><th /></tr>
              </thead>
              <tbody>
                {Object.entries(agree.per_metric ?? {}).map(([m, v]) => {
                  const ag = (v as { agreement: number }).agreement;
                  const mae = (v as { mae: number }).mae;
                  const n = (v as { n: number }).n;
                  return (
                    <tr key={m} className="clickable" onClick={() => setDrillMetric(m)}>
                      <td>{labelKo(m)}</td>
                      <td><span className="badge outline">{n}건</span></td>
                      <td><span className={`badge ${ag >= 0.8 ? "ok" : ag >= 0.5 ? "warn" : "bad"}`}>{(ag * 100).toFixed(0)}%</span></td>
                      <td>{mae.toFixed(3)}</td>
                      <td className="faint" style={{ fontSize: 12 }}>
                        {ag >= 0.8 ? "Judge 신뢰 가능" : ag >= 0.5 ? "검토 권장" : "Judge 교정 필요"}
                      </td>
                      <td className="faint">›</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="faint" style={{ fontSize: 11.5, marginTop: 10 }}>
              동의율이 낮은 메트릭은 Judge 프롬프트/모델 교정 또는 골든셋 보강이 필요합니다.
            </p>
          </>
        )}
      </div>

      {/* 표본 상세 드로어 */}
      {drillMetric && (
        <SampleDrawer tenant={tenant!} metric={drillMetric} onClose={() => setDrillMetric(null)} />
      )}
    </div>
  );
}

function SampleDrawer({ tenant, metric, onClose }: {
  tenant: string; metric: string; onClose: () => void;
}) {
  const { data, loading } = useFetch<AgreementSample[]>(
    () => api.agreementSamples(tenant, metric), [tenant, metric]
  );
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const toggle = (id: string) => setExpandedId((cur) => (cur === id ? null : id));

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", zIndex: 100, display: "flex", justifyContent: "flex-end" }}>
      <div onClick={(e) => e.stopPropagation()}
        style={{ width: 560, maxWidth: "94vw", height: "100%", background: "var(--surface)", borderLeft: "1px solid var(--border)", boxShadow: "var(--shadow-lg)", display: "flex", flexDirection: "column", animation: "drawerIn .18s ease-out" }}>
        <div style={{ padding: "15px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center" }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>📊 {labelKo(metric)} 표본 상세</div>
            <div className="faint" style={{ fontSize: 11.5 }}>행 클릭 → 표본 상세 펼치기 {data ? `· ${data.length}건` : ""}</div>
          </div>
          <button className="btn ghost sm" onClick={onClose}>✕</button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "12px 18px" }}>
          {loading && <div className="muted" style={{ padding: 16 }}>로딩 중…</div>}
          {data && data.length === 0 && <div className="muted" style={{ padding: 16 }}>표본이 없습니다.</div>}
          {data && data.length > 0 && (
            <table>
              <thead>
                <tr><th /><th>세션</th><th>자동</th><th>휴먼</th><th>차이</th><th>일치</th></tr>
              </thead>
              <tbody>
                {data.map((s) => {
                  const open = expandedId === s.eval_id;
                  return (
                    <Fragment key={s.eval_id}>
                      <tr className="clickable" onClick={() => toggle(s.eval_id)}
                        style={{ background: open ? "var(--accent-soft)" : undefined }}>
                        <td className="faint" style={{ width: 18 }}>{open ? "▾" : "▸"}</td>
                        <td>
                          <div className="mono" style={{ fontSize: 12 }}>{s.conversation_id}</div>
                          <div className="faint" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 180 }}>{s.query}</div>
                        </td>
                        <td><ScoreBadge score={s.auto_score} /></td>
                        <td><ScoreBadge score={s.human_score} /></td>
                        <td>
                          <span style={{ color: s.delta === 0 ? "var(--text-muted)" : s.delta < 0 ? "var(--bad)" : "var(--ok)", fontSize: 12.5 }}>
                            {s.delta > 0 ? "+" : ""}{s.delta.toFixed(2)}
                          </span>
                        </td>
                        <td>{s.agree ? <span className="badge ok">일치</span> : <span className="badge bad">불일치</span>}</td>
                      </tr>
                      {open && (
                        <tr>
                          <td colSpan={6} style={{ padding: 0, background: "var(--surface-2)" }}>
                            <SampleDetail sample={s} metric={metric} tenant={tenant} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function SampleDetail({ sample, metric, tenant }: {
  sample: AgreementSample; metric: string; tenant: string;
}) {
  const navigate = useNavigate();
  const { data, loading } = useFetch<import("../types").EvaluationRecord>(
    () => api.evaluationById(sample.eval_id), [sample.eval_id]
  );
  if (loading) return <div className="muted" style={{ padding: 14 }}>로딩 중…</div>;
  if (!data) return <div className="muted" style={{ padding: 14 }}>상세를 불러오지 못했습니다.</div>;

  const ctxs = data.retrieval_result?.contexts ?? [];
  const focusScore = (data.scores ?? []).find((s) => s.metric === metric);
  const claims = focusScore?.claims ?? [];

  return (
    <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
      {/* 점수 비교 요약 */}
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
        <div><div className="faint" style={{ fontSize: 11 }}>자동</div><ScoreBadge score={sample.auto_score} /></div>
        <div><div className="faint" style={{ fontSize: 11 }}>휴먼</div><ScoreBadge score={sample.human_score} /></div>
        <div><div className="faint" style={{ fontSize: 11 }}>검수자</div><span style={{ fontSize: 13 }}>{sample.reviewer ?? "—"}</span></div>
        <div><div className="faint" style={{ fontSize: 11 }}>상태</div>
          <span className={`badge ${sample.review_status === "overridden" ? "info" : "ok"}`}>
            {sample.review_status === "overridden" ? "수정" : "승인"}
          </span>
        </div>
      </div>

      {/* 질문/답변 */}
      <div>
        <div className="faint" style={{ fontSize: 11.5, marginBottom: 3 }}>Q. {data.query}</div>
        <div style={{ fontSize: 13, padding: "8px 11px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 7 }}>
          🤖 {data.generated_answer}
        </div>
      </div>

      {/* claim 분해 (있으면) */}
      {claims.length > 0 && (
        <div>
          <div className="faint" style={{ fontSize: 11.5, marginBottom: 4 }}>🧩 Claim 분해</div>
          {claims.map((c, i) => {
            const ico = c.verdict === "supported" ? "✅" : c.verdict === "contradicted" ? "❌" : "❓";
            const col = c.verdict === "supported" ? "var(--ok)" : c.verdict === "contradicted" ? "var(--bad)" : "var(--warn)";
            return <div key={i} style={{ fontSize: 12, color: col, padding: "1px 0" }}>{ico} {c.claim}</div>;
          })}
        </div>
      )}

      {/* 검색 컨텍스트 */}
      {ctxs.length > 0 && (
        <div>
          <div className="faint" style={{ fontSize: 11.5, marginBottom: 4 }}>🔎 검색 컨텍스트 ({ctxs.length})</div>
          {ctxs.map((c) => (
            <div key={c.chunk_id} style={{ fontSize: 12, border: "1px solid var(--border)", borderLeft: "3px solid var(--ok)", borderRadius: 6, padding: "7px 10px", marginBottom: 6, background: "var(--surface)" }}>
              <span className="faint" style={{ fontSize: 10.5 }}>chunk {c.chunk_id.slice(0, 8)} · rerank {c.score.toFixed(2)}</span><br />
              {c.content}
            </div>
          ))}
        </div>
      )}

      <button className="btn ghost sm" style={{ alignSelf: "flex-start" }}
        onClick={() => navigate(`/t/${tenant}/conversations/${sample.conversation_id}`)}>
        전체 세션 보기 →
      </button>
    </div>
  );
}
