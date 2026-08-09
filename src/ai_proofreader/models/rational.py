"""Exact rational durations for pydantic models.

Musical time is rational, not binary-floating-point: a triplet eighth inside a 7/8 bar is
``7/24`` of a bar and rounding it to a float makes measure-duration checks produce phantom
errors. Every duration in the IR is a :class:`fractions.Fraction` measured in quarter notes.

This module supplies the pydantic v2 glue so ``Fraction`` can be used directly as a field type
and still serialize to JSON as a readable ``"3/2"``.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Any

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

__all__ = ["ONE", "ZERO", "Rational", "to_rational"]

#: Largest denominator we are willing to invent when coercing a float. 1260 covers every
#: tuplet up to 9 against every power-of-two division down to 128th notes.
_MAX_DENOMINATOR = 1260


def to_rational(value: Any) -> Fraction:
    """Coerce ``value`` to an exact :class:`Fraction` of a quarter note."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):  # bool is an int subclass; almost certainly a bug upstream
        raise TypeError("bool is not a valid duration")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, str):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(value).limit_denominator(_MAX_DENOMINATOR)
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return Fraction(int(value[0]), int(value[1]))
    raise TypeError(f"cannot interpret {value!r} as a musical duration")


class _RationalAnnotation:
    """Pydantic core-schema adapter for :class:`fractions.Fraction`."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            to_rational,
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, return_schema=core_schema.str_schema(), when_used="json"
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {
            "type": "string",
            "pattern": r"^-?\d+(/\d+)?$",
            "description": "Exact rational number of quarter notes, e.g. '3/2'.",
        }


Rational = Annotated[Fraction, _RationalAnnotation]

ZERO = Fraction(0)
ONE = Fraction(1)
