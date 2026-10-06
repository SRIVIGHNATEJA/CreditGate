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
    merge_existing_reviews,
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
        company_name="Apex Synthetic Holdings (Demo)",
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
        title="NCLT Section 9 Petition Admitted against Apex Synthetic Holdings (Demo)",
        snippet="Insolvency petition admitted by bench.",
        source_url="https://nclt.gov.in/orders/case101",
        review_status=ReviewStatus.UNREVIEWED
    )
    report = AdverseMediaReport(
        status="COMPLETED",
        findings=[unreviewed_hit],
        query_used='"Apex Synthetic Holdings (Demo)"',
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
        title="NCLT Section 9 Petition Admitted against Apex Synthetic Holdings (Demo)",
        snippet="Insolvency petition admitted by bench.",
        source_url="https://nclt.gov.in/orders/case101",
        review_status=ReviewStatus.UNREVIEWED
    )
    report = AdverseMediaReport(status="COMPLETED", findings=[unreviewed_hit])

    # Officer confirms finding after reviewing court order with mandatory reference note
    confirmed_report = confirm_adverse_finding(
        report,
        "hit-101",
        justification="NCLT Bengaluru Bench Order CP(IB)/102/BB/2026",
        officer_name="Credit Officer Sharma"
    )
    assert confirmed_report.findings[0].review_status == ReviewStatus.CONFIRMED
    assert confirmed_report.findings[0].reviewed_by == "Credit Officer Sharma"
    assert confirmed_report.findings[0].confirmation_note == "NCLT Bengaluru Bench Order CP(IB)/102/BB/2026"

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

    # Officer confirms inquiry with mandatory reference note
    confirmed_report = confirm_adverse_finding(
        report,
        "hit-202",
        justification="DGGI Summons Reference DGGI/ZU/2026/892",
        officer_name="Risk Officer Iyer"
    )
    assert confirmed_report.findings[0].confirmation_note == "DGGI Summons Reference DGGI/ZU/2026/892"
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
        report = search_adverse_media(company_name="Apex Synthetic Holdings (Demo)", api_key=None, mock_mode=False)
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
        company_name="Apex Synthetic Holdings (Demo)",
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
        company_name="Kaveri Fictitious Freight (Demo)",
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


# ============================================================================
# 5. GOVERNANCE, NEGATIVE REGEX & REVIEW PERSISTENCE TESTS
# ============================================================================

def test_confirm_without_justification_raises_error():
    """
    Governance Specification: Confirming an adverse finding requires a mandatory,
    non-empty justification note (e.g., court order or citation).
    """
    hit = AdverseMediaFinding(
        id="hit-gov-1",
        tier=FindingTier.SEVERE,
        title="NCLT Section 9 Admitted",
        snippet="Order passed.",
        source_url="https://nclt.gov.in/order1",
        review_status=ReviewStatus.UNREVIEWED
    )
    report = AdverseMediaReport(status="COMPLETED", findings=[hit])

    # 1. Empty string raises ValueError
    with pytest.raises(ValueError, match="non-empty justification"):
        confirm_adverse_finding(report, "hit-gov-1", justification="")

    # 2. Whitespace only raises ValueError
    with pytest.raises(ValueError, match="non-empty justification"):
        confirm_adverse_finding(report, "hit-gov-1", justification="   \t\n  ")

    # 3. Non-empty justification succeeds
    confirmed = confirm_adverse_finding(
        report,
        "hit-gov-1",
        justification="NCLT Bengaluru Bench Order CP(IB)/102/BB/2026"
    )
    assert confirmed.findings[0].review_status == ReviewStatus.CONFIRMED
    assert confirmed.findings[0].confirmation_note == "NCLT Bengaluru Bench Order CP(IB)/102/BB/2026"


def test_negative_regex_relief_clauses_never_auto_flag_severe(prime_credit_case):
    """
    Specification: Assert that phrases like 'petition dismissed', 'withdrawn',
    'settled', or 'no proceedings' NEVER classify as SEVERE and when UNREVIEWED,
    never cause auto-rejection or policy gate cap.
    """
    relief_cases = [
        ("NCLT Section 9 petition dismissed with costs against entity", "Tribunal rejected operational creditor plea."),
        ("Insolvency petition withdrawn by financial creditor", "Settlement arrived between parties."),
        ("Operational debt dispute settled out of court; NCLT proceedings dropped", "Parties filed joint memo of settlement."),
        ("No insolvency proceedings pending against applicant firm", "Registrar certificate confirms clean record."),
        ("NCLT application disposed of following mediation", "Proceedings concluded amicably."),
    ]

    for title, snippet in relief_cases:
        tier = classify_finding_tier(title, snippet)
        # Must NEVER be SEVERE
        assert tier != FindingTier.SEVERE, f"Failed for title: '{title}'. Classified as {tier} instead of non-severe."

        # When rendered in report as UNREVIEWED, policy gate must leave verdict untouched
        finding = AdverseMediaFinding(
            id="relief-1",
            tier=tier,
            title=title,
            snippet=snippet,
            source_url="https://nclt.gov.in/relief",
            review_status=ReviewStatus.UNREVIEWED
        )
        report = AdverseMediaReport(status="COMPLETED", findings=[finding])
        sev_flags, rev_flags = extract_policy_flags(report)
        assert len(sev_flags) == 0, f"Unreviewed relief finding produced severe flag: {sev_flags}"

        res = score_credit_case(
            credit_case=prime_credit_case,
            severe_flags=sev_flags,
            review_flags=rev_flags
        )
        assert res.final_verdict == Verdict.APPROVE


def test_merge_existing_reviews_persists_officer_state():
    """
    Specification: Re-scans must not wipe existing officer determinations.
    Existing reviews (CONFIRMED / DISMISSED) keyed by finding ID or URL must be merged.
    """
    f1 = AdverseMediaFinding(
        id="f-101",
        tier=FindingTier.SEVERE,
        title="NCLT Admitted Order",
        snippet="Order",
        source_url="https://nclt.gov.in/order1",
        review_status=ReviewStatus.CONFIRMED,
        confirmation_note="Order Ref 101/2026",
        reviewed_by="Credit Officer A",
        reviewed_at="2026-09-20 10:00:00 UTC"
    )
    f2 = AdverseMediaFinding(
        id="f-102",
        tier=FindingTier.REVIEW,
        title="DGGI Notice",
        snippet="Notice",
        source_url="https://taxscan.in/notice2",
        review_status=ReviewStatus.DISMISSED,
        dismissal_reason="Entity homonym verified through PAN mismatch.",
        reviewed_by="Credit Officer B",
        reviewed_at="2026-09-20 10:05:00 UTC"
    )
    existing_report = AdverseMediaReport(status="COMPLETED", findings=[f1, f2])

    # New incoming scan with fresh unreviewed copies of same findings
    fresh_f1 = f1.model_copy(update={"review_status": ReviewStatus.UNREVIEWED, "confirmation_note": None})
    fresh_f2 = f2.model_copy(update={"review_status": ReviewStatus.UNREVIEWED, "dismissal_reason": None})
    fresh_f3 = AdverseMediaFinding(
        id="f-103",
        tier=FindingTier.INFORMATIONAL,
        title="New Expansion",
        snippet="News",
        source_url="https://news.com/item3",
        review_status=ReviewStatus.UNREVIEWED
    )
    new_report = AdverseMediaReport(status="COMPLETED", findings=[fresh_f1, fresh_f2, fresh_f3])

    merged = merge_existing_reviews(new_report, existing_report)

    # f-101 must retain CONFIRMED and confirmation_note
    merged_f1 = next(f for f in merged.findings if f.id == "f-101")
    assert merged_f1.review_status == ReviewStatus.CONFIRMED
    assert merged_f1.confirmation_note == "Order Ref 101/2026"
    assert merged_f1.reviewed_by == "Credit Officer A"

    # f-102 must retain DISMISSED and dismissal_reason
    merged_f2 = next(f for f in merged.findings if f.id == "f-102")
    assert merged_f2.review_status == ReviewStatus.DISMISSED
    assert "Entity homonym" in merged_f2.dismissal_reason

    # f-103 must remain UNREVIEWED
    merged_f3 = next(f for f in merged.findings if f.id == "f-103")
    assert merged_f3.review_status == ReviewStatus.UNREVIEWED


def test_no_adverse_findings_carries_legal_disclaimer():
    """
    Specification: Clean search result carries mandatory disclaimer that
    'screening is not a legal clearance'.
    """
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": []}
    mock_session.post.return_value = mock_resp

    report = search_adverse_media(
        company_name="Apex Synthetic Holdings (Demo)",
        api_key="test-api-key",
        mock_mode=False,
        http_session=mock_session
    )
    assert report.status == "NO_ADVERSE_FINDINGS"
    assert len(report.findings) == 0
    assert report.disclaimer is not None
    assert "not a legal clearance" in report.disclaimer.lower()
