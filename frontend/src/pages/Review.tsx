import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import type { EvaluationRecord } from "../types";

const SCORE_BINS = [0, 0.25, 0.5, 0.75, 1];

function tone(score: number) {
  return score >= 0.8 ? "ok" : score >= 0.6 ? "warn" : "bad";
}
function labelKo(m: string): string {
  const t: Record<string, string> = {
    faithfulness: "충실성", answer_relevance: "관련성", context_precision: "정밀도",
    context_recall: "재현율", resolution: "해결", multiturn_consistency: "일관성",
    efficiency: "효율", escalation_handling: "에스컬레이션", gt_similarity: "정답유사",
  };
  return t[m] ?? m;
}

export default function Review() {
  const { tenant } = useParams();
  const { data: queue, loading, error } = useFetch<EvaluationRecord[]>(
    () => api.reviewQueue(tenant!),
    [tenant]
  );
  const [activeId, setActiveId] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState<Set<string>>(new Set());

  // 첫 항목 자동 선택
  useEffect(() => {
    if (queue && queue.length && !activeId) setActiveId(queue[0].eval_id);
  }, [queue, activeId]);

  const active = useMemo(
    () => (queue ?? []).find((e) => e.eval_id === activeId) ?? null,
    [queue, activeId]
  );

  const remaining = (queue ?? []).filter((e) => !reviewed.has(e.eval_id));

  const onDone = (evalId: string) => {
    setReviewed((s) => new Set(s).add(evalId));
    // 다음 미검수 항목으로 이동
    const next = (queue ?? []).find((e) => e.eval_id !== evalId && !reviewed.has(e.eval_id));
    setActiveId(next?.eval_id ?? null);
  };

  if (loading) return <div className="content"><div className="muted" style={{ padding: 40 }}>로딩 중…</div></div>;
  if (error) return <div className="content"><div className="error">{error}</div></div>;
  if (!queue || queue.length === 0)
    return (
      <div className="content">
        <EmptyState icon="✅" title="검수 대기 항목이 없습니다"
          desc="모든 평가가 확정되었거나, 평가 데이터가 없습니다." />
      </div>
    );

  return (
    <div style={{ display: "grid", gridTemplateColumns: "300px 1fr 380px", height: "calc(100vh - var(--topbar-h))" }}>
      {/* 좌: 검수 큐 */}
      <div style={{ borderRight: "1px solid var(--border)", overflowY: "auto", background: "var(--surface)" }}>
        <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)", position: "sticky", top: 0, background: "var(--surface)" }}>
          <div style={{ fontWeight: 700, fontSize: 14, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            검수 큐 <span className="badge accent">우선순위</span>
          </div>
          <div style={{ height: 6, background: "var(--surface-2)", borderRadius: 999, marginTop: 10, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${(reviewed.size / queue.length) * 100}%`, background: "var(--accent)" }} />
          </div>
          <div className="faint" style={{ fontSize: 11.5, marginTop: 6 }}>
            {reviewed.size} / {queue.length} 완료 · 잔여 {remaining.length}
          </div>
        </div>
        {queue.map((e) => {
          const minScore = Math.min(...(e.scores ?? []).map((s) => s.score), 1);
          const done = reviewed.has(e.eval_id);
          return (
            <div key={e.eval_id} onClick={() => setActiveId(e.eval_id)}
              style={{
                padding: "11px 16px", borderBottom: "1px solid var(--border)", cursor: "pointer",
                borderLeft: `3px solid ${e.eval_id === activeId ? "var(--accent)" : "transparent"}`,
                background: e.eval_id === activeId ? "var(--accent-soft)" : "transparent",
                opacity: done ? 0.5 : 1,
              }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <span className={`badge ${minScore < 0.6 ? "bad" : minScore < 0.8 ? "warn" : "info"}`}>
                  {done ? "✓ 완료" : (e.level === "session" ? "세션" : "턴")}
                </span>
                <span className="faint mono" style={{ fontSize: 11 }}>{e.conversation_id}</span>
              </div>
              <div style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {e.query}
              </div>
            </div>
          );
        })}
      </div>

      {/* 중/우: 워크스페이스 */}
      {active ? (
        <ReviewWorkspace key={active.eval_id} record={active} onDone={() => onDone(active.eval_id)} />
      ) : (
        <div style={{ gridColumn: "2 / 4", display: "grid", placeItems: "center" }}>
          <EmptyState icon="🎉" title="검수 완료" desc="이 배치의 검수 대기 항목을 모두 처리했습니다." />
        </div>
      )}
    </div>
  );
}

// ---- 중앙(대화/근거) + 우측(재평가) ----
function ReviewWorkspace({ record, onDone }: {
  record: EvaluationRecord; onDone: () => void;
}) {
  // 세션 전체 대화 로드 (타임라인 표시)
  const cid = record.conversation_id ?? record.call_id;
  const { data: convo } = useFetch(() => api.conversation(cid), [cid]);

  // 휴먼 점수 편집 상태 (metric → score), 기본은 자동점수
  const [edits, setEdits] = useState<Record<string, number>>({});
  const [labels, setLabels] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [centerView, setCenterView] = useState<"evidence" | "dialogue">("evidence");
  const [showContextTurns, setShowContextTurns] = useState(false);

  const scores = record.scores ?? [];
  const ctxs = record.retrieval_result?.contexts ?? [];

  // 평가 대상 턴의 앞 맥락 턴 추출 (직전 user/bot 1쌍)
  const allTurns = (convo?.turns ?? []) as { role: string; content: string }[];
  const targetIdx = record.turn_index ?? (allTurns.length - 1);
  const contextTurns = allTurns.slice(Math.max(0, targetIdx - 2), targetIdx);
  const hasMoreContext = targetIdx > 2;


  const setScore = (metric: string, v: number) =>
    setEdits((e) => ({ ...e, [metric]: v }));
  const toggleLabel = (l: string) =>
    setLabels((ls) => (ls.includes(l) ? ls.filter((x) => x !== l) : [...ls, l]));

  const submit = async (status: "approved" | "overridden" | "skipped") => {
    setSaving(true);
    try {
      await api.submitReview(record.eval_id, {
        status,
        human_scores: status === "overridden" ? edits : {},
        labels, comment, reviewer: "qa_web",
      });
      onDone();
    } catch (e) {
      alert(String(e));
    } finally {
      setSaving(false);
    }
  };

  const hasEdits = Object.keys(edits).length > 0;

  return (
    <>
      {/* 중앙: 근거 ↔ 전체 대화 토글 (재평가 패널을 가리지 않음) */}
      <div style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "16px 22px 0" }}>
          <div className="faint mono" style={{ fontSize: 12, marginBottom: 4 }}>
            {record.conversation_id} · 가입자 {record.subscriber_id ?? "—"} · {record.judge_provider}/{record.judge_model}
          </div>
          <h3 style={{ margin: "0 0 12px" }}>Q. {record.query}</h3>
          {/* 뷰 전환 세그먼트 */}
          <div style={{ display: "inline-flex", border: "1px solid var(--border-strong)", borderRadius: 7, overflow: "hidden" }}>
            {([["evidence", "🔎 평가 근거"], ["dialogue", `💬 전체 대화${convo?.is_multiturn ? ` (${allTurns.length}턴)` : ""}`]] as [string, string][]).map(([v, label]) => (
              <button key={v} onClick={() => setCenterView(v as "evidence" | "dialogue")}
                style={{
                  border: "none", padding: "7px 14px", fontSize: 12.5, cursor: "pointer", fontFamily: "inherit",
                  background: centerView === v ? "var(--accent)" : "var(--surface)",
                  color: centerView === v ? "#fff" : "var(--text-muted)", fontWeight: centerView === v ? 600 : 400,
                }}>{label}</button>
            ))}
          </div>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "14px 22px 22px" }}>
          {centerView === "dialogue" ? (
            /* 전체 대화 타임라인 — 평가 대상 턴 강조 */
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {allTurns.length === 0 && <div className="muted">대화 원문이 없습니다.</div>}
              {allTurns.map((t, i) => (
                <div key={i} style={{
                  outline: i === targetIdx ? "2px solid var(--accent)" : "none",
                  outlineOffset: 3, borderRadius: 9,
                }}>
                  <MiniBubble role={t.role} content={t.content} />
                  {i === targetIdx && (
                    <div style={{ textAlign: "right", marginTop: 2 }}>
                      <span className="badge accent" style={{ fontSize: 10 }}>← 평가 대상</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <>
              {/* 직전 대화 맥락 (인라인 펼치기) */}
              {contextTurns.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  {!showContextTurns ? (
                    <button className="btn ghost sm" onClick={() => setShowContextTurns(true)}>
                      ▾ 직전 대화 맥락 ({contextTurns.length}턴)
                    </button>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8, opacity: 0.85 }}>
                      {hasMoreContext && (
                        <button className="btn ghost sm" style={{ alignSelf: "flex-start" }}
                          onClick={() => setCenterView("dialogue")}>⋯ 처음부터 보기</button>
                      )}
                      {contextTurns.map((t, i) => (
                        <MiniBubble key={i} role={t.role} content={t.content} />
                      ))}
                      <button className="btn ghost sm" style={{ alignSelf: "flex-start" }}
                        onClick={() => setShowContextTurns(false)}>▴ 맥락 접기</button>
                    </div>
                  )}
                </div>
              )}

              {/* 평가 대상 답변 + 근거 하이라이트 */}
              <div className="card card-pad" style={{ marginBottom: 14 }}>
                <div className="faint" style={{ fontSize: 12, marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
                  <span>🤖 평가 대상 답변</span>
                  {convo?.is_multiturn && <span className="badge outline">멀티턴 · 턴 {targetIdx}</span>}
                </div>
                <div style={{ marginBottom: 8 }}>
                  <MiniBubble role="user" content={record.query} />
                </div>
                <Highlighted answer={record.generated_answer} contexts={ctxs.map((c) => c.content)} />
              </div>

              {/* claim 분해 */}
              {(() => {
                const faith = scores.find((s) => s.metric === "faithfulness");
                if (!faith?.claims?.length) return null;
                return (
                  <div className="card card-pad" style={{ marginBottom: 14 }}>
                    <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>🧩 Claim 분해 (Faithfulness)</div>
                    {faith.claims.map((c, i) => {
                      const ico = c.verdict === "supported" ? "✅" : c.verdict === "contradicted" ? "❌" : "❓";
                      const col = c.verdict === "supported" ? "var(--ok)" : c.verdict === "contradicted" ? "var(--bad)" : "var(--warn)";
                      return <div key={i} style={{ fontSize: 12.5, color: col, padding: "2px 0" }}>{ico} {c.claim}</div>;
                    })}
                  </div>
                );
              })()}

              {/* 검색 컨텍스트 */}
              <div className="card card-pad">
                <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>🔎 검색된 컨텍스트 ({ctxs.length})</div>
                {ctxs.map((c) => (
                  <div key={c.chunk_id} style={{ border: "1px solid var(--border)", borderLeft: "3px solid var(--ok)", borderRadius: 7, padding: "10px 12px", marginBottom: 8, fontSize: 12.5 }}>
                    <div className="faint" style={{ fontSize: 11, display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                      <span>chunk {c.chunk_id.slice(0, 8)}</span><span>rerank {c.score.toFixed(2)}</span>
                    </div>
                    {c.content}
                  </div>
                ))}
                {ctxs.length === 0 && <div className="muted" style={{ fontSize: 12.5 }}>검색 컨텍스트 없음 (세션 평가)</div>}
              </div>
            </>
          )}
        </div>
      </div>

      {/* 우측: 재평가 패널 */}
      <div style={{ borderLeft: "1px solid var(--border)", background: "var(--surface)", display: "flex", flexDirection: "column", overflowY: "auto" }}>
        <div style={{ padding: "15px 18px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <b>재평가</b>
            <span className="badge accent">{record.judge_provider} Judge</span>
          </div>
          <div className="faint" style={{ fontSize: 11.5, marginTop: 4 }}>자동 점수를 검토하고 동의 또는 수정하세요.</div>
        </div>

        <div style={{ padding: "16px 18px", flex: 1 }}>
          {scores.map((s) => {
            const human = edits[s.metric];
            return (
              <div key={s.metric} style={{ border: "1px solid var(--border)", borderRadius: 7, padding: "12px 13px", marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{labelKo(s.metric)}</span>
                  <span className="faint" style={{ fontSize: 12 }}>
                    자동 <b style={{ color: `var(--${tone(s.score)})` }}>{s.score.toFixed(2)}</b>
                  </span>
                </div>
                {/* 점수 pill */}
                <div style={{ display: "flex", gap: 5, marginTop: 9, flexWrap: "wrap" }}>
                  {SCORE_BINS.map((b) => {
                    const sel = human !== undefined ? human === b : Math.abs(s.score - b) < 0.13;
                    const isAuto = Math.abs(s.score - b) < 0.13;
                    return (
                      <span key={b} onClick={() => setScore(s.metric, b)}
                        style={{
                          width: 34, height: 30, borderRadius: 6, display: "grid", placeItems: "center",
                          fontSize: 12, cursor: "pointer",
                          border: `1px solid ${sel ? "var(--accent)" : isAuto ? "var(--text-faint)" : "var(--border-strong)"}`,
                          background: sel ? "var(--accent)" : "transparent",
                          color: sel ? "#fff" : "var(--text)", fontWeight: sel ? 700 : 400,
                        }}>{b}</span>
                    );
                  })}
                </div>
                {human === undefined ? (
                  <div style={{ marginTop: 8 }}><span className="badge ok">✓ 자동과 동의</span></div>
                ) : (
                  <div style={{ marginTop: 8 }}>
                    <span className="badge warn">수정 {s.score.toFixed(2)} → {human.toFixed(2)}</span>
                  </div>
                )}
                {s.reasoning && <div className="faint" style={{ fontSize: 11.5, marginTop: 7 }}>{s.reasoning}</div>}
              </div>
            );
          })}

          {/* 라벨 + 코멘트 */}
          <div className="field" style={{ marginTop: 4 }}>
            <label>라벨</label>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
              {["환각", "검색실패", "양호", "부적절"].map((l) => (
                <span key={l} onClick={() => toggleLabel(l)}
                  className={`badge ${labels.includes(l) ? "bad" : "outline"}`}
                  style={{ cursor: "pointer" }}>{l}</span>
              ))}
            </div>
            <textarea rows={2} placeholder="검수 코멘트..." value={comment}
              onChange={(e) => setComment(e.target.value)} />
          </div>
        </div>

        {/* 푸터 액션 */}
        <div style={{ padding: "14px 18px", borderTop: "1px solid var(--border)", display: "flex", gap: 8, position: "sticky", bottom: 0, background: "var(--surface)" }}>
          <button className="btn ghost sm" disabled={saving} onClick={() => submit("skipped")}>보류</button>
          <div style={{ flex: 1 }} />
          {hasEdits ? (
            <button className="btn primary sm" disabled={saving} onClick={() => submit("overridden")}>
              {saving ? "저장 중…" : "수정 확정 & 다음"}
            </button>
          ) : (
            <button className="btn primary sm" disabled={saving} onClick={() => submit("approved")}>
              {saving ? "저장 중…" : "승인 & 다음"}
            </button>
          )}
        </div>
      </div>
    </>
  );
}

function MiniBubble({ role, content }: { role: string; content: string }) {
  const isBot = role === "bot";
  return (
    <div style={{ display: "flex", justifyContent: isBot ? "flex-end" : "flex-start" }}>
      <div style={{
        maxWidth: "82%", padding: "8px 11px", borderRadius: 9, fontSize: 12.5,
        background: isBot ? "var(--accent-soft)" : "var(--surface-2)",
        border: `1px solid ${isBot ? "color-mix(in srgb, var(--accent) 22%, transparent)" : "var(--border)"}`,
      }}>
        <div className="faint" style={{ fontSize: 10.5, marginBottom: 2 }}>{isBot ? "🤖 콜봇" : "👤 고객"}</div>
        {content}
      </div>
    </div>
  );
}

function Highlighted({ answer, contexts }: { answer: string; contexts: string[] }) {
  const ctxTokens = new Set(
    contexts.join(" ").toLowerCase().replace(/[.,!?·…]/g, " ").split(/\s+/).filter((w) => w.length >= 2)
  );
  const sents = answer.split(/(?<=[.!?。])\s+/).filter(Boolean);
  return (
    <div style={{ lineHeight: 2, fontSize: 14 }}>
      {sents.map((s, i) => {
        const toks = s.toLowerCase().replace(/[.,!?·…]/g, " ").split(/\s+/).filter((w) => w.length >= 2);
        const hit = toks.filter((t) => ctxTokens.has(t)).length;
        const grounded = toks.length === 0 || hit / toks.length >= 0.3;
        return (
          <span key={i} style={{
            background: grounded ? "var(--ok-soft)" : "var(--bad-soft)",
            borderBottom: `2px solid ${grounded ? "var(--ok)" : "var(--bad)"}`,
            borderRadius: 3, padding: "0 2px", marginRight: 2,
          }}>{s} </span>
        );
      })}
    </div>
  );
}
