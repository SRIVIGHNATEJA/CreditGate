from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple


def calculate_monthly_emi(
    principal: Decimal,
    annual_rate_pct: Decimal,
    tenure_months: int
) -> Decimal:
    """
    Computes Equated Monthly Installment (EMI) using the standard banking annuity formula:
    EMI = P * r * (1 + r)^n / ((1 + r)^n - 1)
    Executed entirely in Decimal arithmetic with ROUND_HALF_UP rounding.
    """
    if tenure_months <= 0:
        raise ValueError("Tenure months must be positive")
    if principal <= Decimal("0"):
        return Decimal("0.00")

    # If 0% interest rate, simple principal amortization
    if annual_rate_pct <= Decimal("0"):
        return (principal / Decimal(tenure_months)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Monthly interest rate r = Annual Rate / (12 * 100)
    monthly_rate = annual_rate_pct / (Decimal("12") * Decimal("100"))
    
    # Calculate (1 + r)^n
    compound_factor = (Decimal("1") + monthly_rate) ** tenure_months
    
    # Annuity numerator and denominator
    numerator = principal * monthly_rate * compound_factor
    denominator = compound_factor - Decimal("1")
    
    emi = numerator / denominator
    return emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_dscr(
    profit_after_tax: Decimal,
    depreciation: Decimal,
    interest_paid: Decimal,
    annual_existing_emi: Decimal,
    annual_proposed_emi: Decimal
) -> Decimal:
    """
    Standard Banking DSCR Formula (Without double-counting interest):
    Numerator   = PAT + Depreciation + Interest Paid (Operating Cash Flow available for debt service)
    Denominator = Existing Annual EMIs + Proposed Annual EMI (Total Debt Service)

    Sentinel Handling:
    - If Total Debt Service == 0: Returns Decimal('Infinity') if OCF > 0, Decimal('0.00') if OCF == 0, Decimal('-Infinity') if OCF < 0.
    - If Operating Cash Flow <= 0: Returns Decimal('0.00') (Cash-flow deficit).
    """
    if any(x is None for x in (profit_after_tax, depreciation, interest_paid, annual_existing_emi, annual_proposed_emi)):
        raise ValueError("INSUFFICIENT_DATA: Missing mandatory parameters for DSCR calculation")

    operating_cash_flow = profit_after_tax + depreciation + interest_paid
    total_debt_service = annual_existing_emi + annual_proposed_emi

    if total_debt_service <= Decimal("0"):
        # Zero debt service gives infinite coverage ONLY if operating cash flow is positive
        if operating_cash_flow > Decimal("0"):
            return Decimal("Infinity")
        elif operating_cash_flow == Decimal("0"):
            return Decimal("0.00")
        else:
            return Decimal("-Infinity")

    if operating_cash_flow <= Decimal("0"):
        # Operating cash flow is negative or zero: unable to service any debt
        return Decimal("0.00")

    dscr = operating_cash_flow / total_debt_service
    return dscr.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def calculate_accrual_cash_gap(
    gst_turnover: Decimal,
    bank_inward_credits: Decimal
) -> Tuple[Decimal, str]:
    """
    Bidirectional analysis of the Accrual vs Cash turnover gap.
    Relative Gap = |GST Turnover - Bank Inward Credits| / max(GST Turnover, Bank Inward Credits)

    Returns:
    - gap_ratio: Decimal between 0.00 and 1.00+
    - direction: Narrative explaining the nature of the variance
    """
    max_val = max(gst_turnover, bank_inward_credits)
    if max_val <= Decimal("0"):
        return Decimal("0.00"), "NO_TURNOVER_ACTIVITY"

    diff = abs(gst_turnover - bank_inward_credits)
    gap_ratio = (diff / max_val).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    if diff == Decimal("0"):
        direction = "PERFECT_RECONCILIATION"
    elif gst_turnover > bank_inward_credits:
        direction = "GST_EXCEEDS_BANK: Potential revenue inflation, aggressive accrual recognition, or high uncollected debtor lag."
    else:
        direction = "BANK_EXCEEDS_GST: Potential non-operating deposits, capital/loan infusions, or un-invoiced cash round-tripping."

    return gap_ratio, direction


def safe_divide(
    numerator: Optional[Decimal],
    denominator: Optional[Decimal],
    zero_denominator_sentinel: Optional[Decimal] = None
) -> Optional[Decimal]:
    """
    Defensive Decimal division preventing silent corruption.
    Explicitly distinguishes between zero denominator (undefined/infinite) and zero numerator.
    Returns None if either numerator or denominator is None (never silently coerces missing inputs to 0).
    """
    if numerator is None or denominator is None:
        return None
    if denominator == Decimal("0"):
        return zero_denominator_sentinel
    return (numerator / denominator).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
