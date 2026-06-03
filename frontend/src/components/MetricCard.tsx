import type { AggregatedMetric } from "../types";

function statusClass(rate: number): "ok" | "warn" | "bad" {
  return rate >= 0.9 ? "ok" : rate >= 0.7 ? "warn" : "bad";
}
function statusIcon(rate: number): string {
  return rate >= 0.9 ? "✅" : rate >= 0.7 ? "⚠️" : "❌";
}

export function MetricCard({
  metric,
  onClick,
}: {
  metric: AggregatedMetric;
  onClick: () => void;
}) {
  const cls = statusClass(metric.sla_pass_rate);
  return (
    <div className="metric-card" onClick={onClick} role="button" tabIndex={0}>
      <div className="name">
        {statusIcon(metric.sla_pass_rate)} {metric.metric}
      </div>
      <div className="rate" style={{ color: `var(--${cls})` }}>
        {(metric.sla_pass_rate * 100).toFixed(1)}%
      </div>
      <div className="detail">SLA 통과율 · 평균 {metric.mean.toFixed(3)}</div>
      <div className="detail">
        P10 {metric.p10.toFixed(2)} / P90 {metric.p90.toFixed(2)}
      </div>
      <div className="detail" style={{ color: "var(--bad)" }}>
        미달 {metric.below_sla_count} / {metric.total_count}건 →
      </div>
    </div>
  );
}
