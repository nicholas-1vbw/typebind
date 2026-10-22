"""Optional validators for family-owned variant values."""

import re
from collections.abc import Callable


def validate_kebab_case(value: str) -> None:
    """Require lowercase ASCII words/digits separated by single hyphens, starting a-z."""
    if re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value) is None:
        raise ValueError("expected ASCII kebab-case, such as 'user-created' or 'query-v2'")


def validate_snake_case(value: str) -> None:
    """Require lowercase ASCII words/digits separated by single underscores, starting a-z."""
    if re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", value) is None:
        raise ValueError("expected ASCII snake_case, such as 'user_created' or 'query_v2'")


def validate_camel_case(value: str) -> None:
    """Require ASCII letters/digits starting a-z; uppercase acronyms are allowed."""
    if re.fullmatch(r"[a-z][a-zA-Z0-9]*", value) is None:
        raise ValueError("expected ASCII camelCase, such as 'userCreated' or 'queryV2'")


def validate_pascal_case(value: str) -> None:
    """Require ASCII letters/digits starting A-Z; uppercase acronyms are allowed."""
    if re.fullmatch(r"[A-Z][a-zA-Z0-9]*", value) is None:
        raise ValueError("expected ASCII PascalCase, such as 'UserCreated' or 'QueryV2'")


def validate_regex(pattern: str, *, flags: int = 0) -> Callable[[str], None]:
    """Compile a Python regex and return a validator requiring a full-string match.

    Native compilation errors propagate when creating the validator. The returned
    callable raises ValueError on a mismatch and never transforms the value.
    """
    compiled = re.compile(pattern, flags)

    def validate(value: str) -> None:
        if compiled.fullmatch(value) is None:
            raise ValueError(f"expected a full match for regex {compiled.pattern!r}")

    return validate
