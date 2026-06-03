import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { ScoreBadge } from "../components/ScoreBadge";
import type { ConversationInfo } from "../types";

type Filter = "all" | "multiturn" | "singleturn" | "pending";

export default function Conversations() {
  const { tenant } = useParams();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");

  const { data, loading, error } = useFetch<ConversationInfo[]>(
    () => api.conversations(tenant!),
    [tenant]
  );

  const rows = useMemo(() => {
    let r = data ?? [];
    if (filter === "multiturn") r = r.filter((c) => c.is_multiturn);
    if (filter === "singleturn") r = r.filter((c) => !c.is_multiturn);
    if (filter === "pending") r = r.filter((c) => (c.pending_review ?? 0) > 0);
    if (q.trim())
      r = r.filter(
        (c) =>
          c.conversation_id.toLowerCase().includes(q.toLowerCase()) ||
          (c.subscriber_id ?? "").toLowerCase().includes(q.toLowerCase())
      );
    return r;
  }, [data, filter, q]);

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Conversations</h1>
          <div className="sub">가입자 콜봇의 대화이력 — 싱글턴·멀티턴 세션별 평가</div>
        </div>
      </div>

      <div className="card card-pad">
        {/* 필터 바 */}
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
          <div className="seg-mini" style={{ display: "inline-flex", border: "1px solid var(--border-strong)", borderRadius: 7, overflow: "hidden" }}>
            {([
              ["all", "전체"],
              ["multiturn", "멀티턴"],
              ["singleturn", "싱글턴"],
              ["pending", "검수 대기"],
            ] as [Filter, string][]).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setFilter(k)}
                style={{
                  border: "none", padding: "6px 13px", fontSize: 13, cursor: "pointer",
                  fontFamily: "inherit",
                  background: filter === k ? "var(--accent)" : "var(--surface)",
                  color: filter === k ? "#fff" : "var(--text-muted)",
                  fontWeight: filter === k ? 600 : 400,
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <input
            placeholder="세션 ID·가입자 검색"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            style={{ maxWidth: 220 }}
          />
          <span className="faint" style={{ fontSize: 12, marginLeft: "auto" }}>
            {data ? `${rows.length} / ${data.length}건` : ""}
          </span>
        </div>

        {loading && <div className="muted" style={{ padding: 24 }}>로딩 중…</div>}
        {error && <div className="error" style={{ padding: 12 }}>{error}</div>}

        {data && rows.length === 0 && (
          <EmptyState icon="💬" title="대화가 없습니다" desc="조건에 맞는 세션이 없습니다." />
        )}

        {data && rows.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>세션 ID</th>
                <th>유형</th>
                <th>가입자</th>
                <th>턴</th>
                <th>세션 점수</th>
                <th>검수</th>
                <th>일시</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const ss = c.session_scores ?? {};
                return (
                  <tr
                    key={c.conversation_id}
                    className="clickable"
                    onClick={() => navigate(`/t/${tenant}/conversations/${c.conversation_id}`)}
                  >
                    <td className="mono">{c.conversation_id}</td>
                    <td>
                      <span className={`badge ${c.is_multiturn ? "info" : "outline"}`}>
                        {c.is_multiturn ? "멀티턴" : "싱글턴"}
                      </span>
                    </td>
                    <td>{c.subscriber_id}</td>
                    <td>{c.turn_count}</td>
                    <td>
                      {ss.resolution !== undefined ? (
                        <span style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                          <ScoreBadge score={ss.resolution} label="해결" />
                          {ss.escalation_handling === 0 && (
                            <span className="badge bad">에스컬레이션</span>
                          )}
                        </span>
                      ) : (
                        <span className="faint">싱글턴</span>
                      )}
                    </td>
                    <td>
                      {(c.pending_review ?? 0) > 0 ? (
                        <span className="badge warn">대기 {c.pending_review}</span>
                      ) : (
                        <span className="badge ok">완료</span>
                      )}
                    </td>
                    <td className="faint" style={{ fontSize: 12 }}>
                      {c.started_at?.slice(0, 16).replace("T", " ")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
