import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import type { KbStatus } from "../types";

const SOURCE_TYPES = ["정책", "FAQ", "약관", "매뉴얼", "상품정보", "기타"];
const ACCEPT_EXT = ".txt,.md,.markdown,.rst,.log,.text,.csv,.tsv,.json,.xml,.yaml,.yml,.html,.htm,.pdf,.docx,.xlsx,.xlsm";

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
  const [kb, setKb] = useState<KbStatus | null>(null);

  // 폼 상태
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [sourceType, setSourceType] = useState(SOURCE_TYPES[0]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 파일 업로드 상태
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  useEffect(() => { if (data) setKb(data); }, [data]);

  if (loading) return <div className="content"><div className="muted" style={{ padding: 40 }}>로딩 중…</div></div>;
  if (!kb) return <div className="content"><EmptyState icon="📚" title="KB 데이터 없음" /></div>;

  const recallTone = kb.avg_context_recall >= 0.8 ? "var(--ok)" : kb.avg_context_recall >= 0.6 ? "var(--warn)" : "var(--bad)";
  const built = kb.built_documents ?? [];

  const addDoc = async () => {
    if (!title.trim() || !content.trim()) {
      setErr("제목과 내용을 모두 입력하세요.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const updated = await api.kbAddDocument(tenant!, { title: title.trim(), content: content.trim(), source_type: sourceType });
      setKb(updated);
      setTitle("");
      setContent("");
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const removeDoc = async (docId: string) => {
    setBusy(true);
    try {
      setKb(await api.kbDeleteDocument(tenant!, docId));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const uploadFiles = async (fileList: FileList | null) => {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;
    setUploading(true);
    setUploadMsg(null);
    setErr(null);
    const ok: string[] = [];
    const failed: string[] = [];
    let latest: KbStatus | null = null;
    // 파일별 개별 업로드 → 개별 성공/실패 피드백
    for (const f of files) {
      try {
        latest = await api.kbUploadDocuments(tenant!, [f]);  // 포맷 자동 감지(PDF/Word/Excel 등)
        ok.push(f.name);
      } catch (e) {
        failed.push(`${f.name} (${String(e).replace("Error: ", "")})`);
      }
    }
    if (latest) setKb(latest);
    setUploadMsg(
      `${ok.length}개 업로드 완료${ok.length ? `: ${ok.join(", ")}` : ""}` +
      (failed.length ? ` · 실패 ${failed.length}: ${failed.join("; ")}` : "")
    );
    if (failed.length && !ok.length) setErr(failed.join("; "));
    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Knowledge Base</h1>
          <div className="sub">고객사 지식을 구축하고 검색 커버리지를 점검합니다.</div>
        </div>
      </div>

      {/* 현황 */}
      <div className="row" style={{ marginBottom: 18, flexWrap: "wrap" }}>
        <Stat label="문서(소스)" value={kb.document_count} />
        <Stat label="청크" value={kb.chunk_count} />
        <Stat label="구축 문서" value={kb.built_document_count ?? built.length} tone="var(--accent)" />
        <Stat label="평균 검색 재현율" value={`${(kb.avg_context_recall * 100).toFixed(0)}%`} tone={recallTone} />
        <Stat label="커버리지 갭" value={(kb.coverage_gaps ?? []).length} tone={(kb.coverage_gaps ?? []).length ? "var(--bad)" : "var(--ok)"} />
      </div>

      {/* 고객사 지식 구축 */}
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <h3>📥 고객사 지식 구축</h3>
        <div className="card-sub">파일을 업로드하거나 내용을 직접 입력하면 청킹되어 KB에 추가됩니다 (CP2 인덱싱 입력).</div>

        {/* 파일 업로드 (드래그 앤 드롭 + 선택) */}
        <input ref={fileInputRef} type="file" multiple accept={ACCEPT_EXT}
          style={{ display: "none" }}
          onChange={(e) => uploadFiles(e.target.files)} />
        <div
          className="kb-dropzone"
          data-drag={dragOver ? "1" : "0"}
          onClick={() => !uploading && fileInputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); uploadFiles(e.dataTransfer.files); }}>
          <div style={{ fontSize: 26 }}>{uploading ? "⏳" : "📎"}</div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>
            {uploading ? "업로드 중…" : "파일을 끌어다 놓거나 클릭해 선택"}
          </div>
          <div className="faint" style={{ fontSize: 11.5, marginTop: 2 }}>
            지원: TXT · MD · CSV · TSV · JSON · XML · YAML · HTML · PDF · Word(docx) · Excel(xlsx) · 다중 선택 가능 (최대 5MB/파일)
          </div>
        </div>
        {uploadMsg && (
          <div className="muted" style={{ fontSize: 12, margin: "8px 0 4px" }}>📋 {uploadMsg}</div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "14px 0" }}>
          <hr className="sep" style={{ flex: 1, margin: 0 }} />
          <span className="faint" style={{ fontSize: 11.5 }}>또는 직접 입력</span>
          <hr className="sep" style={{ flex: 1, margin: 0 }} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 160px", gap: 12, marginBottom: 12 }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>문서 제목</label>
            <input type="text" placeholder="예: 5G 요금제 안내" value={title}
              onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>유형</label>
            <select value={sourceType} onChange={(e) => setSourceType(e.target.value)}>
              {SOURCE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
        </div>
        <div className="field">
          <label>문서 내용</label>
          <textarea rows={6} placeholder="고객 응대에 사용할 지식(정책·약관·FAQ 등)을 붙여넣으세요."
            value={content} onChange={(e) => setContent(e.target.value)} />
          <div className="hint">
            글자수 {content.length.toLocaleString()} · 예상 청크 약 {Math.max(content.trim() ? 1 : 0, Math.ceil(content.trim().length / 150))}개
          </div>
        </div>
        {err && <div className="cred-err" style={{ marginBottom: 10 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn primary" onClick={addDoc} disabled={busy || !title.trim() || !content.trim()}>
            {busy ? "추가 중…" : "+ 지식 추가"}
          </button>
          <button className="btn" onClick={() => { setTitle(""); setContent(""); setErr(null); }} disabled={busy}>
            지우기
          </button>
        </div>
      </div>

      {/* 구축된 문서 목록 */}
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <h3>📚 구축된 지식 문서 ({built.length})</h3>
        {built.length === 0 ? (
          <div className="muted" style={{ padding: 16 }}>아직 추가된 지식 문서가 없습니다. 위에서 첫 문서를 추가하세요.</div>
        ) : (
          <table>
            <thead><tr><th>제목</th><th>유형</th><th>글자수</th><th>청크</th><th>등록일</th><th /></tr></thead>
            <tbody>
              {built.map((d) => (
                <tr key={d.doc_id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{d.title}</div>
                    <div className="faint" style={{ fontSize: 11.5 }}>{d.content_preview}</div>
                  </td>
                  <td><span className="badge accent">{d.source_type}</span></td>
                  <td className="mono" style={{ fontSize: 12 }}>{d.char_count.toLocaleString()}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{d.chunk_count}</td>
                  <td className="faint" style={{ fontSize: 12 }}>{d.created_at?.slice(0, 10)}</td>
                  <td>
                    <button className="btn ghost sm" style={{ color: "var(--bad)" }}
                      disabled={busy} onClick={() => removeDoc(d.doc_id)}>삭제</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 인덱스 정보 */}
      <div className="card card-pad" style={{ marginBottom: 18 }}>
        <h3>인덱스 정보</h3>
        <div style={{ display: "flex", gap: 28, flexWrap: "wrap", marginTop: 8 }}>
          <div>
            <div className="faint" style={{ fontSize: 11.5 }}>마지막 인덱싱</div>
            <div style={{ fontSize: 13.5 }}>{kb.last_indexed_at?.slice(0, 19).replace("T", " ") ?? "—"}</div>
          </div>
          <div>
            <div className="faint" style={{ fontSize: 11.5 }}>소스 유형</div>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 2 }}>
              {(kb.source_types ?? []).map((s) => <span key={s} className="badge outline">{s}</span>)}
            </div>
          </div>
        </div>
      </div>

      {/* 커버리지 갭 */}
      <div className="card card-pad">
        <h3>🕳️ 검색 커버리지 갭</h3>
        <div className="card-sub">검색 재현율이 낮았던 질의 — KB 보강이 필요한 영역</div>
        {(kb.coverage_gaps ?? []).length === 0 ? (
          <div className="muted" style={{ padding: 16 }}>커버리지 갭이 없습니다. 검색이 잘 작동하고 있습니다. ✅</div>
        ) : (
          <table>
            <thead><tr><th>질의</th><th>세션</th><th>검색 재현율</th><th /></tr></thead>
            <tbody>
              {(kb.coverage_gaps ?? []).map((g, i) => (
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
          💡 재현율이 낮은 질의는 KB에 관련 문서가 부족하거나 청킹/임베딩 개선이 필요할 수 있습니다. 위 "고객사 지식 구축"에서 보강하세요.
        </p>
      </div>
    </div>
  );
}
