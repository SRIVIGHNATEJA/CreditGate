import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import json
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
import requests

from core.policy import Verdict
from core.data_models import (
    CreditCase,
    BankExtraction,
    GSTExtraction,
    ITRExtraction,
    ReviewStatus,
    FindingTier,
    AdverseMediaFinding,
    AdverseMediaReport,
)
from core.researcher import (
    build_search_query,
    classify_finding_tier,
    generate_synthetic_demo_findings,
    search_adverse_media,
    confirm_adverse_finding,
    dismiss_adverse_finding,
    extract_policy_flags,
)
from core.scorer import (
    score_credit_case,
    final_policy_gate,
    check_missing_financial_fields,
    ScoreResult,
    _safe_serialize_val,
)
from core.analyser import safe_divide


@pytest.fixture
def prime_credit_case():
    return CreditCase(
        company_name="Apex Precision Tools Pvt Ltd",
        cin_or_pan="U29253DL2018PTC334120",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("165000.00"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(
            total_credits=Decimal("125000000.00"),
            total_debits=Decimal("118000000.00"),
            opening_balance=Decimal("4500000.00"),
            closing_balance=Decimal("11500000.00"),
            bounce_count=0,
            confidence="HIGH"
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("124500000.00"),
            total_tax_paid=Decimal("22410000.00"),
            confidence="HIGH"
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("124500000.00"),
            profit_after_tax=Decimal("12000000.00"),
            depreciation=Decimal("3000000.00"),
            interest_paid=Decimal("2500000.00"),
            net_worth=Decimal("65000000.00"),
            total_debt=Decimal("18000000.00"),
            existing_emi_obligations=Decimal("4000000.00"),
            confidence="HIGH"
        )
    )


# ============================================================================
# 1. TRIPARTITE REVIEW LIFECYCLE TESTS (No Auto-Rejection)
# ============================================================================

def test_unreviewed_severe_finding_leaves_verdict_untouched(prime_credit_case):
    """
    Specification 1: Web snippets and search hits must NEVER trigger an automatic Verdict.REJECT.
    An UNREVIEWED severe finding has ZERO effect on the policy gate verdict.
    """
    unreviewed_hit = AdverseMediaFinding(
        id="hit-101",
        tier=FindingTier.SEVERE,
        title="NCLT Section 9 Petition Admitted against Apex Precision Tools",
        snippet="Insolvency petition admitted by bench.",
        source_url="https://nclt.gov.in/orders/case101",
        review_status=ReviewStatus.UNREVIEWED
    )
    report = AdverseMediaReport(
        status="COMPLETED",
        findings=[unreviewed_hit],
        query_used='"Apex Precision Tools"',
        is_synthetic_demo=False
    )

    # 1. Extract flags from unreviewed report -> must be completely empty
    severe_flags, review_flags = extract_policy_flags(report)
    assert len(severe_flags) == 0
    assert len(review_flags) == 0

    # 2. Score credit case -> verdict must remain pristine APPROVE
    res = score_credit_case(
        credit_case=prime_credit_case,
        severe_flags=severe_flags,
        review_flags=review_flags
    )
    assert res.final_verdict == Verdict.APPROVE
    assert res.knockout_applied is None


def test_officer_confirmed_severe_finding_triggers_reject(prime_credit_case):
    """
    Specification 1 & 2: Only an officer-confirmed hit (CONFIRMED) forwards a severe flag
    into final_policy_gate to enforce Verdict.REJECT.
    """
    unreviewed_hit = AdverseMediaFinding(
        id="hit-101",
        tier=FindingTier.SEVERE,
        title="NCLT Section 9 Petition Admitted against Apex Precision Tools",
        snippet="Insolvency petition admitted by bench.",
        source_url="https://nclt.gov.in/orders/case101",
        review_status=ReviewStatus.UNREVIEWED
    )
    report = AdverseMediaReport(status="COMPLETED", findings=[unreviewed_hit])

    # Officer confirms finding after reviewing court order
    confirmed_report = confirm_adverse_finding(report, "hit-101", officer_name="Credit Officer Sharma")
    assert confirmed_report.findings[0].review_status == ReviewStatus.CONFIRMED
    assert confirmed_report.findings[0].reviewed_by == "Credit Officer Sharma"

    severe_flags, review_flags = extract_policy_flags(confirmed_report)
    assert len(severe_flags) == 1
    assert "CONFIRMED_INSOLVENCY" in severe_flags[0]

    # Evaluate policy gate with confirmed flag
    res = score_credit_case(
        credit_case=prime_credit_case,
        severe_flags=severe_flags,
        review_flags=review_flags
    )
    assert res.final_verdict == Verdict.REJECT
    assert res.knockout_applied is not None
    assert "RESEARCH_SEVERE_FLAG" in res.knockout_applied


def test_officer_confirmed_statutory_inquiry_caps_conditional(prime_credit_case):
    """
    Specification 2: Statutory Inquiries (Review) -> DGGI summons, pending petitions,
    tax notices cap verdict at Verdict.CONDITIONAL only after officer confirmation.
    """
    inquiry_hit = AdverseMediaFinding(
        id="hit-202",
        tier=FindingTier.REVIEW,
        title="DGGI issues summons regarding ₹1.2 Cr tax discrepancy",
        snippet="Directorate General of GST Intelligence inquiry active.",
        source_url="https://taxscan.in/dggi/2026/summons",
        review_status=ReviewStatus.UNREVIEWED
    )
    report = AdverseMediaReport(status="COMPLETED", findings=[inquiry_hit])

    # While unreviewed: zero impact
    sev_un, rev_un = extract_policy_flags(report)
    assert len(sev_un) == 0 and len(rev_un) == 0

    # Officer confirms inquiry
    confirmed_report = confirm_adverse_finding(report, "hit-202", officer_name="Risk Officer Iyer")
    sev_conf, rev_conf = extract_policy_flags(confirmed_report)
    assert len(sev_conf) == 0
    assert len(rev_conf) == 1
    assert "CONFIRMED_STATUTORY_INQUIRY" in rev_conf[0]

    res = score_credit_case(
        credit_case=prime_credit_case,
        severe_flags=sev_conf,
        review_flags=rev_conf
    )
    assert res.baseline_verdict == Verdict.APPROVE
    assert res.final_verdict == Verdict.CONDITIONAL
    assert "STATUTORY_INQUIRY_CAP" in res.cap_applied


def test_officer_dismissed_finding_maintains_baseline(prime_credit_case):
    """
    Specification 1: Loan officer marks finding as DISMISSED (e.g. entity homonym)
    with audit justification. System maintains baseline verdict without penalty.
    """
    hit = AdverseMediaFinding(
        id="hit-303",
        tier=FindingTier.SEVERE,
        title="NCLT liquidation order against Apex Tools LLP (Kolkata)",
        snippet="Order of liquidation issued.",
        source_url="https://nclt.gov.in/orders/kolkata303",
        review_status=ReviewStatus.UNREVIEWED
    )
    report = AdverseMediaReport(status="COMPLETED", findings=[hit])

    # Dismissal requires min 10 chars justification
    with pytest.raises(ValueError):
        dismiss_adverse_finding(report, "hit-303", justification="Short")

    dismissed_report = dismiss_adverse_finding(
        report,
        "hit-303",
        justification="Homonym entity collision. Kolkata LLP entity differs from applicant Delhi Pvt Ltd (CIN mismatch).",
        officer_name="Senior Underwriter Verma"
    )
    assert dismissed_report.findings[0].review_status == ReviewStatus.DISMISSED
    assert "Homonym entity" in dismissed_report.findings[0].dismissal_reason

    sev_flags, rev_flags = extract_policy_flags(dismissed_report)
    assert len(sev_flags) == 0
    assert len(rev_flags) == 0

    res = score_credit_case(credit_case=prime_credit_case, severe_flags=sev_flags, review_flags=rev_flags)
    assert res.final_verdict == Verdict.APPROVE


# ============================================================================
# 2. FAIL-SAFE API HANDLING & WATERMARKING TESTS
# ============================================================================

def test_missing_api_key_yields_research_unavailable():
    """
    Specification 3: Missing TAVILY_API_KEY must yield 'RESEARCH_UNAVAILABLE'.
    Under NO circumstances should missing key default to 'Clean' or 'No Adverse Findings'.
    """
    with patch.dict("os.environ", {}, clear=True):
        report = search_adverse_media(company_name="Apex Precision Tools", api_key=None, mock_mode=False)
        assert report.status == "RESEARCH_UNAVAILABLE"
        assert report.status != "NO_ADVERSE_FINDINGS"
        assert "TAVILY_API_KEY is not configured" in report.error_message


def test_api_timeout_or_error_yields_research_unavailable():
    """
    Specification 3: Network timeouts or HTTP errors must fail-closed to 'RESEARCH_UNAVAILABLE'.
    """
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.Timeout("Connection timed out after 8s")

    report = search_adverse_media(
        company_name="Apex Precision Tools",
        api_key="test-dummy-key",
        mock_mode=False,
        http_session=mock_session
    )
    assert report.status == "RESEARCH_UNAVAILABLE"
    assert report.status != "NO_ADVERSE_FINDINGS"
    assert "Network error" in report.error_message


def test_synthetic_demo_payloads_have_strict_watermarks():
    """
    Specification 3: When running in mock/demo mode, every simulated headline,
    snippet, and case number must be prefixed with [SYNTHETIC DEMO DATA].
    """
    report = search_adverse_media(
        company_name="Kaveri Logistics & Freight Pvt Ltd",
        cin="U60200KA2019PTC120481",
        mock_mode=True
    )
    assert report.status == "COMPLETED"
    assert report.is_synthetic_demo is True
    assert len(report.findings) >= 2

    for f in report.findings:
        assert f.title.startswith("[SYNTHETIC DEMO DATA]")
        assert f.snippet.startswith("[SYNTHETIC DEMO DATA]")
        assert f.is_synthetic is True


# ============================================================================
# 3. DISAMBIGUATION & PATTERN CLASSIFIER TESTS
# ============================================================================

def test_disambiguated_query_construction():
    """
    Specification 4: Queries must anchor with identity tokens:
    '{company_name}' AND ('{cin}' OR '{promoter_name}')
    """
    q1 = build_search_query("Apex Tools", cin="U29253DL2018PTC334120", promoter_name="Rajesh Gupta")
    assert q1 == '"Apex Tools" AND ("U29253DL2018PTC334120" OR "Rajesh Gupta")'

    q2 = build_search_query("Apex Tools", cin="U29253DL2018PTC334120")
    assert q2 == '"Apex Tools" AND "U29253DL2018PTC334120"'

    q3 = build_search_query("Apex Tools", cin="UNREGISTERED_MSME")
    assert q3 == '"Apex Tools"'


def test_tier_classifier_pattern_rules():
    """
    Specification 2: Plain nomenclature with keyword/pattern matching.
    """
    assert classify_finding_tier("Order of NCLT Section 9 Admitted", "CIRP initiated") == FindingTier.SEVERE
    assert classify_finding_tier("Liquidation order passed by tribunal", "Moratorium declared under section 14") == FindingTier.SEVERE
    assert classify_finding_tier("DGGI summons issued to directors", "Tax evasion notice served") == FindingTier.REVIEW
    assert classify_finding_tier("NCLT petition filed by vendor", "Section 9 filed") == FindingTier.REVIEW
    assert classify_finding_tier("CFO resignation announced", "Company faces executive turnover") == FindingTier.INFORMATIONAL


# ============================================================================
# 4. POLICY GATE PURITY & SERIALIZATION TESTS
# ============================================================================

def test_final_policy_gate_pure_function_repeatable():
    """
    Specification 5: Calling final_policy_gate multiple times with identical inputs
    produces identical outputs with zero state mutation.
    """
    five_cs = {
        "CHARACTER": Decimal("90.0"),
        "CAPACITY": Decimal("80.0"),
        "CAPITAL": Decimal("85.0"),
        "COLLATERAL": Decimal("80.0"),
        "CONDITIONS": Decimal("85.0")
    }
    comp = Decimal("84.0")
    dscr = Decimal("1.45")
    bounces = 0

    res1 = final_policy_gate(composite_score=comp, five_cs=five_cs, dscr=dscr, bounces=bounces)
    res2 = final_policy_gate(composite_score=comp, five_cs=five_cs, dscr=dscr, bounces=bounces)
    res3 = final_policy_gate(composite_score=comp, five_cs=five_cs, dscr=dscr, bounces=bounces)

    assert res1.final_verdict == res2.final_verdict == res3.final_verdict == Verdict.APPROVE
    assert res1.policy_reasons == res2.policy_reasons == res3.policy_reasons
    assert five_cs["CHARACTER"] == Decimal("90.0")


def test_decimal_infinity_json_serialization_safety():
    """
    Ensure Decimal('Infinity') is safely handled for JSON serialization without crashing.
    """
    assert _safe_serialize_val(Decimal("Infinity")) == "Infinity"
    assert _safe_serialize_val(Decimal("-Infinity")) == "-Infinity"
    assert _safe_serialize_val(Decimal("123.45")) == "123.45"
    assert _safe_serialize_val(None) is None

    # Construct ScoreResult with infinite dscr and test to_dict
    res = ScoreResult(
        five_cs={"CAPACITY": Decimal("100.0")},
        composite_score=Decimal("85.0"),
        baseline_verdict=Verdict.APPROVE,
        final_verdict=Verdict.APPROVE,
        dscr=Decimal("Infinity")
    )
    d = res.to_dict()
    assert d["dscr"] == "Infinity"
    
    # Must be valid JSON string
    json_str = json.dumps(d)
    assert "Infinity" in json_str
    parsed = json.loads(json_str)
    assert parsed["dscr"] == "Infinity"


def test_value_none_strictly_separated_from_numeric_zero(prime_credit_case):
    """
    Verify 'value is None' is strictly separated from numeric zero.
    """
    # 1. Zero values are valid and not flagged as missing
    prime_credit_case.bank_data.bounce_count = 0
    prime_credit_case.itr_data.existing_emi_obligations = Decimal("0.00")
    missing = check_missing_financial_fields(prime_credit_case)
    assert len(missing) == 0

    # 2. None is flagged as missing
    prime_credit_case.itr_data.existing_emi_obligations = None
    missing_none = check_missing_financial_fields(prime_credit_case)
    assert "itr_data.existing_emi_obligations" in missing_none

    # 3. safe_divide distinctions
    assert safe_divide(Decimal("0.0"), Decimal("100.0")) == Decimal("0.0000")
    assert safe_divide(None, Decimal("100.0")) is None
    assert safe_divide(Decimal("100.0"), None) is None
    assert safe_divide(Decimal("100.0"), Decimal("0.0"), zero_denominator_sentinel=None) is None
