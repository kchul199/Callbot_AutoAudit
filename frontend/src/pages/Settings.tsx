import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import CredentialModal from "../components/CredentialModal";
import type { TenantSettings } from "../types";

const PROFILES = ["기본", "빠른 점검", "고신뢰", "검색 진단", "안전성 감사"];
const JUDGES = [
  ["anthropic", "Anthropic Claude"], ["openai", "OpenAI GPT-4o"],
  ["gemini", "Google Gemini"], ["azure", "Azure OpenAI"],
];
function labelKo(m: string): string {
  const t: Record<string, string> = {
    faithfulness: "충실성", answer_relevance: "관련성",
    context_precision: "정밀도", context_recall: "재현율",
  };
  return t[m] ?? m;
}

export default function Settings() {
  const { tenant } = useParams();
  const { data } = useFetch<TenantSettings>(() => api.settings(tenant!), [tenant]);
  const [draft, setDraft] = useState<TenantSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [credProvider, setCredProvider] = useState<string | null>(null);

  useEffect(() => { if (data) setDraft(structuredClone(data)); }, [data]);

  if (!draft) return <div className="content"><div className="muted" style={{ padding: 40 }}>로딩 중…</div></div>;

  const setSla = (m: string, v: number) =>
    setDraft({ ...draft, sla_thresholds: { ...draft.sla_thresholds, [m]: v } });

  const save = async () => {
    setSaving(true);
    try {
      await api.saveSettings(tenant!, draft);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      alert(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <div className="sub">{tenant} 가입자의 SLA·평가 옵션·Judge·알림을 설정합니다.</div>
        </div>
        <div className="spacer" style={{ flex: 1 }} />
        {saved && <span className="badge ok" style={{ alignSelf: "center" }}>✓ 저장됨</span>}
        <button className="btn primary" onClick={save} disabled={saving}>{saving ? "저장 중…" : "변경 저장"}</button>
      </div>

      {/* SLA 임계값 */}
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <h3>SLA 임계값</h3>
        <div className="card-sub">메트릭별 통과 기준 — 이 값 미만이면 SLA 미달로 플래깅됩니다.</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))", gap: 14 }}>
          {Object.entries(draft.sla_thresholds ?? {}).map(([m, v]) => (
            <div className="field" key={m} style={{ marginBottom: 0 }}>
              <label>{labelKo(m)} <span className="faint mono">{m}</span></label>
              <input type="number" min={0} max={1} step={0.05} value={v as number}
                onChange={(e) => setSla(m, parseFloat(e.target.value))} />
            </div>
          ))}
        </div>
      </div>

      {/* 평가 옵션 프로필 + Judge */}
      <div className="row" style={{ marginBottom: 18, gap: 16, flexWrap: "wrap" }}>
        <div className="card card-pad col" style={{ minWidth: 280 }}>
          <h3>평가 옵션 프로필</h3>
          <div className="card-sub">새 평가 실행 시 기본 적용될 방법론 프리셋</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {PROFILES.map((p) => (
              <span key={p} onClick={() => setDraft({ ...draft, eval_profile: p })}
                style={{
                  padding: "7px 13px", borderRadius: 999, fontSize: 12.5, cursor: "pointer",
                  border: "1px solid var(--border-strong)",
                  background: draft.eval_profile === p ? "var(--accent)" : "transparent",
                  color: draft.eval_profile === p ? "#fff" : "var(--text-muted)",
                }}>{p}</span>
            ))}
          </div>
        </div>

        <div className="card card-pad col" style={{ minWidth: 280 }}>
          <h3>기본 Judge 모델</h3>
          <div className="card-sub">평가 실행의 기본 평가자</div>
          <select value={draft.default_judge}
            onChange={(e) => setDraft({ ...draft, default_judge: e.target.value })}>
            {JUDGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
      </div>

      {/* Judge 자격증명 */}
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <h3>Judge 자격증명</h3>
        <div className="card-sub">provider를 클릭해 API 키를 등록·관리합니다 (환경변수 또는 수동 등록)</div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {Object.entries(draft.judge_credentials ?? {}).map(([p, ok]) => {
            const detail = draft.credential_details?.[p];
            return (
              <div key={p} className="cred-chip" role="button" tabIndex={0}
                onClick={() => setCredProvider(p)}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setCredProvider(p)}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{p}</span>
                <span className={`badge ${ok ? "ok" : "warn"}`}>{ok ? "등록됨" : "키 미등록"}</span>
                {detail?.source === "manual" && detail.masked_key && (
                  <span className="mono faint">{detail.masked_key}</span>
                )}
                <span className="cred-chip-cog" aria-hidden>⚙️</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* 알림 + 검수자 */}
      <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
        <div className="card card-pad col" style={{ minWidth: 280 }}>
          <h3>알림</h3>
          <div className="field" style={{ marginBottom: 10 }}>
            <label>Slack Webhook URL</label>
            <input type="text" placeholder="https://hooks.slack.com/..." value={draft.slack_webhook}
              onChange={(e) => setDraft({ ...draft, slack_webhook: e.target.value })} />
          </div>
          <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 7 }}>
            <input type="checkbox" checked={draft.notify_on_regression}
              onChange={(e) => setDraft({ ...draft, notify_on_regression: e.target.checked })} />
            품질 회귀 감지 시 알림
          </label>
        </div>

        <div className="card card-pad col" style={{ minWidth: 280 }}>
          <h3>검수자</h3>
          <div className="card-sub">이 가입자의 평가 검수 담당자</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {(draft.reviewers ?? []).map((r) => <span key={r} className="badge accent">{r}</span>)}
            {(draft.reviewers ?? []).length === 0 && <span className="muted">배정된 검수자 없음</span>}
          </div>
        </div>
      </div>

      {/* Judge 자격증명 등록 모달 */}
      {credProvider && (
        <CredentialModal
          tenant={tenant!}
          provider={credProvider}
          detail={draft.credential_details?.[credProvider]}
          onSaved={(updated) => setDraft(updated)}
          onClose={() => setCredProvider(null)}
        />
      )}
    </div>
  );
}
