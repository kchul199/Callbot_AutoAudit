import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { useTenant } from "./app/TenantContext";
import { AppShell } from "./components/layout/AppShell";
import Overview from "./pages/Overview";
import Conversations from "./pages/Conversations";
import ConversationDetail from "./pages/ConversationDetail";
import AuditConversation from "./pages/AuditConversation";
import RunEvaluation from "./pages/RunEvaluation";
import Review from "./pages/Review";
import Evaluations from "./pages/Evaluations";
import Trends from "./pages/Trends";
import KnowledgeBase from "./pages/KnowledgeBase";
import Settings from "./pages/Settings";

/** 루트 진입 시 첫 tenant로 리다이렉트 */
function RootRedirect() {
  const { current, loading } = useTenant();
  if (loading) return <div style={{ padding: 40, color: "var(--text-muted)" }}>로딩 중…</div>;
  if (!current) return <div style={{ padding: 40 }}>등록된 가입자가 없습니다.</div>;
  return <Navigate to={`/t/${current.tenant_id}`} replace />;
}

/** tenant 파라미터가 유효한지 확인 (없으면 첫 tenant로) */
function TenantGuard() {
  const { tenant } = useParams();
  const { tenants, loading } = useTenant();
  if (loading) return <div style={{ padding: 40, color: "var(--text-muted)" }}>로딩 중…</div>;
  if (tenant !== "_" && tenants.length && !tenants.find((t) => t.tenant_id === tenant)) {
    return <Navigate to="/" replace />;
  }
  return <AppShell />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />
      <Route path="/t/:tenant" element={<TenantGuard />}>
        <Route index element={<Overview />} />
        <Route path="conversations" element={<Conversations />} />
        <Route path="conversations/:id" element={<ConversationDetail />} />
        <Route path="audit" element={<AuditConversation />} />
        <Route path="runs" element={<RunEvaluation />} />
        <Route path="review" element={<Review />} />
        <Route path="evaluations" element={<Evaluations />} />
        <Route path="trends" element={<Trends />} />
        <Route path="kb" element={<KnowledgeBase />} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
