import sys
from decimal import Decimal
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.data_models import (
    CreditCase,
    BankExtraction,
    GSTExtraction,
    ITRExtraction,
    FindingTier,
    ReviewStatus,
    AdverseMediaFinding,
    AdverseMediaReport,
)
from core.policy import Verdict
from core.scorer import score_credit_case
from core.researcher import (
    confirm_adverse_finding,
    extract_policy_flags,
)
from ui.state import (
    compute_composite_hash,
    select_and_reset_cohort,
    init_session_state,
)

APP_PATH = str(BASE_DIR / "app.py")


def test_facility_edit_keeps_lock():
    """
    Stage A Requirement: Facility-term edits (loan, tenure, rate, collateral, CGTMSE)
    do NOT clear locked_case; they mark the score stale and it rescores through the existing hash.
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)

    # 1. Extract and lock on Evidence page
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    next(b for b in at.button if "Extract" in b.label).click().run(timeout=30)
    next(b for b in at.button if "Confirm & Lock" in b.label).click().run(timeout=30)

    assert at.session_state.hitl_confirmed is True
    assert at.session_state.locked_case is not None
    original_fin_hash = at.session_state.locked_financials_hash
    assert original_fin_hash is not None

    # 2. Score on Decision page
    at.switch_page("app_pages/decision.py").run(timeout=30)
    next(b for b in at.button if "Run Deterministic" in b.label).click().run(timeout=30)
    assert at.session_state.score_result is not None
    orig_comp_hash = at.session_state.scored_composite_hash

    # 3. Edit facility terms in Case form per B1 and instruction 3(b)
    at.switch_page("app_pages/case.py").run(timeout=30)
    at.session_state.facility_loan_amount = 7500000.0
    at.session_state.facility_tenure_months = 48
    at.session_state.loan_amount = 7500000.0
    at.session_state.tenure_months = 48
    at.run(timeout=30)
    at.switch_page("app_pages/decision.py").run(timeout=30)

    # Rule check: locked_case is NOT cleared
    assert at.session_state.locked_case is not None
    assert at.session_state.hitl_confirmed is True
    assert at.session_state.locked_financials_hash == original_fin_hash

    # Score is marked stale because composite hash changed
    curr_comp_hash = compute_composite_hash(
        locked_financials_hash=at.session_state.locked_financials_hash,
        loan_amount=float(at.session_state.facility_loan_amount),
        tenure_months=int(at.session_state.facility_tenure_months),
        interest_rate=float(at.session_state.facility_interest_rate),
        proposed_emi=float(at.session_state.app_context["proposed_emi"]),
        collateral_val=float(at.session_state.facility_collateral_val),
        cgtmse_covered=bool(at.session_state.facility_cgtmse_covered),
        cin=at.session_state.app_context["cin_parsed_data"]["cin"],
        vintage=at.session_state.app_context["cin_parsed_data"]["business_vintage_years"],
        pan=at.session_state.app_context["current_pan_val"],
        pan_mismatch=at.session_state.app_context["current_pan_mismatch"],
        research_findings=[],
    )
    assert curr_comp_hash != orig_comp_hash

    # Rescore executes through existing hash and succeeds
    rescore_btn = next((b for b in at.button if "Run Deterministic" in b.label), None)
    assert rescore_btn is not None
    rescore_btn.click().run(timeout=30)
    assert at.session_state.score_result is not None
    assert at.session_state.scored_composite_hash == curr_comp_hash


def test_cohort_and_pan_change_clears_lock_with_message():
    """
    Stage A Requirement: Cohort, CIN, PAN or entity-type changes DO clear lock,
    score, research and notes, and show a visible message 'Lock cleared because <field> changed.'
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)

    # 1. Lock on Evidence page
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    next(b for b in at.button if "Extract" in b.label).click().run(timeout=30)
    next(b for b in at.button if "Confirm & Lock" in b.label).click().run(timeout=30)
    assert at.session_state.locked_case is not None

    # 2. PAN change clears lock and sets message
    at.switch_page("app_pages/case.py").run(timeout=30)
    pan_input = next(t for t in at.text_input if t.key == "case_pan_input")
    pan_input.input("AAACA9999Z").run(timeout=30)
    assert at.session_state.locked_case is None
    assert at.session_state.hitl_confirmed is False
    assert at.session_state.score_result is None
    assert at.session_state.lock_cleared_message == "Lock cleared because PAN changed."

    # 3. Lock again
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    next(b for b in at.button if "Extract" in b.label).click().run(timeout=30)
    next(b for b in at.button if "Confirm & Lock" in b.label).click().run(timeout=30)
    assert at.session_state.locked_case is not None
    assert at.session_state.lock_cleared_message is None

    # 4. Cohort change clears lock and sets message
    at.switch_page("app_pages/case.py").run(timeout=30)
    next(b for b in at.button if b.key == "btn_open_distressed").click().run(timeout=30)  # Select Cohort 2
    assert at.session_state.locked_case is None
    assert at.session_state.hitl_confirmed is False
    assert at.session_state.score_result is None
    assert at.session_state.lock_cleared_message == "Lock cleared because cohort changed."


def test_blank_field_routes_to_insufficient_data_with_message():
    """
    Stage A Requirement: Blank field -> INSUFFICIENT_DATA with list of missing fields.
    """
    case = CreditCase(
        company_name="Applicant with Missing Debt (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164993.54"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(
            total_credits=Decimal("125000000.00"),
            total_debits=Decimal("118000000.00"),
            opening_balance=Decimal("4520500.00"),
            closing_balance=Decimal("11520500.00"),
            bounce_count=0,
            confidence="HIGH",
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("124500000.00"),
            total_tax_paid=Decimal("22410000.00"),
            confidence="HIGH",
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("124500000.00"),
            profit_after_tax=Decimal("12000000.00"),
            depreciation=Decimal("3000000.00"),
            interest_paid=Decimal("2500000.00"),
            net_worth=Decimal("65000000.00"),
            total_debt=None,  # Missing mandatory total_debt field
            existing_emi_obligations=Decimal("4000000.00"),
            confidence="HIGH",
        ),
    )

    res = score_credit_case(case, business_vintage_years=6, cgtmse_covered=True)
    assert res.final_verdict == Verdict.INSUFFICIENT_DATA
    assert len(res.policy_reasons) >= 1
    assert any("total_debt" in r for r in res.policy_reasons)


def test_veto_then_confirm_severe_clears_override_and_enforces_reject():
    """
    Stage A Requirement: Veto submitted first, THEN a SEVERE finding confirmed:
    - Stored override is cleared
    - Final verdict is REJECT
    - Banner must NOT show 'Overridden to APPROVE'
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)

    # 1. Cohort 2 (Distressed)
    next(b for b in at.button if b.key == "btn_open_distressed").click().run(timeout=30)
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    next(b for b in at.button if "Extract" in b.label).click().run(timeout=30)
    next(b for b in at.button if "Confirm & Lock" in b.label).click().run(timeout=30)

    # 2. Decision page: Run scoring
    at.switch_page("app_pages/decision.py").run(timeout=30)
    next(b for b in at.button if "Run Deterministic" in b.label).click().run(timeout=30)
    assert at.session_state.score_result.final_verdict == Verdict.REJECT

    # 3. Apply Executive Veto FIRST (override to APPROVE)
    veto_tgl = next(t for t in at.toggle if "tgl_veto_active" in t.key)
    veto_tgl.set_value(True).run(timeout=30)
    # Select APPROVE
    veto_sel = next(s for s in at.selectbox if s.key == "sb_veto_verdict")
    veto_sel.select_index(0).run(timeout=30)
    # Provide justification
    just_input = next(t for t in at.text_area if t.key == "txt_veto_just")
    just_input.input("Credit committee exception granted due to imminent equity infusion from promoter.").run(timeout=30)
    next(b for b in at.button if b.key == "btn_submit_veto").click().run(timeout=30)

    # Verify veto is active
    assert at.session_state.executive_override is not None
    assert at.session_state.executive_override["override_active"] is True
    assert at.session_state.executive_override["override_verdict"] == "APPROVE"

    # 4. NOW execute adverse media scan and confirm SEVERE finding
    scan_btn = next(b for b in at.button if "Execute Adverse Media" in b.label)
    scan_btn.click().run(timeout=30)

    findings = at.session_state.current_research_report.findings
    sev_f = next(f for f in findings if f.tier == FindingTier.SEVERE)

    note_input = next(t for t in at.text_input if f"inline_note_{sev_f.id}" in t.key)
    note_input.input("NCLT Section 9 order confirmed with court registry.").run(timeout=30)

    conf_btn = next(b for b in at.button if f"btn_inline_conf_{sev_f.id}" in b.key)
    conf_btn.click().run(timeout=30)

    # 5. VERIFY GOVERNANCE RULE:
    # Stored override MUST be cleared
    assert at.session_state.executive_override is None
    # Final verdict MUST be REJECT
    assert at.session_state.score_result is not None
    assert at.session_state.score_result.final_verdict == Verdict.REJECT
    assert at.session_state.score_result.knockout_applied is not None
    assert "RESEARCH_SEVERE_FLAG" in at.session_state.score_result.knockout_applied
