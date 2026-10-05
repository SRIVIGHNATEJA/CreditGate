from decimal import Decimal, ROUND_HALF_UP
from typing import Union, Optional


def format_inr(
    val: Optional[Union[Decimal, float, int, str]],
    include_symbol: bool = True,
    decimals: int = 2
) -> str:
    """
    Formats monetary amounts following standard Indian numbering system (Lakhs and Crores).
    Grouping rule:
    - Last 3 digits form the hundred group.
    - Subsequent digits are grouped in pairs of 2 (thousands, lakhs, crores).
    Examples:
    - 500 -> ₹ 500.00
    - 10000 -> ₹ 10,000.00
    - 164880.03 -> ₹ 1,64,880.03
    - 5000000 -> ₹ 50,00,000.00
    - 12345678.50 -> ₹ 1,23,45,678.50
    - -1500000 -> -₹ 15,00,000.00
    """
    if val is None:
        return "N/A"

    if isinstance(val, (int, float, str)):
        try:
            val = Decimal(str(val))
        except Exception:
            return str(val)

    # Round to specified decimal places
    quantum = Decimal("1") if decimals == 0 else Decimal("10") ** (-decimals)
    rounded_val = val.quantize(quantum, rounding=ROUND_HALF_UP)

    is_negative = rounded_val < Decimal("0")
    abs_val = abs(rounded_val)

    s = f"{abs_val:.{decimals}f}"
    if decimals > 0:
        int_part, frac_part = s.split(".")
    else:
        int_part, frac_part = s, ""

    if len(int_part) <= 3:
        grouped = int_part
    else:
        last3 = int_part[-3:]
        remaining = int_part[:-3]
        chunks = []
        while remaining:
            chunks.append(remaining[-2:])
            remaining = remaining[:-2]
        chunks.reverse()
        grouped = ",".join(chunks) + "," + last3

    formatted = f"{grouped}.{frac_part}" if decimals > 0 else grouped
    symbol = "₹ " if include_symbol else ""

    if is_negative:
        return f"-{symbol}{formatted}"
    return f"{symbol}{formatted}"
