import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTenant } from "../../app/TenantContext";

export function TenantSwitcher() {
  const { tenants, current, setCurrent } = useTenant();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const params = useParams();

  if (!current) return <span className="switcher faint">가입자 없음</span>;

  const select = (id: string) => {
    const t = tenants.find((x) => x.tenant_id === id);
    if (t) {
      setCurrent(t);
      setOpen(false);
      // 같은 섹션 유지하며 tenant만 교체
      const section = params["*"]?.split("/")[0] ?? "";
      navigate(`/t/${t.tenant_id}${section ? `/${section}` : ""}`);
    }
  };

  return (
    <div style={{ position: "relative" }}>
      <div className="switcher" onClick={() => setOpen((o) => !o)}>
        <span className="dot" /> {current.name} <span className="chev">▾</span>
      </div>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            minWidth: 240,
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            boxShadow: "var(--shadow-lg)",
            zIndex: 50,
            padding: 6,
          }}
        >
          {tenants.map((t) => (
            <div
              key={t.tenant_id}
              onClick={() => select(t.tenant_id)}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "8px 11px",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                background:
                  t.tenant_id === current.tenant_id ? "var(--accent-soft)" : "transparent",
              }}
            >
              <span style={{ fontSize: 13.5, fontWeight: 500 }}>{t.name}</span>
              {(t.pending_review_count ?? 0) > 0 && (
                <span className="badge bad">{t.pending_review_count}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
