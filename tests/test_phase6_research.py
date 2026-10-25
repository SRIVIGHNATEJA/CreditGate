import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import json
import pytest
from decimal import Decimal

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
    create_empanelled_advocate_finding,
    extract_legal_docket_text,
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
    Specification 1: Statutory findings must NEVER trigger an automatic Verdict.REJECT.
    An UNREVIEWED severe finding has ZERO effect on the policy gate verdict.
    """
    unreviewed_hit = AdverseMediaFinding(
        id="hit-101",
        tier=FindingTier.SEVERE,
        title="NCLT Section 9 Petition Admitted against Apex Synthetic Holdings (Demo)",
        snippet="Insolvency petition admitted by bench.",
        source_url="docket:NCLT-DEL/CP(IB)/101/2026",
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
        source_url="docket:NCLT-DEL/CP(IB)/101/2026",
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
    Specification 2: Officer-confirmed REVIEW hit (e.g. DGGI Inquiry)
    caps final verdict at Verdict.CONDITIONAL under Credit Policy Rule CP-402.
    """
    review_hit = AdverseMediaFinding(
        id="hit-202",
        tier=FindingTier.REVIEW,
        title="DGGI Zonal Unit issues Summons regarding ITC Mismatch",
        snippet="Summons under Section 70 CGST Act.",
        source_url="ref:DGGI/ZU/4891/2026",
        review_status=ReviewStatus.UNREVIEWED
    )
    report = AdverseMediaReport(status="COMPLETED", findings=[review_hit])

    confirmed_report = confirm_adverse_finding(
        report,
        "hit-202",
        justification="Summons verified against DGGI portal F.No. 4891/2026",
        officer_name="Risk Lead Mukherjee"
    )

    sev_flags, rev_flags = extract_policy_flags(confirmed_report)
    assert len(sev_flags) == 0
    assert len(rev_flags) == 1
    assert "CONFIRMED_STATUTORY_INQUIRY" in rev_flags[0]

    res = score_credit_case(
        credit_case=prime_credit_case,
        severe_flags=sev_flags,
        review_flags=rev_flags
    )
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
        source_url="docket:NCLT-KOL/303/2025",
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
# 2. ANTI-CLEAN-CHIT & STATUTORY REGISTRY INVARIANTS
# ============================================================================

def test_anti_clean_chit_unindexed_custom_company():
    """
    Specification: An unindexed corporate entity without public central registry filings
    must return status='PENDING_ADVOCATE_AUDIT'. Under NO circumstances should an unseen
    custom entity receive an automatic green 'Clean Chit' (0 Suits Found / Clear).
    """
    report = search_adverse_media(
        company_name="Shree Ram Precision Engineering Works Pvt Ltd",
        cin="U29100DL2022PTC987654",
        mock_mode=False,
        bounce_count=0
    )
    assert report.status == "PENDING_ADVOCATE_AUDIT"
    assert report.status != "NO_ADVERSE_FINDINGS"
    assert report.status != "COMPLETED"
    assert len(report.findings) == 0
    assert report.disclaimer is not None
    assert "Unindexed corporate entity" in report.disclaimer
    assert "advocate title search report (TSR)" in report.disclaimer


def test_watchlist_high_risk_name_interceptor():
    """
    Specification: Corporate entity names containing distressed or default tokens
    (e.g., 'distressed', 'defaulter', 'fraud', 'recovery') trigger an automated
    FindingTier.REVIEW alert under the Statutory Watchlist Interceptor.
    """
    report = search_adverse_media(
        company_name="Deccan Distressed Assets & Logistics Ltd",
        cin="U60100TG2020PTC123456",
        mock_mode=False
    )
    assert report.status == "COMPLETED"
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.tier == FindingTier.REVIEW
    assert "High-Risk Watchlist Match" in f.title
    assert "DISTRESSED" in f.title
    assert "watchlist:CENTRAL-DEFAULT" in f.source_url
    assert f.review_status == ReviewStatus.UNREVIEWED


def test_financial_stress_bounces_triangulation():
    """
    Specification: Financial-to-Legal Stress Triangulation.
    When an applicant has 5 or more cheque/mandate returns, the statutory engine
    injects a Section 138 Negotiable Instruments Act Inquiry Alert.
    """
    report = search_adverse_media(
        company_name="Midland Commercial Polymers Pvt Ltd",
        cin="U25200GJ2017PTC098765",
        mock_mode=False,
        bounce_count=6
    )
    assert report.status == "COMPLETED"
    assert len(report.findings) >= 1
    sec138_finding = next((f for f in report.findings if "Section 138 NI Act" in f.title), None)
    assert sec138_finding is not None
    assert sec138_finding.tier == FindingTier.REVIEW
    assert "6 bounces" in sec138_finding.snippet


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
    assert classify_finding_tier("Section 138 NI Act summons issued", "Cheque dishonour complaint") == FindingTier.REVIEW
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
        source_url="docket:NCLT/order1",
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
            source_url="docket:NCLT/relief",
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
        source_url="docket:NCLT/order1",
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
        source_url="ref:DGGI/notice2",
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
        source_url="news:item3",
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


def test_create_empanelled_advocate_finding():
    """
    Specification: Empanelled advocate reports generate valid AdverseMediaFinding
    with official court docket structure and non-synthetic operational designation.
    """
    f = create_empanelled_advocate_finding(
        company_name="Custom MSME Steel Works",
        forum="City Civil Court Bengaluru",
        case_no="OS/412/2025",
        section_law="Commercial Recovery Suit",
        claim_amount="45,00,000",
        tier=FindingTier.REVIEW,
        summary="Vendor recovery dispute over delayed consignment payments.",
    )
    assert f.id is not None
    assert f.tier == FindingTier.REVIEW
    assert "[EMPANELLED ADVOCATE REPORT]" in f.title
    assert "City Civil Court Bengaluru" in f.title
    assert "OS/412/2025" in f.title
    assert "₹ 45,00,000" in f.snippet
    assert f.review_status == ReviewStatus.UNREVIEWED
    assert f.is_synthetic is False


def test_statutory_docket_transcripts_archive():
    """
    Specification: extract_legal_docket_text retrieves certified transcripts
    for benchmark statutory and court orders without external API reliance.
    """
    t_kaveri = extract_legal_docket_text("docket:NCLT-BLR/CP(IB)/102/BB/2026/kaveri_sec9")
    assert t_kaveri is not None
    assert "NATIONAL COMPANY LAW TRIBUNAL" in t_kaveri
    assert "Section 9 of the Insolvency and Bankruptcy Code" in t_kaveri
    assert "ADMITTED" in t_kaveri

    t_drt = extract_legal_docket_text("docket:DRT-II-BLR/OA/219/2025/drt_oa219")
    assert t_drt is not None
    assert "DEBT RECOVERY TRIBUNAL" in t_drt
    assert "SARFAESI" in t_drt

    t_ni = extract_legal_docket_text("docket:ECOURTS-DEL/CC/412/2025/cc_412")
    assert t_ni is not None
    assert "Negotiable Instruments Act" in t_ni
    assert "Section 138" in t_ni

    t_clean = extract_legal_docket_text("roc:MCA21-DEL/FORM-CHG-4/SRN-F62910482/apex_expansion")
    assert t_clean is not None
    assert "MINISTRY OF CORPORATE AFFAIRS" in t_clean


def test_cohorts_deterministic_statutory_screening():
    """
    Specification: Identity-Aware Statutory Screening returns expected statutory dockets:
    - Apex Prime: COMPLETED with INFORMATIONAL Form CHG-4 satisfaction
    - Kaveri Distressed: COMPLETED with SEVERE Section 9 CIRP (even under mock_mode=False)
    - Bharat Steels: COMPLETED with REVIEW Section 138 NI Act
    """
    # Apex Prime (Returns Clean Informational MCA-21 Satisfaction Record)
    rep_apex = search_adverse_media(company_name="Apex Synthetic Holdings (Demo)", cin="U29253DL2018PTC334120", mock_mode=False)
    assert rep_apex.status == "COMPLETED"
    assert len(rep_apex.findings) == 1
    assert rep_apex.findings[0].tier == FindingTier.INFORMATIONAL
    assert "Satisfaction of Charge" in rep_apex.findings[0].title

    # Kaveri Freight (Identity-aware routing matches even under mock_mode=False)
    rep_kaveri = search_adverse_media(company_name="Kaveri Fictitious Freight (Demo)", cin="U60200KA2019PTC120481", mock_mode=False)
    assert rep_kaveri.status == "COMPLETED"
    assert len(rep_kaveri.findings) == 3
    assert any(f.tier == FindingTier.SEVERE for f in rep_kaveri.findings)

    # Bharat Steels
    rep_bharat = search_adverse_media(company_name="Bharat Mock Steels & Infra (Demo)", cin="U27100MH2019PTC324150", mock_mode=False)
    assert rep_bharat.status == "COMPLETED"
    assert len(rep_bharat.findings) == 3
    assert any(f.tier == FindingTier.REVIEW for f in rep_bharat.findings)


def test_empanelled_advocate_finding_scoring_knockout(prime_credit_case):
    """
    Specification: An empanelled advocate finding with SEVERE tier, when confirmed,
    triggers policy knockout and flips verdict to REJECT.
    """
    f = create_empanelled_advocate_finding(
        company_name=prime_credit_case.company_name,
        forum="NCLT Principal Bench New Delhi",
        case_no="CP(IB)/999/2026",
        section_law="Section 7 Financial Creditor CIRP Admitted",
        claim_amount="12,00,00,000",
        tier=FindingTier.SEVERE,
        summary="Bank of Baroda petition admitted. Moratorium declared under Section 14.",
    )
    rep = AdverseMediaReport(
        status="COMPLETED",
        findings=[f],
        query_used="test query",
        is_synthetic_demo=True,
    )
    # Confirm finding
    confirmed_rep = confirm_adverse_finding(rep, f.id, "Verified by empanelled counsel via court search", officer_name="Chief Credit Officer")
    sev_flags, rev_flags = extract_policy_flags(confirmed_rep)
    assert len(sev_flags) == 1

    res = score_credit_case(
        prime_credit_case,
        business_vintage_years=6,
        collateral_value=Decimal("0"),
        cgtmse_covered=True,
        severe_flags=sev_flags,
        review_flags=rev_flags,
    )
    assert res.final_verdict == Verdict.REJECT
    assert res.knockout_applied is not None
