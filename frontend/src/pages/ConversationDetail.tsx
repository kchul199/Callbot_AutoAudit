import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import { ScoreBadge, scoreTone } from "../components/ScoreBadge";
import type { ConversationDetail as ConvDetail } from "../types";

interface Turn { role: string; content: string }
interface MetricScore {
  metric: string; score: number; reasoning?: string; final_score?: number;
  human_score?: number | null;
  claims?: { claim: string; verdict: string }[];
}
interface TurnEval {
  turn_index: number | null;
  generated_answer: string;
  scores: MetricScore[];
  retrieval_result?: { contexts?: { chunk_id: string; content: string; score: number }[] };
}

/** 답변을 문장 단위로 쪼개 컨텍스트 토큰 중첩으로 근거 여부 표시 (휴리스틱) */
function highlightAnswer(answer: string, contexts: string[]) {
  const ctxTokens = new Set(
    contexts.join(" ").toLowerCase().replace(/[.,!?·…]/g, " ").split(/\s+/).filter((w) => w.length >= 2)
  );
  const sents = answer.split(/(?<=[.!?。])\s+/).filter(Boolean);
  return sents.map((s) => {
    const toks = s.toLowerCase().replace(/[.,!?·…]/g, " ").split(/\s+/).filter((w) => w.length >= 2);
    const hit = toks.filter((t) => ctxTokens.has(t)).length;
    const grounded = toks.length === 0 || hit / toks.length >= 0.3;
    return { text: s, grounded };
  });
}

export default function ConversationDetail() {
  const { tenant, id } = useParams();
  const navigate = useNavigate();
  const { data, loading, error } = useFetch<ConvDetail>(() => api.conversation(id!), [id]);

  const turnEvalByIndex = useMemo(() => {
    const m = new Map<number, TurnEval>();
    ((data?.turn_evaluations ?? []) as unknown as TurnEval[]).forEach((e) => {
      if (e.turn_index !== null && e.turn_index !== undefined) m.set(e.turn_index, e);
    });
    return m;
  }, [data]);

  if (loading) return <div className="content"><div className="muted" style={{ padding: 40 }}>로딩 중…</div></div>;
  if (error) return <div className="content"><div className="error">{error}</div></div>;
  if (!data) return null;

  const turns = (data.turns ?? []) as Turn[];
  const sessionEval = data.session_evaluation as { scores?: MetricScore[] } | null;

  return (
    <div className="content">
      <button className="btn ghost sm" onClick={() => navigate(`/t/${tenant}/conversations`)}>
        ← 목록으로
      </button>

      <div className="page-head" style={{ marginTop: 12 }}>
        <div>
          <h1 style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="mono" style={{ fontSize: 18 }}>{data.conversation_id}</span>
            <span className={`badge ${data.is_multiturn ? "info" : "outline"}`}>
              {data.is_multiturn ? "멀티턴" : "싱글턴"}
            </span>
          </h1>
          <div className="sub">가입자 {data.subscriber_id ?? "—"} · {data.started_at?.slice(0, 16).replace("T", " ")}</div>
        </div>
      </div>

      {/* 세션 점수 */}
      {sessionEval?.scores && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <h3>세션 평가</h3>
          <div className="card-sub">대화 전체 품질 (멀티턴) · 메트릭별 평가의견</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))", gap: 12 }}>
            {sessionEval.scores.map((s) => (
              <div key={s.metric} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <div className="faint" style={{ fontSize: 11.5 }}>{labelKo(s.metric)}</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: `var(--${scoreTone(s.score)})` }}>
                    {s.score.toFixed(2)}
                  </div>
                </div>
                {s.reasoning && (
                  <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 5, lineHeight: 1.45 }}>
                    💬 {s.reasoning}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 대화 타임라인 */}
      <div className="card card-pad">
        <h3>대화 타임라인</h3>
        <div className="card-sub">평가 대상 봇 답변에는 근거 하이라이트와 점수가 표시됩니다.</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 8 }}>
          {turns.map((t, i) => {
            const isBot = t.role === "bot";
            const te = turnEvalByIndex.get(i);
            const contexts = te?.retrieval_result?.contexts?.map((c) => c.content) ?? [];
            const sentences = te && isBot ? highlightAnswer(t.content, contexts) : null;
            return (
              <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: isBot ? "flex-end" : "flex-start" }}>
                <div className="faint" style={{ fontSize: 11, marginBottom: 4 }}>
                  {isBot ? "🤖 콜봇" : "👤 고객"} · 턴 {i}
                  {te && " · 평가 대상"}
                </div>
                <div
                  style={{
                    maxWidth: "78%", padding: "11px 14px", borderRadius: 10, fontSize: 13.5,
                    background: isBot ? "var(--accent-soft)" : "var(--surface-2)",
                    border: `1px solid ${isBot ? "color-mix(in srgb, var(--accent) 25%, transparent)" : "var(--border)"}`,
                  }}
                >
                  {sentences
                    ? sentences.map((s, j) => (
                        <span
                          key={j}
                          style={{
                            background: s.grounded ? "var(--ok-soft)" : "var(--bad-soft)",
                            borderBottom: `2px solid ${s.grounded ? "var(--ok)" : "var(--bad)"}`,
                            borderRadius: 3, padding: "0 2px", marginRight: 2,
                          }}
                        >
                          {s.text}{" "}
                        </span>
                      ))
                    : t.content}
                </div>
                {te && (
                  <div style={{ display: "flex", gap: 5, marginTop: 7, flexWrap: "wrap", justifyContent: "flex-end" }}>
                    {te.scores.map((s) => (
                      <ScoreBadge key={s.metric} score={s.final_score ?? s.score} label={labelKo(s.metric)} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 평가 근거 (claim + 컨텍스트) */}
      {[...turnEvalByIndex.values()].map((te) => {
        const faith = te.scores.find((s) => s.metric === "faithfulness");
        const ctxs = te.retrieval_result?.contexts ?? [];
        return (
          <div className="card card-pad" key={te.turn_index} style={{ marginTop: 16 }}>
            <h3>🔎 평가 근거 (턴 {te.turn_index})</h3>
            {/* 메트릭별 평가의견 — 왜 그렇게 평가했는지 */}
            <div style={{ marginBottom: 14 }}>
              <div className="faint" style={{ fontSize: 12, marginBottom: 6 }}>💬 메트릭별 평가의견</div>
              {te.scores.map((s) => (
                <div key={s.metric} style={{ display: "flex", gap: 9, alignItems: "baseline", padding: "7px 0", borderBottom: "1px solid var(--border)", fontSize: 12.5 }}>
                  <span style={{ minWidth: 66, fontWeight: 600 }}>{labelKo(s.metric)}</span>
                  <span style={{ flexShrink: 0 }}><ScoreBadge score={s.final_score ?? s.score} /></span>
                  <span style={{ color: "var(--text-muted)", lineHeight: 1.45 }}>{s.reasoning || "—"}</span>
                </div>
              ))}
            </div>
            {faith?.claims && faith.claims.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div className="faint" style={{ fontSize: 12, marginBottom: 6 }}>Claim 분해 (Faithfulness)</div>
                {faith.claims.map((c, i) => {
                  const ico = c.verdict === "supported" ? "✅" : c.verdict === "contradicted" ? "❌" : "❓";
                  const col = c.verdict === "supported" ? "var(--ok)" : c.verdict === "contradicted" ? "var(--bad)" : "var(--warn)";
                  return (
                    <div key={i} style={{ fontSize: 12.5, color: col, padding: "2px 0" }}>
                      {ico} {c.claim}
                    </div>
                  );
                })}
              </div>
            )}
            <div className="faint" style={{ fontSize: 12, marginBottom: 6 }}>검색된 컨텍스트 ({ctxs.length})</div>
            {ctxs.map((c) => (
              <div key={c.chunk_id} style={{ border: "1px solid var(--border)", borderLeft: "3px solid var(--ok)", borderRadius: 7, padding: "10px 12px", marginBottom: 8, fontSize: 12.5 }}>
                <div className="faint" style={{ fontSize: 11, marginBottom: 4, display: "flex", justifyContent: "space-between" }}>
                  <span>chunk {c.chunk_id.slice(0, 8)}</span>
                  <span>rerank {c.score.toFixed(2)}</span>
                </div>
                {c.content}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function labelKo(metric: string): string {
  const m: Record<string, string> = {
    faithfulness: "충실성", answer_relevance: "관련성",
    context_precision: "정밀도", context_recall: "재현율",
    resolution: "해결", multiturn_consistency: "일관성",
    efficiency: "효율", escalation_handling: "에스컬레이션", gt_similarity: "정답유사",
  };
  return m[metric] ?? metric;
}
