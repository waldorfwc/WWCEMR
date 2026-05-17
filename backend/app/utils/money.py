"""Money handling helpers — keep dollars and cents lossless.

Float is the wrong type for currency: 0.1 + 0.2 = 0.30000000000000004. We
already use `Decimal` on the DB side, but JSON responses sometimes call
`float(decimal_value)` which silently rounds. That breaks balance
reconciliation in subtle ways.

This module provides a single helper:

  money_str(v)  → "10.00" / "12345.67" / "0.00"  (str representation)

Plus a Pydantic v2 type annotation:

  MoneyStr = Annotated[Decimal, ...]

Endpoints that return money should:
  • Use Decimal on the DB / business-logic side
  • Convert to `money_str(...)` for any dict-returning endpoint
  • Or use `MoneyStr` as the type annotation on Pydantic response models

Until every endpoint migrates, money_str() is a drop-in replacement for
`float(decimal_value)` that preserves precision.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union


# Two cents of precision (we never bill fractional cents)
_CENTS = Decimal("0.01")


def money_str(v: Union[Decimal, int, float, str, None]) -> Optional[str]:
    """Convert any numeric to a money-shaped string like '10.00'. Returns
    None for None input. Quantizes to 2 decimal places using banker's
    half-up rounding (the practice's billing convention)."""
    if v is None:
        return None
    if isinstance(v, str):
        v = Decimal(v)
    if not isinstance(v, Decimal):
        v = Decimal(str(v))
    return str(v.quantize(_CENTS, rounding=ROUND_HALF_UP))


def money_dec(v: Union[Decimal, int, float, str, None]) -> Optional[Decimal]:
    """Coerce input to a quantized Decimal. Use for arithmetic, not JSON."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v.quantize(_CENTS, rounding=ROUND_HALF_UP)
    return Decimal(str(v)).quantize(_CENTS, rounding=ROUND_HALF_UP)
