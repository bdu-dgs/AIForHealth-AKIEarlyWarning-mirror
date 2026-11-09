"""Model integration contract. The implemented worker is dashboard/model_worker.py (a separate process)."""
from typing import Protocol
from .schemas import PredictionBatch


class ModelAdapter(Protocol):
    def predict(self, *, patient_id: str, encounter_id: str, input_revision: int,
                observations: list[dict]) -> PredictionBatch:
        """Return versioned windows, validation policy and explanations for the full visible history."""
        ...


# The bundled worker uses HTTP revision/history/snapshot/import endpoints and preserves each input revision.
# requests/*.json and inbox/prediction/*.json remain the file boundary for alternative external workers.
# Queue, preprocessing, inference, explanation and display latency need separate measurement.
