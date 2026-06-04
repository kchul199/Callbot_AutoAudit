"""
cp4_evaluator/options.py
평가 기법 토글을 한 곳에 모은 EvaluationOptions.

모든 고급 기법(편향보정/G-Eval/Ensemble/nugget/진단/도메인메트릭 등)을
이 옵션 객체 하나로 켜고 끈다.

우선순위:
  1) 명시적으로 생성자에 주입된 값
  2) settings.yaml 의 evaluation.* 값
  3) 코드 기본값

CLI(run_pipeline.py)의 --enable/--disable 플래그가 이 객체를 덮어쓴다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from AutoAudit.app.core.config import get as cfg_get


class CalibrationOptions(BaseModel):
    """Judge 편향 보정"""
    enabled: bool = False
    position_swap: bool = True          # 비교 평가 시 순서 swap 평균
    length_normalize: bool = True       # verbosity bias 완화 프롬프트
    use_anchors: bool = True            # 척도 고정용 앵커 예시 삽입
    g_eval_logprobs: bool = False       # logprob 가중 기대점수 (지원 provider 한정)


class EnsembleOptions(BaseModel):
    """다중 Judge 앙상블 + 불일치 에스컬레이션"""
    enabled: bool = False
    providers: list[str] = Field(default_factory=lambda: ["openai", "anthropic"])
    disagreement_threshold: float = 0.25   # 점수 표준편차 임계
    escalate_provider: str | None = None   # 불일치 시 메타 판정 모델
    aggregation: str = "mean"              # mean | median | escalate


class MetaEvalOptions(BaseModel):
    """인간 골든셋 대비 Judge 메타평가"""
    enabled: bool = False
    golden_set_path: str = "data/golden_set.jsonl"
    metrics: list[str] = Field(default_factory=lambda: ["spearman", "kappa", "mae"])


class NuggetOptions(BaseModel):
    """Nugget 기반 context recall (GT-free)"""
    enabled: bool = False
    max_nuggets: int = 8


class DiagnosisOptions(BaseModel):
    """검색 vs 생성 책임 진단 (2x2)"""
    enabled: bool = True
    recall_threshold: float = 0.7
    faithfulness_threshold: float = 0.8


class StatisticsOptions(BaseModel):
    """부트스트랩 신뢰구간 + 유의성 회귀"""
    enabled: bool = True
    bootstrap_samples: int = 1000
    confidence_level: float = 0.95
    significance_alpha: float = 0.05


class RoutingOptions(BaseModel):
    """불확실성 기반 selective evaluation + active sampling"""
    enabled: bool = False
    escalate_on_low_confidence: bool = True
    extra_samples_on_low_conf: int = 4
    active_sampling_quota: int = 20         # 인간검수 큐 크기
    active_sampling_strategy: str = "uncertainty_sla_boundary"


class PPIOptions(BaseModel):
    """Prediction-Powered Inference (저비용 분류기 + 소량 LLM 보정)"""
    enabled: bool = False
    labeled_fraction: float = 0.2          # LLM Judge로 평가할 비율
    classifier: str = "heuristic"          # heuristic | embedding


class DomainMetricsOptions(BaseModel):
    """콜봇 도메인 특화 메트릭"""
    enabled: bool = False
    multiturn_consistency: bool = True
    safety_compliance: bool = True
    pii_patterns: list[str] = Field(
        default_factory=lambda: ["주민등록번호", "카드번호", "전화번호", "계좌번호"]
    )


class CoTOptions(BaseModel):
    """Chain-of-Thought 단계별 추론 강제

    LLM이 점수를 먼저 결정하고 근거를 역으로 생성하는 편향을 방지.
    '근거 먼저, 점수 나중' 구조로 프롬프트를 재구성한다.

    적용 메트릭: answer_relevance / context_precision / context_recall
    (faithfulness는 이미 claim NLI로 CoT 구조)
    """
    enabled: bool = True              # 기본 ON — 품질 향상 대비 비용 증가 없음
    metrics: list[str] = Field(
        default_factory=lambda: ["answer_relevance", "context_precision", "context_recall"]
    )


class ReverseVerificationOptions(BaseModel):
    """역방향 검증 (Reverse Verification)

    순방향(forward)과 역방향(backward) 두 방향으로 독립 평가 후 비교.
    두 방향이 크게 다르면(불일치) is_low_confidence=True 처리.
    최종 점수 = forward × weight_forward + reverse × weight_reverse

    faithfulness:  순방향 = 답변→컨텍스트 지지 여부
                   역방향 = 컨텍스트→답변 생성 가능 여부

    answer_relevance: 순방향 = 답변이 질문에 응답하는가
                      역방향 = 답변만 보고 원래 질문을 추론할 수 있는가
    """
    enabled: bool = False             # 기본 OFF — LLM 호출 2× 비용
    metrics: list[str] = Field(
        default_factory=lambda: ["faithfulness", "answer_relevance"]
    )
    weight_forward: float = 0.6       # 순방향 가중치
    weight_reverse: float = 0.4       # 역방향 가중치
    inconsistency_threshold: float = 0.25   # |forward - reverse| > 이 값 → is_low_confidence


class CorrectnessOptions(BaseModel):
    """정답성(Answer Correctness) + 참조 기반 채점 ① ②

    ground_truth(정답)가 있을 때만 동작.
      ① answer_correctness 메트릭 신설:
         정답 대비 claim F1(TP/FP/FN) × weight_f1
         + 의미 유사도(임베딩) × weight_similarity
      ② reference_guided: answer_relevance 판정 시 정답을 함께 제공해
         판정자가 '모범답안 대비' 채점하도록 → judge-사람 일치도 향상
    '충실하지만 틀린 답변'을 잡는 유일한 축.
    """
    enabled: bool = False             # ground_truth 있을 때만 의미 → 기본 OFF
    metric_name: str = "answer_correctness"
    weight_f1: float = 0.75           # claim F1 가중
    weight_similarity: float = 0.25   # 의미 유사도 가중
    reference_guided: bool = True     # answer_relevance에 정답 주입 (②)


class ContextInjectionOptions(BaseModel):
    """대화 맥락 주입(Multi-turn Context) ③

    턴 평가 시 직전 N턴 대화 이력을 프롬프트에 주입.
    "그건 얼마예요?" 같은 지시대명사·생략 후속 턴의 오채점을 제거.
    추가 LLM 호출 없음 (프롬프트 길이만 증가).
    """
    enabled: bool = False
    max_history_turns: int = 6        # 주입할 직전 턴 최대 개수
    metrics: list[str] = Field(
        default_factory=lambda: ["answer_relevance", "faithfulness"]
    )


class AbstentionOptions(BaseModel):
    """적정 거절/모름 판정(Appropriate Abstention) ④

    "정보가 없습니다/안내가 어렵습니다" 같은 거절이 정당한지 판정.
    컨텍스트에 실제로 정보가 없어 거절이 옳다면 relevance/faithfulness
    감점을 면제(exempt_score 부여) → 정당한 거절의 '거짓 실패'를 제거.
    """
    enabled: bool = False
    exempt_metrics: list[str] = Field(
        default_factory=lambda: ["answer_relevance", "faithfulness"]
    )
    exempt_score: float = 1.0         # 정당 거절 시 부여 점수


class NumericGuardOptions(BaseModel):
    """결정적 수치·엔티티 가드(Numeric/Entity Guard) ⑤

    답변 속 숫자·금액·날짜·기간을 컨텍스트와 결정적으로 대조.
    컨텍스트에 없는 수치를 답변이 주장하면 수치 환각으로 보고
    faithfulness 점수를 충돌 수만큼 감점 → LLM이 놓치는 수치 환각 포착.
    LLM 호출 없음 (정규식 기반, 비용 0).
    """
    enabled: bool = False
    apply_to: str = "faithfulness"    # 가드를 적용할 메트릭
    penalty_per_conflict: float = 0.3  # 충돌 1건당 감점
    check_dates: bool = True          # 날짜/기간 표현도 검사


class AutoCalibrationOptions(BaseModel):
    """휴먼 정합 자동 보정 루프(Auto-Calibration) ⑦

    meta_eval이 '측정'(spearman/kappa/mae)만 했다면, 이 옵션은 '교정'까지:
      - 골든셋(judge→human)으로 보정맵 학습(isotonic/platt/linear)
      - 각 점수에 보정맵 적용 (원점수는 calibrated_from에 보존)
      - SLA 임계를 사람 합격/불합격 F1 최대화 지점으로 자동 튜닝
    정확도를 '측정 가능 + 자동 개선'으로 폐루프화.
    """
    enabled: bool = False
    golden_set_path: str = "data/golden_set.jsonl"
    method: str = "isotonic"          # isotonic | platt | linear | identity
    auto_tune_sla: bool = True        # SLA 임계 자동 탐색
    min_samples: int = 10             # 메트릭별 최소 골든 샘플 (미만이면 보정 생략)


class EvaluationOptions(BaseModel):
    """전체 평가 기법 토글 묶음"""
    calibration: CalibrationOptions = Field(default_factory=CalibrationOptions)
    ensemble: EnsembleOptions = Field(default_factory=EnsembleOptions)
    meta_eval: MetaEvalOptions = Field(default_factory=MetaEvalOptions)
    nugget: NuggetOptions = Field(default_factory=NuggetOptions)
    diagnosis: DiagnosisOptions = Field(default_factory=DiagnosisOptions)
    statistics: StatisticsOptions = Field(default_factory=StatisticsOptions)
    routing: RoutingOptions = Field(default_factory=RoutingOptions)
    ppi: PPIOptions = Field(default_factory=PPIOptions)
    domain: DomainMetricsOptions = Field(default_factory=DomainMetricsOptions)
    cot: CoTOptions = Field(default_factory=CoTOptions)
    reverse: ReverseVerificationOptions = Field(default_factory=ReverseVerificationOptions)
    correctness: CorrectnessOptions = Field(default_factory=CorrectnessOptions)
    context_injection: ContextInjectionOptions = Field(default_factory=ContextInjectionOptions)
    abstention: AbstentionOptions = Field(default_factory=AbstentionOptions)
    numeric_guard: NumericGuardOptions = Field(default_factory=NumericGuardOptions)
    auto_calibration: AutoCalibrationOptions = Field(default_factory=AutoCalibrationOptions)

    # ----------------------------------------------------------
    # 로더
    # ----------------------------------------------------------

    @classmethod
    def from_config(cls, config_path: str = "config/settings.yaml") -> EvaluationOptions:
        """settings.yaml의 evaluation.* 를 읽어 옵션 구성"""
        raw = cfg_get("evaluation", config_path=config_path, default={}) or {}
        return cls.model_validate(raw)

    def apply_cli_overrides(self, enable: list[str] | None, disable: list[str] | None) -> EvaluationOptions:
        """
        --enable calibration,ensemble  --disable nugget 형태의 토글 적용.
        점 표기로 하위 필드도 지정 가능: --enable calibration.g_eval_logprobs
        """
        for path in (enable or []):
            self._set_flag(path, True)
        for path in (disable or []):
            self._set_flag(path, False)
        return self

    def _set_flag(self, path: str, value: bool) -> None:
        parts = path.split(".")
        target = self
        for p in parts[:-1]:
            target = getattr(target, p)
        leaf = parts[-1]
        # 그룹명만 주면 그룹의 enabled 토글
        if hasattr(target, leaf) and isinstance(getattr(target, leaf), BaseModel):
            getattr(target, leaf).enabled = value
        elif hasattr(target, leaf):
            setattr(target, leaf, value)
        else:
            raise ValueError(f"알 수 없는 평가 옵션 경로: {path}")

    def active_summary(self) -> dict[str, bool]:
        """활성화된 기법 요약 (로그/리포트용)"""
        return {
            "calibration": self.calibration.enabled,
            "ensemble": self.ensemble.enabled,
            "meta_eval": self.meta_eval.enabled,
            "nugget": self.nugget.enabled,
            "diagnosis": self.diagnosis.enabled,
            "statistics": self.statistics.enabled,
            "routing": self.routing.enabled,
            "ppi": self.ppi.enabled,
            "domain": self.domain.enabled,
            "cot": self.cot.enabled,
            "reverse": self.reverse.enabled,
            "correctness": self.correctness.enabled,
            "context_injection": self.context_injection.enabled,
            "abstention": self.abstention.enabled,
            "numeric_guard": self.numeric_guard.enabled,
            "auto_calibration": self.auto_calibration.enabled,
        }
