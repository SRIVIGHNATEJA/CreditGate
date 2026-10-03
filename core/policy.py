from enum import IntEnum
from decimal import Decimal
from typing import Dict, List, Tuple


class Verdict(IntEnum):
    """
    Strict ordered enum for credit decisions.
    Enforces that final verdict can only be equal to or lower than any policy cap.
    REJECT (1) < CONDITIONAL (2) < APPROVE (3)
    """
    REJECT = 1
    CONDITIONAL = 2
    APPROVE = 3

    def __str__(self) -> str:
        return self.name


# ============================================================================
# CONFIGURABLE PROTOTYPE POLICY PARAMETERS
# (Illustrative parameters calibrated for MSME lending prototype)
# ============================================================================

# Five Cs Pillar Weights (Sum must equal 1.00)
WEIGHTS: Dict[str, Decimal] = {
    "CHARACTER": Decimal("0.20"),
    "CAPACITY": Decimal("0.30"),
    "CAPITAL": Decimal("0.20"),
    "COLLATERAL": Decimal("0.15"),
    "CONDITIONS": Decimal("0.15")
}

# The Weakest-Link Floor (Applies to operational pillars: Character, Capacity, Capital, Conditions)
# Excludes Collateral to accommodate CGTMSE collateral-free lending schemes.
WEAKEST_LINK_THRESHOLD: Decimal = Decimal("40.0")

# Composite Score to Baseline Verdict Cutoffs
COMPOSITE_APPROVE_CUTOFF: Decimal = Decimal("70.0")
COMPOSITE_CONDITIONAL_CUTOFF: Decimal = Decimal("50.0")

# Hard Knock-Out Thresholds (Force REJECT regardless of composite score)
DSCR_KNOCKOUT_THRESHOLD: Decimal = Decimal("1.20")
BOUNCE_KNOCKOUT_THRESHOLD: int = 5  # In a 12-month statement window

# ============================================================================
# BANDED SCORING LOOKUP TABLES
# ============================================================================

# DSCR Scoring Bands: (Min DSCR, Max DSCR, Score)
DSCR_BANDS: List[Tuple[Decimal, Decimal, Decimal]] = [
    (Decimal("1.50"), Decimal("Infinity"), Decimal("100.0")),
    (Decimal("1.30"), Decimal("1.50"), Decimal("80.0")),
    (Decimal("1.20"), Decimal("1.30"), Decimal("60.0")),
    (Decimal("1.00"), Decimal("1.20"), Decimal("40.0")),
    (Decimal("0.00"), Decimal("1.00"), Decimal("20.0")),
]

# Debt-to-Equity (D/E) Scoring Bands: (Min DE, Max DE, Score)
DE_BANDS: List[Tuple[Decimal, Decimal, Decimal]] = [
    (Decimal("0.00"), Decimal("1.00"), Decimal("100.0")),
    (Decimal("1.00"), Decimal("2.00"), Decimal("80.0")),
    (Decimal("2.00"), Decimal("3.00"), Decimal("60.0")),
    (Decimal("3.00"), Decimal("4.00"), Decimal("40.0")),
    (Decimal("4.00"), Decimal("Infinity"), Decimal("20.0")),
]

# Cheque / Mandate Bounce Penalties (12-month window)
BOUNCE_BANDS: List[Tuple[int, int, Decimal]] = [
    (0, 0, Decimal("0.0")),     # 0 bounces: No deduction (Base: 100)
    (1, 2, Decimal("20.0")),    # 1-2 bounces: Minor penalty
    (3, 4, Decimal("40.0")),    # 3-4 bounces: Serious distress
    (5, 999, Decimal("70.0")),  # 5+ bounces: Critical banking default
]

# Accrual vs Cash Gap Bands (|GST - Bank Inward| / max(GST, Bank))
ACCRUAL_CASH_GAP_BANDS: List[Tuple[Decimal, Decimal, Decimal]] = [
    (Decimal("0.00"), Decimal("0.15"), Decimal("100.0")),  # <= 15% gap: Normal working capital friction
    (Decimal("0.15"), Decimal("0.30"), Decimal("65.0")),   # 15%-30% gap: Moderate receivables lag / variance
    (Decimal("0.30"), Decimal("Infinity"), Decimal("30.0")), # > 30% gap: Severe mismatch / inflation signal
]

# Collateral Coverage Bands (Asset Value / Loan Amount)
COLLATERAL_COVERAGE_BANDS: List[Tuple[Decimal, Decimal, Decimal]] = [
    (Decimal("1.25"), Decimal("Infinity"), Decimal("100.0")),
    (Decimal("1.00"), Decimal("1.25"), Decimal("80.0")),
    (Decimal("0.50"), Decimal("1.00"), Decimal("50.0")),
    (Decimal("0.00"), Decimal("0.50"), Decimal("20.0")),
]
