import streamlit as st
from decimal import Decimal
from ui.state import get_app_context, record_event, select_and_reset_cohort
from ui.components import render_kpi_card, render_callout, status_pill
from core.formatting import format_inr
from core.onboarding import validate_cin_syntax, parse_cin, check_entity_consistency, NIC_SECTOR_TAXONOMY

st.session_state.current_page = "Case"
ctx = get_app_context()

is_corporate = ctx["is_corporate"]
cin_parsed_data = ctx["cin_parsed_data"]
pan_check = ctx["pan_check"]
pan_input = ctx["pan_input"]
loan_amount = ctx["loan_amount"]
tenure_months = ctx["tenure_months"]
interest_rate = ctx["interest_rate"]
proposed_emi = ctx["proposed_emi"]
cohort_selection = ctx["cohort_selection"]

st.markdown("### Case Gallery & Applicant Profile")
st.caption("Select a pre-configured synthetic underwriting scenario or review active borrower onboarding parameters.")

if st.session_state.get("lock_cleared_message"):
    render_callout(st.session_state.lock_cleared_message, title="Lock Status Alert", variant="warning")

# ----------------------------------------------------------------------------
# 1. CASE GALLERY (LANDING VIEW: 3 CARDS + COHORT SELECTOR)
# ----------------------------------------------------------------------------
is_c1 = (cohort_selection == "Cohort 1: Prime MSME (Apex Synthetic Demo)")
is_c2 = (cohort_selection == "Cohort 2: Distressed (Kaveri Fictitious Demo)")
is_c3 = (cohort_selection == "Cohort 3: Mismatch Trap (Bharat Mock Demo)")

g_col1, g_col2, g_col3 = st.columns(3)

with g_col1:
    with st.container(border=True):
        c1_chip = f" &nbsp; {status_pill('ACTIVE', variant='info')}" if is_c1 else ""
        st.markdown(f"**1. Prime MSME**{c1_chip}", unsafe_allow_html=True)
        st.markdown(f"**Apex Synthetic Holdings (Demo)** &nbsp; {status_pill('APPROVE', variant='approve')}", unsafe_allow_html=True)
        st.caption("Deciding Signal: Clean banking, 0 mandate bounces, DSCR 1.62x, balanced ledger.")
        st.button(
            "Selected (Active)" if is_c1 else "Open Case",
            key="btn_open_prime",
            on_click=select_and_reset_cohort,
            args=("Cohort 1: Prime MSME (Apex Synthetic Demo)",),
            type="primary" if is_c1 else "secondary",
            width="stretch",
        )

with g_col2:
    with st.container(border=True):
        c2_chip = f" &nbsp; {status_pill('ACTIVE', variant='info')}" if is_c2 else ""
        st.markdown(f"**2. Distressed**{c2_chip}", unsafe_allow_html=True)
        st.markdown(f"**Kaveri Fictitious Freight (Demo)** &nbsp; {status_pill('REJECT', variant='reject')}", unsafe_allow_html=True)
        st.caption("Deciding Signal: 5 mandate bounces enforce hard policy knockout (BOUNCE_KNOCKOUT).")
        st.button(
            "Selected (Active)" if is_c2 else "Open Case",
            key="btn_open_distressed",
            on_click=select_and_reset_cohort,
            args=("Cohort 2: Distressed (Kaveri Fictitious Demo)",),
            type="primary" if is_c2 else "secondary",
            width="stretch",
        )

with g_col3:
    with st.container(border=True):
        c3_chip = f" &nbsp; {status_pill('ACTIVE', variant='info')}" if is_c3 else ""
        st.markdown(f"**3. Mismatch Trap**{c3_chip}", unsafe_allow_html=True)
        st.markdown(f"**Bharat Mock Steels & Infra (Demo)** &nbsp; {status_pill('CONDITIONAL', variant='conditional')}", unsafe_allow_html=True)
        st.caption("Deciding Signal: ₹14.5 Cr turnover vs thin ₹18L PAT triggers WEAKEST_LINK_CAP (< 40.0).")
        st.button(
            "Selected (Active)" if is_c3 else "Open Case",
            key="btn_open_mismatch",
            on_click=select_and_reset_cohort,
            args=("Cohort 3: Mismatch Trap (Bharat Mock Demo)",),
            type="primary" if is_c3 else "secondary",
            width="stretch",
        )

cohort_options = [
    "None (Manual Upload)",
    "Cohort 1: Prime MSME (Apex Synthetic Demo)",
    "Cohort 2: Distressed (Kaveri Fictitious Demo)",
    "Cohort 3: Mismatch Trap (Bharat Mock Demo)",
]
active_cohort = st.session_state.get("active_cohort_selection", cohort_options[1])
active_idx = cohort_options.index(active_cohort) if active_cohort in cohort_options else 1
selected_cohort = st.selectbox("Quick-Load Test Cohort", cohort_options, index=active_idx, key="case_cohort_select")
if selected_cohort != active_cohort:
    select_and_reset_cohort(selected_cohort)
    st.rerun()

st.markdown("---")

# ----------------------------------------------------------------------------
# 2. BORROWER PROFILE & FACILITY TERMS (SIDE-BY-SIDE per B1)
# ----------------------------------------------------------------------------
col_borrower, col_facility = st.columns([1, 1], gap="medium")

# LEFT COLUMN: BORROWER PROFILE
with col_borrower:
    with st.container(border=True):
        st.markdown("#### :material/domain: Borrower Profile")
        is_corp = st.toggle("Registered Corporate? (MCA CIN)", value=bool(is_corporate), key="case_is_corporate")

        if is_corp != is_corporate:
            st.session_state.active_is_corporate = is_corp
            st.session_state.extracted_case = None
            st.session_state.extraction_meta = None
            st.session_state.hitl_confirmed = False
            st.session_state.editable_df = None
            st.session_state.original_df = None
            st.session_state.locked_case = None
            st.session_state.locked_financials_hash = None
            st.session_state.scored_financials_hash = None
            st.session_state.scored_composite_hash = None
            st.session_state.score_result = None
            st.session_state.officer_notes = []
            st.session_state.executive_override = None
            st.session_state.cam_markdown = None
            st.session_state.current_research_report = None
            st.session_state.last_research_cache_key = None
            st.session_state.lock_cleared_message = "Lock cleared because entity type changed."
            st.rerun()

        if is_corp:
            cin_val = st.text_input("Corporate ID Number (CIN)", value=ctx["cin_input"], key="case_cin_input")
            if cin_val != ctx["cin_input"]:
                st.session_state.active_cin_selection = cin_val
                st.session_state.extracted_case = None
                st.session_state.extraction_meta = None
                st.session_state.hitl_confirmed = False
                st.session_state.editable_df = None
                st.session_state.original_df = None
                st.session_state.locked_case = None
                st.session_state.locked_financials_hash = None
                st.session_state.scored_financials_hash = None
                st.session_state.scored_composite_hash = None
                st.session_state.score_result = None
                st.session_state.officer_notes = []
                st.session_state.executive_override = None
                st.session_state.cam_markdown = None
                st.session_state.current_research_report = None
                st.session_state.last_research_cache_key = None
                st.session_state.lock_cleared_message = "Lock cleared because CIN changed."
                st.rerun()

            is_cin_valid, cin_err = validate_cin_syntax(cin_val)
            if not is_cin_valid:
                render_callout(cin_err, title="Invalid CIN Format", variant="error")
            else:
                render_callout(
                    f"MCA Valid: {cin_parsed_data['state_name']} | Vintage: {cin_parsed_data['business_vintage_years']} Years",
                    title="MCA Validated",
                    variant="success",
                )
                if cin_parsed_data.get("low_vintage_risk_flag"):
                    render_callout("Business vintage < 3 years indicates elevated early-stage default risk.", title="Vintage Warning", variant="warning")
        else:
            render_callout("Unregistered MSME / Proprietorship mode active.", title="Entity Mode", variant="info")
            st.selectbox("Industry Macro Sector", list(NIC_SECTOR_TAXONOMY.values()), index=0, key="case_manual_sector")
            st.number_input("Business Vintage (Years)", min_value=1, max_value=60, value=7, key="case_manual_vintage")

        pan_val = st.text_input("Permanent Account Number (PAN)", value=ctx["pan_input"], key="case_pan_input")
        if pan_val.strip().upper() != ctx["pan_input"].strip().upper():
            st.session_state.active_pan_input = pan_val.strip().upper()
            st.session_state.extracted_case = None
            st.session_state.extraction_meta = None
            st.session_state.hitl_confirmed = False
            st.session_state.editable_df = None
            st.session_state.original_df = None
            st.session_state.locked_case = None
            st.session_state.locked_financials_hash = None
            st.session_state.scored_financials_hash = None
            st.session_state.scored_composite_hash = None
            st.session_state.score_result = None
            st.session_state.officer_notes = []
            st.session_state.executive_override = None
            st.session_state.cam_markdown = None
            st.session_state.current_research_report = None
            st.session_state.last_research_cache_key = None
            st.session_state.lock_cleared_message = "Lock cleared because PAN changed."
            st.rerun()

        if not pan_check.get("is_valid", False):
            render_callout(pan_check.get("error", "Invalid PAN Format"), title="Invalid PAN Format", variant="error")
        elif pan_check.get("mismatch_detected", False):
            render_callout(pan_check.get("warning_message", ""), title="Consistency Alert", variant="error")
        else:
            render_callout(f"PAN 4th character '{pan_check.get('pan_entity_char')}' aligns with {pan_check.get('pan_entity_type')}.", title="Entity Verified", variant="success")

        bcol1, bcol2 = st.columns(2)
        with bcol1:
            render_kpi_card("Entity Status", "Active Corporate" if is_corporate else "Proprietorship", subtext="MCA Status")
            v_years = cin_parsed_data["business_vintage_years"] if (cin_parsed_data and "business_vintage_years" in cin_parsed_data) else 5
            render_kpi_card("Operating Vintage", f"{v_years} Years", subtext="Incorporation")
        with bcol2:
            raw_sec = cin_parsed_data["macro_sector"] if (cin_parsed_data and "macro_sector" in cin_parsed_data) else "General Corporate"
            sec_short = raw_sec[:22] + "..." if len(raw_sec) > 22 else raw_sec
            render_kpi_card("Industry Sector", sec_short, subtext="NIC Taxonomy")
            s_name = cin_parsed_data["state_name"] if (cin_parsed_data and "state_name" in cin_parsed_data) else "Pan-India"
            render_kpi_card("Jurisdiction State", s_name, subtext="RoC Jurisdiction")

# RIGHT COLUMN: FACILITY TERMS (ONE st.form per B1)
with col_facility:
    with st.container(border=True):
        st.markdown("#### :material/payments: Facility Terms")
        with st.form(key="case_facility_form"):
            form_loan = st.number_input(
                "Requested Facility (₹)",
                min_value=100000.0,
                value=float(st.session_state.facility_loan_amount),
                step=500000.0,
                format="%.2f",
                key="form_loan_amount",
            )
            form_tenure = st.slider(
                "Tenure (Months)",
                min_value=12,
                max_value=84,
                value=int(st.session_state.facility_tenure_months),
                step=6,
                key="form_tenure_months",
            )
            form_rate = st.slider(
                "Indicative Rate (%)",
                min_value=7.5,
                max_value=24.0,
                value=float(st.session_state.facility_interest_rate),
                step=0.25,
                key="form_interest_rate",
            )
            form_collat = st.number_input(
                "Pledged Collateral (₹)",
                min_value=0.0,
                value=float(st.session_state.facility_collateral_val),
                step=100000.0,
                format="%.2f",
                help="Immovable property, fixed assets, or liquid collateral pledged.",
                key="form_collateral_val",
            )
            form_cgtmse = st.checkbox(
                "CGTMSE Guarantee Covered",
                value=bool(st.session_state.facility_cgtmse_covered),
                help="Credit Guarantee Fund Trust for Micro and Small Enterprises coverage active.",
                key="form_cgtmse_covered",
            )
            submitted = st.form_submit_button(":material/save: Apply Facility Terms", type="primary", width="stretch")
            if submitted:
                # B2: Persist in plain session_state keys
                st.session_state.facility_loan_amount = float(form_loan)
                st.session_state.facility_tenure_months = int(form_tenure)
                st.session_state.facility_interest_rate = float(form_rate)
                st.session_state.facility_collateral_val = float(form_collat)
                st.session_state.facility_cgtmse_covered = bool(form_cgtmse)
                # Synced plain aliases
                st.session_state.loan_amount = float(form_loan)
                st.session_state.tenure_months = int(form_tenure)
                st.session_state.interest_rate = float(form_rate)
                st.session_state.collateral_value = float(form_collat)
                st.session_state.cgtmse_covered = bool(form_cgtmse)
                st.rerun()

        # Computed EMI shown directly after Apply per B1
        render_kpi_card(
            "Computed Monthly EMI",
            format_inr(ctx["computed_monthly_emi"]),
            subtext="Banking Annuity Formula",
            tooltip="P*r*(1+r)^n / ((1+r)^n - 1)",
        )
        st.caption(":material/info: Click **Apply Facility Terms** to persist parameter changes and recalculate EMI.")

# ----------------------------------------------------------------------------
# 3. ONBOARDING AUDIT & NAVIGATION
# ----------------------------------------------------------------------------
st.markdown("---")
ocol1, ocol2 = st.columns([1, 1])

with ocol1:
    if st.button("Log Onboarding Verification Event", key="btn_log_onboarding"):
        record_event(
            event_type="ONBOARDING_VERIFIED",
            actor="OFFICER",
            details={
                "cin": cin_parsed_data["cin"] if (cin_parsed_data and "cin" in cin_parsed_data) else (ctx.get("cin_input") or "UNKNOWN"),
                "pan": pan_input,
                "sector": cin_parsed_data["macro_sector"] if (cin_parsed_data and "macro_sector" in cin_parsed_data) else "Unknown",
                "vintage_years": cin_parsed_data["business_vintage_years"] if (cin_parsed_data and "business_vintage_years" in cin_parsed_data) else 0,
                "loan_amount": str(loan_amount),
            }
        )
        st.toast("Onboarding verified and stamped in Audit Trail.")

with ocol2:
    if st.button("Proceed to Evidence Ingestion →", type="primary", key="btn_next_evidence", width="stretch"):
        st.session_state.current_page = "Evidence"
        st.switch_page("app_pages/evidence.py")
