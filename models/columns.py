"""Column helpers shared by the models: enum values stored as plain strings."""

import enum
from typing import Type


def one_of(column: str, values: Type[enum.Enum]) -> str:
    """SQL check that ``column`` holds one of the enum's values.

    Args:
        column: The column's name.
        values: The enum whose values are allowed.

    Returns:
        The check's SQL, for a ``CheckConstraint``.
    """
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"
