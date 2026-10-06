from decimal import Decimal
import pytest

from core.policy import Verdict
from core.scorer import score_credit_case, final_policy_gate
from core.parser import find_amount_after_label
from core.data_models import coerce_currency, CreditCase, BankExtraction, GSTExtraction, ITRExtraction
from core import config


def test_b03_final_policy_gate_preserves_insufficient_data():
    """B-03: final_policy_gate returns target unmodified if baseline_verdict is INSUFFICIENT_DATA."""
    # When missing mandatory data, score_credit_case produces INSUFFICIENT_DATA
    res = score_credit_case(None)
    assert res.baseline_verdict == Verdict.INSUFFICIENT_DATA
    assert res.final_verdict == Verdict.INSUFFICIENT_DATA

    # Calling final_policy_gate must NOT corrupt it to REJECT
    re_evaluated = final_policy_gate(res)
    assert re_evaluated.baseline_verdict == Verdict.INSUFFICIENT_DATA
    assert re_evaluated.final_verdict == Verdict.INSUFFICIENT_DATA


def test_b06_cgtmse_collateral_score_floor():
    """B-06: If cgtmse_covered is True, collateral_score must be max(Decimal('75.0'), calculated_coverage_score)."""
    b = BankExtraction(total_credits=Decimal("10000000"), total_debits=Decimal("9000000"), opening_balance=Decimal("1000000"), closing_balance=Decimal("2000000"), bounce_count=0)
    g = GSTExtraction(taxable_value=Decimal("10000000"), total_tax_paid=Decimal("1800000"))
    it = ITRExtraction(gross_total_income=Decimal("10000000"), profit_after_tax=Decimal("1500000"), depreciation=Decimal("300000"), interest_paid=Decimal("200000"), net_worth=Decimal("5000000"), total_debt=Decimal("2000000"), existing_emi_obligations=Decimal("500000"))

    case = CreditCase(
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
        requested_loan_amount=Decimal("5000000"),
        proposed_emi=Decimal("164993.54"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36,
        bank_data=b,
        gst_data=g,
        itr_data=it
    )

    # 1. Zero collateral with CGTMSE -> 75.0
    res_zero = score_credit_case(case, collateral_value=Decimal("0"), cgtmse_covered=True)
    assert res_zero.five_cs["COLLATERAL"] == Decimal("75.0")

    # 2. Partial collateral (₹5 Lakhs / ₹50 Lakhs = 0.10x coverage, which without CGTMSE would score 20.0)
    # With CGTMSE, it must NOT drop below 75.0!
    res_partial = score_credit_case(case, collateral_value=Decimal("500000"), cgtmse_covered=True)
    assert res_partial.five_cs["COLLATERAL"] == Decimal("75.0")

    # 3. High collateral (₹70 Lakhs / ₹50 Lakhs = 1.40x coverage -> 100.0)
    # With CGTMSE, max(75.0, 100.0) -> 100.0
    res_high = score_credit_case(case, collateral_value=Decimal("7000000"), cgtmse_covered=True)
    assert res_high.five_cs["COLLATERAL"] == Decimal("100.0")


def test_b08_find_amount_after_label_handles_integers():
    """B-08: find_amount_after_label regex makes decimal portion optional."""
    text_integer = "Gross Total Income: ₹ 50,00,000\nOther text..."
    res_int = find_amount_after_label(text_integer, r"Gross Total Income")
    assert res_int == "50,00,000"

    text_decimal = "Gross Total Income: ₹ 50,00,000.50\nOther text..."
    res_dec = find_amount_after_label(text_decimal, r"Gross Total Income")
    assert res_dec == "50,00,000.50"


def test_b09_coerce_currency_strips_inr_and_rs():
    """B-09: coerce_currency handles INR, Rs., and Rs prefixes."""
    assert coerce_currency("INR 15,00,000") == Decimal("1500000")
    assert coerce_currency("inr 500000.50") == Decimal("500000.50")
    assert coerce_currency("Rs. 25,000") == Decimal("25000")
    assert coerce_currency("Rs 1,000") == Decimal("1000")
    assert coerce_currency("(INR 1.5 Cr)") == Decimal("-15000000")


def test_b13_config_tolerance_aligned():
    """B-13: MAX_BOUNCES_TOLERANCE and BOUNCE_KNOCKOUT_THRESHOLD aligned to 5."""
    assert config.MAX_BOUNCES_TOLERANCE == 5
    assert config.BOUNCE_KNOCKOUT_THRESHOLD == 5
