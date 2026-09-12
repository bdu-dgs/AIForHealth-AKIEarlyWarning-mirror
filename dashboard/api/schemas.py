from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('时间必须包含时区，例如 +08:00 或 Z')
    return value.astimezone(timezone.utc).isoformat()


ID = Annotated[str, Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')]
Text = Annotated[str, Field(min_length=1, max_length=120)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)


class Patient(Strict):
    patient_id: ID
    encounter_id: ID
    name: Text
    icu_admitted_at: AwareDatetime
    bed: str = Field(default='', max_length=40)
    note: str = Field(default='', max_length=500)


class Observation(Strict):
    record_id: ID
    revision: int = Field(default=1, ge=1)
    patient_id: ID
    encounter_id: ID
    metric: Text
    label: Text
    unit: str = Field(default='', max_length=40)
    value: float
    measured_at: AwareDatetime
    available_at: AwareDatetime | None = None


class Threshold(Strict):
    value: float = Field(ge=0, le=1)
    comparison: Literal['>=', '>'] = '>='
    validation_run_id: Text
    policy_version: Text
    monitoring_window: Text
    calibration_version: Text
    locked: bool
    selection_basis: str = Field(max_length=2000)
    stability_assessment_id: Text | None = None


class Driver(Strict):
    feature: Text
    label: Text
    contribution: float
    value: str = Field(default='', max_length=120)
    unit: str = Field(default='', max_length=40)


class ForecastPoint(Strict):
    time: AwareDatetime
    value: float
    lower: float | None = None
    upper: float | None = None


class Trajectory(Strict):
    metric: Text
    label: Text
    unit: str = Field(default='', max_length=40)
    points: list[ForecastPoint] = Field(max_length=10000)


class Prediction(Strict):
    record_id: ID
    revision: int = Field(default=1, ge=1)
    patient_id: ID
    encounter_id: ID
    input_revision: int = Field(ge=0)
    input_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    model_id: Text
    model_version: Text
    target: Text
    origin_time: AwareDatetime
    data_cutoff: AwareDatetime
    horizon_end: AwareDatetime
    generated_at: AwareDatetime
    available_at: AwareDatetime | None = None
    risk: float = Field(ge=0, le=1)
    data_confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_definition: str | None = Field(default=None, max_length=1000)
    threshold: Threshold | None = None
    drivers: list[Driver] = Field(default_factory=list, max_length=100)
    trajectories: list[Trajectory] = Field(default_factory=list, max_length=30)
    stability_assessment_id: Text | None = None

    @model_validator(mode='after')
    def times(self):
        if not self.data_cutoff <= self.origin_time < self.horizon_end:
            raise ValueError('必须满足 data_cutoff ≤ origin_time < horizon_end')
        if self.generated_at < self.origin_time:
            raise ValueError('generated_at 不能早于 origin_time')
        if self.data_confidence is not None and not self.confidence_definition:
            raise ValueError('提供置信度时必须同时说明 confidence_definition')
        for series in self.trajectories:
            previous = self.origin_time
            for point in series.points:
                if not previous <= point.time <= self.horizon_end:
                    raise ValueError('轨迹时间必须有序且位于预测窗口内')
                if point.lower is not None and point.lower > point.value:
                    raise ValueError('轨迹下界大于预测值')
                if point.upper is not None and point.upper < point.value:
                    raise ValueError('轨迹上界小于预测值')
                previous = point.time
        return self


class InputBatch(Strict):
    schema_version: Literal[1] = 1
    kind: Literal['input'] = 'input'
    patients: list[Patient] = Field(default_factory=list, max_length=1000)
    observations: list[Observation] = Field(default_factory=list, max_length=10000)
    actor: str = Field(default='local-user', min_length=1, max_length=100)


class PredictionBatch(Strict):
    schema_version: Literal[1] = 1
    kind: Literal['prediction'] = 'prediction'
    predictions: list[Prediction] = Field(max_length=10000)


Batch = Annotated[InputBatch | PredictionBatch, Field(discriminator='kind')]
