import { useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import { ScoreBadge } from "../components/ScoreBadge";
import type { AuditRunResult, KbStatus } from "../types";

const SAMPLE = `고객: 5G 프리미엄 요금제 얼마예요?
콜봇: 5G 프리미엄은 월 69,000원이고 데이터 무제한입니다.
[정답] 5G 프리미엄 요금제는 월 69,000원, 데이터 무제한입니다.
고객: 해지하면 위약금 있나요?
콜봇: 네, 위약금은 약정 잔여 기간에 비례합니다.`;

const ACCEPT = ".txt,.json,.csv";

function labelKo(m: string): string {
  const t: Record<string, string> = {
    faithfulness: "충실성(KB근거)", answer_relevance: "관련성",
    context_precision: "정밀도", context_recall: "재현율(KB커버)",
    answer_correctness: "정답성", safety_compliance: "안전성",
  };
  return t[m] ?? m;
}

export default function AuditConversation() {
  const { tenant } = useParams();
  const navigate = useNavigate();
  const { data: kb } = useFetch<KbStatus>(() => api.kb(tenant!), [tenant]);

  const [mode, setMode] = useState<"text" | "file">("text");
  const [text, setText] = useState(SAMPLE);
  const [convId, setConvId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<AuditRunResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const builtDocs = kb?.built_document_count ?? 0;
  const kbReady = builtDocs > 0;

  const runText = async () => {
    if (!text.trim()) { setErr("대화 내용을 입력하세요."); return; }
    setBusy(true); setErr(null); setResult(null);
    try {
      setResult(await api.auditConversation(tenant!, { text, conversation_id: convId, ground_truths: [], enable: [] }));
    } catch (e) { setErr(String(e).replace("Error: ", "")); }
    finally { setBusy(false); }
  };

  const runFile = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true); setErr(null); setResult(null);
    try {
      setResult(await api.auditConversationUpload(tenant!, f, convId));
    } catch (e) { setErr(String(e).replace("Error: ", "")); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>대화 검증</h1>
          <div className="sub">콜봇 대화를 입력받아 <b>고객사 KB 문서를 근거로</b> 품질을 검증합니다.</div>
        </div>
      </div>

      {/* KB 준비 상태 */}
      <div className="card card-pad" style={{ marginBottom: 16, borderLeft: `3px solid var(--${kbReady ? "ok" : "warn"})` }}>
        {kbReady ? (
          <div style={{ fontSize: 13 }}>
            ✅ <b>{tenant}</b> 지식베이스 준비됨 — 구축 문서 <b>{builtDocs}</b>개 · 청크 <b>{kb?.built_chunk_count ?? 0}</b>개를 검색 근거로 사용합니다.
          </div>
        ) : (
          <div style={{ fontSize: 13 }}>
            ⚠️ 구축된 KB 문서가 없습니다. 먼저{" "}
            <a onClick={() => navigate(`/t/${tenant}/kb`)} style={{ color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}>
              Knowledge Base에서 지식을 추가
            </a>
            해야 대화를 검증할 수 있습니다.
          </div>
        )}
      </div>

      {/* 입력 */}
      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
          <div style={{ display: "inline-flex", border: "1px solid var(--border-strong)", borderRadius: 7, overflow: "hidden" }}>
            {([["text", "✏️ 직접 입력"], ["file", "📎 파일 업로드"]] as [string, string][]).map(([v, l]) => (
              <button key={v} onClick={() => setMode(v as "text" | "file")}
                style={{
                  border: "none", padding: "7px 14px", fontSize: 12.5, cursor: "pointer", fontFamily: "inherit",
                  background: mode === v ? "var(--accent)" : "var(--surface)",
                  color: mode === v ? "#fff" : "var(--text-muted)", fontWeight: mode === v ? 600 : 400,
                }}>{l}</button>
            ))}
          </div>
          <input type="text" placeholder="대화 ID (선택)" value={convId} onChange={(e) => setConvId(e.target.value)}
            style={{ maxWidth: 200 }} />
        </div>

        {mode === "text" ? (
          <>
            <div className="field">
              <label>대화 내용 (transcript 또는 JSON)</label>
              <textarea rows={9} value={text} onChange={(e) => setText(e.target.value)}
                style={{ fontFamily: "var(--mono)", fontSize: 12.5 }} />
              <div className="hint">
                형식: <code>고객:</code> / <code>콜봇:</code> 줄, 선택적 <code>[정답] …</code>(정답성 평가) · 또는 JSON <code>{`{"turns":[…]}`}</code>
              </div>
            </div>
            <button className="btn primary" onClick={runText} disabled={busy || !kbReady}>
              {busy ? "검증 중…" : "🎯 KB 근거로 검증 실행"}
            </button>
          </>
        ) : (
          <>
            <input ref={fileRef} type="file" accept={ACCEPT} style={{ display: "none" }}
              onChange={(e) => runFile(e.target.files?.[0])} />
            <div className="kb-dropzone" data-drag={dragOver ? "1" : "0"}
              onClick={() => !busy && kbReady && fileRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => { e.preventDefault(); setDragOver(false); if (kbReady) runFile(e.dataTransfer.files?.[0]); }}>
              <div style={{ fontSize: 26 }}>{busy ? "⏳" : "📎"}</div>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{busy ? "검증 중…" : "대화 파일을 끌어다 놓거나 클릭"}</div>
              <div className="faint" style={{ fontSize: 11.5, marginTop: 2 }}>지원: TXT(로그/transcript) · JSON · CSV</div>
            </div>
          </>
        )}

        {err && <div className="cred-err" style={{ marginTop: 12 }}>{err}</div>}
      </div>

      {/* 결과 */}
      {result && (
        <div className="card card-pad">
          <h3>✅ 검증 결과</h3>
          <div className="card-sub">{result.message} · run <span className="mono">{result.run_id}</span></div>
          <table style={{ marginTop: 6 }}>
            <thead><tr><th>메트릭</th><th>평균</th><th>SLA 통과율</th><th>미달</th></tr></thead>
            <tbody>
              {(result.metrics ?? []).map((m) => (
                <tr key={m.metric}>
                  <td>{labelKo(m.metric)} <span className="faint mono" style={{ fontSize: 11 }}>{m.metric}</span></td>
                  <td><ScoreBadge score={m.mean} /></td>
                  <td>{(m.sla_pass_rate * 100).toFixed(0)}%</td>
                  <td>{m.below_sla_count > 0
                    ? <span className="badge bad">{m.below_sla_count}/{m.total_count}</span>
                    : <span className="badge ok">0/{m.total_count}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
            <button className="btn primary sm" onClick={() => navigate(`/t/${tenant}/conversations/${result.conversation_id}`)}>
              대화 상세·근거 보기 →
            </button>
            <button className="btn sm" onClick={() => navigate(`/t/${tenant}/evaluations`)}>
              Evaluations에서 탐색
            </button>
            <button className="btn ghost sm" onClick={() => navigate(`/t/${tenant}/review`)}>
              검수 큐로
            </button>
          </div>
          <p className="faint" style={{ fontSize: 11.5, marginTop: 10 }}>
            💡 <b>충실성(KB근거)</b>이 낮으면 KB에 없는 내용(환각), <b>재현율(KB커버)</b>이 낮으면 KB 보강이 필요합니다.
          </p>
        </div>
      )}
    </div>
  );
}
