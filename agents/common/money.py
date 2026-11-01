"""Money helpers. All arithmetic is done in Decimal so the NAV bridge balances to the cent."""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def D(value) -> Decimal:
    """Convert int/float/str/Decimal to Decimal without binary-float noise."""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(str(value).replace(",", "").strip())


def cents(value) -> Decimal:
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_json_number(value) -> float:
    """Round to cents and emit as a JSON number (2 dp values are exact enough at this size)."""
    return float(cents(value))
