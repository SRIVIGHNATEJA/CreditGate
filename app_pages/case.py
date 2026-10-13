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
        border_c1 = "border: 2px solid #15803D;" if is_c1 else ""
        st.markdown(f'<div style="height: 4px; background: #15803D; border-radius: 2px; margin-bottom: 6px; {border_c1}"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size: 10px; font-weight: 700; color: #15803D; letter-spacing: 0.05em; text-transform: uppercase;">Archetype: Tier-1 Prime MSME</div>', unsafe_allow_html=True)
        c1_chip = f" &nbsp; {status_pill('ACTIVE', variant='info')}" if is_c1 else ""
        st.markdown(f"**1. Prime MSME**{c1_chip}", unsafe_allow_html=True)
        st.markdown(f"**Apex Synthetic Holdings (Demo)** &nbsp; {status_pill('APPROVE', variant='approve')}", unsafe_allow_html=True)
        st.markdown("<div style='font-size: 11px; color: #0F766E; font-weight: 600; margin: 4px 0;'>DSCR: 1.62x &nbsp;•&nbsp; Bounces: 0 &nbsp;•&nbsp; Gap: 5.2%</div>", unsafe_allow_html=True)
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
        border_c2 = "border: 2px solid #B91C1C;" if is_c2 else ""
        st.markdown(f'<div style="height: 4px; background: #B91C1C; border-radius: 2px; margin-bottom: 6px; {border_c2}"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size: 10px; font-weight: 700; color: #B91C1C; letter-spacing: 0.05em; text-transform: uppercase;">Archetype: Distressed Logistics</div>', unsafe_allow_html=True)
        c2_chip = f" &nbsp; {status_pill('ACTIVE', variant='info')}" if is_c2 else ""
        st.markdown(f"**2. Distressed**{c2_chip}", unsafe_allow_html=True)
        st.markdown(f"**Kaveri Fictitious Freight (Demo)** &nbsp; {status_pill('REJECT', variant='reject')}", unsafe_allow_html=True)
        st.markdown("<div style='font-size: 11px; color: #B91C1C; font-weight: 600; margin: 4px 0;'>DSCR: 0.82x &nbsp;•&nbsp; Bounces: 5 &nbsp;•&nbsp; Gap: 12.1%</div>", unsafe_allow_html=True)
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
        border_c3 = "border: 2px solid #B45309;" if is_c3 else ""
        st.markdown(f'<div style="height: 4px; background: #B45309; border-radius: 2px; margin-bottom: 6px; {border_c3}"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size: 10px; font-weight: 700; color: #B45309; letter-spacing: 0.05em; text-transform: uppercase;">Archetype: High-Turnover Trader</div>', unsafe_allow_html=True)
        c3_chip = f" &nbsp; {status_pill('ACTIVE', variant='info')}" if is_c3 else ""
        st.markdown(f"**3. Mismatch Trap**{c3_chip}", unsafe_allow_html=True)
        st.markdown(f"**Bharat Mock Steels & Infra (Demo)** &nbsp; {status_pill('CONDITIONAL', variant='conditional')}", unsafe_allow_html=True)
        st.markdown("<div style='font-size: 11px; color: #B45309; font-weight: 600; margin: 4px 0;'>DSCR: 1.15x &nbsp;•&nbsp; Bounces: 1 &nbsp;•&nbsp; Gap: 24.5%</div>", unsafe_allow_html=True)
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
if "case_cohort_select" in st.session_state:
    selected_cohort = st.selectbox("Quick-Load Test Cohort", cohort_options, key="case_cohort_select")
else:
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
            render_callout("Tip: For Sole Proprietorships, 4th character of PAN is 'P' (Individual) or 'F' (Partnership Firm).", title="MSME PAN Guidance", variant="info")
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

        if is_corporate:
            bcol1, bcol2 = st.columns(2)
            with bcol1:
                render_kpi_card("Entity Status", "Active Corporate", subtext="MCA Status")
                v_years = cin_parsed_data["business_vintage_years"] if (cin_parsed_data and "business_vintage_years" in cin_parsed_data) else 5
                render_kpi_card("Operating Vintage", f"{v_years} Years", subtext="Incorporation")
            with bcol2:
                raw_sec = cin_parsed_data["macro_sector"] if (cin_parsed_data and "macro_sector" in cin_parsed_data) else "General Corporate"
                sec_short = raw_sec[:22] + "..." if len(raw_sec) > 22 else raw_sec
                render_kpi_card("Industry Sector", sec_short, subtext="NIC Taxonomy")
                s_name = cin_parsed_data["state_name"] if (cin_parsed_data and "state_name" in cin_parsed_data) else "Pan-India"
                render_kpi_card("Jurisdiction State", s_name, subtext="RoC Jurisdiction")
        else:
            bcol1, bcol2 = st.columns(2)
            with bcol1:
                render_kpi_card("Entity Mode", "Unregistered MSME", subtext="Non-Corporate Entity")
                man_vin = st.session_state.get("case_manual_vintage", 7)
                render_kpi_card("Operating Vintage", f"{man_vin} Years", subtext="Promoter Track Record")
            with bcol2:
                man_sec = st.session_state.get("case_manual_sector", list(NIC_SECTOR_TAXONOMY.values())[0])
                sec_short = man_sec[:22] + "..." if len(man_sec) > 22 else man_sec
                render_kpi_card("Industry Sector", sec_short, subtext="MSME Activity")
                render_kpi_card("Registration Base", "State Udyam Portal", subtext="MSME Registration")

# RIGHT COLUMN: FACILITY TERMS & CAPITAL VISUALIZATION
with col_facility:
    with st.container(border=True):
        st.markdown("#### :material/monitoring: Facility Term Sheet & Risk Pricing")
        
        # 1. Bank Policy Pricing Context
        is_large_ticket = float(st.session_state.facility_loan_amount) > 10000000.0
        base_spread = 3.75 if is_c1 else (7.25 if is_c2 else 5.00)
        spread_tier_add = 0.50 if is_large_ticket else 0.0
        effective_spread = base_spread + spread_tier_add
        policy_tier_name = "Tier-1 Prime" if is_c1 else ("Tier-3 Stressed" if is_c2 else "Tier-2 Moderate")
        if is_large_ticket:
            policy_tier_name += " (Large Ticket +0.50%)"
        benchmark_rate = 6.50 + effective_spread
        curr_rate = float(st.session_state.facility_interest_rate)
        delta_rate = curr_rate - benchmark_rate
        if abs(delta_rate) < 0.01:
            concession_chip = status_pill("Policy Standard", variant="approve")
        elif delta_rate < 0:
            concession_chip = status_pill(f"Concession: {delta_rate:.2f}%", variant="info")
        else:
            concession_chip = status_pill(f"Risk Surcharge: +{delta_rate:.2f}%", variant="conditional")

        pricing_badge = f'<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; font-size: 12px; color: #1E293B; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;"><div><strong>Bank Policy Benchmark:</strong> RBI Repo (6.50%) + {effective_spread:.2f}% ({policy_tier_name}) = <strong>{benchmark_rate:.2f}% p.a.</strong></div><div>{concession_chip}</div></div>'
        st.markdown(pricing_badge, unsafe_allow_html=True)

        # 2. Live Amortization & Debt Service Outflow Calculation
        P_dec = Decimal(str(st.session_state.facility_loan_amount))
        r_dec = Decimal(str(st.session_state.facility_interest_rate))
        n_int = int(st.session_state.facility_tenure_months)

        from core.analyser import calculate_monthly_emi
        live_emi = calculate_monthly_emi(P_dec, r_dec, n_int)
        total_repayment = live_emi * Decimal(str(n_int))
        total_interest_cost = total_repayment - P_dec
        pct_p = float(P_dec / total_repayment * Decimal("100")) if total_repayment > 0 else 100.0
        pct_i = float(total_interest_cost / total_repayment * Decimal("100")) if total_repayment > 0 else 0.0

        # 3. High-Impact Visual Term Sheet HUD (4 KPI Cards in 2x2 grid)
        hud_col1, hud_col2 = st.columns(2)
        with hud_col1:
            render_kpi_card(
                "Requested Facility",
                f"₹ {float(P_dec)/100000:.1f} Lakhs",
                subtext=f"{format_inr(P_dec)}",
                tooltip="Sanctioned credit facility limit",
            )
            render_kpi_card(
                "Monthly EMI Obligation",
                format_inr(live_emi),
                subtext=f"Over {n_int} Months ({n_int//12} Yrs)",
                tooltip="Equated Monthly Installment via Annuity Formula",
            )
        with hud_col2:
            render_kpi_card(
                "Lending Rate",
                f"{float(r_dec):.2f}% p.a.",
                delta=f"{delta_rate:+.2f}% vs Policy" if abs(delta_rate) >= 0.01 else "At Benchmark",
                delta_color="normal" if abs(delta_rate) < 0.01 else ("teal" if delta_rate < 0 else "warning"),
                tooltip="Risk-adjusted lending rate",
            )
            render_kpi_card(
                "Total Interest Outflow",
                format_inr(total_interest_cost),
                subtext=f"{pct_i:.1f}% Cost of Capital",
                tooltip=f"Cumulative interest cost over {n_int} months",
            )

        # 4. Interactive Capital Outflow Visualizer (Plotly Horizontal Breakdown)
        import plotly.graph_objects as go
        fig_amort = go.Figure()
        fig_amort.add_trace(go.Bar(
            y=["Capital Split"],
            x=[float(P_dec)],
            name=f"Principal ({pct_p:.1f}%)",
            orientation="h",
            marker=dict(color="#0F766E"),
            text=f"Principal: ₹{float(P_dec)/100000:.1f}L ({pct_p:.1f}%)",
            textposition="inside",
            insidetextanchor="middle",
            hoverinfo="text",
        ))
        fig_amort.add_trace(go.Bar(
            y=["Capital Split"],
            x=[float(total_interest_cost)],
            name=f"Interest Cost ({pct_i:.1f}%)",
            orientation="h",
            marker=dict(color="#64748B"),
            text=f"Interest: ₹{float(total_interest_cost)/100000:.1f}L ({pct_i:.1f}%)",
            textposition="inside",
            insidetextanchor="middle",
            hoverinfo="text",
        ))
        fig_amort.update_layout(
            barmode="stack",
            height=60,
            margin=dict(l=0, r=0, t=5, b=5),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
            xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
            yaxis=dict(showgrid=False, showticklabels=False),
        )
        st.plotly_chart(fig_amort, use_container_width=True, config={"displayModeBar": False})

        # 5. Smart One-Click Scenario Controls (No manual numeric typing required!)
        st.markdown("<div style='font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase; margin: 4px 0 2px 0;'>1-Click Facility Presets:</div>", unsafe_allow_html=True)
        col_ps1, col_ps2, col_ps3, col_ps4 = st.columns(4)
        with col_ps1:
            if st.button("₹25L", key="btn_pre_25l", width="stretch", help="Set facility to ₹25 Lakhs (Micro-Enterprise)"):
                st.session_state.facility_loan_amount = 2500000.0
                st.session_state.loan_amount = 2500000.0
                st.rerun()
        with col_ps2:
            if st.button("₹50L", key="btn_pre_50l", width="stretch", help="Set facility to ₹50 Lakhs (Standard Term Loan)"):
                st.session_state.facility_loan_amount = 5000000.0
                st.session_state.loan_amount = 5000000.0
                st.rerun()
        with col_ps3:
            if st.button("₹75L", key="btn_pre_75l", width="stretch", help="Set facility to ₹75 Lakhs (Growth Capital)"):
                st.session_state.facility_loan_amount = 7500000.0
                st.session_state.loan_amount = 7500000.0
                st.rerun()
        with col_ps4:
            if st.button("₹1.0Cr", key="btn_pre_1cr", width="stretch", help="Set facility to ₹1.00 Crore (Syndicate Limit)"):
                st.session_state.facility_loan_amount = 10000000.0
                st.session_state.loan_amount = 10000000.0
                st.rerun()

        # 6. Smart Rate Calibration Controls
        r_col1, r_col2 = st.columns([1.2, 1])
        with r_col1:
            if st.button(f":material/bolt: Auto-Align to Policy ({benchmark_rate:.2f}%)", key="btn_auto_rate", width="stretch", help=f"Auto-sets interest rate to {benchmark_rate:.2f}% p.a. matching bank policy"):
                st.session_state.facility_interest_rate = benchmark_rate
                st.session_state.interest_rate = benchmark_rate
                st.rerun()
        with r_col2:
            if st.button(":material/percent: -0.50% Concession", key="btn_concession_quick", width="stretch", help="Apply 50 bps discretionary credit committee concession"):
                new_c_rate = max(7.5, float(st.session_state.facility_interest_rate) - 0.50)
                st.session_state.facility_interest_rate = new_c_rate
                st.session_state.interest_rate = new_c_rate
                st.rerun()

        # 7. Complete Formal Specification Form (preserves test indices & allows fine-tuning)
        with st.expander("⚙️ Calibrate Facility Parameters & Discretionary Pricing (Credit Committee)", expanded=False):
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
                    help="Applicant requested repayment amortization horizon. Milestones: 12M (1 Yr), 36M (3 Yrs), 60M (5 Yrs), 84M (7 Yrs)."
                )
                form_rate = st.slider(
                    "Policy Lending Rate (%)",
                    min_value=7.5,
                    max_value=24.0,
                    value=float(st.session_state.facility_interest_rate),
                    step=0.25,
                    key="form_interest_rate",
                    help="Derived autonomously from Bank Risk Policy matrix. Adjust if discretionary credit committee concession is sanctioned."
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
                    st.session_state.facility_loan_amount = float(form_loan)
                    st.session_state.facility_tenure_months = int(form_tenure)
                    st.session_state.facility_interest_rate = float(form_rate)
                    st.session_state.facility_collateral_val = float(form_collat)
                    st.session_state.facility_cgtmse_covered = bool(form_cgtmse)
                    st.session_state.loan_amount = float(form_loan)
                    st.session_state.tenure_months = int(form_tenure)
                    st.session_state.interest_rate = float(form_rate)
                    st.session_state.collateral_value = float(form_collat)
                    st.session_state.cgtmse_covered = bool(form_cgtmse)
                    st.rerun()

        # 8. Visual Collateral Coverage Progress Meter
        req_l = float(st.session_state.facility_loan_amount)
        pled_c = float(st.session_state.facility_collateral_val)
        cov_pct = (pled_c / req_l * 100.0) if req_l > 0 else 0.0
        cov_variant = "approve" if cov_pct >= 100.0 else ("conditional" if cov_pct >= 50.0 else "neutral")
        cov_color = "#15803D" if cov_pct >= 100.0 else ("#B45309" if cov_pct >= 50.0 else "#94A3B8")
        cov_pill = status_pill(f"{cov_pct:.1f}% Covered", variant=cov_variant)

        cov_meter_html = f"""<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 12px; margin: 8px 0;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <span style="font-size: 12px; font-weight: 600; color: #1E293B;">Collateral Coverage Ratio:</span>
                <div>{cov_pill} &nbsp; <span style="font-size: 11px; color: #64748B;">({format_inr(pled_c)} / {format_inr(req_l)})</span></div>
            </div>
            <div style="background: #E2E8F0; border-radius: 9999px; height: 6px; width: 100%; overflow: hidden;">
                <div style="background: {cov_color}; width: {min(100.0, cov_pct)}%; height: 100%; border-radius: 9999px;"></div>
            </div>
        </div>"""
        st.markdown(cov_meter_html, unsafe_allow_html=True)

        if st.session_state.facility_cgtmse_covered:
            st.caption(":material/verified_user: CGTMSE Trust Cover Active: Credit guarantee covers eligible debt. Annual fee approx 0.75%–1.00% p.a.")

        if st.button(":material/restart_alt: Reset to Policy Standard", key="btn_reset_policy_terms", width="stretch"):
            st.session_state.facility_loan_amount = 5000000.0
            st.session_state.facility_tenure_months = 36
            st.session_state.facility_interest_rate = 11.5
            st.session_state.facility_collateral_val = 2500000.0
            st.session_state.facility_cgtmse_covered = False
            st.session_state.loan_amount = 5000000.0
            st.session_state.tenure_months = 36
            st.session_state.interest_rate = 11.5
            st.session_state.collateral_value = 2500000.0
            st.session_state.cgtmse_covered = False
            st.rerun()

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
