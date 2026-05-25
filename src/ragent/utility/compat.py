"""Python 3.10 compatibility shims."""

from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover — Python < 3.11 only; CI runs 3.11+
    # Python < 3.11: StrEnum not in stdlib; replicate the essential contract:
    # str(member) == member.value (StrEnum.__str__ returns value, not "Class.MEMBER").
    from enum import Enum

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        def __str__(self) -> str:
            return self.value


__all__ = ["StrEnum"]
