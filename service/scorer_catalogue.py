"""Scorer catalogue: the backend's only door to buddhist-eval.

Every Scorer, parameter check, Ground Truth check and JSONL loader the arena
uses comes from buddhist-eval's registry through this module. It adds no
metric logic; other backend modules import from here, never from
``buddhist_eval`` directly.

Scorers are identified by name (``"cer"``, ``"span_f1"``...). A name always
means the same computation (ADR 0001), so the name and ``PACKAGE_VERSION``
are what a Challenge stores.

Errors: every failure is a ``ScorerError``. The subclasses below let callers
tell an unknown name, a bad parameter, mismatched ids, a malformed JSONL file
and a bad Reference or Input apart; their messages name the line or ids.
"""

from __future__ import annotations

import io
from typing import Any, Mapping

import buddhist_eval
from buddhist_eval import (
    BuddhistEvalError as ScorerError,
    IdMismatchError,
    InvalidInputError,
    InvalidParameterError,
    InvalidPredictionError,
    InvalidReferenceError,
    MalformedJsonlError,
    ScoreResult,
    UnknownScorerError,
)

__all__ = [
    "PACKAGE_VERSION",
    "ScorerError",
    "IdMismatchError",
    "InvalidInputError",
    "InvalidParameterError",
    "InvalidPredictionError",
    "InvalidReferenceError",
    "MalformedJsonlError",
    "UnknownScorerError",
    "ScoreResult",
    "list_scorers",
    "describe_scorer",
    "validate_params",
    "validate_ground_truth",
    "validate_predictions",
    "score",
    "load_inputs",
    "load_media_paths",
    "load_ground_truth",
    "load_predictions",
]

PACKAGE_VERSION: str = buddhist_eval.__version__
"""The pinned buddhist-eval version, recorded on each Challenge for audit."""


def list_scorers() -> list[dict[str, Any]]:
    """Describe every registered Scorer, in registration order.

    Each description is ``Scorer.describe()``: ``name``, ``label``,
    ``direction`` (``"lower_is_better"`` / ``"higher_is_better"``),
    ``parameter_schema`` (JSON Schema with defaults), ``input_schema``,
    ``reference_schema``, ``prediction_schema`` and ``sample_prediction``.
    """
    return [scorer.describe() for scorer in buddhist_eval.list_scorers()]


def describe_scorer(name: str) -> dict[str, Any]:
    """Describe one Scorer, as in ``list_scorers``.

    Raises:
        UnknownScorerError: If no Scorer has that name.
    """
    return buddhist_eval.get_scorer(name).describe()


def validate_params(name: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Check a Scorer's parameters and return all of them with defaults filled in.

    Raises:
        UnknownScorerError: If no Scorer has that name.
        InvalidParameterError: If a parameter is unknown or its value invalid.
    """
    return buddhist_eval.get_scorer(name).validate_params(params)


def validate_ground_truth(
    name: str,
    inputs: Mapping[str, Any],
    references: Mapping[str, Any],
    params: Mapping[str, Any],
) -> None:
    """Check that the Ground Truth can be scored by the Scorer against the Inputs.

    Raises:
        UnknownScorerError: If no Scorer has that name.
        InvalidParameterError: If a parameter is unknown or invalid.
        IdMismatchError: If the id sets differ (``missing_ids``/``extra_ids``).
        InvalidInputError: If an Input has the wrong shape.
        InvalidReferenceError: If a Reference has the wrong shape or cannot be scored.
    """
    buddhist_eval.get_scorer(name).validate_ground_truth(inputs, references, **params)


def validate_predictions(name: str, inputs: Mapping[str, Any], predictions: Mapping[str, Any]) -> None:
    """Check a Predictions File: exactly one Prediction per Input id, each
    matching the Scorer's Prediction schema.

    Raises:
        UnknownScorerError: If no Scorer has that name.
        IdMismatchError: If the id sets differ (``missing_ids``/``extra_ids``).
        InvalidPredictionError: If a Prediction has the wrong shape.
    """
    buddhist_eval.get_scorer(name).validate_predictions(inputs, predictions)


def score(
    name: str,
    inputs: Mapping[str, Any],
    references: Mapping[str, Any],
    predictions: Mapping[str, Any],
    params: Mapping[str, Any],
) -> ScoreResult:
    """Score Predictions against the Ground Truth with the named Scorer.

    Returns:
        The unrounded Headline Metric and the Scorer's details as JSON values.

    Raises:
        ScorerError: Whatever the Scorer or its metric raises, with its message.
    """
    return buddhist_eval.get_scorer(name).score(inputs, references, predictions, **params)


def load_inputs(data: bytes) -> dict[str, Any]:
    """Parse Inputs JSONL (``{"id", "input", "media"?}`` rows) into id -> Input.

    Raises:
        MalformedJsonlError: With the offending line number.
    """
    return buddhist_eval.load_inputs(_stream(data, "inputs.jsonl"))


def load_media_paths(data: bytes) -> dict[str, str]:
    """Return id -> ``media`` path for the Inputs rows that have one.

    Raises:
        MalformedJsonlError: With the offending line number.
    """
    return buddhist_eval.load_media_paths(_stream(data, "inputs.jsonl"))


def load_ground_truth(data: bytes) -> dict[str, Any]:
    """Parse Ground Truth JSONL (``{"id", "reference"}`` rows) into id -> Reference.

    Raises:
        MalformedJsonlError: With the offending line number.
    """
    return buddhist_eval.load_ground_truth(_stream(data, "ground_truth.jsonl"))


def load_predictions(data: bytes) -> dict[str, Any]:
    """Parse a Predictions File (``{"id", "prediction"}`` rows) into id -> Prediction.

    Raises:
        MalformedJsonlError: With the offending line number.
    """
    return buddhist_eval.load_predictions(_stream(data, "predictions.jsonl"))


def _stream(data: bytes, name: str):
    """A named binary stream, so loader messages say which file a line is in."""
    stream = io.BytesIO(data)
    stream.name = name
    return stream
