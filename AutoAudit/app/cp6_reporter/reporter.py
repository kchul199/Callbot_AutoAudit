"""
cp6_reporter/reporter.py
HTML 대시보드 + JSON 리포트 자동 생성
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import AuditSummary, EvaluationRecord

logger = get_logger(__name__)


class AuditReporter:
    """CP5 AuditSummary → HTML 대시보드 + JSON 아카이브"""

    def __init__(self) -> None:
        self.report_dir = Path(cfg_get("cp6.report_dir", default="data/results/reports"))
        self.report_dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------
    # 진입점
    # ----------------------------------------------------------

    def generate(
        self,
        summary: AuditSummary,
        records: list[EvaluationRecord],
    ) -> dict[str, Path]:
        """HTML + JSON 리포트 생성 후 경로 반환"""
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        base_name = f"audit_{summary.run_id}_{timestamp}"

        paths: dict[str, Path] = {}

        formats: list[str] = cfg_get("cp6.output_format", default=["html", "json"])

        if "json" in formats:
            paths["json"] = self._write_json(summary, records, base_name)

        if "html" in formats:
            paths["html"] = self._write_html(summary, base_name)

        logger.info(f"Reports generated: {paths}")
        return paths

    # ----------------------------------------------------------
    # JSON 리포트
    # ----------------------------------------------------------

    def _write_json(
        self,
        summary: AuditSummary,
        records: list[EvaluationRecord],
        base_name: str,
    ) -> Path:
        path = self.report_dir / f"{base_name}.json"
        data = {
            "summary": summary.model_dump(mode="json"),
            "evaluations": [r.model_dump(mode="json") for r in records],
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"JSON report → {path}")
        return path

    # ----------------------------------------------------------
    # HTML 대시보드
    # ----------------------------------------------------------

    def _write_html(self, summary: AuditSummary, base_name: str) -> Path:
        path = self.report_dir / f"{base_name}.html"
        html = self._render_html(summary)
        path.write_text(html, encoding="utf-8")
        logger.info(f"HTML report → {path}")
        return path

    def _render_html(self, summary: AuditSummary) -> str:
        metric_rows = ""
        for m in summary.metrics:
            color = (
                "#27ae60" if m.sla_pass_rate >= 0.9
                else "#f39c12" if m.sla_pass_rate >= 0.7
                else "#e74c3c"
            )
            icon = "✅" if m.sla_pass_rate >= 0.9 else "⚠️" if m.sla_pass_rate >= 0.7 else "❌"
            metric_rows += f"""
            <tr>
              <td>{icon} {m.metric}</td>
              <td>{m.mean:.3f}</td>
              <td>{m.median:.3f}</td>
              <td>{m.p10:.3f}</td>
              <td>{m.p90:.3f}</td>
              <td style="color:{color}; font-weight:bold;">{m.sla_pass_rate:.1%}</td>
              <td>{m.below_sla_count} / {m.total_count}</td>
            </tr>
            """

        flagged_list = (
            "<ul>" + "".join(f"<li>{c}</li>" for c in summary.flagged_call_ids) + "</ul>"
            if summary.flagged_call_ids
            else "<p>없음</p>"
        )

        return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>CallBot AutoAudit — {summary.run_id}</title>
  <style>
    body {{ font-family: 'Segoe UI', sans-serif; margin: 40px; background: #f5f6fa; }}
    h1 {{ color: #2c3e50; }}
    .card {{ background: white; border-radius: 8px; padding: 24px; margin: 16px 0;
             box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; }}
    td {{ padding: 8px 10px; border-bottom: 1px solid #eee; }}
    tr:hover {{ background: #f0f3ff; }}
    .stat {{ display: inline-block; margin: 8px 24px 8px 0; }}
    .stat-num {{ font-size: 2em; font-weight: bold; color: #2c3e50; }}
    .stat-label {{ color: #7f8c8d; font-size: 0.9em; }}
  </style>
</head>
<body>
  <h1>🤖 CallBot AutoAudit 리포트</h1>
  <div class="card">
    <h2>📊 실행 요약</h2>
    <div class="stat"><div class="stat-num">{summary.total_calls}</div><div class="stat-label">총 콜 수</div></div>
    <div class="stat"><div class="stat-num">{summary.total_evaluations}</div><div class="stat-label">총 평가 수</div></div>
    <div class="stat"><div class="stat-num">{len(summary.flagged_call_ids)}</div><div class="stat-label">SLA 미달 콜</div></div>
    <p>Run ID: <code>{summary.run_id}</code> | 생성: {summary.generated_at.strftime('%Y-%m-%d %H:%M UTC')}</p>
  </div>

  <div class="card">
    <h2>📈 메트릭 상세</h2>
    <table>
      <thead>
        <tr>
          <th>메트릭</th><th>평균</th><th>중앙값</th><th>P10</th><th>P90</th>
          <th>SLA 통과율</th><th>미달/전체</th>
        </tr>
      </thead>
      <tbody>{metric_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>🚨 SLA 미달 콜 목록</h2>
    {flagged_list}
  </div>
</body>
</html>
"""
