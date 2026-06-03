export function EmptyState({
  icon,
  title,
  desc,
  milestone,
}: {
  icon: string;
  title: string;
  desc?: string;
  milestone?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "72px 24px",
        textAlign: "center",
        color: "var(--text-muted)",
      }}
    >
      <div style={{ fontSize: 44, marginBottom: 14, opacity: 0.8 }}>{icon}</div>
      <div style={{ fontSize: 17, fontWeight: 700, color: "var(--text)", marginBottom: 6 }}>
        {title}
      </div>
      {desc && <div style={{ fontSize: 13.5, maxWidth: 420 }}>{desc}</div>}
      {milestone && (
        <span className="badge accent" style={{ marginTop: 16 }}>
          {milestone} 에서 구현 예정
        </span>
      )}
    </div>
  );
}
