import { NavLink } from "react-router-dom";
import { useTenant } from "../../app/TenantContext";
import { useTheme } from "../../app/ThemeContext";

interface NavDef {
  to: string;
  icon: string;
  label: string;
  badgeKey?: "review";
}

const SECTIONS: { title: string; items: NavDef[] }[] = [
  {
    title: "모니터링",
    items: [
      { to: "", icon: "🏠", label: "Overview" },
      { to: "conversations", icon: "💬", label: "Conversations" },
    ],
  },
  {
    title: "평가",
    items: [
      { to: "audit", icon: "🎯", label: "대화 검증" },
      { to: "runs", icon: "▶", label: "Run Evaluation" },
      { to: "review", icon: "✅", label: "Review", badgeKey: "review" },
      { to: "evaluations", icon: "🔍", label: "Evaluations" },
      { to: "trends", icon: "📈", label: "Trends" },
    ],
  },
  {
    title: "설정",
    items: [
      { to: "kb", icon: "📚", label: "Knowledge Base" },
      { to: "settings", icon: "⚙️", label: "Settings" },
    ],
  },
];

export function Sidebar() {
  const { current } = useTenant();
  const { theme, toggle } = useTheme();
  const base = current ? `/t/${current.tenant_id}` : "/t/_";
  const pending = current?.pending_review_count ?? 0;

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="logo">🤖</span>
        <div>
          CallBot AutoAudit
          <small>품질 평가 콘솔</small>
        </div>
      </div>
      <nav className="nav">
        {SECTIONS.map((sec) => (
          <div key={sec.title}>
            <div className="nav-section">{sec.title}</div>
            {sec.items.map((it) => (
              <NavLink
                key={it.to}
                to={it.to ? `${base}/${it.to}` : base}
                end={!it.to}
                className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
              >
                <span className="ico">{it.icon}</span> {it.label}
                {it.badgeKey === "review" && pending > 0 && (
                  <span className="badge bad badge-count">{pending}</span>
                )}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-foot">
        <span className="theme-toggle" onClick={toggle}>
          {theme === "dark" ? "☀️ 라이트" : "🌙 다크"}
        </span>
      </div>
    </aside>
  );
}
