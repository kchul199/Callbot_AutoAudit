"""
run_pipeline.py — AutoAudit 통합 파이프라인 진입점

사용 예:
  python run_pipeline.py                          # CP1~CP6 전체 실행
  python run_pipeline.py --until cp3              # CP3까지만 실행
  python run_pipeline.py --reindex                # KB 재인덱싱
  python run_pipeline.py --data data/raw/test.json

CP 실행 순서:
  CP1: 로그 파싱
  CP2: 청킹 + 인덱싱
  CP3: 검색 (샘플 질의로 검증)
  CP4: LLM-as-a-Judge 평가
  CP5: 결과 집계
  CP6: 리포트 생성
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.llm_client import CostTracker, create_provider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.tracer import Tracer
from AutoAudit.app.core.types import PipelineContext

logger = get_logger("run_pipeline")

CP_ORDER = ["cp1", "cp2", "cp3", "cp4", "cp5", "cp6"]


def should_run(cp: str, until: str | None) -> bool:
    if until is None:
        return True
    return CP_ORDER.index(cp) <= CP_ORDER.index(until.lower())


# ============================================================
# CP1
# ============================================================

def run_cp1(ctx: PipelineContext, data_path: str) -> list:
    from AutoAudit.app.cp1_preprocessing.parser import CallLogParser

    logger.info("=" * 60)
    logger.info("[CP1] 데이터 전처리 시작")
    parser = CallLogParser()
    path = Path(data_path)
    if path.is_dir():
        logs = parser.parse_directory(path)
    else:
        log = parser.parse_file(path)
        logs = [log] if log else []

    tracer = Tracer(ctx.run_id, ctx.results_dir)
    tracer.save("cp1_call_logs", logs)
    logger.info(f"[CP1] 완료 — {len(logs)} call logs")
    return logs


# ============================================================
# CP2
# ============================================================

async def run_cp2(ctx: PipelineContext, logs: list, reindex: bool, provider) -> object:
    from AutoAudit.app.cp2_knowledge_base.chunker import ParentChildChunker
    from AutoAudit.app.cp2_knowledge_base.indexer import KnowledgeBaseIndexer

    logger.info("=" * 60)
    logger.info("[CP2] 지식 베이스 구축 시작")
    chunker = ParentChildChunker()
    all_chunks = []
    for log in logs:
        chunks = chunker.chunk(log)
        all_chunks.extend(chunks)

    tracer = Tracer(ctx.run_id, ctx.results_dir)
    tracer.save("cp2_chunks", all_chunks)

    indexer = KnowledgeBaseIndexer(reindex=reindex, provider=provider)
    indexed = await indexer.index_chunks(all_chunks)
    logger.info(f"[CP2] 완료 — {indexed} chunks indexed")
    return indexer


# ============================================================
# CP3
# ============================================================

async def run_cp3(ctx: PipelineContext, indexer, logs: list, provider, ckpt) -> list:
    """CP3: 실제 대화에서 QA 쌍 추출 → 컨텍스트 검색 부착"""
    from AutoAudit.app.cp3_retrieval.retriever import HybridRetriever
    from AutoAudit.app.cp4_evaluator.qa_builder import QAPairBuilder
    from AutoAudit.app.core.types import QAPair

    # resume: cp3 체크포인트 존재 시 재사용
    if ckpt.has_stage("cp3"):
        data = ckpt.load_stage("cp3")
        qa_pairs = [QAPair(**d) for d in data]
        logger.info(f"[CP3] resume — {len(qa_pairs)} QA pairs 체크포인트에서 로드")
        return qa_pairs

    logger.info("=" * 60)
    logger.info("[CP3] QA 추출 + 검색 시작")
    retriever = HybridRetriever(indexer, provider=provider)
    builder = QAPairBuilder(retriever=retriever)
    qa_pairs = await builder.build(logs)

    Tracer(ctx.run_id, ctx.results_dir).save("cp3_qa_pairs", qa_pairs)
    ckpt.save_stage("cp3", qa_pairs)
    logger.info(f"[CP3] 완료 — {len(qa_pairs)} QA pairs with retrieval")
    return qa_pairs


# ============================================================
# CP4 (항목 단위 incremental 체크포인트 → 멱등 재개)
# ============================================================

async def run_cp4(ctx: PipelineContext, qa_pairs: list, provider, ckpt, options, logs=None) -> list:
    from AutoAudit.app.core.async_utils import gather_with_concurrency
    from AutoAudit.app.cp4_evaluator.judge import LLMJudge
    from AutoAudit.app.core.types import EvaluationRecord

    logger.info("=" * 60)
    logger.info(f"[CP4] 활성 평가 옵션: {options.active_summary()}")

    # 이미 평가된 qa_id 로드 → skip (멱등 재개)
    done_records = ckpt.load_items("cp4")
    done_qa_ids = {r.get("qa_id") for r in done_records}
    pending = [p for p in qa_pairs if p.qa_id not in done_qa_ids]

    judge = LLMJudge(provider=provider, options=options)

    # PPI: 일부만 LLM 평가, 나머지는 분류기 (비용 절감)
    classifier_only = []
    if options.ppi.enabled and pending:
        pending, classifier_only = _ppi_split(pending, options.ppi.labeled_fraction)
        logger.info(f"[CP4] PPI 모드 — LLM {len(pending)} / 분류기 {len(classifier_only)}")

    logger.info(
        f"[CP4] 평가 시작 — 전체 {len(qa_pairs)} / 완료 {len(done_qa_ids)} / LLM잔여 {len(pending)}"
    )

    async def _eval_and_persist(pair):
        rec = await judge.evaluate_pair(pair)
        if options.domain.enabled and options.domain.safety_compliance:
            await _append_safety(rec, pair, provider, options)
        ckpt.append_item("cp4", rec)
        return rec

    new_records = await gather_with_concurrency(
        [_eval_and_persist(p) for p in pending], concurrency=judge.concurrency
    )

    clf_records = _ppi_classifier_records(classifier_only, judge.metrics) if classifier_only else []
    all_records = [EvaluationRecord(**r) for r in done_records] + list(new_records) + clf_records

    # 도메인: 멀티턴 일관성 (콜 단위)
    if options.domain.enabled and options.domain.multiturn_consistency and logs:
        await _append_multiturn(all_records, logs, provider, options)

    Tracer(ctx.run_id, ctx.results_dir).save("cp4_eval_records", all_records)

    # SQLite 인덱스 적재 (대량 쿼리용)
    from AutoAudit.app.core.store import ResultStore
    ResultStore().upsert_evaluations(ctx.run_id, all_records)

    logger.info(
        f"[CP4] 완료 — {len(all_records)} evaluations "
        f"(LLM신규 {len(new_records)}, 분류기 {len(clf_records)})"
    )
    return all_records


def _ppi_split(pairs: list, labeled_fraction: float):
    """PPI: 결정적으로 labeled_fraction 만큼 LLM 평가 대상 분리"""
    import hashlib
    labeled, unlabeled = [], []
    for p in pairs:
        h = int(hashlib.md5(p.qa_id.encode()).hexdigest(), 16) % 100
        (labeled if h < labeled_fraction * 100 else unlabeled).append(p)
    if not labeled and pairs:
        labeled.append(pairs[0]); unlabeled = pairs[1:]
    return labeled, unlabeled


def _ppi_classifier_records(pairs: list, metrics: list) -> list:
    import uuid

    from AutoAudit.app.cp4_evaluator.ppi_classifier import HeuristicClassifier
    from AutoAudit.app.core.types import EvaluationRecord, MetricScore
    clf = HeuristicClassifier()
    out = []
    for p in pairs:
        scores = [
            MetricScore(metric=m, score=clf.score(p, m),
                        reasoning="PPI 분류기 추정", method="ppi_classifier", confidence=0.5)
            for m in metrics
        ]
        out.append(EvaluationRecord(
            eval_id=str(uuid.uuid4()), qa_id=p.qa_id, call_id=p.call_id,
            subscriber_id=p.subscriber_id, query=p.question,
            generated_answer=p.bot_answer, retrieval_result=p.retrieval_result, scores=scores,
        ))
    return out


async def _append_safety(rec, pair, provider, options) -> None:
    from AutoAudit.app.cp4_evaluator.domain_metrics import DomainMetricsEvaluator
    ev = DomainMetricsEvaluator(provider, options.domain)
    rec.scores.append(await ev.safety_compliance(pair.bot_answer))


async def _append_multiturn(records, logs, provider, options) -> None:
    from AutoAudit.app.core.async_utils import gather_with_concurrency
    from AutoAudit.app.cp4_evaluator.domain_metrics import DomainMetricsEvaluator
    ev = DomainMetricsEvaluator(provider, options.domain)
    first_by_call = {}
    for r in records:
        first_by_call.setdefault(r.call_id, r)
    log_by_call = {log.call_id: log for log in logs}

    async def _one(call_id, rec):
        log = log_by_call.get(call_id)
        if log is not None:
            rec.scores.append(await ev.multiturn_consistency(log))

    await gather_with_concurrency(
        [_one(cid, rec) for cid, rec in first_by_call.items()], concurrency=5
    )


# ============================================================
# CP5
# ============================================================

def run_cp5(ctx: PipelineContext, eval_records: list, options=None) -> object:
    from AutoAudit.app.cp5_aggregator.aggregator import ResultAggregator

    logger.info("=" * 60)
    logger.info("[CP5] 결과 집계 시작")
    agg = ResultAggregator()
    summary = agg.aggregate(eval_records, run_id=ctx.run_id, options=options)

    tracer = Tracer(ctx.run_id, ctx.results_dir)
    tracer.save("cp5_audit_summary", summary)

    # SQLite 인덱스 적재
    from AutoAudit.app.core.store import ResultStore
    ResultStore().upsert_summary(summary)

    return summary


# ============================================================
# CP6
# ============================================================

def _scores_by_metric(records: list) -> dict:
    """EvaluationRecord 목록 → {metric: [score, ...]}"""
    from collections import defaultdict
    out = defaultdict(list)
    for r in records:
        for s in r.scores:
            out[s.metric].append(s.score)
    return dict(out)


def _prev_scores_by_metric(ctx: PipelineContext, prev_run_id) -> dict:
    """이전 run의 원시 점수를 SQLite에서 로드 (유의성 검정용)"""
    if not prev_run_id:
        return {}
    from collections import defaultdict
    from AutoAudit.app.core.store import ResultStore
    recs = ResultStore().query_evaluations(prev_run_id, limit=100000)
    out = defaultdict(list)
    for r in recs:
        for s in r.get("scores", []):
            out[s["metric"]].append(s["score"])
    return dict(out)


def run_cp6(ctx: PipelineContext, summary, eval_records: list, options=None) -> None:
    from AutoAudit.app.api.repository import ResultsRepository
    from AutoAudit.app.cp6_reporter.notifier import SlackNotifier
    from AutoAudit.app.cp6_reporter.regression import (
        detect_regression,
        detect_regression_significant,
    )
    from AutoAudit.app.cp6_reporter.reporter import AuditReporter

    logger.info("=" * 60)
    logger.info("[CP6] 리포트 생성 시작")
    reporter = AuditReporter()
    paths = reporter.generate(summary, eval_records)

    # 회귀 감지: 직전 run summary와 비교
    repo = ResultsRepository(ctx.results_dir)
    previous = next(
        (repo.get_summary(r["run_id"]) for r in repo.list_runs() if r["run_id"] != ctx.run_id),
        None,
    )

    # statistics 옵션 ON → 유의성 기반(원시 점수 순열검정), OFF → 고정 임계값
    if options and options.statistics.enabled and previous:
        cur_scores = _scores_by_metric(eval_records)
        prev_scores = _prev_scores_by_metric(ctx, previous.get("run_id"))
        if prev_scores:
            reg = detect_regression_significant(
                cur_scores, prev_scores, alpha=options.statistics.significance_alpha
            )
        else:
            reg = detect_regression(summary, previous)
    else:
        reg = detect_regression(summary, previous)

    # 알림: 회귀 또는 SLA 미달 콜 존재 시
    flagged = summary.flagged_call_ids if hasattr(summary, "flagged_call_ids") else []
    if reg.has_regression or flagged:
        notifier = SlackNotifier()
        msg = f"*[AutoAudit] {ctx.run_id}*\n"
        if reg.has_regression:
            msg += f"🚨 품질 회귀 감지\n{reg.summary_text()}\n"
        if flagged:
            msg += f"SLA 미달 콜 {len(flagged)}건"
        notifier.send(msg)

    logger.info(f"[CP6] 완료 — reports: {paths}")


# ============================================================
# 메인
# ============================================================

async def main(args: argparse.Namespace) -> None:
    run_id = args.resume or f"run_{uuid.uuid4().hex[:8]}"
    ctx = PipelineContext(
        run_id=run_id,
        until_cp=args.until,
        reindex=args.reindex,
        results_dir=cfg_get("paths.results", default="data/results"),
    )
    logger.info(f"Pipeline started | run_id={run_id} | until={args.until or 'cp6'}")

    # 공유 Provider + 비용 추적기 (전 CP가 동일 인스턴스 사용)
    cost_tracker = CostTracker(budget_usd=cfg_get("llm.budget_usd", default=None))
    provider = create_provider(cost_tracker)

    # 평가 옵션 로드 (config + CLI 오버라이드)
    from AutoAudit.app.cp4_evaluator.options import EvaluationOptions
    options = EvaluationOptions.from_config(ctx.config_path).apply_cli_overrides(
        args.enable, args.disable
    )

    # 체크포인트 저장소 (resume 지원)
    from AutoAudit.app.core.checkpoint import CheckpointStore
    ckpt = CheckpointStore(ctx.run_id, ctx.results_dir)
    cp3_cached = ckpt.has_stage("cp3")

    try:
        # CP1/CP2 — CP3 체크포인트가 있으면 생략 (검색 결과 재사용)
        indexer = None
        logs = []
        if not cp3_cached:
            # CP1
            logs = run_cp1(ctx, args.data)
            if not should_run("cp2", ctx.until_cp):
                return
            # CP2
            indexer = await run_cp2(ctx, logs, reindex=args.reindex, provider=provider)
            if not should_run("cp3", ctx.until_cp):
                return
        elif args.resume:
            logger.info("[resume] CP3 체크포인트 발견 → CP1/CP2 생략")

        # CP3 — 실제 대화에서 QA 추출 + 검색 (또는 체크포인트 로드)
        qa_pairs = await run_cp3(ctx, indexer, logs, provider, ckpt)
        if not should_run("cp4", ctx.until_cp):
            return

        # CP4 — QA 쌍 평가 (incremental resume + 고급 옵션)
        eval_records = await run_cp4(ctx, qa_pairs, provider, ckpt, options, logs=logs)
        if not should_run("cp5", ctx.until_cp):
            return

        # CP5
        summary = run_cp5(ctx, eval_records, options)
        if not should_run("cp6", ctx.until_cp):
            return

        # CP6
        run_cp6(ctx, summary, eval_records, options)

    finally:
        cost = cost_tracker.summary()
        logger.info(
            f"\n{'='*60}\n[비용 요약] calls={cost['call_count']} | "
            f"in={cost['input_tokens']} out={cost['output_tokens']} tokens | "
            f"${cost['total_cost_usd']:.4f}\n{'='*60}"
        )

    logger.info(f"\n{'='*60}\nPipeline COMPLETE | run_id={run_id}\n{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CallBot AutoAudit Pipeline")
    parser.add_argument(
        "--data",
        default=cfg_get("paths.raw_data", default="data/raw"),
        help="입력 데이터 경로 (파일 또는 디렉토리)",
    )
    parser.add_argument(
        "--until",
        choices=CP_ORDER,
        default=None,
        help="중간 종료 CP (예: --until cp3)",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="ChromaDB 재인덱싱",
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="기존 run_id 재개 (체크포인트 기반)",
    )
    parser.add_argument(
        "--enable",
        type=lambda s: s.split(","),
        default=[],
        help="평가 기법 켜기 (쉼표구분): calibration,ensemble,meta_eval,nugget,"
             "diagnosis,statistics,routing,ppi,domain,cot,reverse "
             "(하위필드: calibration.g_eval_logprobs)",
    )
    parser.add_argument(
        "--disable",
        type=lambda s: s.split(","),
        default=[],
        help="평가 기법 끄기 (쉼표구분), --enable과 동일 문법",
    )
    args = parser.parse_args()
    asyncio.run(main(args))
