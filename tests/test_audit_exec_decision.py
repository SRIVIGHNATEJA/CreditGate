import io
import sys
from decimal import Decimal
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.parser import extract_all_documents
from core.scorer import score_credit_case
from core.policy import Verdict
from core.data_models import FindingTier, ReviewStatus, AdverseMediaFinding, AdverseMediaReport
from core.researcher import (
    confirm_adverse_finding,
    dismiss_adverse_finding,
    extract_policy_flags,
    generate_synthetic_demo_findings,
)

APP_PATH = str(BASE_DIR / "app.py")
SYNTHETIC_DIR = BASE_DIR / "data" / "synthetic"


def _get_cohort_case(cohort_dir_name: str, company_name: str, cin: str):
    cdir = SYNTHETIC_DIR / cohort_dir_name
    case, _ = extract_all_documents(
        (cdir / "bank_statement.pdf").read_bytes(),
        (cdir / "gstr3b.pdf").read_bytes(),
        (cdir / "itr_acknowledgement.pdf").read_bytes(),
        company_name=company_name,
        cin_or_pan=cin,
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164993.54"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
    )
    return case


def test_severe_confirm_forces_reject_and_score_not_none():
    """
    Test 1: Confirm a SEVERE finding -> REJECT and score_result is not None.
    Uses Cohort 1 (baseline APPROVE) to prove SEVERE flips verdict to REJECT.
    """
    case = _get_cohort_case("cohort_1_prime", "Apex Synthetic Holdings (Demo)", "U29253DL2018PTC334120")
    
    # Baseline score with no adverse findings
    base_res = score_credit_case(case, business_vintage_years=6, collateral_value=Decimal("0"), cgtmse_covered=True)
    assert base_res.final_verdict == Verdict.APPROVE
    
    # Create a severe finding
    sev_finding = AdverseMediaFinding(
        id="sev_01",
        tier=FindingTier.SEVERE,
        title="[SYNTHETIC DEMO DATA] Admitted Section 9 Insolvency Petition",
        snippet="NCLT admitted insolvency petition under Section 9 of IBC.",
        source_url="https://example.com/order.pdf",
        review_status=ReviewStatus.UNREVIEWED,
        is_synthetic=True,
    )
    rep = AdverseMediaReport(
        status="COMPLETED",
        company_name=case.company_name,
        query_used="test query",
        findings=[sev_finding],
        is_synthetic_demo=True,
    )
    
    # Unreviewed -> zero effect
    sev_flags, rev_flags = extract_policy_flags(rep)
    assert len(sev_flags) == 0
    res_unreviewed = score_credit_case(case, business_vintage_years=6, collateral_value=Decimal("0"), cgtmse_covered=True, severe_flags=sev_flags, review_flags=rev_flags)
    assert res_unreviewed.final_verdict == Verdict.APPROVE
    
    # Confirm finding
    confirmed_rep = confirm_adverse_finding(rep, "sev_01", "Verified via NCLT Order CP/102/2026", officer_name="Credit Officer")
    sev_flags, rev_flags = extract_policy_flags(confirmed_rep)
    assert len(sev_flags) == 1
    assert "CONFIRMED_INSOLVENCY" in sev_flags[0]
    
    res_confirmed = score_credit_case(case, business_vintage_years=6, collateral_value=Decimal("0"), cgtmse_covered=True, severe_flags=sev_flags, review_flags=rev_flags)
    assert res_confirmed is not None
    assert res_confirmed.final_verdict == Verdict.REJECT
    assert res_confirmed.knockout_applied is not None
    assert "RESEARCH_SEVERE_FLAG" in res_confirmed.knockout_applied


def test_review_tier_confirm_caps_at_conditional():
    """
    Test 2: Confirm a statutory (REVIEW-tier) finding -> capped at CONDITIONAL.
    Uses Cohort 1 (baseline APPROVE) to prove REVIEW finding caps to CONDITIONAL.
    """
    case = _get_cohort_case("cohort_1_prime", "Apex Synthetic Holdings (Demo)", "U29253DL2018PTC334120")
    
    rev_finding = AdverseMediaFinding(
        id="rev_01",
        tier=FindingTier.REVIEW,
        title="[SYNTHETIC DEMO DATA] DGGI Summons for Tax Investigation",
        snippet="DGGI issued summons regarding ₹1.45 Cr ITC discrepancy.",
        source_url="https://example.com/dggi.pdf",
        review_status=ReviewStatus.UNREVIEWED,
        is_synthetic=True,
    )
    rep = AdverseMediaReport(
        status="COMPLETED",
        company_name=case.company_name,
        query_used="test query",
        findings=[rev_finding],
        is_synthetic_demo=True,
    )
    
    # Confirm review finding
    confirmed_rep = confirm_adverse_finding(rep, "rev_01", "Summons verified with tax counsel", officer_name="Credit Officer")
    sev_flags, rev_flags = extract_policy_flags(confirmed_rep)
    assert len(sev_flags) == 0
    assert len(rev_flags) == 1
    assert "CONFIRMED_STATUTORY_INQUIRY" in rev_flags[0]
    
    res_confirmed = score_credit_case(case, business_vintage_years=6, collateral_value=Decimal("0"), cgtmse_covered=True, severe_flags=sev_flags, review_flags=rev_flags)
    assert res_confirmed is not None
    assert res_confirmed.final_verdict == Verdict.CONDITIONAL
    assert res_confirmed.cap_applied is not None
    assert "STATUTORY_INQUIRY_CAP" in res_confirmed.cap_applied


def test_dismiss_leaves_baseline_unchanged():
    """
    Test 3: Dismiss a finding -> baseline unchanged.
    """
    case = _get_cohort_case("cohort_1_prime", "Apex Synthetic Holdings (Demo)", "U29253DL2018PTC334120")
    base_res = score_credit_case(case, business_vintage_years=6, collateral_value=Decimal("0"), cgtmse_covered=True)
    assert base_res.final_verdict == Verdict.APPROVE
    
    # Create severe finding and then dismiss it
    sev_finding = AdverseMediaFinding(
        id="sev_01",
        tier=FindingTier.SEVERE,
        title="[SYNTHETIC DEMO DATA] NCLT Petition against different entity",
        snippet="Homonym entity collision in registry.",
        source_url="https://example.com/order.pdf",
        review_status=ReviewStatus.UNREVIEWED,
        is_synthetic=True,
    )
    rep = AdverseMediaReport(
        status="COMPLETED",
        company_name=case.company_name,
        query_used="test query",
        findings=[sev_finding],
        is_synthetic_demo=True,
    )
    
    dismissed_rep = dismiss_adverse_finding(rep, "sev_01", "Homonym entity mismatch; CIN does not match applicant", officer_name="Credit Officer")
    sev_flags, rev_flags = extract_policy_flags(dismissed_rep)
    assert len(sev_flags) == 0
    assert len(rev_flags) == 0
    
    res_dismissed = score_credit_case(case, business_vintage_years=6, collateral_value=Decimal("0"), cgtmse_covered=True, severe_flags=sev_flags, review_flags=rev_flags)
    assert res_dismissed.final_verdict == base_res.final_verdict
    assert res_dismissed.composite_score == base_res.composite_score
    assert res_dismissed.knockout_applied == base_res.knockout_applied
    assert res_dismissed.cap_applied == base_res.cap_applied


def test_forward_buttons_navigation_pipeline():
    """
    Test 4: Forward buttons between pages (Case -> Evidence -> Decision -> CAM & Audit)
    work properly via st.switch_page and router.
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)

    # 1. On Case page: Click forward button to Evidence
    fwd_ev = [b for b in at.button if "Proceed to Evidence" in b.label]
    assert len(fwd_ev) == 1
    fwd_ev[0].click().run(timeout=30)
    assert any("Document Ingestion" in m.value for m in at.markdown)

    # 2. On Evidence page: Forward button to Decision should NOT be visible until financials are locked
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    assert len([b for b in at.button if "Proceed to Decision" in b.label]) == 0

    # Extract & Lock financials
    ext_btn = next((b for b in at.button if "Extract" in b.label), None)
    assert ext_btn is not None
    ext_btn.click().run(timeout=30)

    lock_btn = next((b for b in at.button if "Confirm & Lock" in b.label), None)
    assert lock_btn is not None
    lock_btn.click().run(timeout=30)

    # Now forward button to Decision MUST be visible and functional
    fwd_dec = [b for b in at.button if "Proceed to Decision" in b.label]
    assert len(fwd_dec) == 1
    fwd_dec[0].click().run(timeout=30)
    assert any("Run Deterministic Risk Engine" in b.label for b in at.button)

    # 3. On Decision page: Forward button to CAM & Audit should NOT be visible until scored
    at.switch_page("app_pages/decision.py").run(timeout=30)
    assert len([b for b in at.button if "Proceed to CAM" in b.label]) == 0

    # Run deterministic scoring
    score_btn = next((b for b in at.button if "Run Deterministic" in b.label), None)
    assert score_btn is not None
    score_btn.click().run(timeout=30)

    assert at.session_state.score_result is not None

    # Now forward button to CAM & Audit MUST be visible and functional
    fwd_cam = [b for b in at.button if "Proceed to CAM" in b.label]
    assert len(fwd_cam) == 1
    fwd_cam[0].click().run(timeout=30)

    # Verify arrived on CAM & Audit page
    assert any("Credit Appraisal Memorandum" in t.value or "Audit Trail" in t.value for t in at.markdown)


def test_apptest_decision_adverse_media_rescores_in_place():
    """
    Test 5: Decision page adverse media inline confirm/dismiss:
    - Rescores with updated flags before rerun
    - score_result is NEVER None
    - Hero card, KPI cards stay on screen and update in place
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)
    # Select Cohort 2
    next(b for b in at.button if b.key == "btn_open_distressed").click().run(timeout=30)
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    next((b for b in at.button if "Extract" in b.label)).click().run(timeout=30)
    next((b for b in at.button if "Confirm & Lock" in b.label)).click().run(timeout=30)
    at.switch_page("app_pages/decision.py").run(timeout=30)
    next((b for b in at.button if "Run Deterministic" in b.label)).click().run(timeout=30)

    assert at.session_state.score_result is not None

    # Run adverse media scan
    scan_btn = next((b for b in at.button if "Execute Adverse Media" in b.label), None)
    assert scan_btn is not None
    scan_btn.click().run(timeout=30)

    # After scan, score_result MUST NOT be None
    assert at.session_state.score_result is not None
    assert at.session_state.current_research_report is not None
    findings = at.session_state.current_research_report.findings
    assert len(findings) >= 1
    # Find severe finding input note and confirm button
    sev_f = next(f for f in findings if f.tier == FindingTier.SEVERE)
    note_inputs = [t for t in at.text_input if f"inline_note_{sev_f.id}" in t.key]
    assert len(note_inputs) == 1
    note_inputs[0].input("NCLT Order CP(IB)/102/2026 confirmed").run(timeout=30)

    conf_btn = next((b for b in at.button if f"btn_inline_conf_{sev_f.id}" in b.key), None)
    assert conf_btn is not None
    conf_btn.click().run(timeout=30)

    # score_result is not None and reflects confirmed finding
    assert at.session_state.score_result is not None
    assert at.session_state.score_result.final_verdict == Verdict.REJECT
    assert at.session_state.score_result.knockout_applied is not None


def test_decision_programmatic_tab_navigation():
    """
    Test 6: Verify bidirectional programmatic tab navigation buttons across all 3 decision tabs:
    - Tab 1 -> btn_adv_tab1 advances to Tab 2
    - Tab 2 -> btn_adv_tab2 advances to Tab 3
    - Tab 3 -> btn_back_tab3 reviews Tab 2
    - Tab 2 -> btn_back_tab2 reviews Tab 1
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)
    next(b for b in at.button if b.key == "btn_open_prime").click().run(timeout=30)
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    next((b for b in at.button if "Extract" in b.label)).click().run(timeout=30)
    next((b for b in at.button if "Confirm & Lock" in b.label)).click().run(timeout=30)
    at.switch_page("app_pages/decision.py").run(timeout=30)
    next((b for b in at.button if "Run Deterministic" in b.label)).click().run(timeout=30)

    assert at.session_state.score_result is not None
    assert at.session_state.decision_active_tab == "1. Quantitative Scorecard"

    # Tab 1 -> Advance to Tab 2
    adv_tab1 = next((b for b in at.button if b.key == "btn_adv_tab1"), None)
    assert adv_tab1 is not None
    adv_tab1.click().run(timeout=30)
    assert at.session_state.decision_active_tab == "2. Forensic Legal Radar"

    # Tab 2 -> Advance to Tab 3
    adv_tab2 = next((b for b in at.button if b.key == "btn_adv_tab2"), None)
    assert adv_tab2 is not None
    adv_tab2.click().run(timeout=30)
    assert at.session_state.decision_active_tab == "3. Committee Quorum & Sanction"

    # Tab 3 -> Back to Tab 2
    back_tab3 = next((b for b in at.button if b.key == "btn_back_tab3"), None)
    assert back_tab3 is not None
    back_tab3.click().run(timeout=30)
    assert at.session_state.decision_active_tab == "2. Forensic Legal Radar"

    # Tab 2 -> Back to Tab 1
    back_tab2 = next((b for b in at.button if b.key == "btn_back_tab2"), None)
    assert back_tab2 is not None
    back_tab2.click().run(timeout=30)
    assert at.session_state.decision_active_tab == "1. Quantitative Scorecard"


