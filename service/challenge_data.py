"""Challenge data validator: checks a Challenge's Inputs, Ground Truth, Media
Archive, Scorer and parameters together, for both create and edit.

    scorer = validate_challenge_data(
        inputs=b"...", ground_truth=b"...",
        scorer_name="cer", scorer_params='{"language": "bo"}',
        media_archive=None,  # or a seekable zip file object
    )

Rules: both JSONL files parse; ids are unique per file; the two id sets are
equal; every Input and Reference matches the Scorer's schemas; the parameters
pass the Scorer's parameter schema; every Input ``media`` path is a file in
the Media Archive; the archive is a zip of at most 2 GB.

It returns the ``ValidScorer`` the data was checked against, ready to store
on the Challenge (``Challenge.apply_scorer``), or raises ``InvalidChallengeData``
whose ``errors`` list every problem found (each a ``ChallengeDataError`` naming
the form field, and the line or id when known). All Scorer-specific rules go
through the Scorer catalogue; this module adds no metric logic.

``sample_predictions_file`` builds the sample Predictions File on its own, for
the publisher.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import asdict, dataclass
from typing import Any, BinaryIO, Dict, Iterable, List, Mapping, Optional, Union

from models.challenge import Direction
from service import scorer_catalogue

INPUTS_FIELD = "inputs_file"
GROUND_TRUTH_FIELD = "ground_truth_file"
SCORER_NAME_FIELD = "scorer_name"
SCORER_PARAMS_FIELD = "scorer_params"
MEDIA_ARCHIVE_FIELD = "media_archive_file"

# The form fields that make up a Challenge's data and Scorer.
DATA_FIELDS = (SCORER_NAME_FIELD, SCORER_PARAMS_FIELD, INPUTS_FIELD, GROUND_TRUTH_FIELD, MEDIA_ARCHIVE_FIELD)

MAX_MEDIA_ARCHIVE_BYTES = 2 * 1024**3

# Stop listing per-row problems after this many; the admin fixes and retries.
MAX_REPORTED_ERRORS = 100


@dataclass(frozen=True)
class ChallengeDataError:
    """One problem with the submitted data.

    ``field`` is the multipart form field it concerns; ``line`` (1-based) and
    ``id`` are set when the problem is tied to a row or an Input id.
    """

    field: str
    message: str
    line: Optional[int] = None
    id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class InvalidChallengeData(ValueError):
    """The data cannot make a valid Challenge. ``errors`` lists every problem."""

    def __init__(self, errors: List[ChallengeDataError]):
        self.errors = list(errors)
        super().__init__("; ".join(f"{e.field}: {e.message}" for e in self.errors))


@dataclass(frozen=True)
class ValidScorer:
    """The Scorer that valid data is scored with, as a Challenge stores it:
    ``params`` has the defaults filled in."""

    name: str
    label: str
    direction: Direction
    params: Dict[str, Any]
    package_version: str


def validate_challenge_data(
    *,
    inputs: bytes,
    ground_truth: bytes,
    scorer_name: str,
    scorer_params: Union[str, Mapping[str, Any], None] = None,
    media_archive: Optional[BinaryIO] = None,
    max_media_archive_bytes: int = MAX_MEDIA_ARCHIVE_BYTES,
) -> ValidScorer:
    """Check the data against every rule and return the Scorer it is valid for.

    ``media_archive`` is a seekable binary file (a zip) or None; it is read
    in place, never loaded whole, and left at position 0.

    Raises:
        InvalidChallengeData: listing every problem found.
    """
    errors: List[ChallengeDataError] = []

    scorer = _describe_scorer(scorer_name, errors)
    params = _validate_params(scorer, scorer_params, errors)
    parsed_inputs = _load(scorer_catalogue.load_inputs, inputs, INPUTS_FIELD, errors)
    references = _load(scorer_catalogue.load_ground_truth, ground_truth, GROUND_TRUTH_FIELD, errors)
    if parsed_inputs is not None:
        _check_media(inputs, media_archive, max_media_archive_bytes, errors)
    elif media_archive is not None:
        _archive_names(media_archive, max_media_archive_bytes, errors)

    if errors:
        raise InvalidChallengeData(errors)

    try:
        scorer_catalogue.validate_ground_truth(scorer["name"], parsed_inputs, references, params)
    except scorer_catalogue.IdMismatchError as exc:
        raise InvalidChallengeData(_id_mismatch_errors(exc)) from None
    except scorer_catalogue.ScorerError as exc:
        raise InvalidChallengeData(
            _errors_per_id(scorer["name"], parsed_inputs, references, params) or [_row_error(exc)]
        ) from None

    return ValidScorer(
        name=scorer["name"],
        label=scorer["label"],
        direction=Direction(scorer["direction"]),
        params=params,
        package_version=scorer_catalogue.PACKAGE_VERSION,
    )


def sample_predictions_file(scorer_name: str, input_ids: Iterable[str]) -> bytes:
    """The sample Predictions File: one ``{"id", "prediction"}`` row per Input
    id, in the given order, with the Scorer's placeholder Prediction.

    Raises:
        scorer_catalogue.UnknownScorerError: If no Scorer has that name.
    """
    placeholder = scorer_catalogue.describe_scorer(scorer_name)["sample_prediction"]
    return "".join(
        json.dumps({"id": input_id, "prediction": placeholder}, ensure_ascii=False) + "\n"
        for input_id in input_ids
    ).encode("utf-8")


def _id_mismatch_errors(exc) -> List[ChallengeDataError]:
    return [
        ChallengeDataError(GROUND_TRUTH_FIELD, f"Input id {i!r} has no Reference in the Ground Truth", id=i)
        for i in exc.missing_ids
    ] + [
        ChallengeDataError(GROUND_TRUTH_FIELD, f"Reference id {i!r} is not in the Inputs", id=i)
        for i in exc.extra_ids
    ]


def media_archive_size_limit() -> int:
    """The largest Media Archive accepted, in bytes (2 GB). A FastAPI
    dependency, so tests can lower it."""
    return MAX_MEDIA_ARCHIVE_BYTES


def _archive_names(archive: BinaryIO, max_bytes: int, errors: List[ChallengeDataError]) -> Optional[set]:
    """The file names in the zip, or None after recording why it is unusable."""
    archive.seek(0, os.SEEK_END)
    size = archive.tell()
    archive.seek(0)
    if size > max_bytes:
        errors.append(ChallengeDataError(
            MEDIA_ARCHIVE_FIELD, f"the Media Archive is {size} bytes, larger than the {max_bytes}-byte limit"
        ))
        return None
    try:
        with zipfile.ZipFile(archive) as opened:
            return {name for name in opened.namelist() if not name.endswith("/")}
    except zipfile.BadZipFile:
        errors.append(ChallengeDataError(MEDIA_ARCHIVE_FIELD, "the Media Archive is not a valid zip file"))
        return None
    finally:
        archive.seek(0)


def _check_media(inputs: bytes, archive, max_bytes: int, errors: List[ChallengeDataError]) -> None:
    """Every Input's ``media`` path must be a file in the Media Archive."""
    names = _archive_names(archive, max_bytes, errors) if archive is not None else None
    if archive is not None and names is None:
        return  # the archive itself is unusable; already reported
    media_paths = scorer_catalogue.load_media_paths(inputs)
    missing = [(i, path) for i, path in media_paths.items() if names is None or path not in names]
    for row_id, path in missing[:MAX_REPORTED_ERRORS]:
        where = "the Media Archive" if archive is not None else "a Media Archive (none was uploaded)"
        errors.append(ChallengeDataError(
            INPUTS_FIELD, f"Input {row_id!r} refers to media {path!r}, which is not in {where}", id=row_id
        ))


def _row_error(exc, row_id: Optional[str] = None) -> ChallengeDataError:
    form_field = INPUTS_FIELD if isinstance(exc, scorer_catalogue.InvalidInputError) else GROUND_TRUTH_FIELD
    return ChallengeDataError(form_field, str(exc), id=row_id)


def _errors_per_id(scorer_name, inputs, references, params) -> List[ChallengeDataError]:
    """The Scorer stops at the first bad row; check each id on its own so every
    bad Input or Reference is reported with its id (up to MAX_REPORTED_ERRORS)."""
    errors: List[ChallengeDataError] = []
    for row_id, value in inputs.items():
        try:
            scorer_catalogue.validate_ground_truth(
                scorer_name, {row_id: value}, {row_id: references[row_id]}, params
            )
        except scorer_catalogue.ScorerError as exc:
            errors.append(_row_error(exc, row_id))
            if len(errors) >= MAX_REPORTED_ERRORS:
                break
    return errors


def _describe_scorer(name: str, errors: List[ChallengeDataError]) -> Optional[dict]:
    try:
        return scorer_catalogue.describe_scorer(name)
    except scorer_catalogue.ScorerError as exc:
        errors.append(ChallengeDataError(SCORER_NAME_FIELD, str(exc)))
        return None


def _validate_params(scorer, raw, errors: List[ChallengeDataError]) -> Optional[dict]:
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            errors.append(ChallengeDataError(SCORER_PARAMS_FIELD, f"not valid JSON ({exc})"))
            return None
    raw = {} if raw is None else raw
    if not isinstance(raw, Mapping):
        errors.append(ChallengeDataError(SCORER_PARAMS_FIELD, "must be a JSON object"))
        return None
    if scorer is None:
        return None
    try:
        return scorer_catalogue.validate_params(scorer["name"], raw)
    except scorer_catalogue.ScorerError as exc:
        errors.append(ChallengeDataError(SCORER_PARAMS_FIELD, str(exc)))
        return None


# buddhist-eval's JSONL loaders say "<file> line <n>: <problem>".
_LINE_MESSAGE = re.compile(r"\bline (\d+):")


def line_of(exc: scorer_catalogue.ScorerError) -> Optional[int]:
    """The line number a JSONL loader error names, if it names one.

    Args:
        exc: An error raised by one of the catalogue's JSONL loaders.

    Returns:
        The 1-based line number, or None for whole-file problems.
    """
    match = _LINE_MESSAGE.search(str(exc))
    return int(match.group(1)) if match else None


def _load(loader, data: bytes, form_field: str, errors: List[ChallengeDataError]):
    try:
        return loader(data)
    except scorer_catalogue.ScorerError as exc:
        errors.append(ChallengeDataError(form_field, str(exc), line=line_of(exc)))
        return None
