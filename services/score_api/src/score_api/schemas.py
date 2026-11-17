"""Request and response models.

The request model is generated from the scorecard that is loaded and from
``credit_score.bounds``: one field per model input, with the same valid
ranges the training data was held to. Anything outside them is a 422.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from credit_score.bounds import CATEGORICAL, DERIVED, NUMERIC
from credit_score.scoring import required_inputs


def build_request_model(card: dict) -> type[BaseModel]:
    fields = {}
    for name in required_inputs(card, DERIVED):
        if name in CATEGORICAL:
            fields[name] = (Literal[CATEGORICAL[name]], Field(...))
            continue
        spec = NUMERIC[name]
        if name == "term":
            fields[name] = (Literal[36, 60], Field(..., description=spec.description))
            continue
        number = Annotated[float, Field(ge=spec.low, le=spec.high, allow_inf_nan=False)]
        # Nullable inputs must still be sent, as null: an omitted field is a
        # client error, a null is a value the scorecard has points for.
        annotation = number | None if spec.nullable else number
        fields[name] = (annotation, Field(..., description=spec.description))
    return create_model(
        "ScoreRequest", __config__=ConfigDict(extra="forbid", strict=False), **fields
    )


class Reason(BaseModel):
    code: str
    variable: str
    description: str
    points_lost: int


class ScoreResponse(BaseModel):
    request_id: str
    model_version: str
    score: int = Field(description="Higher means lower risk")
    pd: float = Field(description="Probability of bad within 12 months, development calibration")
    band: str = Field(description="Risk band, A (safest) to E")
    reasons: list[Reason] = Field(description="Variables that cost the most points, worst first")
    points: dict[str, int] = Field(description="Points contributed by each variable")


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    model_version: str
    database: Literal["ok", "disabled", "error"]


class ModelInfo(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_version: str
    trained_at: str
    code_commit: str
    target: str
    variables: list[str]
    inputs: list[str]
    scaling: dict[str, float]
    bands: list[dict]
    validation_metrics: dict[str, float]
