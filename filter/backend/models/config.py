from typing import Literal
from pydantic import BaseModel, Field, model_validator

class Variable(BaseModel):
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]*$')
    itemid: int
    source: Literal['CHARTEVENTS', 'LABEVENTS']
    unit: str

class Config(BaseModel):
    version: str = '1.0.0'
    raw_dir: str
    workspace_dir: str
    output_dir: str
    local_storage_confirmed: bool = False
    baseline: Literal['unselected', 'prior_7d_min', 'prior_7d_first'] = 'unselected'
    followup: Literal['unselected', 'icu_72h', 'hospital_72h'] = 'unselected'
    exclusions: Literal['unselected', 'reviewed_file'] = 'unselected'
    methods_confirmed: bool = False
    availability: Literal['charttime_proxy', 'require_storetime'] = 'charttime_proxy'
    variables: list[Variable] = Field(default_factory=list)
    creatinine_itemid: int = 50912
    negative_min_measurements: int = Field(default=2, ge=1)
    negative_max_gap_hours: float = Field(default=24, gt=0, le=48)
    missing: Literal['median','mean','most_frequent','unknown','drop_feature'] = 'median'
    missing_indicator: bool = True
    train: float = Field(default=.7, gt=0, lt=1)
    validation: float = Field(default=.15, gt=0, lt=1)
    seed: int = 42
    csv_export: bool = False
    chunk_size: int = Field(default=100000, ge=100, le=500000)
    @model_validator(mode='after')
    def ratios(self):
        if self.train + self.validation >= 1:
            raise ValueError('Train + validation must be less than 1.')
        keys = [(v.source,v.itemid) for v in self.variables]
        if len(keys) != len(set(keys)):
            raise ValueError('Each source / ITEMID may be selected only once.')
        return self
