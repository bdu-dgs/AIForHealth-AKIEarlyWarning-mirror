"""Model integration contract. The implemented worker is dashboard/model_worker.py (a separate process)."""
from typing import Protocol
from .schemas import PredictionBatch


class ModelAdapter(Protocol):
    def predict(self, *, patient_id: str, encounter_id: str, input_revision: int,
                observations: list[dict]) -> PredictionBatch:
        """Return versioned windows, validation policy and explanations for the full visible history."""
        ...


# The current deployment uses requests/*.json and inbox/prediction/*.json as the adapter boundary.
# The future model worker must coalesce by patient/input_revision, cancel superseded jobs,
# and measure queue + preprocessing + inference + explanation + display latency separately.
