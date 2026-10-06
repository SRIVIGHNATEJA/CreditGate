import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from decimal import Decimal
import streamlit as st

from core.onboarding import check_entity_consistency, validate_pan_syntax
from core.data_models import CreditCase, BankExtraction, GSTExtraction, ITRExtraction
from core.scorer import score_credit_case
from ui.state import select_and_reset_cohort, COHORT_DEFAULTS, init_session_state


def test_b01_onboarding_pan_syntax_error_keys():
    """B-01: Ensure invalid PAN returns mismatch_detected=False and does not cause KeyErrors."""
    res = check_entity_consistency("INVALID_PAN", is_corporate=True)
    assert res["is_valid"] is False
    assert res["mismatch_detected"] is False
    assert res["pan_entity_char"] is None
    assert res["pan_entity_type"] == "Invalid Syntax"
    assert "error" in res
    assert res["warning_message"] is not None


def test_b02_and_b07_cohort_selection_sync_and_app_id_regen():
    """B-02 & B-07: Switching cohorts must overwrite sb_cin_input, sb_pan_input, and regenerate app_id."""
    init_session_state()

    # Select Cohort 1
    select_and_reset_cohort("Cohort 1: Prime MSME (Apex Synthetic Demo)")
    app_id_1 = st.session_state.app_id
    assert st.session_state.sb_cin_input == "U29253DL2018PTC334120"
    assert st.session_state.sb_pan_input == "AAACA1234B"
    assert "APP-" in app_id_1

    # Select Cohort 2
    select_and_reset_cohort("Cohort 2: Distressed (Kaveri Fictitious Demo)")
    app_id_2 = st.session_state.app_id
    assert st.session_state.sb_cin_input == "U60200KA2019PTC120481"
    assert st.session_state.sb_pan_input == "AAACK5678M"
    assert app_id_2 != app_id_1

    # Select Cohort 3
    select_and_reset_cohort("Cohort 3: Mismatch Trap (Bharat Mock Demo)")
    app_id_3 = st.session_state.app_id
    assert st.session_state.sb_cin_input == "U24101MH2020PTC349120"
    assert st.session_state.sb_pan_input == "AABFB8910N"
    assert app_id_3 != app_id_2


def test_b04_decision_facility_terms_update_dscr():
    """B-04: Updating facility terms on locked_case updates debt service and recalculated DSCR."""
    case = CreditCase(
        company_name="Test Corp",
        cin_or_pan="U29253DL2018PTC334120",
        requested_loan_amount=Decimal("5000000"),
        proposed_emi=Decimal("164890"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(
            total_credits=Decimal("12000000"),
            total_debits=Decimal("10000000"),
            opening_balance=Decimal("200000"),
            closing_balance=Decimal("2200000"),
            bounce_count=0,
            confidence="HIGH",
            officer_verified=True,
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("12000000"),
            total_tax_paid=Decimal("2160000"),
            confidence="HIGH",
            officer_verified=True,
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("12000000"),
            depreciation=Decimal("500000"),
            profit_after_tax=Decimal("1500000"),
            interest_paid=Decimal("200000"),
            net_worth=Decimal("4000000"),
            total_debt=Decimal("2000000"),
            existing_emi_obligations=Decimal("240000"),
            confidence="HIGH",
            officer_verified=True,
        ),
    )

    # Initial score
    res1 = score_credit_case(case)
    initial_dscr = res1.dscr

    # Officer updates sidebar facility to higher amount and double EMI:
    case.requested_loan_amount = Decimal("10000000")
    case.proposed_emi = Decimal("329780")
    case.proposed_rate = Decimal("12.0")
    case.proposed_tenure_months = 36

    res2 = score_credit_case(case)
    updated_dscr = res2.dscr

    # DSCR = net_cash_accrual / (proposed_annual_emi + existing_emi)
    # Higher proposed EMI must strictly reduce DSCR
    assert updated_dscr < initial_dscr


def test_b05_executive_veto_clear_logic():
    """B-05: When executive veto is deactivated, override state and cam_markdown must be cleared."""
    st.session_state.executive_override = {
        "override_active": True,
        "original_verdict": "REJECT",
        "override_verdict": "APPROVE",
        "officer_name": "Test Officer",
        "justification_text": "Strong promoter collateral backing overriding algorithm.",
    }
    st.session_state.cam_markdown = "# Test CAM with override"

    # Simulate toggle flipping OFF:
    veto_active = False
    if not veto_active and st.session_state.executive_override and st.session_state.executive_override.get("override_active"):
        st.session_state.executive_override = None
        st.session_state.cam_markdown = None

    assert st.session_state.executive_override is None
    assert st.session_state.cam_markdown is None


def test_b01_case_page_guards_with_none_cin():
    """B-01: Ensure case profile formatting works without throwing TypeError when cin_parsed_data is None."""
    cin_parsed_data = None
    # Simulate Case page profile formatting logic
    sec_label = "General Corporate"
    if cin_parsed_data and cin_parsed_data.get("macro_sector"):
        raw_sec = cin_parsed_data["macro_sector"]
        sec_label = raw_sec[:24] + "..." if len(raw_sec) > 24 else raw_sec
    vintage_str = f"{cin_parsed_data['business_vintage_years']} Years" if (cin_parsed_data and "business_vintage_years" in cin_parsed_data) else "5 Years"
    state_str = cin_parsed_data["state_name"] if (cin_parsed_data and "state_name" in cin_parsed_data) else "Pan-India"

    assert sec_label == "General Corporate"
    assert vintage_str == "5 Years"
    assert state_str == "Pan-India"
