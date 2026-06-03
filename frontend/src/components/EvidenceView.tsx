import { useMemo } from "react";
import type { EvaluationRecord, MetricScore } from "../types";

/**
 * EvidenceView — 이 제품의 차별화 포인트.
 * 답변을 문장 단위로 나누고, 각 문장이 검색된 컨텍스트에 근거하는지
 * 토큰 중첩 휴리스틱으로 표시 → 환각(ungrounded) 시각화.
 *
 * (서버 Judge의 grounding_chunks가 있으면 우선 사용, 없으면 휴리스틱)
 */

function tokenize(text: string): Set<string> {
  const words = text
    .toLowerCase()
    .replace(/[.,!?·…"'()[\]]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length >= 2);
  return new Set(words);
}

function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[.!?。])\s+|\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function overlapRatio(a: Set<string>, b: Set<string>): number {
  if (a.size === 0) return 0;
  let hit = 0;
  a.forEach((t) => b.has(t) && hit++);
  return hit / a.size;
}

function badgeClass(rate: number) {
  return rate >= 0.9 ? "ok" : rate >= 0.7 ? "warn" : "bad";
}

function MetricBar({ s }: { s: MetricScore }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
        <strong>{s.metric}</strong>
        <span>
          <span className={`badge ${badgeClass(s.score)}`}>{s.score.toFixed(2)}</span>{" "}
          {s.is_low_confidence && <span className="badge lowconf">⚠ 낮은 신뢰도</span>}
        </span>
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
        {s.reasoning}
      </div>
      {(s.claims?.length ?? 0) > 0 && (
        <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12 }}>
          {(s.claims ?? []).map((c, i) => {
            const icon =
              c.verdict === "supported" ? "✅" : c.verdict === "contradicted" ? "❌" : "❓";
            const color =
              c.verdict === "supported"
                ? "var(--ok)"
                : c.verdict === "contradicted"
                ? "var(--bad)"
                : "var(--warn)";
            return (
              <li key={i} style={{ color, marginBottom: 2 }}>
                {icon} {c.claim}
              </li>
            );
          })}
        </ul>
      )}
      {(s.sample_scores?.length ?? 0) > 1 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
          샘플: [{(s.sample_scores ?? []).map((x) => x.toFixed(2)).join(", ")}] · 신뢰도{" "}
          {(s.confidence * 100).toFixed(0)}%
        </div>
      )}
    </div>
  );
}

export function EvidenceView({
  record,
  onBack,
}: {
  record: EvaluationRecord;
  onBack: () => void;
}) {
  const contextTokens = useMemo(
    () => (record.retrieval_result?.contexts ?? []).map((c) => tokenize(c.content)),
    [record]
  );

  const sentences = useMemo(() => {
    const sents = splitSentences(record.generated_answer);
    return sents.map((sent) => {
      const st = tokenize(sent);
      const best = Math.max(0, ...contextTokens.map((ct) => overlapRatio(st, ct)));
      return { text: sent, grounded: best >= 0.3 };
    });
  }, [record, contextTokens]);

  const ungroundedCount = sentences.filter((s) => !s.grounded).length;

  return (
    <div>
      <button className="link" onClick={onBack}>
        ← 목록으로
      </button>

      <div className="card" style={{ marginTop: 12 }}>
        <div className="muted" style={{ fontSize: 12 }}>
          콜 {record.call_id} · 가입자 {record.subscriber_id ?? "—"} · {record.judge_model}
        </div>
        <h3 style={{ margin: "8px 0" }}>Q. {record.query}</h3>
      </div>

      <div className="card">
        <h4 style={{ marginTop: 0 }}>
          🔍 Evidence — 답변 근거 매핑
          {ungroundedCount > 0 && (
            <span className="badge bad" style={{ marginLeft: 8 }}>
              환각 의심 문장 {ungroundedCount}개
            </span>
          )}
        </h4>
        <div className="legend">
          <span><i className="dot" style={{ background: "#e3f9e5", border: "1px solid #1f9d55" }} /> 근거 있음</span>
          <span><i className="dot" style={{ background: "#fce8e8", border: "1px solid #dc2626" }} /> 근거 없음(환각 의심)</span>
        </div>
        <div className="evidence-answer">
          {sentences.map((s, i) => (
            <span key={i} className={`sent ${s.grounded ? "grounded" : "ungrounded"}`}>
              {s.text}{" "}
            </span>
          ))}
        </div>
      </div>

      <div className="card">
        <h4 style={{ marginTop: 0 }}>📊 메트릭 점수 + 근거</h4>
        {(record.scores ?? []).map((s) => (
          <MetricBar key={s.metric} s={s} />
        ))}
      </div>

      <div className="card">
        <h4 style={{ marginTop: 0 }}>
          📚 검색된 컨텍스트 ({record.retrieval_result?.contexts?.length ?? 0})
        </h4>
        {(record.retrieval_result?.contexts ?? []).map((c) => (
          <div key={c.chunk_id} className="ctx-item used">
            <div className="ctx-head">
              <span>chunk {c.chunk_id.slice(0, 8)} · 콜 {c.source_call_id}</span>
              <span>
                score {c.score.toFixed(3)}
                {c.dense_score != null && ` · dense ${c.dense_score.toFixed(2)}`}
                {c.bm25_score != null && ` · bm25 ${c.bm25_score.toFixed(2)}`}
              </span>
            </div>
            <div style={{ fontSize: 13 }}>{c.content}</div>
          </div>
        ))}
        {record.retrieval_result?.hyde_query && (
          <p className="muted" style={{ fontSize: 12 }}>
            HyDE: {record.retrieval_result.hyde_query}
          </p>
        )}
      </div>
    </div>
  );
}
