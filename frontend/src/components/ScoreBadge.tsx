export function scoreTone(score: number): "ok" | "warn" | "bad" {
  return score >= 0.8 ? "ok" : score >= 0.6 ? "warn" : "bad";
}

export function ScoreBadge({ score, label }: { score: number | null | undefined; label?: string }) {
  if (score === null || score === undefined) return <span className="faint">—</span>;
  return (
    <span className={`badge ${scoreTone(score)}`}>
      {label ? `${label} ` : ""}
      {score.toFixed(2)}
    </span>
  );
}
