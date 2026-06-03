import { useNavigate, useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import type { KbStatus } from "../types";

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="card card-pad" style={{ flex: 1, minWidth: 150 }}>
      <div style={{ fontSize: 26, fontWeight: 700, color: tone ?? "var(--text)" }}>{value}</div>
      <div className="muted" style={{ fontSize: 12.5 }}>{label}</div>
    </div>
  );
}

export default function KnowledgeBase() {
  const { tenant } = useParams();
  const navigate = useNavigate();
  const { data, loading } = useFetch<KbStatus>(() => api.kb(tenant!), [tenant]);

  if (loading) return <div className="content"><div className="muted" style={{ padding: 40 }}>로딩 중…</div></div>;
  if (!data) return <div className="content"><EmptyState icon="📚" title="KB 데이터 없음" /></div>;

  const recallTone = data.avg_context_recall >= 0.8 ? "var(--ok)" : data.avg_context_recall >= 0.6 ? "var(--warn)" : "var(--bad)";

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Knowledge Base</h1>
          <div className="sub">가입자 지식베이스 현황과 검색 커버리지 갭을 확인합니다.</div>
        </div>
      </div>

      {/* 현황 */}
      <div className="row" style={{ marginBottom: 18, flexWrap: "wrap" }}>
        <Stat label="문서(소스)" value={data.document_count} />
        <Stat label="청크" value={data.chunk_count} />
        <Stat label="평균 검색 재현율" value={`${(data.avg_context_recall * 100).toFixed(0)}%`} tone={recallTone} />
        <Stat label="커버리지 갭" value={(data.coverage_gaps ?? []).length} tone={(data.coverage_gaps ?? []).length ? "var(--bad)" : "var(--ok)"} />
      </div>

      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <h3>인덱스 정보</h3>
        <div style={{ display: "flex", gap: 28, flexWrap: "wrap", marginTop: 8 }}>
          <div>
            <div className="faint" style={{ fontSize: 11.5 }}>마지막 인덱싱</div>
            <div style={{ fontSize: 13.5 }}>{data.last_indexed_at?.slice(0, 19).replace("T", " ") ?? "—"}</div>
          </div>
          <div>
            <div className="faint" style={{ fontSize: 11.5 }}>소스 유형</div>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 2 }}>
              {(data.source_types ?? []).map((s) => <span key={s} className="badge outline">{s}</span>)}
            </div>
          </div>
        </div>
      </div>

      {/* 커버리지 갭 */}
      <div className="card card-pad">
        <h3>🕳️ 검색 커버리지 갭</h3>
        <div className="card-sub">검색 재현율이 낮았던 질의 — KB 보강이 필요한 영역</div>
        {(data.coverage_gaps ?? []).length === 0 ? (
          <div className="muted" style={{ padding: 16 }}>커버리지 갭이 없습니다. 검색이 잘 작동하고 있습니다. ✅</div>
        ) : (
          <table>
            <thead><tr><th>질의</th><th>세션</th><th>검색 재현율</th><th /></tr></thead>
            <tbody>
              {(data.coverage_gaps ?? []).map((g, i) => (
                <tr key={i} className="clickable" onClick={() => g.conversation_id && navigate(`/t/${tenant}/conversations/${g.conversation_id}`)}>
                  <td>{g.query}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{g.conversation_id}</td>
                  <td><span className="badge bad">{(g.context_recall * 100).toFixed(0)}%</span></td>
                  <td className="faint">›</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="faint" style={{ fontSize: 11.5, marginTop: 10 }}>
          💡 재현율이 낮은 질의는 KB에 관련 문서가 부족하거나 청킹/임베딩 개선이 필요할 수 있습니다.
        </p>
      </div>
    </div>
  );
}
