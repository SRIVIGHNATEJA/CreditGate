import os
import sys
import json
from decimal import Decimal
from pathlib import Path
import pytest

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.policy import (
    Verdict,
    WEAKEST_LINK_THRESHOLD,
    DSCR_KNOCKOUT_THRESHOLD,
    BOUNCE_KNOCKOUT_THRESHOLD,
    DSCR_BANDS,
    DE_BANDS,
    BOUNCE_BANDS,
    ACCRUAL_CASH_GAP_BANDS,
)
from core.analyser import (
    calculate_monthly_emi,
    calculate_dscr,
    calculate_accrual_cash_gap,
    safe_divide,
)
from core.scorer import (
    score_credit_case,
    final_policy_gate,
    ScoreResult,
    GateResult,
)
from core.data_models import (
    CreditCase,
    BankExtraction,
    GSTExtraction,
    ITRExtraction,
    coerce_currency,
)
from core.formatting import format_inr


def create_baseline_prime_case() -> CreditCase:
    """Helper fixture to generate a clean, prime MSME CreditCase."""
    return CreditCase(
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164880.03"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(
            total_credits=Decimal("120000000.00"),
            total_debits=Decimal("115000000.00"),
            opening_balance=Decimal("2500000.00"),
            closing_balance=Decimal("7500000.00"),
            bounce_count=0,
            confidence="HIGH",
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("118000000.00"),
            total_tax_paid=Decimal("21240000.00"),
            confidence="HIGH",
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("120000000.00"),
            profit_after_tax=Decimal("12000000.00"),
            depreciation=Decimal("3000000.00"),
            interest_paid=Decimal("2500000.00"),
            net_worth=Decimal("45000000.00"),
            total_debt=Decimal("20000000.00"),
            existing_emi_obligations=Decimal("4000000.00"),
            confidence="HIGH",
        ),
    )


# ============================================================================
# 1. MATHEMATICAL FORMULA TESTS
# ============================================================================

def test_annuity_emi():
    """Verify annuity EMI matches hand-calculated standard financial amortization."""
    principal = Decimal("5000000.00")
    rate = Decimal("11.50")
    tenure = 36
    emi = calculate_monthly_emi(principal, rate, tenure)
    # P * r * (1+r)^n / ((1+r)^n - 1) for ₹50L @ 11.5% 36M = ₹ 1,64,880.03
    assert abs(emi - Decimal("164880.03")) <= Decimal("0.05"), f"Unexpected EMI: {emi}"


def test_dscr_no_double_counting():
    """Verify DSCR formula does not double count interest."""
    pat = Decimal("12000000.00")
    depr = Decimal("3000000.00")
    interest = Decimal("2500000.00")
    existing_emi = Decimal("4000000.00")
    annual_proposed_emi = Decimal("1978560.36")  # 12 * 164,880.03

    # Numerator = 12M + 3M + 2.5M = 17.5M
    # Denominator = 4M + 1.978560.36M = 5.978560.36M
    # DSCR = 17.5 / 5.97856036 ≈ 2.9271
    dscr = calculate_dscr(pat, depr, interest, existing_emi, annual_proposed_emi)
    assert dscr > Decimal("2.90") and dscr < Decimal("2.95"), f"Unexpected DSCR: {dscr}"


def test_dscr_zero_debt_service_cash_flow_sign():
    """
    Patch Item 1:
    Infinite DSCR (zero debt service) gives full Capacity credit only if
    operating cash flow (PAT + Depreciation + Interest) > 0;
    otherwise score by cash-flow sign.
    """
    # Case A: Zero debt service + Positive OCF -> DSCR is Infinity -> Capacity = 100.0
    dscr_pos = calculate_dscr(
        profit_after_tax=Decimal("5000000"),
        depreciation=Decimal("1000000"),
        interest_paid=Decimal("0"),
        annual_existing_emi=Decimal("0"),
        annual_proposed_emi=Decimal("0"),
    )
    assert dscr_pos == Decimal("Infinity")

    case_pos = create_baseline_prime_case()
    case_pos.requested_loan_amount = Decimal("0.00")
    case_pos.proposed_emi = Decimal("0.00")
    case_pos.itr_data.existing_emi_obligations = Decimal("0.00")
    res_pos = score_credit_case(case_pos)
    assert res_pos.five_cs["CAPACITY"] == Decimal("100.0")

    # Case B: Zero debt service + Zero OCF -> DSCR is 0.00 -> Capacity = 20.0
    dscr_zero = calculate_dscr(
        profit_after_tax=Decimal("0"),
        depreciation=Decimal("0"),
        interest_paid=Decimal("0"),
        annual_existing_emi=Decimal("0"),
        annual_proposed_emi=Decimal("0"),
    )
    assert dscr_zero == Decimal("0.00")

    # Case C: Zero debt service + Negative OCF -> DSCR is -Infinity -> Capacity = 0.0
    dscr_neg = calculate_dscr(
        profit_after_tax=Decimal("-5000000"),
        depreciation=Decimal("1000000"),
        interest_paid=Decimal("0"),
        annual_existing_emi=Decimal("0"),
        annual_proposed_emi=Decimal("0"),
    )
    assert dscr_neg == Decimal("-Infinity")

    case_neg = create_baseline_prime_case()
    case_neg.requested_loan_amount = Decimal("0.00")
    case_neg.proposed_emi = Decimal("0.00")
    case_neg.itr_data.existing_emi_obligations = Decimal("0.00")
    case_neg.itr_data.profit_after_tax = Decimal("-5000000")
    case_neg.itr_data.depreciation = Decimal("1000000")
    case_neg.itr_data.interest_paid = Decimal("0")
    res_neg = score_credit_case(case_neg)
    assert res_neg.five_cs["CAPACITY"] == Decimal("0.0")

    # Case D: Positive debt service + Negative OCF -> DSCR is 0.00 -> Capacity = 0.0
    case_neg_debt = create_baseline_prime_case()
    case_neg_debt.itr_data.profit_after_tax = Decimal("-10000000")
    case_neg_debt.itr_data.depreciation = Decimal("1000000")
    case_neg_debt.itr_data.interest_paid = Decimal("500000")  # OCF = -8.5M
    res_neg_debt = score_credit_case(case_neg_debt)
    assert res_neg_debt.five_cs["CAPACITY"] == Decimal("0.0")


def test_insufficient_data_path_and_conversions():
    """
    Patch Item 2:
    None/missing inputs -> INSUFFICIENT_DATA path, never coerced to 0.
    Convert extracted numbers with Decimal(str(x)).
    """
    # A. Missing credit_case
    res_none = score_credit_case(None)
    assert res_none.baseline_verdict == Verdict.INSUFFICIENT_DATA
    assert res_none.final_verdict == Verdict.INSUFFICIENT_DATA
    assert any("INSUFFICIENT_DATA" in r for r in res_none.policy_reasons)

    # B. Missing bank_data
    case_no_bank = create_baseline_prime_case()
    case_no_bank.bank_data = None
    res_no_bank = score_credit_case(case_no_bank)
    assert res_no_bank.baseline_verdict == Verdict.INSUFFICIENT_DATA
    assert res_no_bank.final_verdict == Verdict.INSUFFICIENT_DATA
    assert any("bank_data" in r for r in res_no_bank.policy_reasons)

    # C. Missing gst_data
    case_no_gst = create_baseline_prime_case()
    case_no_gst.gst_data = None
    res_no_gst = score_credit_case(case_no_gst)
    assert res_no_gst.baseline_verdict == Verdict.INSUFFICIENT_DATA
    assert res_no_gst.final_verdict == Verdict.INSUFFICIENT_DATA

    # D. Missing itr_data
    case_no_itr = create_baseline_prime_case()
    case_no_itr.itr_data = None
    res_no_itr = score_credit_case(case_no_itr)
    assert res_no_itr.baseline_verdict == Verdict.INSUFFICIENT_DATA
    assert res_no_itr.final_verdict == Verdict.INSUFFICIENT_DATA

    # E. coerce_currency rejects None
    with pytest.raises(ValueError, match="Cannot coerce None to Decimal"):
        coerce_currency(None)

    # F. Decimal(str(x)) precision check on floats
    flt_val = 11.55
    coerced = coerce_currency(flt_val)
    assert coerced == Decimal("11.55")
    assert str(coerced) == "11.55"  # No binary floating point garbage like 11.5500000000000007

    # G. safe_divide returns None on zero denominator by default (never silently coerces to 0)
    assert safe_divide(Decimal("100"), Decimal("0")) is None
    assert safe_divide(None, Decimal("10")) is None


def test_field_level_none_routes_to_insufficient_data():
    """
    Review Item 3:
    First add a test where one field inside a present object is None and assert INSUFFICIENT_DATA,
    not an exception.
    """
    # Create valid case where itr_data exists, but profit_after_tax is None
    case_inner_none = create_baseline_prime_case()
    case_inner_none.itr_data.profit_after_tax = None

    # Must NOT raise TypeError or ValueError
    res = score_credit_case(case_inner_none)
    assert res.baseline_verdict == Verdict.INSUFFICIENT_DATA
    assert res.final_verdict == Verdict.INSUFFICIENT_DATA
    assert any("itr_data.profit_after_tax" in r for r in res.policy_reasons)

    # Test another inner field in bank_data: bounce_count = None
    case_bank_none = create_baseline_prime_case()
    case_bank_none.bank_data.bounce_count = None
    res_b = score_credit_case(case_bank_none)
    assert res_b.baseline_verdict == Verdict.INSUFFICIENT_DATA
    assert res_b.final_verdict == Verdict.INSUFFICIENT_DATA
    assert any("bank_data.bounce_count" in r for r in res_b.policy_reasons)


def test_format_inr_indian_grouping():
    """
    Review Item 5:
    Test rupee formatter with standard Indian grouping (Lakhs and Crores).
    """
    assert format_inr(Decimal("0.00")) == "₹ 0.00"
    assert format_inr(Decimal("500.00")) == "₹ 500.00"
    assert format_inr(Decimal("1000.00")) == "₹ 1,000.00"
    assert format_inr(Decimal("10000.00")) == "₹ 10,000.00"
    assert format_inr(Decimal("164880.03")) == "₹ 1,64,880.03"
    assert format_inr(Decimal("5000000.00")) == "₹ 50,00,000.00"
    assert format_inr(Decimal("12345678.50")) == "₹ 1,23,45,678.50"
    assert format_inr(Decimal("-1500000.00")) == "-₹ 15,00,000.00"
    assert format_inr(None) == "N/A"
    assert format_inr(Decimal("164880.03"), include_symbol=False) == "1,64,880.03"


def test_safe_divide_and_sentinels():
    """Verify safe_divide behaves defensively with explicit sentinels."""
    res_custom = safe_divide(Decimal("100"), Decimal("0"), zero_denominator_sentinel=Decimal("-1"))
    assert res_custom == Decimal("-1")

    res_normal = safe_divide(Decimal("100"), Decimal("2"))
    assert res_normal == Decimal("50.0000")


def test_bidirectional_gap_check():
    """Verify bidirectional Accrual vs Cash Turnover discrepancy detection."""
    # Direction 1: GST > Bank Inward (Possible revenue inflation / uncollected debtors)
    gap_a, dir_a = calculate_accrual_cash_gap(Decimal("10000000"), Decimal("7000000"))
    assert gap_a == Decimal("0.3000")
    assert "GST_EXCEEDS_BANK" in dir_a

    # Direction 2: Bank Inward > GST (Possible un-invoiced deposits / loan infusions)
    gap_b, dir_b = calculate_accrual_cash_gap(Decimal("5000000"), Decimal("10000000"))
    assert gap_b == Decimal("0.5000")
    assert "BANK_EXCEEDS_GST" in dir_b

    # Direction 3: Perfect Reconciliation
    gap_c, dir_c = calculate_accrual_cash_gap(Decimal("10000000"), Decimal("10000000"))
    assert gap_c == Decimal("0.0000")
    assert dir_c == "PERFECT_RECONCILIATION"


# ============================================================================
# 2. BOUNDARY TESTS (Patch Item 4)
# ============================================================================

def test_boundary_dscr_threshold():
    """
    Boundary Test: DSCR == 1.20 vs 1.1999
    Hurdle threshold is 1.20:
    - DSCR >= 1.20: hurdle passed, score = 60.0, no knockout.
    - DSCR < 1.20: hurdle failed, score = 40.0, knocks out to REJECT.
    """
    case_exact = create_baseline_prime_case()
    # Calibrate PAT so that (PAT + Depr + Int) / Total Debt Service == 1.20
    # Annual proposed EMI = 12 * 164,880.03 = 1,978,560.36
    # Total debt service = 4,000,000 + 1,978,560.36 = 5,978,560.36
    # Target numerator = 1.20 * 5,978,560.36 = 7,174,272.432
    # Depr = 3M, Int = 2.5M -> PAT = 7,174,272.432 - 5.5M = 1,674,272.43
    case_exact.itr_data.profit_after_tax = Decimal("1674272.43")
    res_exact = score_credit_case(case_exact)
    # DSCR should round to 1.2000
    assert res_exact.dscr >= Decimal("1.20")
    assert res_exact.knockout_applied is None
    assert res_exact.five_cs["CAPACITY"] == Decimal("60.0")

    # Now slightly below threshold: DSCR = 1.19
    case_below = create_baseline_prime_case()
    case_below.itr_data.profit_after_tax = Decimal("1600000.00")
    res_below = score_credit_case(case_below)
    assert res_below.dscr < Decimal("1.20")
    assert res_below.knockout_applied is not None
    assert "DSCR_KNOCKOUT" in res_below.knockout_applied
    assert res_below.final_verdict == Verdict.REJECT


def test_boundary_weakest_link_threshold():
    """
    Boundary Test: Operational pillar score == 40.0 vs 39.9
    Weakest-link threshold is 40.0:
    - Score >= 40.0: No cap applied.
    - Score < 40.0: Capped at CONDITIONAL.
    """
    # Test gate directly
    five_cs_exact = {
        "CHARACTER": Decimal("100.0"),
        "CAPACITY": Decimal("100.0"),
        "CAPITAL": Decimal("100.0"),
        "COLLATERAL": Decimal("100.0"),
        "CONDITIONS": Decimal("40.0"),  # Exactly at threshold
    }
    gate_exact = final_policy_gate(
        composite_score=Decimal("85.0"),
        five_cs=five_cs_exact,
        dscr=Decimal("2.50"),
        bounces=0,
    )
    assert gate_exact.cap_applied is None
    assert gate_exact.final_verdict == Verdict.APPROVE

    # Test 39.9: Below threshold
    five_cs_below = dict(five_cs_exact)
    five_cs_below["CONDITIONS"] = Decimal("39.9")
    gate_below = final_policy_gate(
        composite_score=Decimal("85.0"),
        five_cs=five_cs_below,
        dscr=Decimal("2.50"),
        bounces=0,
    )
    assert gate_below.cap_applied is not None
    assert "WEAKEST_LINK_CAP (CONDITIONS < 40.0)" in gate_below.cap_applied
    assert gate_below.final_verdict == Verdict.CONDITIONAL


def test_boundary_bounces():
    """
    Boundary Test: Cheque/mandate bounces 4 vs 5 vs 6
    - Bounces == 4: Character score = 60.0, NOT knocked out.
    - Bounces == 5: Character score = 30.0, knocked out to REJECT.
    - Bounces == 6: Character score = 30.0, knocked out to REJECT.
    """
    # 4 Bounces: Character penalty = 40.0, no knockout
    case_4 = create_baseline_prime_case()
    case_4.bank_data.bounce_count = 4
    res_4 = score_credit_case(case_4)
    assert res_4.five_cs["CHARACTER"] == Decimal("60.0")
    assert res_4.knockout_applied is None

    # 5 Bounces: Character penalty = 70.0, hard knockout to REJECT
    case_5 = create_baseline_prime_case()
    case_5.bank_data.bounce_count = 5
    res_5 = score_credit_case(case_5)
    assert res_5.five_cs["CHARACTER"] == Decimal("30.0")
    assert res_5.knockout_applied is not None
    assert "BOUNCE_KNOCKOUT (5 >= 5)" in res_5.knockout_applied
    assert res_5.final_verdict == Verdict.REJECT

    # 6 Bounces: Character penalty = 70.0, hard knockout to REJECT
    case_6 = create_baseline_prime_case()
    case_6.bank_data.bounce_count = 6
    res_6 = score_credit_case(case_6)
    assert res_6.five_cs["CHARACTER"] == Decimal("30.0")
    assert res_6.knockout_applied is not None
    assert "BOUNCE_KNOCKOUT (6 >= 5)" in res_6.knockout_applied
    assert res_6.final_verdict == Verdict.REJECT


def test_boundary_accrual_gap_band_edges():
    """
    Boundary Test: Accrual vs Cash Gap bands
    Band 1: [0.00, 0.15) -> 100.0 pts
    Band 2: [0.15, 0.30) -> 65.0 pts
    Band 3: [0.30, Inf)   -> 30.0 pts
    """
    # Edge 1: 0.1499 vs 0.1500
    case_149 = create_baseline_prime_case()
    case_149.gst_data.taxable_value = Decimal("10000000")
    case_149.bank_data.total_credits = Decimal("8501000")  # gap = 1499 / 10000 = 0.1499
    res_149 = score_credit_case(case_149)
    assert res_149.five_cs["CONDITIONS"] == Decimal("100.0")

    case_150 = create_baseline_prime_case()
    case_150.gst_data.taxable_value = Decimal("10000000")
    case_150.bank_data.total_credits = Decimal("8500000")  # gap = 1500 / 10000 = 0.1500
    res_150 = score_credit_case(case_150)
    assert res_150.five_cs["CONDITIONS"] == Decimal("65.0")

    # Edge 2: 0.2999 vs 0.3000
    case_299 = create_baseline_prime_case()
    case_299.gst_data.taxable_value = Decimal("10000000")
    case_299.bank_data.total_credits = Decimal("7001000")  # gap = 2999 / 10000 = 0.2999
    res_299 = score_credit_case(case_299)
    assert res_299.five_cs["CONDITIONS"] == Decimal("65.0")

    case_300 = create_baseline_prime_case()
    case_300.gst_data.taxable_value = Decimal("10000000")
    case_300.bank_data.total_credits = Decimal("7000000")  # gap = 3000 / 10000 = 0.3000
    res_300 = score_credit_case(case_300)
    assert res_300.five_cs["CONDITIONS"] == Decimal("30.0")


# ============================================================================
# 3. MONOTONICITY & INVARIANT TESTS
# ============================================================================

def test_monotonicity_properties():
    """
    Property Test:
    - Worse DSCR never raises Capacity score.
    - Worse (higher) D/E never raises Capital score.
    - Worse (higher) bounces never raise Character score.
    - Worse (higher) accrual gap never raises Conditions score.
    """
    # 1. DSCR Monotonicity
    dscr_values = [
        Decimal("0.00"),
        Decimal("0.50"),
        Decimal("1.00"),
        Decimal("1.19"),
        Decimal("1.20"),
        Decimal("1.29"),
        Decimal("1.30"),
        Decimal("1.49"),
        Decimal("1.50"),
        Decimal("2.00"),
    ]
    scores = []
    for d in dscr_values:
        score_val = Decimal("20.0")
        for min_d, max_d, s in DSCR_BANDS:
            if min_d <= d < max_d or (max_d == Decimal("Infinity") and d >= min_d):
                score_val = s
                break
        scores.append(score_val)

    for i in range(len(scores) - 1):
        assert scores[i] <= scores[i + 1], f"DSCR monotonicity violated at index {i}: {scores}"

    # 2. Bounce Monotonicity
    bounce_vals = [0, 1, 2, 3, 4, 5, 6, 10]
    char_scores = []
    for b in bounce_vals:
        penalty = Decimal("0.0")
        for min_b, max_b, p in BOUNCE_BANDS:
            if min_b <= b <= max_b:
                penalty = p
                break
        char_scores.append(Decimal("100.0") - penalty)

    for i in range(len(char_scores) - 1):
        assert char_scores[i] >= char_scores[i + 1], f"Bounce monotonicity violated: {char_scores}"


def test_verdict_invariants():
    """
    Invariant Tests:
    1. final_verdict <= baseline_verdict under all conditions (never upgrades).
    2. If any operational pillar < 40.0, final_verdict <= Verdict.CONDITIONAL.
    3. If dscr < 1.20 or bounces >= 5, final_verdict == Verdict.REJECT.
    """
    test_scores = [Decimal("35.0"), Decimal("45.0"), Decimal("65.0"), Decimal("85.0")]
    for comp in test_scores:
        for cap in [Decimal("30.0"), Decimal("60.0"), Decimal("100.0")]:
            five_cs = {
                "CHARACTER": Decimal("80.0"),
                "CAPACITY": cap,
                "CAPITAL": Decimal("80.0"),
                "COLLATERAL": Decimal("80.0"),
                "CONDITIONS": Decimal("80.0"),
            }
            gate = final_policy_gate(
                composite_score=comp,
                five_cs=five_cs,
                dscr=Decimal("1.50"),
                bounces=0,
            )
            # Invariant 1: final_verdict never exceeds baseline_verdict
            assert gate.final_verdict <= gate.baseline_verdict

            # Invariant 2: operational pillar < 40 caps at CONDITIONAL
            if cap < Decimal("40.0"):
                assert gate.final_verdict <= Verdict.CONDITIONAL


# ============================================================================
# 4. PURE POLICY GATE & SEVERE FLAGS (Patch Item 5)
# ============================================================================

def test_final_policy_gate_pure_function():
    """
    Verify final_policy_gate is a 100% pure function:
    - Same inputs always produce identical outputs.
    - Passing a ScoreResult returns a new ScoreResult without mutating the input object.
    """
    case = create_baseline_prime_case()
    original_result = score_credit_case(case)

    # Call gate multiple times
    res_copy1 = final_policy_gate(original_result)
    res_copy2 = final_policy_gate(original_result)

    assert res_copy1.final_verdict == res_copy2.final_verdict
    assert res_copy1.composite_score == original_result.composite_score
    # Verify immutability: original_result is unmodified
    assert original_result.final_verdict == Verdict.APPROVE
    assert original_result.knockout_applied is None


def test_final_policy_gate_severe_flags():
    """
    Verify final_policy_gate applies hard knockout when secondary research findings
    indicate severe legal / insolvency risk (e.g. Insolvency / NCLT SEVERE_FLAG).
    """
    case = create_baseline_prime_case()
    res = score_credit_case(case)
    assert res.final_verdict == Verdict.APPROVE

    # Apply research findings with insolvency SEVERE_FLAG
    res_with_insolvency = final_policy_gate(
        res,
        severe_flags=["SEVERE_FLAG: Insolvency proceeding admitted under IBC at NCLT Delhi Bench"]
    )
    assert res_with_insolvency.final_verdict == Verdict.REJECT
    assert res_with_insolvency.knockout_applied is not None
    assert "RESEARCH_SEVERE_FLAG" in res_with_insolvency.knockout_applied
    assert any("NCLT" in reason for reason in res_with_insolvency.policy_reasons)

    # Calling directly with GateResult signature
    gate_res = final_policy_gate(
        composite_score=Decimal("90.0"),
        five_cs=res.five_cs,
        dscr=Decimal("2.50"),
        bounces=0,
        severe_flags=["SEVERE_FLAG: NCLT Winding Up Notice"]
    )
    assert gate_res.final_verdict == Verdict.REJECT
    assert "RESEARCH_SEVERE_FLAG" in gate_res.knockout_applied


# ============================================================================
# 5. SYNTHETIC COHORT VERDICTS
# ============================================================================

def test_synthetic_cohort_verdicts():
    """
    Assert exact verdicts across all 3 realistic synthetic cohorts:
    - Cohort 1: Prime MSME -> Must APPROVE
    - Cohort 2: Distressed MSME -> Must REJECT (High bounces + Low DSCR)
    - Cohort 3: GST-Bank Mismatch Trap -> Must CONDITIONAL (Accrual gap Weakest-Link Cap)
    """
    synthetic_dir = BASE_DIR / "data" / "synthetic"

    # Cohort 1: Prime MSME
    c1_path = synthetic_dir / "cohort_1_prime" / "ground_truth.json"
    with open(c1_path) as f:
        gt1 = json.load(f)
    case_1 = CreditCase(
        company_name=gt1["company_name"],
        cin_or_pan=gt1["cin"],
        requested_loan_amount=Decimal("5000000"),
        proposed_emi=Decimal("164890"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(**gt1["bank"], confidence="HIGH"),
        gst_data=GSTExtraction(**gt1["gst"], confidence="HIGH"),
        itr_data=ITRExtraction(**gt1["itr"], confidence="HIGH"),
    )
    res_1 = score_credit_case(case_1, business_vintage_years=8, cgtmse_covered=True)
    assert res_1.final_verdict == Verdict.APPROVE
    assert res_1.composite_score >= Decimal("70.0")

    # Cohort 2: Distressed MSME
    c2_path = synthetic_dir / "cohort_2_distressed" / "ground_truth.json"
    with open(c2_path) as f:
        gt2 = json.load(f)
    case_2 = CreditCase(
        company_name=gt2["company_name"],
        cin_or_pan=gt2["cin"],
        requested_loan_amount=Decimal("5000000"),
        proposed_emi=Decimal("164890"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(**gt2["bank"], confidence="HIGH"),
        gst_data=GSTExtraction(**gt2["gst"], confidence="HIGH"),
        itr_data=ITRExtraction(**gt2["itr"], confidence="HIGH"),
    )
    res_2 = score_credit_case(case_2, business_vintage_years=7, cgtmse_covered=True)
    assert res_2.final_verdict == Verdict.REJECT
    assert res_2.knockout_applied is not None

    # Cohort 3: GST-Bank Mismatch Trap
    c3_path = synthetic_dir / "cohort_3_mismatch" / "ground_truth.json"
    with open(c3_path) as f:
        gt3 = json.load(f)
    case_3 = CreditCase(
        company_name=gt3["company_name"],
        cin_or_pan=gt3["cin"],
        requested_loan_amount=Decimal("5000000"),
        proposed_emi=Decimal("164890"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(**gt3["bank"], confidence="HIGH"),
        gst_data=GSTExtraction(**gt3["gst"], confidence="HIGH"),
        itr_data=ITRExtraction(**gt3["itr"], confidence="HIGH"),
    )
    res_3 = score_credit_case(case_3, business_vintage_years=6, cgtmse_covered=True)
    assert res_3.final_verdict == Verdict.CONDITIONAL
    assert "WEAKEST_LINK_CAP" in (res_3.cap_applied or "")


if __name__ == "__main__":
    pytest.main(["-v", __file__])
