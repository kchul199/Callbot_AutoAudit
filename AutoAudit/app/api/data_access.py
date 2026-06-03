"""
api/data_access.py
API용 통합 데이터 접근 파사드.

우선순위:
  1) SQLite ResultStore (대량 쿼리/필터/집계 — 빠름)
  2) JSON ResultsRepository (DB 미적재 run의 폴백 — 하위 호환)

API 서버는 이 파사드만 의존하므로, 저장 백엔드 교체가 서버에 영향 없음.
"""
from __future__ import annotations

from typing import Any

from AutoAudit.app.api.repository import ResultsRepository
from AutoAudit.app.core.store import ResultStore


class DataAccess:
    def __init__(
        self,
        store: ResultStore | None = None,
        repo: ResultsRepository | None = None,
    ) -> None:
        self.store = store if store is not None else ResultStore()
        self.repo = repo if repo is not None else ResultsRepository()
        # tenant 설정 (M6) — 인메모리 (운영 시 DB/파일로 영속)
        self._settings: dict[str, dict[str, Any]] = {}

    def list_runs(self) -> list[dict[str, Any]]:
        db_runs = self.store.list_runs()
        db_ids = {r["run_id"] for r in db_runs}
        # DB에 없는 run은 JSON에서 보충
        json_only = [r for r in self.repo.list_runs() if r["run_id"] not in db_ids]
        return sorted(
            db_runs + json_only,
            key=lambda r: r.get("generated_at") or "",
            reverse=True,
        )

    def latest_run_id(self) -> str | None:
        return self.store.latest_run_id() or self.repo.latest_run_id()

    # ---- 테넌트 / 대화 / 검수 (M0~M2) ----

    _TENANT_NAMES = {
        "acme": "Acme Telecom",
        "globex": "Globex 보험",
        "initech": "Initech 커머스",
    }

    def list_tenants(self) -> list[dict[str, Any]]:
        rows = self.store.list_tenants()
        out = []
        for r in rows:
            tid = r["tenant_id"]
            out.append({
                "tenant_id": tid,
                "name": self._TENANT_NAMES.get(tid, tid),
                "conversation_count": r.get("conversation_count", 0),
                "pending_review_count": r.get("pending_review_count", 0),
            })
        return out

    def list_conversations(self, tenant_id: str) -> list[dict[str, Any]]:
        return self.store.list_conversations(tenant_id=tenant_id)

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        return self.store.get_conversation(conversation_id)

    def review_queue(self, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.review_queue(tenant_id=tenant_id, limit=limit)

    def record_review(self, eval_id: str, **kwargs: Any) -> bool:
        return self.store.record_human_review(eval_id, **kwargs)

    def get_evaluation_any(self, eval_id: str) -> dict[str, Any] | None:
        """run 무관 단일 평가 조회 (검수 워크스페이스용)."""
        return self.store.get_evaluation_by_id(eval_id)

    def explore_evaluations(self, tenant_id: str, **filters: Any) -> list[dict[str, Any]]:
        return self.store.query_evaluations_by_tenant(tenant_id, **filters)

    def tenant_trends(self, tenant_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.tenant_trends(tenant_id, **kwargs)

    def agreement(self, tenant_id: str) -> dict[str, Any]:
        return self.store.human_auto_agreement(tenant_id)

    def agreement_samples(self, tenant_id: str, metric: str) -> list[dict[str, Any]]:
        return self.store.agreement_samples(tenant_id, metric)

    # ---- KB / Settings (M6) ----

    def kb_status(self, tenant_id: str) -> dict[str, Any]:
        return self.store.kb_status(tenant_id)

    def get_settings(self, tenant_id: str) -> dict[str, Any]:
        import os

        from AutoAudit.app.core.config import get as cfg_get
        if tenant_id in self._settings:
            return self._settings[tenant_id]
        mock = os.environ.get("AUTOAUDIT_MOCK") == "1"
        defaults = {
            "tenant_id": tenant_id,
            "sla_thresholds": cfg_get("cp5.sla_thresholds", default={
                "faithfulness": 0.8, "answer_relevance": 0.75,
                "context_precision": 0.7, "context_recall": 0.7,
            }),
            "eval_profile": "기본",
            "default_judge": "anthropic",
            "judge_credentials": {
                "anthropic": mock or bool(os.environ.get("ANTHROPIC_API_KEY")),
                "openai": mock or bool(os.environ.get("OPENAI_API_KEY")),
                "gemini": mock or bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
                "azure": mock or bool(os.environ.get("AZURE_OPENAI_API_KEY")),
            },
            "slack_webhook": "",
            "notify_on_regression": True,
            "reviewers": ["qa_kim", "qa_lee"],
        }
        return defaults

    def save_settings(self, tenant_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        merged = {**self.get_settings(tenant_id), **settings, "tenant_id": tenant_id}
        self._settings[tenant_id] = merged
        return merged

    def get_summary(self, run_id: str) -> dict[str, Any] | None:
        return self.store.get_summary(run_id) or self.repo.get_summary(run_id)

    def get_evaluations(self, run_id: str, **filters: Any) -> list[dict[str, Any]]:
        # DB에 해당 run이 있으면 SQL 필터 사용
        if self.store.get_summary(run_id) is not None:
            return self.store.query_evaluations(run_id, **filters)
        # 폴백: JSON repository (인메모리 필터)
        return self.repo.get_evaluations(run_id, **filters)

    def get_evaluation(self, run_id: str, eval_id: str) -> dict[str, Any] | None:
        return self.store.get_evaluation(run_id, eval_id) or self.repo.get_evaluation(
            run_id, eval_id
        )

    def trends(self) -> dict[str, Any]:
        db = self.store.trends()
        if db["points"]:
            return db
        # 폴백: JSON 기반 추이
        points = []
        series: set[str] = set()
        for r in reversed(self.repo.list_runs()):
            summary = self.repo.get_summary(r["run_id"])
            if not summary:
                continue
            point = {"run_id": r["run_id"], "generated_at": summary.get("generated_at")}
            for m in summary.get("metrics", []):
                point[m["metric"]] = m["mean"]
                series.add(m["metric"])
            points.append(point)
        return {"points": points, "metrics": sorted(series)}
