"""
core/store.py
SQLite 기반 결과 저장소 — 대량 쿼리/필터/집계 성능.

JSON Tracer(data/results/<run>/*.json)는 원본 추적용으로 유지하고,
이 Store는 '쿼리 가능한 인덱스'로서 CP4/CP5 결과를 정규화 적재한다.

설계:
  - 단일 파일 DB (data/results/autoaudit.db) — 운영 시 Postgres로 교체 용이한 SQL
  - WAL 모드 + 인덱스로 동시 읽기 / 필터 성능 확보
  - upsert(멱등) — resume/재집계 시 중복 없이 갱신
  - scores는 메트릭별 행으로 펼쳐 저장 → "faithfulness < 0.7" 같은 필터를 SQL로

스키마:
  runs(run_id PK, generated_at, total_calls, total_evaluations, flagged_count)
  evaluations(eval_id PK, run_id, qa_id, call_id, subscriber_id, query,
              generated_answer, retrieval_json, judge_model, evaluated_at)
  scores(eval_id, run_id, metric, score, confidence, is_low_confidence,
         reasoning, grounding_json, PRIMARY KEY(eval_id, metric))
  metric_summary(run_id, metric, mean, median, p10, p90,
                 below_sla_count, total_count, sla_pass_rate,
                 PRIMARY KEY(run_id, metric))
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import AuditSummary, EvaluationRecord

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    generated_at TEXT,
    total_calls INTEGER,
    total_evaluations INTEGER,
    flagged_count INTEGER,
    flagged_json TEXT
);
CREATE TABLE IF NOT EXISTS evaluations (
    eval_id TEXT PRIMARY KEY,
    run_id TEXT,
    qa_id TEXT,
    call_id TEXT,
    tenant_id TEXT DEFAULT 'default',
    conversation_id TEXT,
    subscriber_id TEXT,
    level TEXT DEFAULT 'turn',
    turn_index INTEGER,
    query TEXT,
    generated_answer TEXT,
    retrieval_json TEXT,
    ground_truth TEXT,
    judge_provider TEXT,
    judge_model TEXT,
    evaluated_at TEXT,
    review_status TEXT DEFAULT 'pending',
    human_scores_json TEXT,
    human_label_json TEXT,
    human_comment TEXT,
    reviewer TEXT,
    reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS scores (
    eval_id TEXT,
    run_id TEXT,
    metric TEXT,
    score REAL,
    confidence REAL,
    is_low_confidence INTEGER,
    reasoning TEXT,
    grounding_json TEXT,
    PRIMARY KEY (eval_id, metric)
);
CREATE TABLE IF NOT EXISTS metric_summary (
    run_id TEXT,
    metric TEXT,
    mean REAL, median REAL, p10 REAL, p90 REAL,
    below_sla_count INTEGER, total_count INTEGER, sla_pass_rate REAL,
    PRIMARY KEY (run_id, metric)
);
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    tenant_id TEXT DEFAULT 'default',
    subscriber_id TEXT,
    is_multiturn INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    turns_json TEXT,
    started_at TEXT,
    metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    title TEXT,
    content TEXT,
    source_type TEXT DEFAULT '수동',
    char_count INTEGER DEFAULT 0,
    chunk_count INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_kb_tenant ON kb_documents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_eval_run ON evaluations(run_id);
CREATE INDEX IF NOT EXISTS idx_eval_call ON evaluations(call_id);
CREATE INDEX IF NOT EXISTS idx_eval_sub ON evaluations(subscriber_id);
CREATE INDEX IF NOT EXISTS idx_eval_tenant ON evaluations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_eval_conv ON evaluations(conversation_id);
CREATE INDEX IF NOT EXISTS idx_eval_review ON evaluations(review_status);
CREATE INDEX IF NOT EXISTS idx_scores_run_metric ON scores(run_id, metric);
CREATE INDEX IF NOT EXISTS idx_scores_score ON scores(metric, score);
"""


class ResultStore:
    def __init__(self, db_path: str | None = None) -> None:
        resolved = db_path or cfg_get("paths.db", default=None)
        if resolved is None:
            results_dir = cfg_get("paths.results", default="data/results")
            resolved = str(Path(results_dir) / "autoaudit.db")
        self.db_path = resolved
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    # 구 스키마 DB에 누락된 컬럼 추가 (경량 마이그레이션)
    _MIGRATIONS = {
        "evaluations": {
            "tenant_id": "TEXT DEFAULT 'default'",
            "conversation_id": "TEXT",
            "level": "TEXT DEFAULT 'turn'",
            "turn_index": "INTEGER",
            "ground_truth": "TEXT",
            "judge_provider": "TEXT",
            "review_status": "TEXT DEFAULT 'pending'",
            "human_scores_json": "TEXT",
            "human_label_json": "TEXT",
            "human_comment": "TEXT",
            "reviewer": "TEXT",
            "reviewed_at": "TEXT",
        },
    }

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # 기존 DB 마이그레이션: 누락 컬럼 ALTER
            for table, cols in self._MIGRATIONS.items():
                existing = {
                    r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for col, decl in cols.items():
                    if col not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    # ----------------------------------------------------------
    # 적재 (CP4/CP5 → DB)
    # ----------------------------------------------------------

    def upsert_evaluations(self, run_id: str, records: list[EvaluationRecord]) -> int:
        with self._connect() as conn:
            for rec in records:
                level = rec.level.value if hasattr(rec.level, "value") else str(rec.level)
                rstatus = (
                    rec.review_status.value
                    if hasattr(rec.review_status, "value") else str(rec.review_status)
                )
                conn.execute(
                    """INSERT INTO evaluations
                       (eval_id, run_id, qa_id, call_id, tenant_id, conversation_id,
                        subscriber_id, level, turn_index, query, generated_answer,
                        retrieval_json, ground_truth, judge_provider, judge_model, evaluated_at,
                        review_status, human_scores_json, human_label_json, human_comment,
                        reviewer, reviewed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(eval_id) DO UPDATE SET
                         run_id=excluded.run_id, qa_id=excluded.qa_id,
                         call_id=excluded.call_id, tenant_id=excluded.tenant_id,
                         conversation_id=excluded.conversation_id,
                         subscriber_id=excluded.subscriber_id, level=excluded.level,
                         turn_index=excluded.turn_index, query=excluded.query,
                         generated_answer=excluded.generated_answer,
                         retrieval_json=excluded.retrieval_json, ground_truth=excluded.ground_truth,
                         judge_provider=excluded.judge_provider, judge_model=excluded.judge_model,
                         evaluated_at=excluded.evaluated_at, review_status=excluded.review_status,
                         human_scores_json=excluded.human_scores_json,
                         human_label_json=excluded.human_label_json,
                         human_comment=excluded.human_comment, reviewer=excluded.reviewer,
                         reviewed_at=excluded.reviewed_at""",
                    (
                        rec.eval_id, run_id, rec.qa_id, rec.call_id, rec.tenant_id,
                        rec.conversation_id or rec.call_id, rec.subscriber_id, level,
                        rec.turn_index, rec.query, rec.generated_answer,
                        json.dumps(rec.retrieval_result.model_dump(mode="json"), ensure_ascii=False),
                        rec.ground_truth, rec.judge_provider, rec.judge_model,
                        rec.evaluated_at.isoformat() if rec.evaluated_at else None,
                        rstatus,
                        json.dumps(rec.human_scores, ensure_ascii=False),
                        json.dumps(rec.human_label, ensure_ascii=False),
                        rec.human_comment, rec.reviewer,
                        rec.reviewed_at.isoformat() if rec.reviewed_at else None,
                    ),
                )
                for ms in rec.scores:
                    conn.execute(
                        """INSERT INTO scores
                           (eval_id, run_id, metric, score, confidence,
                            is_low_confidence, reasoning, grounding_json)
                           VALUES (?,?,?,?,?,?,?,?)
                           ON CONFLICT(eval_id, metric) DO UPDATE SET
                             score=excluded.score, confidence=excluded.confidence,
                             is_low_confidence=excluded.is_low_confidence,
                             reasoning=excluded.reasoning, grounding_json=excluded.grounding_json""",
                        (
                            rec.eval_id, run_id, ms.metric, ms.score, ms.confidence,
                            int(ms.is_low_confidence), ms.reasoning,
                            json.dumps(ms.grounding_chunks, ensure_ascii=False),
                        ),
                    )
        logger.info(f"[store] {len(records)} evaluations upserted (run={run_id})")
        return len(records)

    def upsert_summary(self, summary: AuditSummary) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO runs
                   (run_id, generated_at, total_calls, total_evaluations,
                    flagged_count, flagged_json)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET
                     generated_at=excluded.generated_at, total_calls=excluded.total_calls,
                     total_evaluations=excluded.total_evaluations,
                     flagged_count=excluded.flagged_count, flagged_json=excluded.flagged_json""",
                (
                    summary.run_id,
                    summary.generated_at.isoformat() if summary.generated_at else None,
                    summary.total_calls, summary.total_evaluations,
                    len(summary.flagged_call_ids),
                    json.dumps(summary.flagged_call_ids, ensure_ascii=False),
                ),
            )
            for m in summary.metrics:
                conn.execute(
                    """INSERT INTO metric_summary
                       (run_id, metric, mean, median, p10, p90,
                        below_sla_count, total_count, sla_pass_rate)
                       VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(run_id, metric) DO UPDATE SET
                         mean=excluded.mean, median=excluded.median,
                         p10=excluded.p10, p90=excluded.p90,
                         below_sla_count=excluded.below_sla_count,
                         total_count=excluded.total_count,
                         sla_pass_rate=excluded.sla_pass_rate""",
                    (
                        summary.run_id, m.metric, m.mean, m.median, m.p10, m.p90,
                        m.below_sla_count, m.total_count, m.sla_pass_rate,
                    ),
                )
        logger.info(f"[store] summary upserted (run={summary.run_id})")

    # ----------------------------------------------------------
    # 조회 (API → DB)
    # ----------------------------------------------------------

    def list_runs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT run_id, generated_at, total_calls, total_evaluations, flagged_count
                   FROM runs ORDER BY generated_at DESC"""
            ).fetchall()
        return [
            {**dict(r), "has_summary": True} for r in rows
        ]

    def latest_run_id(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_id FROM runs ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
        return row["run_id"] if row else None

    def get_summary(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                return None
            metrics = conn.execute(
                "SELECT * FROM metric_summary WHERE run_id=?", (run_id,)
            ).fetchall()
        return {
            "run_id": run["run_id"],
            "generated_at": run["generated_at"],
            "total_calls": run["total_calls"],
            "total_evaluations": run["total_evaluations"],
            "flagged_call_ids": json.loads(run["flagged_json"] or "[]"),
            "metrics": [
                {k: m[k] for k in (
                    "metric", "mean", "median", "p10", "p90",
                    "below_sla_count", "total_count", "sla_pass_rate",
                )}
                for m in metrics
            ],
        }

    def query_evaluations(
        self,
        run_id: str,
        call_id: str | None = None,
        subscriber_id: str | None = None,
        flagged_only: bool = False,
        low_confidence_only: bool = False,
        below_metric: str | None = None,
        below_threshold: float | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """SQL 필터로 대량 평가 조회 — eval_id 집합 선별 후 상세 조립"""
        where = ["e.run_id = ?"]
        params: list[Any] = [run_id]

        if call_id:
            where.append("e.call_id = ?")
            params.append(call_id)
        if subscriber_id:
            where.append("e.subscriber_id = ?")
            params.append(subscriber_id)

        # 점수 기반 필터는 scores 테이블 EXISTS 서브쿼리로
        if below_metric is not None and below_threshold is not None:
            where.append(
                "EXISTS (SELECT 1 FROM scores s WHERE s.eval_id=e.eval_id "
                "AND s.metric=? AND s.score < ?)"
            )
            params.extend([below_metric, below_threshold])
        if low_confidence_only:
            where.append(
                "EXISTS (SELECT 1 FROM scores s WHERE s.eval_id=e.eval_id "
                "AND s.is_low_confidence=1)"
            )
        if flagged_only:
            where.append(
                "e.call_id IN (SELECT value FROM json_each("
                "(SELECT flagged_json FROM runs WHERE run_id=?)))"
            )
            params.append(run_id)

        sql = (
            f"SELECT * FROM evaluations e WHERE {' AND '.join(where)} "
            f"ORDER BY e.call_id LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        with self._connect() as conn:
            eval_rows = conn.execute(sql, params).fetchall()
            results = []
            for er in eval_rows:
                score_rows = conn.execute(
                    "SELECT * FROM scores WHERE eval_id=?", (er["eval_id"],)
                ).fetchall()
                results.append(self._assemble(er, score_rows))
        return results

    def query_evaluations_by_tenant(
        self,
        tenant_id: str,
        level: str | None = None,
        metric: str | None = None,
        review_status: str | None = None,
        low_confidence_only: bool = False,
        below_metric: str | None = None,
        below_threshold: float | None = None,
        diagnosis: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """tenant 단위 평가 탐색 (Evaluations 화면용)."""
        where = ["e.tenant_id = ?"]
        params: list[Any] = [tenant_id]
        if level:
            where.append("e.level = ?")
            params.append(level)
        if review_status:
            where.append("e.review_status = ?")
            params.append(review_status)
        if below_metric is not None and below_threshold is not None:
            where.append(
                "EXISTS (SELECT 1 FROM scores s WHERE s.eval_id=e.eval_id "
                "AND s.metric=? AND s.score < ?)"
            )
            params.extend([below_metric, below_threshold])
        elif metric:
            where.append("EXISTS (SELECT 1 FROM scores s WHERE s.eval_id=e.eval_id AND s.metric=?)")
            params.append(metric)
        if low_confidence_only:
            where.append("EXISTS (SELECT 1 FROM scores s WHERE s.eval_id=e.eval_id AND s.is_low_confidence=1)")

        sql = (
            f"SELECT * FROM evaluations e WHERE {' AND '.join(where)} "
            f"ORDER BY e.evaluated_at DESC LIMIT ?"
        )
        params.append(limit)
        with self._connect() as conn:
            eval_rows = conn.execute(sql, params).fetchall()
            results = []
            for er in eval_rows:
                score_rows = conn.execute(
                    "SELECT * FROM scores WHERE eval_id=?", (er["eval_id"],)
                ).fetchall()
                rec = self._assemble(er, score_rows)
                if diagnosis and (rec.get("diagnosis") or {}).get("category") != diagnosis:
                    # diagnosis는 evaluations 테이블 컬럼이 아니므로 후필터 (현재 미저장 → skip)
                    pass
                results.append(rec)
        return results

    def tenant_trends(
        self,
        tenant_id: str,
        group_by: str = "run",
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """
        tenant 메트릭 평균 추이.
        group_by="day": 일자별 집계, "run": 평가배치별 집계.
        date_from/date_to: ISO 날짜(YYYY-MM-DD) 범위 필터.
        """
        where = ["e.tenant_id = ?", "e.level = 'turn'"]
        params: list[Any] = [tenant_id]
        if date_from:
            where.append("e.evaluated_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("e.evaluated_at <= ?")
            params.append(date_to + "T23:59:59")
        wsql = " AND ".join(where)

        if group_by == "day":
            bucket = "substr(e.evaluated_at, 1, 10)"   # YYYY-MM-DD
            key = "day"
        else:
            bucket = "e.run_id"
            key = "run_id"

        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT {bucket} AS bucket, MIN(e.evaluated_at) AS ts,
                          s.metric, AVG(s.score) AS avg_score, COUNT(DISTINCT e.eval_id) AS n
                   FROM evaluations e JOIN scores s ON s.eval_id=e.eval_id
                   WHERE {wsql}
                   GROUP BY bucket, s.metric
                   ORDER BY ts ASC""",
                params,
            ).fetchall()
        points: dict[str, dict[str, Any]] = {}
        metrics: set[str] = set()
        for r in rows:
            p = points.setdefault(
                r["bucket"],
                {key: r["bucket"], "label": r["bucket"], "generated_at": r["ts"], "n": r["n"]},
            )
            p[r["metric"]] = round(r["avg_score"], 4)
            metrics.add(r["metric"])
        return {"points": list(points.values()), "metrics": sorted(metrics), "group_by": group_by}

    def agreement_samples(self, tenant_id: str, metric: str) -> list[dict[str, Any]]:
        """특정 메트릭의 휴먼·자동 표본 상세 (일치도 드릴다운)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT e.eval_id, e.conversation_id, e.query, e.review_status,
                          e.human_scores_json, e.reviewer, s.score AS auto_score
                   FROM evaluations e JOIN scores s ON s.eval_id=e.eval_id
                   WHERE e.tenant_id=? AND s.metric=?
                     AND e.review_status IN ('approved','overridden')
                   ORDER BY e.evaluated_at DESC""",
                (tenant_id, metric),
            ).fetchall()
        out = []
        for r in rows:
            human = json.loads(r["human_scores_json"] or "{}")
            hv = human.get(metric, r["auto_score"])
            out.append({
                "eval_id": r["eval_id"],
                "conversation_id": r["conversation_id"],
                "query": r["query"],
                "review_status": r["review_status"],
                "reviewer": r["reviewer"],
                "auto_score": round(r["auto_score"], 4),
                "human_score": round(hv, 4),
                "delta": round(hv - r["auto_score"], 4),
                "agree": abs(hv - r["auto_score"]) < 0.13,
            })
        return out

    def human_auto_agreement(self, tenant_id: str) -> dict[str, Any]:
        """휴먼 재평가가 있는 항목에서 자동 점수와의 일치도(MAE·동의율)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT e.human_scores_json, s.metric, s.score
                   FROM evaluations e JOIN scores s ON s.eval_id=e.eval_id
                   WHERE e.tenant_id=? AND e.review_status IN ('approved','overridden')""",
                (tenant_id,),
            ).fetchall()
        per_metric: dict[str, list[tuple[float, float]]] = {}
        for r in rows:
            human = json.loads(r["human_scores_json"] or "{}")
            auto = r["score"]
            # 동의(approved)면 human=auto, 수정(overridden)이면 human_scores 우선
            hv = human.get(r["metric"], auto)
            per_metric.setdefault(r["metric"], []).append((auto, hv))
        out = {}
        total_pairs = 0
        agree = 0
        for m, pairs in per_metric.items():
            mae = sum(abs(a - h) for a, h in pairs) / len(pairs) if pairs else 0.0
            ag = sum(1 for a, h in pairs if abs(a - h) < 0.13) / len(pairs) if pairs else 0.0
            out[m] = {"n": len(pairs), "mae": round(mae, 4), "agreement": round(ag, 4)}
            total_pairs += len(pairs)
            agree += sum(1 for a, h in pairs if abs(a - h) < 0.13)
        return {
            "available": total_pairs > 0,
            "per_metric": out,
            "overall_agreement": round(agree / total_pairs, 4) if total_pairs else 0.0,
            "n": total_pairs,
        }

    def kb_status(self, tenant_id: str) -> dict[str, Any]:
        """tenant 지식베이스 현황 — 검색 컨텍스트/recall에서 파생."""
        with self._connect() as conn:
            # 검색 컨텍스트(retrieval_json)에서 고유 chunk 추출 + recall 집계
            ev_rows = conn.execute(
                """SELECT e.retrieval_json, e.conversation_id, e.query, e.evaluated_at,
                          (SELECT s.score FROM scores s WHERE s.eval_id=e.eval_id AND s.metric='context_recall') AS recall
                   FROM evaluations e
                   WHERE e.tenant_id=? AND e.level='turn'""",
                (tenant_id,),
            ).fetchall()
        chunks: set[str] = set()
        sources: set[str] = set()
        recalls: list[float] = []
        gaps: list[dict[str, Any]] = []
        last_ts: str | None = None
        for r in ev_rows:
            try:
                rr = json.loads(r["retrieval_json"] or "{}")
            except json.JSONDecodeError:
                rr = {}
            for c in rr.get("contexts", []):
                chunks.add(c.get("chunk_id", ""))
                sources.add(c.get("source_call_id", "doc"))
            if r["recall"] is not None:
                recalls.append(r["recall"])
                if r["recall"] < 0.7:
                    gaps.append({
                        "query": r["query"], "conversation_id": r["conversation_id"],
                        "context_recall": round(r["recall"], 4),
                    })
            if r["evaluated_at"] and (last_ts is None or r["evaluated_at"] > last_ts):
                last_ts = r["evaluated_at"]
        chunks.discard("")

        # 수동 구축 KB 문서 병합 (Knowledge Base 메뉴에서 추가한 고객사 지식)
        built = self.list_kb_documents(tenant_id)
        built_chunks = sum(d["chunk_count"] for d in built)
        built_types = {d["source_type"] for d in built}
        if built:
            built_last = max((d["created_at"] for d in built if d["created_at"]), default=None)
            if built_last and (last_ts is None or built_last > last_ts):
                last_ts = built_last

        return {
            "tenant_id": tenant_id,
            "document_count": len(sources) + len(built),
            "chunk_count": len(chunks) + built_chunks,
            "source_types": sorted(built_types | sources)[:12],
            "last_indexed_at": last_ts,
            "avg_context_recall": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
            "coverage_gaps": sorted(gaps, key=lambda g: g["context_recall"])[:10],
            "built_documents": built,
            "built_document_count": len(built),
            "built_chunk_count": built_chunks,
        }

    # ----------------------------------------------------------
    # 고객사 지식 구축 (KB 문서 수동 추가/관리)
    # ----------------------------------------------------------

    @staticmethod
    def _chunk_text(content: str, child_size: int = 200, overlap: int = 50) -> int:
        """CP2 child 청크(기본 200자, overlap 50) 기준으로 청크 수 산정."""
        text = (content or "").strip()
        if not text:
            return 0
        step = max(1, child_size - overlap)
        return max(1, (len(text) + step - 1) // step)

    def add_kb_document(
        self, tenant_id: str, title: str, content: str, source_type: str = "수동",
    ) -> dict[str, Any]:
        """고객사 지식 문서 추가 — 청킹 후 저장. CP2 인덱싱 입력 후보가 된다."""
        import uuid
        from datetime import UTC, datetime
        title = (title or "").strip()
        content = (content or "").strip()
        if not title:
            raise ValueError("문서 제목이 필요합니다.")
        if not content:
            raise ValueError("문서 내용이 필요합니다.")
        doc_id = f"kbdoc_{uuid.uuid4().hex[:10]}"
        char_count = len(content)
        chunk_count = self._chunk_text(content)
        created_at = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO kb_documents
                   (doc_id, tenant_id, title, content, source_type, char_count, chunk_count, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (doc_id, tenant_id, title, content, source_type or "수동",
                 char_count, chunk_count, created_at),
            )
        logger.info(f"[kb] 문서 추가 tenant={tenant_id} title={title!r} chunks={chunk_count}")
        return {
            "doc_id": doc_id, "tenant_id": tenant_id, "title": title,
            "source_type": source_type or "수동", "char_count": char_count,
            "chunk_count": chunk_count, "created_at": created_at,
            "content_preview": content[:160],
        }

    def list_kb_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        """tenant 구축 KB 문서 목록 (최신순)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT doc_id, tenant_id, title, source_type, char_count, chunk_count,
                          created_at, substr(content, 1, 160) AS content_preview
                   FROM kb_documents WHERE tenant_id=? ORDER BY created_at DESC""",
                (tenant_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_kb_document(self, doc_id: str) -> bool:
        """구축 KB 문서 삭제."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM kb_documents WHERE doc_id=?", (doc_id,))
            return cur.rowcount > 0

    def get_evaluation(self, run_id: str, eval_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            er = conn.execute(
                "SELECT * FROM evaluations WHERE run_id=? AND eval_id=?", (run_id, eval_id)
            ).fetchone()
            if not er:
                return None
            score_rows = conn.execute(
                "SELECT * FROM scores WHERE eval_id=?", (eval_id,)
            ).fetchall()
        return self._assemble(er, score_rows)

    def get_evaluation_by_id(self, eval_id: str) -> dict[str, Any] | None:
        """run_id 없이 eval_id로 단일 평가 조회 (검수용)."""
        with self._connect() as conn:
            er = conn.execute(
                "SELECT * FROM evaluations WHERE eval_id=?", (eval_id,)
            ).fetchone()
            if not er:
                return None
            score_rows = conn.execute(
                "SELECT * FROM scores WHERE eval_id=?", (eval_id,)
            ).fetchall()
        return self._assemble(er, score_rows)

    def trends(self) -> dict[str, Any]:
        """run별 메트릭 평균 추이 (SQL 집계)"""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT r.run_id, r.generated_at, ms.metric, ms.mean
                   FROM runs r JOIN metric_summary ms ON r.run_id=ms.run_id
                   ORDER BY r.generated_at ASC"""
            ).fetchall()
        points: dict[str, dict[str, Any]] = {}
        metrics: set[str] = set()
        for r in rows:
            p = points.setdefault(
                r["run_id"], {"run_id": r["run_id"], "generated_at": r["generated_at"]}
            )
            p[r["metric"]] = r["mean"]
            metrics.add(r["metric"])
        return {"points": list(points.values()), "metrics": sorted(metrics)}

    # ----------------------------------------------------------
    # 조립 헬퍼
    # ----------------------------------------------------------

    @staticmethod
    def _col(row: sqlite3.Row, name: str, default: Any = None) -> Any:
        """스키마 진화 호환: 컬럼이 없으면 default."""
        try:
            return row[name]
        except (IndexError, KeyError):
            return default

    @classmethod
    def _assemble(cls, er: sqlite3.Row, score_rows: list[sqlite3.Row]) -> dict[str, Any]:
        human_scores = json.loads(cls._col(er, "human_scores_json") or "{}")
        scores = [
            {
                "metric": s["metric"],
                "score": s["score"],
                "confidence": s["confidence"],
                "is_low_confidence": bool(s["is_low_confidence"]),
                "reasoning": s["reasoning"],
                "grounding_chunks": json.loads(s["grounding_json"] or "[]"),
                "human_score": human_scores.get(s["metric"]),
                "final_score": human_scores.get(s["metric"], s["score"]),
            }
            for s in score_rows
        ]
        return {
            "eval_id": er["eval_id"],
            "qa_id": er["qa_id"],
            "call_id": er["call_id"],
            "tenant_id": cls._col(er, "tenant_id", "default"),
            "conversation_id": cls._col(er, "conversation_id") or er["call_id"],
            "subscriber_id": er["subscriber_id"],
            "level": cls._col(er, "level", "turn"),
            "turn_index": cls._col(er, "turn_index"),
            "query": er["query"],
            "generated_answer": er["generated_answer"],
            "ground_truth": cls._col(er, "ground_truth"),
            "judge_provider": cls._col(er, "judge_provider", "openai"),
            "judge_model": er["judge_model"],
            "evaluated_at": er["evaluated_at"],
            "review_status": cls._col(er, "review_status", "pending"),
            "human_scores": human_scores,
            "human_label": json.loads(cls._col(er, "human_label_json") or "[]"),
            "human_comment": cls._col(er, "human_comment") or "",
            "reviewer": cls._col(er, "reviewer"),
            "reviewed_at": cls._col(er, "reviewed_at"),
            "retrieval_result": json.loads(er["retrieval_json"] or "{}"),
            "scores": scores,
        }

    # ----------------------------------------------------------
    # 휴먼 재평가 & 대화/검수 큐 (M0)
    # ----------------------------------------------------------

    def record_human_review(
        self,
        eval_id: str,
        status: str,
        human_scores: dict[str, float] | None = None,
        labels: list[str] | None = None,
        comment: str = "",
        reviewer: str | None = None,
    ) -> bool:
        """검수자 확정값 저장 (Final Score는 조회 시 human 우선 적용)."""
        from datetime import UTC, datetime
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE evaluations SET
                     review_status=?, human_scores_json=?, human_label_json=?,
                     human_comment=?, reviewer=?, reviewed_at=?
                   WHERE eval_id=?""",
                (
                    status,
                    json.dumps(human_scores or {}, ensure_ascii=False),
                    json.dumps(labels or [], ensure_ascii=False),
                    comment, reviewer, datetime.now(UTC).isoformat(), eval_id,
                ),
            )
            return cur.rowcount > 0

    def list_tenants(self) -> list[dict[str, Any]]:
        """가입자 목록 — conversations/evaluations에서 tenant 집계."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT tenant_id,
                          COUNT(DISTINCT conversation_id) AS conv_count
                   FROM conversations GROUP BY tenant_id"""
            ).fetchall()
            tenants = []
            for r in rows:
                pending = conn.execute(
                    """SELECT COUNT(*) AS n FROM evaluations
                       WHERE tenant_id=? AND level='turn' AND review_status='pending'""",
                    (r["tenant_id"],),
                ).fetchone()
                tenants.append({
                    "tenant_id": r["tenant_id"],
                    "conversation_count": r["conv_count"],
                    "pending_review_count": pending["n"] if pending else 0,
                })
        return tenants

    def upsert_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
        subscriber_id: str,
        turns: list[dict[str, Any]],
        started_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """대화 원문(turns) 저장 — 세션 타임라인 렌더링용."""
        is_multi = sum(1 for t in turns if t.get("role") == "bot") > 1
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO conversations
                   (conversation_id, tenant_id, subscriber_id, is_multiturn,
                    turn_count, turns_json, started_at, metadata_json)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                     tenant_id=excluded.tenant_id, subscriber_id=excluded.subscriber_id,
                     is_multiturn=excluded.is_multiturn, turn_count=excluded.turn_count,
                     turns_json=excluded.turns_json, started_at=excluded.started_at,
                     metadata_json=excluded.metadata_json""",
                (
                    conversation_id, tenant_id, subscriber_id, int(is_multi),
                    len(turns), json.dumps(turns, ensure_ascii=False),
                    started_at, json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )

    def list_conversations(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """세션(대화) 목록 — conversations 테이블 우선, 평가 점수/검수상태 조인."""
        where = []
        params: list[Any] = []
        if tenant_id:
            where.append("c.tenant_id = ?")
            params.append(tenant_id)
        wsql = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT c.*,
                      (SELECT COUNT(*) FROM evaluations e
                        WHERE e.conversation_id=c.conversation_id
                          AND e.level='turn' AND e.review_status='pending') AS pending
                   FROM conversations c {wsql}
                   ORDER BY c.started_at DESC""",
                params,
            ).fetchall()
            out = []
            for c in rows:
                # 세션 점수(있으면) 가져오기
                sess = conn.execute(
                    """SELECT s.metric, s.score FROM evaluations e
                       JOIN scores s ON s.eval_id=e.eval_id
                       WHERE e.conversation_id=? AND e.level='session'""",
                    (c["conversation_id"],),
                ).fetchall()
                out.append({
                    "conversation_id": c["conversation_id"],
                    "tenant_id": c["tenant_id"],
                    "subscriber_id": c["subscriber_id"],
                    "is_multiturn": bool(c["is_multiturn"]),
                    "turn_count": c["turn_count"],
                    "pending_review": c["pending"],
                    "started_at": c["started_at"],
                    "session_scores": {s["metric"]: s["score"] for s in sess},
                })
        return out

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        """세션 상세 — 대화 원문 + 턴별 평가 + 세션 평가."""
        with self._connect() as conn:
            c = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            eval_rows = conn.execute(
                "SELECT * FROM evaluations WHERE conversation_id=? ORDER BY turn_index",
                (conversation_id,),
            ).fetchall()
            evals = []
            for er in eval_rows:
                srows = conn.execute(
                    "SELECT * FROM scores WHERE eval_id=?", (er["eval_id"],)
                ).fetchall()
                evals.append(self._assemble(er, srows))
        if not c and not evals:
            return None
        turn_evals = [e for e in evals if e["level"] == "turn"]
        session_eval = next((e for e in evals if e["level"] == "session"), None)
        return {
            "conversation_id": conversation_id,
            "tenant_id": c["tenant_id"] if c else (evals[0]["tenant_id"] if evals else "default"),
            "subscriber_id": c["subscriber_id"] if c else None,
            "is_multiturn": bool(c["is_multiturn"]) if c else len(turn_evals) > 1,
            "turns": json.loads(c["turns_json"]) if c and c["turns_json"] else [],
            "started_at": c["started_at"] if c else None,
            "turn_evaluations": turn_evals,
            "session_evaluation": session_eval,
        }

    def review_queue(self, tenant_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """검수 대기(pending) 평가를 우선순위(저신뢰·미검수)로 반환."""
        where = ["e.review_status = 'pending'"]
        params: list[Any] = []
        if tenant_id:
            where.append("e.tenant_id = ?")
            params.append(tenant_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT e.*,
                      (SELECT MIN(s.confidence) FROM scores s WHERE s.eval_id=e.eval_id) AS min_conf
                   FROM evaluations e
                   WHERE {' AND '.join(where)}
                   ORDER BY min_conf ASC LIMIT ?""",
                [*params, limit],
            ).fetchall()
            out = []
            for er in rows:
                score_rows = conn.execute(
                    "SELECT * FROM scores WHERE eval_id=?", (er["eval_id"],)
                ).fetchall()
                out.append(self._assemble(er, score_rows))
        return out
