import { useMemo, useState } from "react";
import { api, useFetch } from "../api/client";
import type { EvaluationRecord } from "../types";

function metricScore(rec: EvaluationRecord, metric: string): number | null {
  const s = (rec.scores ?? []).find((x) => x.metric === metric);
  return s ? s.score : null;
}

function scoreBadge(score: number | null) {
  if (score === null) return <span className="muted">—</span>;
  const cls = score >= 0.8 ? "ok" : score >= 0.6 ? "warn" : "bad";
  return <span className={`badge ${cls}`}>{score.toFixed(2)}</span>;
}

export function EvaluationList({
  runId,
  initialMetric,
  onSelect,
}: {
  runId: string;
  initialMetric?: string;
  onSelect: (evalId: string) => void;
}) {
  const [callId, setCallId] = useState("");
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [lowConfOnly, setLowConfOnly] = useState(false);
  const [metric] = useState(initialMetric ?? "");

  const params = useMemo(
    () => ({
      call_id: callId || undefined,
      flagged_only: flaggedOnly,
      low_confidence_only: lowConfOnly,
    }),
    [callId, flaggedOnly, lowConfOnly]
  );

  const { data, loading, error } = useFetch<EvaluationRecord[]>(
    () => api.evaluations(runId, params),
    [runId, callId, flaggedOnly, lowConfOnly]
  );

  const firstScores = data?.[0]?.scores ?? [];
  const metrics: string[] = firstScores.length
    ? firstScores.map((s) => s.metric)
    : ["faithfulness", "answer_relevance", "context_precision", "context_recall"];

  return (
    <div className="card">
      <h4 style={{ marginTop: 0 }}>
        🔎 평가 상세 {metric && <span className="badge warn">메트릭: {metric}</span>}
      </h4>

      <div className="filters">
        <input
          placeholder="콜 ID 검색"
          value={callId}
          onChange={(e) => setCallId(e.target.value)}
        />
        <label>
          <input
            type="checkbox"
            checked={flaggedOnly}
            onChange={(e) => setFlaggedOnly(e.target.checked)}
          />{" "}
          SLA 미달만
        </label>
        <label>
          <input
            type="checkbox"
            checked={lowConfOnly}
            onChange={(e) => setLowConfOnly(e.target.checked)}
          />{" "}
          낮은 신뢰도만
        </label>
        <span className="muted">{data ? `${data.length}건` : ""}</span>
      </div>

      {loading && <div className="loading">로딩 중…</div>}
      {error && <div className="error">{error}</div>}

      {data && (
        <table>
          <thead>
            <tr>
              <th>콜 ID</th>
              <th>질문</th>
              {metrics.map((m) => (
                <th key={m}>{m.slice(0, 8)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((rec) => (
              <tr
                key={rec.eval_id}
                className="clickable"
                onClick={() => onSelect(rec.eval_id)}
              >
                <td>{rec.call_id}</td>
                <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {rec.query}
                </td>
                {metrics.map((m) => (
                  <td key={m}>{scoreBadge(metricScore(rec, m))}</td>
                ))}
              </tr>
            ))}
            {data.length === 0 && (
              <tr>
                <td colSpan={2 + metrics.length} className="muted">
                  조건에 맞는 평가가 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
