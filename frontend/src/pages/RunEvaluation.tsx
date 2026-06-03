import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api, useFetch } from "../api/client";
import type { JudgeModel, RunEvalResult } from "../types";

// ---- 정적 정의 ----
const LEVELS = [
  { id: "retrieval", icon: "🔎", name: "Retrieval", desc: "검색 컨텍스트 품질 (precision·recall·순위)" },
  { id: "turn", icon: "💬", name: "턴(답변)", desc: "생성 답변 품질 (faithfulness·relevance)" },
  { id: "session", icon: "🧵", name: "세션", desc: "멀티턴 일관성·해결 여부" },
];

const METRICS_BY_LEVEL: Record<string, { id: string; label: string }[]> = {
  retrieval: [
    { id: "context_precision", label: "Context Precision" },
    { id: "context_recall", label: "Context Recall" },
  ],
  turn: [
    { id: "faithfulness", label: "Faithfulness (환각)" },
    { id: "answer_relevance", label: "Answer Relevance" },
  ],
  session: [
    { id: "resolution", label: "해결 여부" },
    { id: "multiturn_consistency", label: "멀티턴 일관성" },
    { id: "efficiency", label: "대화 효율성" },
    { id: "escalation_handling", label: "에스컬레이션 감지" },
  ],
};

const METHODS = [
  { id: "calibration", icon: "🎯", name: "Calibration", desc: "편향 보정 + G-Eval 기대점수" },
  { id: "ensemble", icon: "🤝", name: "Ensemble", desc: "다중 LLM 교차 + 불일치 에스컬레이션" },
  { id: "claim", icon: "🧩", name: "Claim 분해 (RAGAS)", desc: "답변을 claim 단위로 NLI 판정" },
  { id: "nugget", icon: "💎", name: "Nugget Recall", desc: "질문 핵심정보의 검색 커버리지" },
  { id: "statistics", icon: "📊", name: "Statistics", desc: "부트스트랩 신뢰구간 + 유의성 회귀" },
  { id: "routing", icon: "🔀", name: "Routing", desc: "저신뢰 항목 추가샘플 + 검수 큐" },
  { id: "diagnosis", icon: "🩺", name: "Diagnosis", desc: "검색 vs 생성 책임 2×2 진단" },
  { id: "domain", icon: "🛡️", name: "Domain (안전성)", desc: "PII·컴플라이언스·멀티턴 일관성" },
];

const PRESETS: Record<string, string[]> = {
  "빠른 점검": [],
  "고신뢰": ["calibration", "ensemble", "claim", "statistics"],
  "검색 진단": ["nugget", "diagnosis"],
  "안전성 감사": ["domain", "claim"],
};

const STEPS = ["대상 선택", "Judge 모델", "평가 레벨", "메트릭", "정확도 방법론", "검토 & 실행"];

export default function RunEvaluation() {
  const { tenant } = useParams();
  const { data: judges } = useFetch<JudgeModel[]>(() => api.judges(), []);
  const { data: convos } = useFetch(() => api.conversations(tenant!), [tenant]);

  const [step, setStep] = useState(0);
  const [target, setTarget] = useState("all");
  const [ensemble, setEnsemble] = useState(false);
  const [selJudges, setSelJudges] = useState<string[]>(["mock"]);
  const [temperature, setTemp] = useState(0);
  const [levels, setLevels] = useState<string[]>(["retrieval", "turn", "session"]);
  const [metrics, setMetrics] = useState<string[]>([
    "context_precision", "context_recall", "faithfulness", "answer_relevance",
    "resolution", "multiturn_consistency",
  ]);
  const [methods, setMethods] = useState<string[]>(["claim", "statistics", "diagnosis"]);
  const [preset, setPreset] = useState<string>("");
  const [result, setResult] = useState<RunEvalResult | null>(null);
  const [running, setRunning] = useState(false);

  const convoCount = convos?.length ?? 0;
  const turnCount = useMemo(
    () => (convos ?? []).reduce((a, c) => a + (c.turn_count ?? 0), 0),
    [convos]
  );

  const toggle = (arr: string[], set: (v: string[]) => void, id: string) =>
    set(arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id]);

  const availableMetrics = useMemo(
    () => levels.flatMap((l) => METRICS_BY_LEVEL[l] ?? []),
    [levels]
  );

  const applyPreset = (name: string) => {
    setPreset(name);
    setMethods(PRESETS[name] ?? []);
  };

  const run = async () => {
    setRunning(true);
    try {
      const r = await api.createRun(tenant!, {
        judges: selJudges, ensemble, levels, metrics, methods, target, temperature,
      });
      setResult(r);
    } catch (e) {
      alert(String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>새 평가 실행</h1>
          <div className="sub">평가자(LLM)·대상 레벨·정확도 방법론을 구성해 평가배치를 실행합니다.</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "210px 1fr", gap: 22, alignItems: "start" }}>
        {/* 스텝 레일 */}
        <div style={{ position: "sticky", top: 76, display: "flex", flexDirection: "column", gap: 2 }}>
          {STEPS.map((s, i) => (
            <div
              key={s}
              onClick={() => !result && setStep(i)}
              style={{
                display: "flex", gap: 11, padding: "10px 12px", borderRadius: 7,
                cursor: result ? "default" : "pointer",
                background: i === step ? "var(--accent-soft)" : "transparent",
              }}
            >
              <span style={{
                width: 22, height: 22, borderRadius: "50%", flex: "none",
                display: "grid", placeItems: "center", fontSize: 12, fontWeight: 700,
                background: i < step ? "var(--ok)" : i === step ? "var(--accent)" : "var(--surface-2)",
                color: i <= step ? "#fff" : "var(--text-muted)",
              }}>{i < step ? "✓" : i + 1}</span>
              <span style={{ fontSize: 13, fontWeight: 600, alignSelf: "center" }}>{s}</span>
            </div>
          ))}
        </div>

        {/* 스텝 본문 */}
        <div>
          {result ? (
            <ResultPanel result={result} onReset={() => { setResult(null); setStep(0); }} />
          ) : (
            <>
              {step === 0 && (
                <Card title="① 평가 대상 선택" sub="이 가입자의 어떤 대화를 평가할지 선택합니다.">
                  <div className="field">
                    <label>대상 범위</label>
                    <select value={target} onChange={(e) => setTarget(e.target.value)}>
                      <option value="all">전체 대화 ({convoCount}건 · {turnCount}턴)</option>
                      <option value="unreviewed">미평가 대화만</option>
                      <option value="multiturn">멀티턴만</option>
                    </select>
                    <div className="hint">현재 {convoCount}개 대화 / 약 {turnCount}개 턴이 대상입니다.</div>
                  </div>
                </Card>
              )}

              {step === 1 && (
                <Card title="② 평가자(Judge) 모델" sub="여러 LLM으로 평가할 수 있습니다. 앙상블 시 불일치를 메타 판정으로 해소합니다.">
                  <div className="field">
                    <label>평가 방식</label>
                    <Seg
                      value={ensemble ? "ensemble" : "single"}
                      options={[["single", "단일 Judge"], ["ensemble", "앙상블 (교차검증)"]]}
                      onChange={(v) => setEnsemble(v === "ensemble")}
                    />
                  </div>
                  <div className="field">
                    <label>참여 모델</label>
                    {(judges ?? []).map((j) => (
                      <JudgeRow
                        key={j.provider}
                        judge={j}
                        selected={selJudges.includes(j.provider)}
                        onToggle={() => toggle(selJudges, setSelJudges, j.provider)}
                      />
                    ))}
                  </div>
                  <div className="row">
                    <div className="field col">
                      <label>Temperature</label>
                      <input type="number" value={temperature} step={0.1}
                        onChange={(e) => setTemp(parseFloat(e.target.value))} />
                    </div>
                  </div>
                </Card>
              )}

              {step === 2 && (
                <Card title="③ 평가 레벨" sub="검색 품질과 답변 품질을 독립적으로 평가합니다.">
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                    {LEVELS.map((l) => (
                      <OptCard key={l.id} sel={levels.includes(l.id)}
                        onClick={() => toggle(levels, setLevels, l.id)}
                        title={`${l.icon} ${l.name}`} desc={l.desc} />
                    ))}
                  </div>
                </Card>
              )}

              {step === 3 && (
                <Card title="④ 메트릭" sub="선택한 레벨에서 평가할 메트릭을 고릅니다.">
                  {levels.map((lv) => (
                    <div key={lv} style={{ marginBottom: 14 }}>
                      <div className="faint" style={{ fontSize: 12, marginBottom: 6 }}>
                        {LEVELS.find((l) => l.id === lv)?.name}
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                        {(METRICS_BY_LEVEL[lv] ?? []).map((m) => (
                          <OptCard key={m.id} sel={metrics.includes(m.id)}
                            onClick={() => toggle(metrics, setMetrics, m.id)} title={m.label} />
                        ))}
                      </div>
                    </div>
                  ))}
                  {availableMetrics.length === 0 && (
                    <div className="muted">먼저 평가 레벨을 선택하세요.</div>
                  )}
                </Card>
              )}

              {step === 4 && (
                <Card title="⑤ 정확도 향상 방법론" sub="검증 신뢰도를 높이는 기법을 선택합니다.">
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
                    <span className="faint" style={{ alignSelf: "center", fontSize: 12 }}>프리셋:</span>
                    {Object.keys(PRESETS).map((p) => (
                      <span key={p} onClick={() => applyPreset(p)}
                        style={{
                          padding: "6px 13px", borderRadius: 999, fontSize: 12.5, cursor: "pointer",
                          border: "1px solid var(--border-strong)",
                          background: preset === p ? "var(--accent)" : "transparent",
                          color: preset === p ? "#fff" : "var(--text-muted)",
                        }}>{p}</span>
                    ))}
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    {METHODS.map((m) => (
                      <OptCard key={m.id} sel={methods.includes(m.id)}
                        onClick={() => { toggle(methods, setMethods, m.id); setPreset(""); }}
                        title={`${m.icon} ${m.name}`} desc={m.desc} />
                    ))}
                  </div>
                </Card>
              )}

              {step === 5 && (
                <Card title="⑥ 검토 & 실행" sub="구성을 확인하고 평가배치를 실행합니다.">
                  <Summary label="대상" value={`${convoCount}개 대화 · ${turnCount}턴 (${target})`} />
                  <Summary label="Judge" value={`${selJudges.join(", ")}${ensemble ? " · 앙상블" : ""}`} />
                  <Summary label="레벨" value={levels.join(", ") || "—"} />
                  <Summary label="메트릭" value={`${metrics.length}개`} />
                  <Summary label="방법론" value={methods.join(", ") || "기본"} />
                  <div style={{
                    background: "var(--ok-soft)", color: "var(--ok)", borderRadius: 10,
                    padding: "12px 14px", fontSize: 13, marginTop: 14,
                  }}>
                    💰 mock 모드 · 비용 $0 — 실제 LLM 호출 없이 구성 검증 후 배치 등록
                  </div>
                </Card>
              )}

              {/* 푸터 */}
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 20 }}>
                <button className="btn ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>
                  ← 이전
                </button>
                {step < STEPS.length - 1 ? (
                  <button className="btn primary" onClick={() => setStep(step + 1)}>
                    다음: {STEPS[step + 1]} →
                  </button>
                ) : (
                  <button className="btn primary" onClick={run} disabled={running || !selJudges.length}>
                    {running ? "실행 중…" : "▶ 평가 실행"}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- 보조 컴포넌트 ----

function Card({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <div className="card card-pad">
      <h3>{title}</h3>
      {sub && <div className="card-sub">{sub}</div>}
      {children}
    </div>
  );
}

function Seg({ value, options, onChange }: {
  value: string; options: [string, string][]; onChange: (v: string) => void;
}) {
  return (
    <div style={{ display: "inline-flex", border: "1px solid var(--border-strong)", borderRadius: 7, overflow: "hidden" }}>
      {options.map(([v, label]) => (
        <button key={v} onClick={() => onChange(v)}
          style={{
            border: "none", padding: "7px 14px", fontSize: 13, cursor: "pointer", fontFamily: "inherit",
            background: value === v ? "var(--accent)" : "var(--surface)",
            color: value === v ? "#fff" : "var(--text-muted)", fontWeight: value === v ? 600 : 400,
          }}>{label}</button>
      ))}
    </div>
  );
}

function OptCard({ sel, onClick, title, desc }: {
  sel: boolean; onClick: () => void; title: string; desc?: string;
}) {
  return (
    <div onClick={onClick}
      style={{
        border: `1px solid ${sel ? "var(--accent)" : "var(--border-strong)"}`,
        background: sel ? "var(--accent-soft)" : "transparent",
        borderRadius: 10, padding: "13px 15px", cursor: "pointer", position: "relative",
      }}>
      <div style={{
        position: "absolute", top: 12, right: 13, width: 18, height: 18, borderRadius: 5,
        border: `1.5px solid ${sel ? "var(--accent)" : "var(--border-strong)"}`,
        background: sel ? "var(--accent)" : "transparent", color: "#fff",
        display: "grid", placeItems: "center", fontSize: 11,
      }}>{sel ? "✓" : ""}</div>
      <div style={{ fontWeight: 600, fontSize: 13.5, paddingRight: 24 }}>{title}</div>
      {desc && <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>{desc}</div>}
    </div>
  );
}

function JudgeRow({ judge, selected, onToggle }: {
  judge: JudgeModel; selected: boolean; onToggle: () => void;
}) {
  const logo: Record<string, [string, string]> = {
    anthropic: ["A", "#d97757"], openai: ["O", "#10a37f"],
    gemini: ["G", "#4285f4"], azure: ["Az", "#0078d4"], mock: ["M", "#6b7280"],
  };
  const [ch, color] = logo[judge.provider] ?? ["?", "#888"];
  const disabled = !judge.available;
  return (
    <div onClick={() => !disabled && onToggle()}
      style={{
        display: "flex", alignItems: "center", gap: 10, padding: "11px 13px",
        border: "1px solid var(--border)", borderRadius: 7, marginBottom: 8,
        opacity: disabled ? 0.55 : 1, cursor: disabled ? "not-allowed" : "pointer",
      }}>
      <span style={{ width: 26, height: 26, borderRadius: 6, background: color, color: "#fff",
        display: "grid", placeItems: "center", fontSize: 13, fontWeight: 700 }}>{ch}</span>
      <div style={{ flex: 1 }}>
        <b>{judge.label}</b> <span className="faint mono">{judge.model}</span>
      </div>
      {judge.note && <span className={`badge ${disabled ? "warn" : "ok"}`}>{judge.note}</span>}
      <div style={{
        width: 18, height: 18, borderRadius: 5,
        border: `1.5px solid ${selected ? "var(--accent)" : "var(--border-strong)"}`,
        background: selected ? "var(--accent)" : "transparent", color: "#fff",
        display: "grid", placeItems: "center", fontSize: 11,
      }}>{selected ? "✓" : ""}</div>
    </div>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "9px 0",
      borderBottom: "1px dashed var(--border)", fontSize: 13 }}>
      <span className="muted">{label}</span>
      <b style={{ fontWeight: 600 }}>{value}</b>
    </div>
  );
}

function ResultPanel({ result, onReset }: { result: RunEvalResult; onReset: () => void }) {
  return (
    <div className="card card-pad">
      <div style={{ textAlign: "center", padding: "24px 0" }}>
        <div style={{ fontSize: 44, marginBottom: 12 }}>✅</div>
        <h3 style={{ marginBottom: 6 }}>평가배치 등록 완료</h3>
        <div className="mono faint" style={{ marginBottom: 16 }}>{result.run_id}</div>
        <div style={{ maxWidth: 460, margin: "0 auto" }}>
          <Summary label="상태" value={result.status} />
          <Summary label="Judge" value={(result.judges ?? []).join(", ")} />
          <Summary label="레벨" value={(result.levels ?? []).join(", ")} />
          <Summary label="방법론" value={(result.methods ?? []).join(", ") || "기본"} />
          <Summary label="평가 수" value={String(result.total_evaluations)} />
        </div>
        <p className="muted" style={{ fontSize: 12.5, marginTop: 14 }}>{result.message}</p>
        <button className="btn" style={{ marginTop: 16 }} onClick={onReset}>새 평가 구성</button>
      </div>
    </div>
  );
}
