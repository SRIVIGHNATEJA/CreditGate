from enum import IntEnum
from decimal import Decimal
from typing import Dict, List, Tuple, Any, Optional


class Verdict(IntEnum):
    """
    Strict ordered enum for credit decisions.
    Enforces that final verdict can only be equal to or lower than any policy cap.
    INSUFFICIENT_DATA (0) < REJECT (1) < CONDITIONAL (2) < APPROVE (3)
    """
    INSUFFICIENT_DATA = 0
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

# ============================================================================
# BANK RISK-BASED FACILITY PRICING MATRIX
# Benchmark: RBI Repo Rate / MCLR Base Rate = 6.50%
# ============================================================================
import os

try:
    BANK_REPO_BENCHMARK_RATE = Decimal(os.getenv("CREDITGATE_REPO_RATE", "6.50"))
except Exception:
    BANK_REPO_BENCHMARK_RATE = Decimal("6.50")

BANK_PRICING_SPREADS: Dict[Verdict, Dict[str, Any]] = {
    Verdict.APPROVE: {
        "spread": Decimal("3.75"),
        "tier_name": "Tier-1 Prime MSME",
        "rate": Decimal("10.25"),  # 6.50% + 3.75%
    },
    Verdict.CONDITIONAL: {
        "spread": Decimal("5.00"),
        "tier_name": "Tier-2 Moderate Risk",
        "rate": Decimal("11.50"),  # 6.50% + 5.00%
    },
    Verdict.REJECT: {
        "spread": Decimal("7.25"),
        "tier_name": "Tier-3 Stressed / Knockout",
        "rate": Decimal("13.75"),  # 6.50% + 7.25% (or Denied)
    },
    Verdict.INSUFFICIENT_DATA: {
        "spread": Decimal("5.00"),
        "tier_name": "Incomplete File",
        "rate": Decimal("11.50"),
    },
}


def derive_facility_pricing(
    verdict: Verdict,
    requested_amount: Decimal,
    tenor_months: int,
    concession_spread: Decimal = Decimal("0.00"),
) -> Dict[str, Any]:
    """
    Derives the bank risk-based facility pricing and monthly EMI from policy benchmark and score tier.
    """
    pricing_tier = BANK_PRICING_SPREADS.get(verdict, BANK_PRICING_SPREADS[Verdict.CONDITIONAL])
    base_spread = pricing_tier["spread"]
    effective_spread = base_spread + concession_spread
    effective_rate = BANK_REPO_BENCHMARK_RATE + effective_spread

    from core.analyser import calculate_monthly_emi
    monthly_emi = calculate_monthly_emi(
        principal=requested_amount,
        annual_rate_pct=effective_rate,
        tenure_months=tenor_months
    )

    return {
        "benchmark_rate": BANK_REPO_BENCHMARK_RATE,
        "base_spread": base_spread,
        "concession_spread": concession_spread,
        "effective_spread": effective_spread,
        "effective_rate": effective_rate,
        "tier_name": pricing_tier["tier_name"],
        "monthly_emi": monthly_emi,
        "sanctionable": verdict != Verdict.REJECT
    }


def derive_stipulated_covenants(
    case: Any,
    score_result: Optional[Any] = None,
    custom_covenants: Optional[List[str]] = None,
    collateral_value: Optional[Decimal] = None,
    cgtmse_covered: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Deterministic Credit Covenants Resolver.
    Categorizes stipulations into:
    1. Statutory Mandates (Legally required under Companies Act 2013 / Banking Law / CGTMSE).
    2. Risk-Triggered Mitigants (Dynamically derived from DSCR, turnover gap, facility exposure, score).
    3. Custom Officer Stipulations.

    Returns a structured payload with items, badges, and plain-text list for CAM export.
    """
    if collateral_value is None and case is not None:
        collateral_value = getattr(case, "collateral_value", Decimal("0.00")) or Decimal("0.00")
    if collateral_value is None:
        collateral_value = Decimal("0.00")

    if cgtmse_covered is None and case is not None:
        cgtmse_covered = getattr(case, "cgtmse_covered", False) or False
    if cgtmse_covered is None:
        cgtmse_covered = False

    statutory_covenants: List[Dict[str, Any]] = [
        {
            "title": "RoC Form CHG-1 & CERSAI Security Interest Registration within 30 Days (Sec 77 Companies Act, 2013)",
            "category": "STATUTORY",
            "badge": "STATUTORY",
            "trigger": "Statutory Mandate (Companies Act 2013 & SARFAESI Act)",
        },
        {
            "title": "Clear Title Search Report (TSR) & Non-Encumbrance Certificate by Empanelled Legal Counsel",
            "category": "STATUTORY",
            "badge": "STATUTORY",
            "trigger": "Statutory Mandate (Credit Policy §3.1)",
        },
    ]

    if cgtmse_covered:
        statutory_covenants.append({
            "title": "CGTMSE Sovereign Guarantee Enrolment & Annual Guarantee Fee (AGF) Remittance",
            "category": "STATUTORY",
            "badge": "STATUTORY",
            "trigger": "CGTMSE Scheme Mandate (§4.1 Scheme Operating Guidelines)",
        })

    risk_covenants: List[Dict[str, Any]] = []

    # 1. DSCR Trigger (< 1.30x)
    if score_result is not None:
        dscr_val = getattr(score_result, "dscr", None)
        if dscr_val is not None and dscr_val != Decimal("Infinity") and dscr_val < Decimal("1.30"):
            risk_covenants.append({
                "title": "3-Month Debt Service Reserve Account (DSRA) Cash Deposit",
                "category": "RISK_TRIGGERED",
                "badge": "RISK-TRIGGERED",
                "trigger": f"Trigger: DSCR ({float(dscr_val):.2f}x) < 1.30x",
            })

    # 2. Turnover Gap Trigger (> 15%)
    turnover_gap = Decimal("0.0")
    if case is not None and getattr(case, "gst_data", None) and getattr(case, "bank_data", None):
        gst_val = getattr(case.gst_data, "taxable_value", None) or Decimal("0")
        bank_val = getattr(case.bank_data, "total_credits", None) or Decimal("0")
        max_val = max(gst_val, bank_val)
        if max_val > Decimal("0"):
            turnover_gap = abs(gst_val - bank_val) / max_val
            if turnover_gap > Decimal("0.15"):
                risk_covenants.append({
                    "title": "Cashflow Escrow Mechanism via Designated Account",
                    "category": "RISK_TRIGGERED",
                    "badge": "RISK-TRIGGERED",
                    "trigger": f"Trigger: Turnover Gap ({float(turnover_gap)*100:.1f}%) > 15%",
                })

    # 3. Facility Exposure Trigger (≥ ₹25 Lakhs)
    req_amt = Decimal("0")
    if case is not None:
        req_amt = getattr(case, "requested_loan_amount", None) or getattr(case, "requested_amount", None) or Decimal("0")
    if req_amt >= Decimal("2500000"):
        amt_lakhs = float(req_amt) / 100000.0
        risk_covenants.append({
            "title": "Unconditional Personal Guarantee of All Promoters",
            "category": "RISK_TRIGGERED",
            "badge": "RISK-TRIGGERED",
            "trigger": f"Trigger: Facility Exposure (₹{amt_lakhs:.2f}L) ≥ ₹25 Lakhs",
        })

    # 4. Composite Score Trigger (< 70)
    if score_result is not None:
        comp_score = getattr(score_result, "composite_score", None)
        if comp_score is not None and comp_score < Decimal("70"):
            risk_covenants.append({
                "title": "Quarterly Physical Stock & Receivables Audit by Bank Auditor",
                "category": "RISK_TRIGGERED",
                "badge": "RISK-TRIGGERED",
                "trigger": f"Trigger: Composite Score ({float(comp_score):.1f}) < 70",
            })

    # 5. Custom Officer Stipulations
    officer_covenants: List[Dict[str, Any]] = []
    if custom_covenants:
        for c in custom_covenants:
            c_clean = str(c).strip()
            if c_clean:
                officer_covenants.append({
                    "title": c_clean,
                    "category": "OFFICER_DISCRETION",
                    "badge": "DISCRETIONARY",
                    "trigger": "Desk Officer Stipulation",
                })

    all_items = statutory_covenants + risk_covenants + officer_covenants
    plain_list = [c["title"] for c in all_items]

    return {
        "statutory": statutory_covenants,
        "risk_triggered": risk_covenants,
        "custom": officer_covenants,
        "all_items": all_items,
        "plain_list": plain_list,
    }


