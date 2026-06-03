import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { ScoreBadge } from "../components/ScoreBadge";
import type { EvaluationRecord } from "../types";

const LEVELS = [["", "전체 레벨"], ["retrieval", "검색"], ["turn", "턴"], ["session", "세션"]];
const STATUSES = [["", "전체 검수"], ["pending", "미검수"], ["approved", "승인"], ["overridden", "수정"]];

function labelKo(m: string): string {
  const t: Record<string, string> = {
    faithfulness: "충실성", answer_relevance: "관련성", context_precision: "정밀도",
    context_recall: "재현율", resolution: "해결", multiturn_consistency: "일관성",
    efficiency: "효율", escalation_handling: "에스컬레이션", gt_similarity: "정답유사",
  };
  return t[m] ?? m;
}

export default function Evaluations() {
  const { tenant } = useParams();
  const navigate = useNavigate();
  const [level, setLevel] = useState("");
  const [status, setStatus] = useState("");
  const [lowConf, setLowConf] = useState(false);
  const [selected, setSelected] = useState<EvaluationRecord | null>(null);

  const { data, loading, error } = useFetch<EvaluationRecord[]>(
    () => api.exploreEvaluations(tenant!, {
      level: level || undefined,
      review_status: status || undefined,
      low_confidence_only: lowConf,
    }),
    [tenant, level, status, lowConf]
  );

  const metrics = useMemo(() => {
    const set = new Set<string>();
    (data ?? []).forEach((e) => (e.scores ?? []).forEach((s) => set.add(s.metric)));
    return [...set];
  }, [data]);

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Evaluations</h1>
          <div className="sub">평가를 필터로 탐색하고 근거(Evidence)를 확인합니다.</div>
        </div>
      </div>

      <div className="card card-pad">
        {/* 필터 */}
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
          <select value={level} onChange={(e) => setLevel(e.target.value)} style={{ maxWidth: 130 }}>
            {LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ maxWidth: 130 }}>
            {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 5 }}>
            <input type="checkbox" checked={lowConf} onChange={(e) => setLowConf(e.target.checked)} />
            낮은 신뢰도만
          </label>
          <span className="faint" style={{ fontSize: 12, marginLeft: "auto" }}>{data ? `${data.length}건` : ""}</span>
        </div>

        {loading && <div className="muted" style={{ padding: 20 }}>로딩 중…</div>}
        {error && <div className="error" style={{ padding: 12 }}>{error}</div>}
        {data && data.length === 0 && <EmptyState icon="🔍" title="평가가 없습니다" desc="조건에 맞는 평가가 없습니다." />}

        {data && data.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>세션</th><th>레벨</th><th>질문</th>
                {metrics.slice(0, 4).map((m) => <th key={m}>{labelKo(m)}</th>)}
                <th>검수</th>
              </tr>
            </thead>
            <tbody>
              {data.map((e) => {
                const byMetric = Object.fromEntries((e.scores ?? []).map((s) => [s.metric, s]));
                return (
                  <tr key={e.eval_id} className="clickable" onClick={() => setSelected(e)}>
                    <td className="mono">{e.conversation_id}</td>
                    <td><span className="badge outline">{e.level}</span></td>
                    <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.query}</td>
                    {metrics.slice(0, 4).map((m) => (
                      <td key={m}>{byMetric[m] ? <ScoreBadge score={byMetric[m].score} /> : <span className="faint">—</span>}</td>
                    ))}
                    <td>
                      <span className={`badge ${e.review_status === "pending" ? "warn" : e.review_status === "overridden" ? "info" : "ok"}`}>
                        {e.review_status === "pending" ? "대기" : e.review_status === "overridden" ? "수정" : "승인"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Evidence Drawer */}
      {selected && (
        <EvidenceDrawer record={selected} tenant={tenant!} navigate={navigate} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function EvidenceDrawer({ record, tenant, navigate, onClose }: {
  record: EvaluationRecord; tenant: string; navigate: (to: string) => void; onClose: () => void;
}) {
  const ctxs = record.retrieval_result?.contexts ?? [];
  const faith = (record.scores ?? []).find((s) => s.metric === "faithfulness");
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", zIndex: 100, display: "flex", justifyContent: "flex-end" }}>
      <div onClick={(e) => e.stopPropagation()}
        style={{ width: 480, maxWidth: "92vw", height: "100%", background: "var(--surface)", borderLeft: "1px solid var(--border)", boxShadow: "var(--shadow-lg)", display: "flex", flexDirection: "column", animation: "drawerIn .18s ease-out" }}>
        <div style={{ padding: "15px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>🔎 Evidence</div>
            <div className="faint mono" style={{ fontSize: 11.5 }}>{record.conversation_id} · {record.level}</div>
          </div>
          <button className="btn ghost sm" onClick={() => navigate(`/t/${tenant}/conversations/${record.conversation_id}`)}>세션 →</button>
          <button className="btn ghost sm" onClick={onClose}>✕</button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 18px" }}>
          <h4 style={{ margin: "0 0 8px" }}>Q. {record.query}</h4>
          <div className="card card-pad" style={{ marginBottom: 14 }}>
            <div className="faint" style={{ fontSize: 12, marginBottom: 6 }}>🤖 답변</div>
            <div style={{ fontSize: 13.5 }}>{record.generated_answer}</div>
          </div>

          <div className="card card-pad" style={{ marginBottom: 14 }}>
            <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>📊 메트릭</div>
            {(record.scores ?? []).map((s) => (
              <div key={s.metric} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", fontSize: 13 }}>
                <span>{labelKo(s.metric)}</span>
                <span style={{ display: "flex", gap: 6 }}>
                  <ScoreBadge score={s.score} />
                  {s.human_score != null && s.human_score !== s.score && (
                    <span className="badge info">사람 {s.human_score.toFixed(2)}</span>
                  )}
                </span>
              </div>
            ))}
          </div>

          {faith?.claims && faith.claims.length > 0 && (
            <div className="card card-pad" style={{ marginBottom: 14 }}>
              <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>🧩 Claim 분해</div>
              {faith.claims.map((c, i) => {
                const ico = c.verdict === "supported" ? "✅" : c.verdict === "contradicted" ? "❌" : "❓";
                const col = c.verdict === "supported" ? "var(--ok)" : c.verdict === "contradicted" ? "var(--bad)" : "var(--warn)";
                return <div key={i} style={{ fontSize: 12.5, color: col, padding: "2px 0" }}>{ico} {c.claim}</div>;
              })}
            </div>
          )}

          <div className="card card-pad">
            <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>🔎 검색 컨텍스트 ({ctxs.length})</div>
            {ctxs.map((c) => (
              <div key={c.chunk_id} style={{ border: "1px solid var(--border)", borderLeft: "3px solid var(--ok)", borderRadius: 7, padding: "9px 11px", marginBottom: 8, fontSize: 12.5 }}>
                <div className="faint" style={{ fontSize: 11, marginBottom: 4 }}>chunk {c.chunk_id.slice(0, 8)} · rerank {c.score.toFixed(2)}</div>
                {c.content}
              </div>
            ))}
            {ctxs.length === 0 && <div className="muted" style={{ fontSize: 12.5 }}>검색 컨텍스트 없음</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
