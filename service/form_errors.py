"""Form errors: how the arena reports what is wrong with an upload.

A Challenge's data and a Submission are both refused with a list of
``FormError``, each naming the multipart form field it concerns. Long lists
are capped (``capped``); the last error then says how many more were found,
so the uploader knows the list is incomplete.
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from service import scorer_catalogue


@dataclass(frozen=True)
class FormError:
    """One problem with an upload.

    ``field`` is the multipart form field it concerns; ``line`` (1-based) and
    ``id`` are set when the problem is tied to a row or an Input id.
    """

    field: str
    message: str
    line: Optional[int] = None
    id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """The error as JSON for the API response."""
        return asdict(self)


def id_mismatch_errors(
    exc: scorer_catalogue.IdMismatchError, field: str, *, missing: str, extra: str
) -> List[FormError]:
    """One error per id that is in one file and not the other.

    Args:
        exc: The Scorer's report of the two id sets differing.
        field: The form field of the file being checked.
        missing: Message for an Input id the file lacks, with ``{id!r}`` in it.
        extra: Message for an id in the file that is not an Input id, likewise.

    Returns:
        The errors for the missing ids, then for the extra ones.
    """
    return [FormError(field, missing.format(id=i), id=i) for i in exc.missing_ids] + [
        FormError(field, extra.format(id=i), id=i) for i in exc.extra_ids
    ]


def capped(errors: List[FormError], limit: int) -> List[FormError]:
    """The first ``limit`` errors, then one on the same field saying how many more.

    Args:
        errors: Every error found.
        limit: How many to list.

    Returns:
        ``errors`` itself when it is within ``limit``.
    """
    if len(errors) <= limit:
        return errors
    return errors[:limit] + [FormError(errors[0].field, unlisted_problems(len(errors) - limit))]


def unlisted_problems(count: int) -> str:
    """The message saying how many problems a capped list leaves out.

    Args:
        count: How many problems were found beyond the listed ones.

    Returns:
        A sentence naming the count.
    """
    noun = "problem was" if count == 1 else "problems were"
    return f"{count} more {noun} found and not listed; fix these and upload again to see them"
