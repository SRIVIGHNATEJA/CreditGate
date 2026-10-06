import streamlit as st
from ui.state import get_app_context, record_event, select_and_reset_cohort
from ui.components import render_kpi_card, render_callout, status_pill

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

# ----------------------------------------------------------------------------
# 1. CASE GALLERY (LANDING VIEW: 3 CARDS)
# ----------------------------------------------------------------------------
g_col1, g_col2, g_col3 = st.columns(3)

with g_col1:
    with st.container(border=True):
        st.markdown("**1. Prime MSME**")
        st.markdown(f"**Apex Synthetic Holdings (Demo)** &nbsp; {status_pill('APPROVE', variant='approve')}", unsafe_allow_html=True)
        st.caption("Deciding Signal: Clean banking, 0 mandate bounces, DSCR 1.62x, balanced ledger.")
        st.button(
            "Open Case",
            key="btn_open_prime",
            on_click=select_and_reset_cohort,
            args=("Cohort 1: Prime MSME (Apex Synthetic Demo)",),
            width="stretch",
        )

with g_col2:
    with st.container(border=True):
        st.markdown("**2. Distressed**")
        st.markdown(f"**Kaveri Fictitious Freight (Demo)** &nbsp; {status_pill('REJECT', variant='reject')}", unsafe_allow_html=True)
        st.caption("Deciding Signal: 5 mandate bounces enforce hard policy knockout (BOUNCE_KNOCKOUT).")
        st.button(
            "Open Case",
            key="btn_open_distressed",
            on_click=select_and_reset_cohort,
            args=("Cohort 2: Distressed (Kaveri Fictitious Demo)",),
            width="stretch",
        )

with g_col3:
    with st.container(border=True):
        st.markdown("**3. Mismatch Trap**")
        st.markdown(f"**Bharat Mock Steels & Infra (Demo)** &nbsp; {status_pill('CONDITIONAL', variant='conditional')}", unsafe_allow_html=True)
        st.caption("Deciding Signal: ₹14.5 Cr turnover vs thin ₹18L PAT triggers WEAKEST_LINK_CAP (< 40.0).")
        st.button(
            "Open Case",
            key="btn_open_mismatch",
            on_click=select_and_reset_cohort,
            args=("Cohort 3: Mismatch Trap (Bharat Mock Demo)",),
            width="stretch",
        )

st.markdown("---")

# ----------------------------------------------------------------------------
# 2. ACTIVE BORROWER PROFILE & JURISDICTION INTEL
# ----------------------------------------------------------------------------
st.markdown("#### Active Applicant Corporate Profile")
st.markdown(f"**Current Case:** `{ctx['company_name']}` &nbsp;|&nbsp; `{cohort_selection}`")

col1, col2, col3, col4 = st.columns(4)
with col1:
    render_kpi_card("Entity Status", "Active Corporate" if is_corporate else "Proprietorship", subtext="MCA Status")
with col2:
    sec_label = "General Corporate"
    if cin_parsed_data and cin_parsed_data.get("macro_sector"):
        raw_sec = cin_parsed_data["macro_sector"]
        sec_label = raw_sec[:24] + "..." if len(raw_sec) > 24 else raw_sec
    render_kpi_card("Industry Sector", sec_label, subtext="NIC Taxonomy")
with col3:
    vintage_str = f"{cin_parsed_data['business_vintage_years']} Years" if (cin_parsed_data and "business_vintage_years" in cin_parsed_data) else "5 Years"
    render_kpi_card("Operating Vintage", vintage_str, subtext="Incorporation")
with col4:
    state_str = cin_parsed_data["state_name"] if (cin_parsed_data and "state_name" in cin_parsed_data) else "Pan-India"
    render_kpi_card("Jurisdiction State", state_str, subtext="RoC Jurisdiction")

st.markdown("#### Entity Identification Consistency Check")
if not pan_check.get("is_valid", False):
    render_callout(pan_check.get("error", "Invalid PAN Format"), title="Invalid PAN Format", variant="error")
elif pan_check.get("mismatch_detected", False):
    render_callout(pan_check.get("warning_message", ""), title="Consistency Alert", variant="error")
else:
    render_callout(f"PAN 4th character '{pan_check.get('pan_entity_char')}' aligns with {pan_check.get('pan_entity_type')}.", title="Entity Verified", variant="success")

st.markdown("#### Facility Parameters Summary")
fcol1, fcol2, fcol3 = st.columns(3)
with fcol1:
    render_kpi_card("Requested Facility", f"₹ {loan_amount:,.2f}", subtext="Sanction Request")
with fcol2:
    render_kpi_card("Tenure & Rate", f"{tenure_months}M @ {interest_rate}% p.a.", subtext="Facility Terms")
with fcol3:
    render_kpi_card("Debt Service (EMI)", f"₹ {proposed_emi:,.2f}", subtext="Monthly Annuity")

if st.button("Log Onboarding Verification Event", key="btn_log_onboarding"):
    record_event(
        event_type="ONBOARDING_VERIFIED",
        actor="OFFICER",
        details={
            "cin": cin_parsed_data["cin"] if (cin_parsed_data and "cin" in cin_parsed_data) else (ctx.get("cin_input") or "UNKNOWN"),
            "pan": pan_input,
            "sector": cin_parsed_data["macro_sector"] if (cin_parsed_data and "macro_sector" in cin_parsed_data) else "Unknown",
            "vintage_years": cin_parsed_data["business_vintage_years"] if (cin_parsed_data and "business_vintage_years" in cin_parsed_data) else 0,
            "loan_amount": str(loan_amount)
        }
    )
    st.toast("Onboarding verified and stamped in Audit Trail.")

st.markdown("---")
if st.button("Proceed to Evidence Ingestion →", type="primary", key="btn_next_evidence", width="stretch"):
    st.session_state.current_page = "Evidence"
    st.switch_page("app_pages/evidence.py")
