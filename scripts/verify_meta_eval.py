"""
scripts/verify_meta_eval.py
설계서 §7 검증 절차 — 골든셋 기반 meta-eval로 각 개선 항목의 ρ/MAE 개선폭 측정.

각 '트랙'은 해당 개선이 노리는 오류 유형의 구성 케이스 + 인간 골든 라벨을 정의하고,
baseline(개선 off) vs treatment(개선 on)으로 동일 케이스를 재평가하여
judge↔human 일치도(Spearman ρ) / 오차(MAE)를 비교한다.

⚠️ 측정 방식(method) 표기:
   - pipeline   : 실제 product 평가 경로(LLMJudge + MetaEvaluator) 그대로 실행.
   - component  : 실제 product 함수(apply_guard 등)를 '현실적 judge 기준선' 위에 적용해
                  해당 메커니즘의 정합 효과만 분리 측정 (전체 파이프라인은 아님).
   - n/a(mock)  : MockProvider로는 surfacing 불가 → 실제 LLM 키 필요(사유 명시).

이 환경은 실제 LLM 호출이 불가하여 MockProvider(결정적 휴리스틱)로 동작한다.
MockProvider의 faithfulness는 프롬프트 템플릿 토큰 중첩에 지배되어 의미를 반영하지 못하므로,
faithfulness 기반 항목(#1·#6)은 component 방식으로, #4는 pipeline 방식으로 측정한다.
실제 키(OPENAI_API_KEY 등)로는 모든 트랙을 pipeline 방식으로 측정하도록 확장 가능하다.

실행:  AUTOAUDIT_MOCK=1 python scripts/verify_meta_eval.py [--md OUT.md]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from AutoAudit.app.core.mock_provider import MockProvider
from AutoAudit.app.core.types import QAPair, RetrievalResult, RetrievedContext
from AutoAudit.app.cp4_evaluator.judge import LLMJudge
from AutoAudit.app.cp4_evaluator.meta_eval import MetaEvaluator
from AutoAudit.app.cp4_evaluator.numeric_guard import apply_guard
from AutoAudit.app.cp4_evaluator.options import EvaluationOptions, MetaEvalOptions

_EV = MetaEvaluator(MetaEvalOptions())
_TOK = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def _rho_mae(pairs):
    """pairs=[(judge, human)] → (ρ, MAE, n)."""
    if not pairs:
        return None, None, 0
    rho = round(_EV._spearman([p[0] for p in pairs], [p[1] for p in pairs]), 4)
    mae = round(sum(abs(a - b) for a, b in pairs) / len(pairs), 4)
    return rho, mae, len(pairs)


def _tokens(t):
    base = set(_TOK.findall(t.lower()))
    c = re.sub(r"\s+", "", t)
    base |= {c[i:i + 2] for i in range(len(c) - 1)}
    return base


def _sim_faith(answer, context):
    """현실적 judge 기준선 모사: 답변↔컨텍스트 토큰 중첩 비율(0.1~1.0).

    실제 LLM judge가 '문장이 컨텍스트로 충분히 뒷받침되면 높게' 주는 경향을 근사.
    수치 1자리 차이·부정 반전 같은 미세 오류는 토큰이 대부분 겹쳐 높게 통과한다(=약점).
    """
    a, c = _tokens(answer), _tokens(context)
    if not a:
        return 0.5
    ov = len(a & c) / len(a)
    return round(min(1.0, 0.3 + 0.7 * ov), 4)


def _ctx(cid, content, score=0.9):
    return RetrievedContext(chunk_id=cid, content=content, score=score, source_call_id="C")


def _rr(query, *contents):
    return RetrievalResult(query=query, contexts=[_ctx(f"c{i}", c, 0.95 - 0.05 * i) for i, c in enumerate(contents)])


# ──────────────────────────────────────────────────────────────
# #1 컨텍스트 출처 분리 (component)
# ──────────────────────────────────────────────────────────────

def track_context_source():
    """봇 실제 컨텍스트는 답변을 지지하나 감사기 재검색은 빗나간 경우.
    baseline=감사기 컨텍스트로 faithfulness, treatment=봇 실제 컨텍스트로 faithfulness."""
    cases = [
        # (q?, answer, auditor_ctx(빗나감), bot_ctx(지지), human)
        ("프리미엄 요금은 월 49000원입니다.", "오늘 배송 지연 안내드립니다.", "프리미엄 요금제는 월 49000원입니다.", 1.0),
        ("로밍은 마이페이지에서 신청 가능합니다.", "약관 일반 동의 조항입니다.", "로밍은 마이페이지에서 신청 가능합니다.", 1.0),
        ("멤버십 포인트는 1% 적립됩니다.", "고객센터 운영시간 안내입니다.", "멤버십 포인트는 1% 적립됩니다.", 1.0),
        ("약정 기간은 24개월입니다.", "약정 기간은 24개월입니다.", "약정 기간은 24개월입니다.", 1.0),  # 양쪽 일치(대조군)
    ]
    base = [(_sim_faith(a, auditor), h) for a, auditor, bot, h in cases]
    treat = [(_sim_faith(a, bot), h) for a, auditor, bot, h in cases]
    return ("#1 컨텍스트 출처 분리", "faithfulness", "component", _rho_mae(base), _rho_mae(treat))


# ──────────────────────────────────────────────────────────────
# #6 결정적 가드 (component — 실제 apply_guard 사용)
# ──────────────────────────────────────────────────────────────

def track_numeric_guard():
    """현실적 judge가 수치/부정극성 환각을 '통과'시킨 기준선(≈0.9)에
    실제 product apply_guard를 적용했을 때 인간 라벨로 얼마나 정렬되는지."""
    BASE = 0.9  # 환각이지만 유창해서 judge가 높게 준 상태(실제 judge 약점 모사)
    cases = [
        # (answer, context, human)
        ("기본 요금제는 월 35000원입니다.", "기본 요금제는 월 30000원입니다.", 0.2),   # 수치 환각
        ("데이터는 10GB 제공됩니다.", "데이터는 5GB 제공됩니다.", 0.2),             # 수치 환각
        ("중도 해지는 불가능합니다.", "중도 해지는 위약금 없이 가능합니다.", 0.1),       # 부정 반전
        ("약정은 24개월입니다.", "약정은 24개월입니다.", 1.0),                     # 충실(대조군)
        ("요금 할인은 30% 적용됩니다.", "요금 할인은 30% 입니다.", 1.0),            # 충실(대조군)
    ]
    base = [(BASE, h) for _, _, h in cases]
    treat = []
    for ans, ctx, h in cases:
        guarded, _ = apply_guard(BASE, ans, ctx, check_negation=True, check_entities=True)
        treat.append((guarded, h))
    return ("#6 결정적 가드(수치·부정극성)", "faithfulness", "component", _rho_mae(base), _rho_mae(treat))


# ──────────────────────────────────────────────────────────────
# #4 순위가중 precision@k (pipeline — 실제 LLMJudge)
# ──────────────────────────────────────────────────────────────

async def track_precision_at_k():
    q = "프리미엄 요금제 혜택"
    rel_a, rel_b, irr = (
        "프리미엄 요금제 혜택은 데이터 무제한 제공",
        "프리미엄 요금제 혜택 멤버십 할인 포함",
        "배송 반품 정책 일반 안내",
    )
    cases = [
        ("P4-1", _rr(q, rel_a, rel_b, irr), 1.0),       # 관련 2개 상위(1,2위)
        ("P4-2", _rr(q, irr, rel_a, rel_b), 0.5833),    # 관련 2개 하위(2,3위)
    ]
    golden = {c[0]: c[2] for c in cases}
    pairs = [QAPair(qa_id=c[0], call_id="C", subscriber_id="S", question=q,
                    bot_answer="프리미엄 혜택 안내드립니다.", turn_index=0, retrieval_result=c[1]) for c in cases]

    async def _eval(precision_on):
        judge = LLMJudge(provider=MockProvider(), options=EvaluationOptions())
        judge.precision_at_k = precision_on
        judge.sampling_strategy = "temperature"
        recs = await judge.evaluate_pairs(pairs)
        out = []
        for r in recs:
            for ms in r.scores:
                if ms.metric == "context_precision":
                    out.append((ms.score, golden[r.qa_id]))
        return out

    base = await _eval(False)   # mock 비율(순위 무시)
    treat = await _eval(True)   # 순위가중 precision@k
    return ("#4 순위가중 precision@k", "context_precision", "pipeline", _rho_mae(base), _rho_mae(treat))


# ──────────────────────────────────────────────────────────────
# #5 패러프레이즈 (mock 불가)
# ──────────────────────────────────────────────────────────────

def track_paraphrase_note():
    return ("#5 패러프레이즈 샘플링", "answer_relevance(std)", "n/a(mock)", None, None)


# ──────────────────────────────────────────────────────────────
# 리포트
# ──────────────────────────────────────────────────────────────

def _f(v):
    return "—" if v is None else f"{v:.4f}"


def _row(name, metric, method, base, treat):
    if method == "n/a(mock)":
        return (f"| {name} | {metric} | {method} | — | — | "
                "MockProvider는 표현 변주에 점수가 불변 → 실제 LLM 필요 |")
    (b_rho, b_mae, n) = base
    (t_rho, t_mae, _) = treat
    drho = "—" if (b_rho is None or t_rho is None) else f"{t_rho - b_rho:+.4f}"
    dmae = "—" if (b_mae is None or t_mae is None) else f"{t_mae - b_mae:+.4f}"
    return (f"| {name} | {metric} | {method} | {n} | "
            f"ρ {_f(b_rho)}→{_f(t_rho)} ({drho}) | "
            f"MAE {_f(b_mae)}→{_f(t_mae)} ({dmae}) | 개선={'예' if (t_mae is not None and b_mae is not None and t_mae < b_mae) else '—'} |")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md")
    args = ap.parse_args()

    tracks = [
        track_context_source(),
        track_numeric_guard(),
        await track_precision_at_k(),
        track_paraphrase_note(),
    ]

    L = []
    L.append("# §7 검증 — meta-eval ρ/MAE 개선폭 측정 (MockProvider)\n")
    L.append("> ⚠️ 실제 LLM 불가 환경. method=pipeline은 실제 평가경로, component는 실제 product 함수를")
    L.append("> '현실적 judge 기준선'에 적용한 분리 측정, n/a(mock)은 실제 키 필요. 운영 품질 개선폭이 아닌 방향성 검증.\n")
    L.append("\n| 항목 | 지표 | method | n | Spearman ρ (Δ) | MAE (Δ) | 개선 |")
    L.append("|------|------|--------|---|----------------|---------|------|")
    for name, metric, method, base, treat in tracks:
        L.append(_row(name, metric, method, base, treat))
    L.append("\n해석: ρ↑·MAE↓ 이면 해당 토글이 judge 점수를 인간 라벨 쪽으로 정렬함을 의미.")
    L.append("실제 키로 동일 케이스를 pipeline 방식으로 돌리면 #1·#6·#5도 같은 표에 실측치가 채워진다.")

    report = "\n".join(L)
    print(report)
    if args.md:
        Path(args.md).write_text(report + "\n", encoding="utf-8")
        print(f"\n[saved] {args.md}")


if __name__ == "__main__":
    asyncio.run(main())
