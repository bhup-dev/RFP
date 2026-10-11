"""Load and validate version-controlled evaluation datasets."""

import json
from pathlib import Path

from app.evaluation.schemas import EvaluationDataset

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "synthetic_v1.json"


def load_dataset(path: Path = DEFAULT_DATASET) -> EvaluationDataset:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"evaluation dataset does not exist: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"evaluation dataset is malformed: {exc}") from exc
    return EvaluationDataset.model_validate(body)
