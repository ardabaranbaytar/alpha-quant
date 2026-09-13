"""Immutable, JSON-safe contracts for Hermes shadow-risk analysis."""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Any


class AuditEventKind(str, Enum):
    PAIR_ORDER_TERMINAL = "PAIR_ORDER_TERMINAL"
    DIAGNOSIS_CREATED = "DIAGNOSIS_CREATED"
    STABILITY_ALERT = "STABILITY_ALERT"
    DE_RISK_TRIGGERED = "DE_RISK_TRIGGERED"


def _json_safe(value: Any):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {key: _json_safe(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON payload values must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Unsupported JSON payload value: {type(value).__name__}")


@dataclass(frozen=True)
class StabilityConfig:
    min_observations: int = 200
    max_coint_pvalue: float = 0.05
    max_adf_pvalue: float = 0.05
    max_half_life: float = 20.0
    max_hurst: float = 0.45

    def __post_init__(self):
        if type(self.min_observations) is not int or self.min_observations < 100:
            raise ValueError("min_observations must be an integer of at least 100")
        thresholds = (self.max_coint_pvalue, self.max_adf_pvalue, self.max_hurst, self.max_half_life)
        if not all(isfinite(value) for value in thresholds):
            raise ValueError("Stability thresholds must be finite")
        if not 0 < self.max_coint_pvalue < 1 or not 0 < self.max_adf_pvalue < 1:
            raise ValueError("P-value thresholds must lie in (0, 1)")
        if not 0 < self.max_half_life or not 0 < self.max_hurst <= 1:
            raise ValueError("Half-life and Hurst thresholds must be positive")

    def to_dict(self) -> dict:
        return _json_safe(self)


@dataclass(frozen=True)
class StabilityReport:
    pair: str
    observations: int
    alpha: float | None
    beta: float | None
    coint_pvalue: float | None
    adf_pvalue: float | None
    ou_half_life: float | None
    hurst: float | None
    stable: bool
    rejection_reasons: tuple[str, ...]

    def __post_init__(self):
        if not self.pair or type(self.observations) is not int or self.observations < 0:
            raise ValueError("Report requires a nonempty pair and nonnegative observation count")
        for value in (self.alpha, self.beta, self.coint_pvalue, self.adf_pvalue, self.ou_half_life, self.hurst):
            if value is not None and not isfinite(value):
                raise ValueError("Report metrics must be finite or None")
        if not isinstance(self.stable, bool) or any(not isinstance(reason, str) or not reason for reason in self.rejection_reasons):
            raise ValueError("Report status and rejection reasons are invalid")
        if self.stable and self.rejection_reasons:
            raise ValueError("A stable report cannot have rejection reasons")

    def to_dict(self) -> dict:
        return _json_safe(self)


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    kind: AuditEventKind
    occurred_at: str
    payload: dict

    def __post_init__(self):
        if not self.event_id or not isinstance(self.kind, AuditEventKind) or not isinstance(self.payload, dict):
            raise ValueError("Audit event requires an id, event kind, and dictionary payload")
        try:
            datetime.fromisoformat(self.occurred_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise ValueError("occurred_at must be an ISO-8601 timestamp") from error
        _json_safe(self.payload)

    def to_dict(self) -> dict:
        return _json_safe(self)
