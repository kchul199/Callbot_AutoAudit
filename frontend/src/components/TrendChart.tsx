import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendData } from "../types";

const COLORS = ["#2c3e50", "#1f9d55", "#d97706", "#7c3aed", "#dc2626"];

export function TrendChart({ data, xKey = "label" }: { data: TrendData; xKey?: string }) {
  const points = (data.points ?? []) as Array<Record<string, unknown>>;
  const metrics = data.metrics ?? [];
  if (!points.length) {
    return <p className="muted">추이 데이터가 없습니다.</p>;
  }
  // label 우선, 없으면 run_id
  const dataKey = points[0] && points[0][xKey] !== undefined ? xKey : "run_id";
  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={points} margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
        <XAxis dataKey={dataKey} tick={{ fontSize: 11 }} />
        <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} />
        <Tooltip />
        <Legend />
        {metrics.map((m, i) => (
          <Line
            key={m}
            type="monotone"
            dataKey={m}
            stroke={COLORS[i % COLORS.length]}
            strokeWidth={2}
            dot={{ r: 3 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
