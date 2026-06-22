import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CredentialInfo, TenantSettings } from "../types";

const PROVIDER_META: Record<string, { label: string; keyHint: string; azure?: boolean; baseUrl?: boolean }> = {
  anthropic: { label: "Anthropic Claude", keyHint: "sk-ant-...", baseUrl: true },
  openai: { label: "OpenAI GPT-4o", keyHint: "sk-...", baseUrl: true },
  gemini: { label: "Google Gemini", keyHint: "AIza..." },
  azure: { label: "Azure OpenAI", keyHint: "Azure API Key", azure: true },
};

interface Props {
  tenant: string;
  provider: string;
  detail?: CredentialInfo;
  onSaved: (settings: TenantSettings) => void;
  onClose: () => void;
}

export default function CredentialModal({ tenant, provider, detail, onSaved, onClose }: Props) {
  const meta = PROVIDER_META[provider] ?? { label: provider, keyHint: "API Key" };
  const isManual = detail?.source === "manual";

  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [baseUrl, setBaseUrl] = useState(detail?.base_url ?? "");
  const [endpoint, setEndpoint] = useState(detail?.endpoint ?? "");
  // 빈 문자열도 기본값으로 대체되도록 || 사용 (?? 는 ""를 통과시킴)
  const [apiVersion, setApiVersion] = useState(detail?.api_version || "2024-06-01");
  const [deployment, setDeployment] = useState(detail?.deployment ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // ESC로 닫기
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const save = async () => {
    if (!apiKey.trim()) {
      setErr("API 키를 입력하세요.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const updated = await api.saveCredential(tenant, provider, {
        api_key: apiKey.trim(),
        base_url: meta.baseUrl ? baseUrl.trim() : "",
        endpoint: meta.azure ? endpoint.trim() : "",
        api_version: meta.azure ? apiVersion.trim() : "",
        deployment: meta.azure ? deployment.trim() : "",
      });
      onSaved(updated);
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setErr(null);
    try {
      const updated = await api.deleteCredential(tenant, provider);
      onSaved(updated);
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="modal-head">
          <h3>{meta.label} 자격증명</h3>
          <span className="modal-close" onClick={onClose} aria-label="닫기">✕</span>
        </div>

        <div className="modal-body">
          {/* 현재 상태 */}
          <div className="cred-status">
            <span className="muted" style={{ fontSize: 12.5 }}>현재 상태</span>
            {detail?.registered ? (
              <span className="badge ok">
                {detail.source === "manual" ? "등록됨 (수동)" : detail.source === "env" ? "등록됨 (환경변수)" : "등록됨"}
              </span>
            ) : (
              <span className="badge warn">키 미등록</span>
            )}
            {isManual && detail?.masked_key && (
              <span className="mono faint" style={{ marginLeft: "auto" }}>{detail.masked_key}</span>
            )}
          </div>

          {detail?.source === "env" && (
            <div className="cred-note">
              환경변수로 이미 등록되어 있습니다. 아래에 키를 입력하면 이 가입자에 한해 덮어씁니다.
            </div>
          )}

          {/* API Key */}
          <div className="field" style={{ marginTop: 14 }}>
            <label>API Key {isManual && <span className="faint">(재입력 시 교체)</span>}</label>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                type={showKey ? "text" : "password"}
                placeholder={meta.keyHint}
                value={apiKey}
                autoFocus
                onChange={(e) => setApiKey(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && save()}
              />
              <button type="button" className="btn sm" onClick={() => setShowKey((s) => !s)}>
                {showKey ? "숨김" : "표시"}
              </button>
            </div>
            <div className="hint">입력한 키는 마스킹되어 저장되며 평문은 화면에 다시 표시되지 않습니다.</div>
          </div>

          {/* Azure 전용 필드 */}
          {meta.azure && (
            <>
              <div className="field">
                <label>Endpoint</label>
                <input type="text" placeholder="https://{resource}.openai.azure.com"
                  value={endpoint} onChange={(e) => setEndpoint(e.target.value)} />
              </div>
              <div className="row">
                <div className="field col">
                  <label>API Version</label>
                  <input type="text" placeholder="2024-06-01"
                    value={apiVersion} onChange={(e) => setApiVersion(e.target.value)} />
                </div>
                <div className="field col">
                  <label>Deployment</label>
                  <input type="text" placeholder="gpt-4o"
                    value={deployment} onChange={(e) => setDeployment(e.target.value)} />
                </div>
              </div>
            </>
          )}

          {/* 선택: Base URL (openai/anthropic 호환 게이트웨이) */}
          {meta.baseUrl && (
            <div className="field">
              <label>Base URL <span className="faint">(선택 — 호환 게이트웨이)</span></label>
              <input type="text" placeholder="https://api.openai.com/v1"
                value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            </div>
          )}

          {err && <div className="cred-err">{err}</div>}
        </div>

        <div className="modal-foot">
          {isManual && (
            <button className="btn ghost" onClick={remove} disabled={busy} style={{ color: "var(--bad)", marginRight: "auto" }}>
              삭제
            </button>
          )}
          <button className="btn" onClick={onClose} disabled={busy}>취소</button>
          <button className="btn primary" onClick={save} disabled={busy || !apiKey.trim()}>
            {busy ? "저장 중…" : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}
