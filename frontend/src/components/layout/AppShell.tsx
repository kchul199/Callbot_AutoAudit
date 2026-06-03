import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TenantSwitcher } from "./TenantSwitcher";

const CRUMB: Record<string, string> = {
  "": "Overview",
  conversations: "Conversations",
  runs: "Run Evaluation",
  review: "Review",
  evaluations: "Evaluations",
  trends: "Trends",
  kb: "Knowledge Base",
  settings: "Settings",
};

export function AppShell() {
  const loc = useLocation();
  // /t/:tenant/<section>/... → section 추출
  const parts = loc.pathname.split("/").filter(Boolean); // ["t",":tenant","section",...]
  const section = parts[2] ?? "";
  const crumb = CRUMB[section] ?? "Overview";

  return (
    <div className="app">
      <Sidebar />
      <div className="main">
        <header className="topbar">
          <div className="crumb">
            <b>{crumb}</b>
          </div>
          <div className="spacer" />
          <TenantSwitcher />
          <span className="env-badge">MOCK</span>
        </header>
        <Outlet />
      </div>
    </div>
  );
}
