"""Submission intake: the one way a Submission enters the arena.

    intake = SubmissionIntake(db=db, storage=storage, submission_queue=queue, clock=clock)
    submission = intake.submit(challenge, user, SubmissionForm(
        type="model", name="Google Vision OCR", description="...",
        predictions=b'{"id": "1", "prediction": "..."}\\n', model_link=None,
    ))

``submit`` first refuses the Submission with ``DailyLimitReached`` if the
user already stored today's limit on the Challenge (``service.daily_limit``).
It runs every other check before it stores anything. If any check fails it
raises ``SubmissionRejected``, whose ``errors`` list every problem found (each
a ``SubmissionError`` naming the form field, and the line or Input id when
known); nothing is stored, nothing is counted and the Challenge is not Locked.
Otherwise it stores the Predictions File and the Artifact (a Prompt as
markdown) privately, saves the Submission as
``pending`` (Locking the Challenge if it is the first), and queues it for the
submission worker to score.

The checks form one ordered pipeline per Submission Type (``TYPE_INTAKES``):
the form rules every type shares, the type's own form rules, the Predictions
File against the Challenge's Inputs and Scorer, then the type's Artifact
rules. Every rule runs, so the participant gets every reason at once. A
Submission Type without an entry is refused. All Scorer-specific rules go
through the Scorer catalogue; this module adds no metric logic.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from CRUD.model import create_or_get_model
from models import Challenge, Submission, User
from models.submission import SubmissionStatus, SubmissionType
from ports.clock import Clock
from ports.object_storage import ObjectStorage, private_key
from service import scorer_catalogue
from service.challenge_data import MAX_REPORTED_ERRORS, line_of
from service.daily_limit import check_daily_limit
from submission_worker import SubmissionQueue, SubmissionTask

TYPE_FIELD = "type"
NAME_FIELD = "name"
DESCRIPTION_FIELD = "description"
MODEL_LINK_FIELD = "model_link"
PREFERRED_MODEL_FIELD = "preferred_model"
LICENSE_FIELD = "license"
PROMPT_TEXT_FIELD = "prompt_text"
PROMPT_FILE_FIELD = "prompt_file"
PREDICTIONS_FIELD = "predictions_file"

LICENSES: Tuple[str, ...] = ("CC-BY-4.0", "CC0-1.0", "MIT", "Apache-2.0")
"""The licenses a Prompt or Skill may carry, as SPDX ids."""


@dataclass(frozen=True)
class SubmissionForm:
    """What the participant sent: the form fields, the Artifact and the
    Predictions File. A Prompt comes as ``prompt_text`` or ``prompt_file``."""

    type: str
    name: str
    description: str
    predictions: bytes
    model_link: Optional[str] = None
    preferred_model: Optional[str] = None
    license: Optional[str] = None
    prompt_text: Optional[str] = None
    prompt_file: Optional[bytes] = None


@dataclass(frozen=True)
class SubmissionError:
    """One problem with a Submission.

    ``field`` is the multipart form field it concerns; ``line`` (1-based) and
    ``id`` are set when the problem is tied to a row or an Input id.
    """

    field: str
    message: str
    line: Optional[int] = None
    id: Optional[str] = None

    def to_dict(self) -> dict:
        """The error as JSON for the API response."""
        return asdict(self)


class SubmissionRejected(ValueError):
    """The Submission was refused and nothing stored. ``errors`` lists why."""

    def __init__(self, errors: List[SubmissionError]):
        self.errors = list(errors)
        super().__init__("; ".join(f"{e.field}: {e.message}" for e in self.errors))


@dataclass(frozen=True)
class Candidate:
    """A Submission under check: the cleaned form and the Challenge it is for."""

    challenge: Challenge
    form: SubmissionForm
    storage: ObjectStorage


Rule = Callable[[Candidate], List[SubmissionError]]
"""A check on a Candidate, returning the problems it finds (none if it passes)."""

@dataclass
class Stash:
    """Puts one Submission's files in private object storage and remembers
    them, so they can all be discarded if the Submission is not saved."""

    storage: ObjectStorage
    submission_id: uuid.UUID
    keys: List[str] = field(default_factory=list)

    def put(self, filename: str, body: bytes, content_type: str) -> str:
        """Store ``body`` under the Submission's private prefix.

        Args:
            filename: The object's name within the Submission's prefix.
            body: The bytes to store.
            content_type: The object's media type.

        Returns:
            The object's storage key.
        """
        key = private_key("submissions", str(self.submission_id), filename)
        self.storage.put(key, body, content_type=content_type)
        self.keys.append(key)
        return key

    def discard(self) -> None:
        """Delete every object this stash stored."""
        for key in self.keys:
            self.storage.delete(key)


Columns = Callable[[Session, User, SubmissionForm, Stash], Dict[str, Any]]
"""The type's own Submission columns, storing its Artifact (through the
stash, so it is cleaned up on a failed save) if it has one."""


@dataclass(frozen=True)
class TypeIntake:
    """How one Submission Type is checked and stored.

    ``form_rules`` run after the shared form rules, ``artifact_rules`` after
    the Predictions File check; ``columns`` runs only once every rule passed.
    """

    form_rules: Tuple[Rule, ...]
    artifact_rules: Tuple[Rule, ...]
    columns: Columns


class SubmissionIntake:
    """Checks and stores Submissions, with the outside world it needs."""

    def __init__(
        self, *, db: Session, storage: ObjectStorage, submission_queue: SubmissionQueue, clock: Clock
    ) -> None:
        self.db = db
        self.storage = storage
        self.submission_queue = submission_queue
        self.clock = clock

    def submit(self, challenge: Challenge, user: User, form: SubmissionForm) -> Submission:
        """Check a Submission and, if it passes, store it and queue its scoring.

        Args:
            challenge: The (live) Challenge submitted to.
            user: The signed-in submitter.
            form: What the participant sent.

        Returns:
            The stored Submission, ``pending``.

        Raises:
            DailyLimitReached: The user used today's Submissions to this
                Challenge; checked first, and nothing was stored.
            SubmissionRejected: Listing every problem; nothing was stored.
        """
        check_daily_limit(self.db, challenge, user, self.clock())
        form = _cleaned(form)
        type_intake = _type_intake(form.type)
        candidate = Candidate(challenge=challenge, form=form, storage=self.storage)
        errors = [error for rule in _pipeline(type_intake) for error in rule(candidate)]
        if errors:
            raise SubmissionRejected(errors)
        submission = self._store(challenge, user, form, type_intake)
        self.submission_queue.add_task(SubmissionTask(submission_id=str(submission.id)))
        return submission

    def _store(
        self, challenge: Challenge, user: User, form: SubmissionForm, type_intake: TypeIntake
    ) -> Submission:
        """Store the Predictions File and the Artifact, then save the row and
        Lock the Challenge in one commit. If the commit fails the stored
        objects are deleted."""
        submission_id = uuid.uuid4()
        stash = Stash(storage=self.storage, submission_id=submission_id)
        key = stash.put("predictions.jsonl", form.predictions, content_type="application/jsonl")
        now = self.clock()
        try:
            submission = Submission(
                id=submission_id,
                user_id=user.id,
                challenge_id=challenge.id,
                type=form.type,
                name=form.name,
                description=form.description,
                predictions_url=key,
                status=SubmissionStatus.PENDING,
                created_at=now,
                updated_at=now,
                **type_intake.columns(self.db, user, form, stash),
            )
            if challenge.locked_at is None:
                challenge.locked_at = now
            self.db.add(submission)
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            stash.discard()
            raise
        self.db.refresh(submission)
        return submission


def _cleaned(form: SubmissionForm) -> SubmissionForm:
    """Text fields stripped and a blank optional field is no field. A typed
    prompt keeps its exact text unless it is blank."""
    return replace(
        form,
        type=form.type.strip().lower(),
        name=form.name.strip(),
        description=form.description.strip(),
        model_link=_blank_to_none(form.model_link),
        preferred_model=_blank_to_none(form.preferred_model),
        license=_blank_to_none(form.license),
        prompt_text=form.prompt_text if _blank_to_none(form.prompt_text) else None,
        prompt_file=form.prompt_file or None,
    )


def _blank_to_none(text: Optional[str]) -> Optional[str]:
    return (text or "").strip() or None


def _type_intake(submission_type: str) -> TypeIntake:
    """The pipeline for the type, or a rejection naming why there is none."""
    try:
        known = SubmissionType(submission_type)
    except ValueError:
        choices = ", ".join(t.value for t in SubmissionType)
        raise SubmissionRejected([SubmissionError(TYPE_FIELD, f"must be one of {choices}")]) from None
    if known not in TYPE_INTAKES:
        raise SubmissionRejected([
            SubmissionError(TYPE_FIELD, f"{known.value.capitalize()} Submissions are not accepted yet")
        ])
    return TYPE_INTAKES[known]


def _pipeline(type_intake: TypeIntake) -> Tuple[Rule, ...]:
    return SHARED_FORM_RULES + type_intake.form_rules + (_check_predictions,) + type_intake.artifact_rules


def _required(field: str) -> Rule:
    def rule(candidate: Candidate) -> List[SubmissionError]:
        if getattr(candidate.form, field):
            return []
        return [SubmissionError(field, "is required")]

    return rule


def _not_accepted(field_name: str, submission_type: SubmissionType) -> Rule:
    message = f"is not accepted for {submission_type.value.capitalize()} Submissions"

    def rule(candidate: Candidate) -> List[SubmissionError]:
        if getattr(candidate.form, field_name) is None:
            return []
        return [SubmissionError(field_name, message)]

    return rule


def _check_predictions(candidate: Candidate) -> List[SubmissionError]:
    """One Prediction per Input id, each matching the Scorer's Prediction schema."""
    challenge = candidate.challenge
    try:
        predictions = scorer_catalogue.load_predictions(candidate.form.predictions)
    except scorer_catalogue.ScorerError as exc:
        return [SubmissionError(PREDICTIONS_FIELD, str(exc), line=line_of(exc))]
    inputs = scorer_catalogue.load_inputs(candidate.storage.get(challenge.inputs_key))
    try:
        scorer_catalogue.validate_predictions(challenge.scorer_name, inputs, predictions)
    except scorer_catalogue.IdMismatchError as exc:
        return _id_mismatch_errors(exc)
    except scorer_catalogue.ScorerError:
        return _errors_per_id(challenge.scorer_name, inputs, predictions)
    return []


def _id_mismatch_errors(exc: scorer_catalogue.IdMismatchError) -> List[SubmissionError]:
    missing = [
        SubmissionError(PREDICTIONS_FIELD, f"Input id {i!r} has no Prediction", id=i) for i in exc.missing_ids
    ]
    extra = [
        SubmissionError(PREDICTIONS_FIELD, f"Prediction id {i!r} is not an Input id", id=i)
        for i in exc.extra_ids
    ]
    return (missing + extra)[:MAX_REPORTED_ERRORS]


def _errors_per_id(scorer_name: str, inputs: dict, predictions: dict) -> List[SubmissionError]:
    """The Scorer stops at the first bad Prediction; check each id on its own
    so every bad one is reported with its id (up to MAX_REPORTED_ERRORS)."""
    errors: List[SubmissionError] = []
    for row_id, prediction in predictions.items():
        try:
            scorer_catalogue.validate_predictions(scorer_name, {row_id: inputs[row_id]}, {row_id: prediction})
        except scorer_catalogue.ScorerError as exc:
            errors.append(SubmissionError(PREDICTIONS_FIELD, str(exc), id=row_id))
            if len(errors) >= MAX_REPORTED_ERRORS:
                break
    return errors


def _check_model_link(candidate: Candidate) -> List[SubmissionError]:
    """A model link, when given, is an ``https`` URL with a host."""
    link = candidate.form.model_link
    if link is None:
        return []
    parsed = urlparse(link)
    if parsed.scheme == "https" and parsed.netloc:
        return []
    return [SubmissionError(MODEL_LINK_FIELD, f"must be an https:// link (got {link!r})")]


def _check_license(candidate: Candidate) -> List[SubmissionError]:
    """A license, when given, is one of the offered SPDX ids, exactly."""
    license_id = candidate.form.license
    if license_id is None or license_id in LICENSES:
        return []
    return [SubmissionError(LICENSE_FIELD, f"must be one of {', '.join(LICENSES)} (got {license_id!r})")]


def _check_prompt(candidate: Candidate) -> List[SubmissionError]:
    """Exactly one of a typed prompt or a prompt file, and a file is UTF-8
    text. The prompt's content is otherwise not checked."""
    form = candidate.form
    if form.prompt_file is None:
        if form.prompt_text is None:
            return [SubmissionError(PROMPT_TEXT_FIELD, "is required: type the prompt or upload it as a file")]
        return []
    if form.prompt_text is not None:
        return [SubmissionError(PROMPT_FILE_FIELD, "type the prompt or upload it as a file, not both")]
    try:
        form.prompt_file.decode("utf-8")
    except UnicodeDecodeError:
        return [SubmissionError(PROMPT_FILE_FIELD, "must be a UTF-8 text file")]
    return []


def _model_columns(db: Session, user: User, form: SubmissionForm, stash: Stash) -> Dict[str, Any]:
    """A Model Submission reuses the model of that name, or creates it."""
    model = create_or_get_model(db, form.name, user.id)
    return {"model_id": model.id, "model_link": form.model_link}


def _prompt_columns(db: Session, user: User, form: SubmissionForm, stash: Stash) -> Dict[str, Any]:
    """A Prompt is stored as markdown, typed or uploaded alike."""
    prompt = form.prompt_file if form.prompt_file is not None else form.prompt_text.encode()
    return {
        "artifact_url": stash.put("prompt.md", prompt, content_type="text/markdown; charset=utf-8"),
        "preferred_model": form.preferred_model,
        "license": form.license,
    }


SHARED_FORM_RULES: Tuple[Rule, ...] = (_required(NAME_FIELD), _required(DESCRIPTION_FIELD))

TYPE_INTAKES: Dict[SubmissionType, TypeIntake] = {
    SubmissionType.MODEL: TypeIntake(
        form_rules=tuple(
            _not_accepted(f, SubmissionType.MODEL)
            for f in (PREFERRED_MODEL_FIELD, LICENSE_FIELD, PROMPT_TEXT_FIELD, PROMPT_FILE_FIELD)
        ),
        artifact_rules=(_check_model_link,),
        columns=_model_columns,
    ),
    SubmissionType.PROMPT: TypeIntake(
        form_rules=(
            _not_accepted(MODEL_LINK_FIELD, SubmissionType.PROMPT),
            _required(PREFERRED_MODEL_FIELD),
            _required(LICENSE_FIELD),
            _check_license,
        ),
        artifact_rules=(_check_prompt,),
        columns=_prompt_columns,
    ),
}
