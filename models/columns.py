"""Column helpers shared by the models: enum values stored as plain strings."""

import enum
from typing import Optional, Type

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def one_of(column: str, values: Type[enum.Enum]) -> str:
    """SQL check that ``column`` holds one of the enum's values.

    Args:
        column: The column's name.
        values: The enum whose values are allowed.

    Returns:
        The check's SQL, for a ``CheckConstraint``.
    """
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"


class EnumString(TypeDecorator):
    """A ``String`` column read back as a member of an enum."""

    impl = String
    cache_ok = True

    def __init__(self, enum_class: Type[enum.Enum]) -> None:
        super().__init__()
        self.enum_class = enum_class

    def process_bind_param(self, value: Optional[str], dialect: Dialect) -> Optional[str]:
        """Store the member's value; a plain string must be one of the values."""
        return None if value is None else self.enum_class(value).value

    def process_result_value(self, value: Optional[str], dialect: Dialect) -> Optional[enum.Enum]:
        """Read the stored string as the enum's member."""
        return None if value is None else self.enum_class(value)
