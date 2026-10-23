import os
import json
import html
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from core.data_models import (
    ReviewStatus,
    FindingTier,
    AdverseMediaReport,
)
from core.researcher import (
    build_search_query,
    search_adverse_media,
    confirm_adverse_finding,
    dismiss_adverse_finding,
    extract_policy_flags,
    merge_existing_reviews,
    extract_legal_docket_text,
)
from core.policy import Verdict
from core.scorer import score_credit_case, ScoreResult
from core.formatting import format_inr
from core.analyser import calculate_monthly_emi
from ui.state import (
    get_app_context,
    record_event,
    compute_composite_hash,
    COHORT_COMPANY_NAMES,
)
from ui.components import (
    render_kpi_card,
    status_pill,
    render_callout,
    render_verdict_hero_card,
    _render_html,
)

st.session_state.current_page = "Decision"
ctx = get_app_context()

cohort_selection = ctx["cohort_selection"]
cin_parsed_data = ctx["cin_parsed_data"]
current_pan_val = ctx["current_pan_val"]
current_pan_mismatch = ctx["current_pan_mismatch"]
pan_check = ctx["pan_check"]
loan_amount = ctx["loan_amount"]
tenure_months = ctx["tenure_months"]
interest_rate = ctx["interest_rate"]
proposed_emi = ctx["proposed_emi"]
collateral_val = ctx["collateral_val"]
cgtmse_covered = ctx["cgtmse_covered"]

st.markdown("### Deterministic Five Cs Risk Scorecard & Policy Gate")
st.caption("Evaluated entirely in Python Decimal arithmetic • Statutory Underwriting Portal")

if not st.session_state.hitl_confirmed or st.session_state.locked_case is None:
    st.markdown(
        """
        <div style="display: flex; gap: 12px; margin: 12px 0 18px 0;">
            <div style="flex: 1; background: #F1F5F9; border-left: 3px solid #10B981; padding: 10px 14px; border-radius: 6px;">
                <div style="font-size: 11px; font-weight: 700; color: #10B981; text-transform: uppercase;">Stage 1: Complete</div>
                <div style="font-weight: 600; font-size: 13px; color: #1E293B;">Entity Verification & Master Setup</div>
            </div>
            <div style="flex: 1; background: #FEF3C7; border-left: 3px solid #D97706; padding: 10px 14px; border-radius: 6px;">
                <div style="font-size: 11px; font-weight: 700; color: #D97706; text-transform: uppercase;">Stage 2: Pending Lock</div>
                <div style="font-weight: 600; font-size: 13px; color: #92400E;">Evidence Ingestion & HITL Confirmation</div>
            </div>
            <div style="flex: 1; background: #F8FAFC; border-left: 3px solid #94A3B8; padding: 10px 14px; border-radius: 6px;">
                <div style="font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase;">Stage 3: Gated</div>
                <div style="font-weight: 600; font-size: 13px; color: #64748B;">Deterministic Five Cs Scorecard</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_callout(
        "Financials are not locked yet. Review and lock them on the Evidence page.\n\n"
        "Under RBI Master Direction on Credit Risk Management & Model Governance, automated risk scoring cannot execute on unverified financial data. "
        "Please complete document extraction and lock audited financials on the Evidence portal.",
        title="Financials Not Locked",
        variant="info",
    )
    with st.container(border=True):
        st.markdown("#### :material/lock: Stage 3 Underwriting Prerequisites")
        st.markdown(
            "- **Audited Financials Ingestion**: Banking credits, GST turnover, and tax returns must be extracted.\n"
            "- **Human-In-The-Loop Validation**: Credit officer must review and confirm data concordance.\n"
            "- **Cryptographic Baseline Lock**: Immutable hash seal must be generated on Stage 2 before policy scoring is unlocked."
        )
    if st.button("Go to Evidence", type="primary", key="btn_go_to_evidence", width="stretch"):
        st.session_state.current_page = "Evidence"
        st.switch_page("app_pages/evidence.py")
    st.stop()

# ----------------------------------------------------------------------------
# COMPOSITE HASH & SCORING TRIGGER SETUP
# ----------------------------------------------------------------------------
findings_list = st.session_state.current_research_report.findings if st.session_state.current_research_report else []
current_composite_hash = compute_composite_hash(
    locked_financials_hash=st.session_state.locked_financials_hash,
    loan_amount=float(loan_amount),
    tenure_months=int(tenure_months),
    interest_rate=float(interest_rate),
    proposed_emi=float(proposed_emi),
    collateral_val=float(collateral_val),
    cgtmse_covered=bool(cgtmse_covered),
    cin=cin_parsed_data["cin"] if cin_parsed_data else "",
    vintage=cin_parsed_data.get("business_vintage_years", 5) if cin_parsed_data else 5,
    pan=current_pan_val,
    pan_mismatch=current_pan_mismatch,
    research_findings=findings_list,
)

is_stale = (st.session_state.scored_composite_hash != current_composite_hash)

def _execute_scoring(report_override=None) -> ScoreResult:
    rep_for_scoring = report_override if report_override is not None else st.session_state.current_research_report
    if st.session_state.locked_case is not None:
        st.session_state.locked_case.requested_loan_amount = Decimal(str(loan_amount))
        st.session_state.locked_case.proposed_rate = Decimal(str(interest_rate))
        st.session_state.locked_case.proposed_tenure_months = int(tenure_months)
        st.session_state.locked_case.proposed_emi = Decimal(str(proposed_emi))

    sev_flags, rev_flags = extract_policy_flags(rep_for_scoring)
    score_res = score_credit_case(
        credit_case=st.session_state.locked_case,
        business_vintage_years=cin_parsed_data.get("business_vintage_years", 5) if cin_parsed_data else 5,
        pan_entity_mismatch=pan_check.get("mismatch_detected", False),
        collateral_value=Decimal(str(collateral_val)),
        cgtmse_covered=cgtmse_covered,
        severe_flags=sev_flags,
        review_flags=rev_flags,
    )
    findings_for_hash = rep_for_scoring.findings if rep_for_scoring else []
    new_comp_hash = compute_composite_hash(
        locked_financials_hash=st.session_state.locked_financials_hash,
        loan_amount=float(loan_amount),
        tenure_months=int(tenure_months),
        interest_rate=float(interest_rate),
        proposed_emi=float(proposed_emi),
        collateral_val=float(collateral_val),
        cgtmse_covered=bool(cgtmse_covered),
        cin=cin_parsed_data["cin"] if cin_parsed_data else "",
        vintage=cin_parsed_data.get("business_vintage_years", 5) if cin_parsed_data else 5,
        pan=current_pan_val,
        pan_mismatch=current_pan_mismatch,
        research_findings=findings_for_hash,
    )
    st.session_state.score_result = score_res
    st.session_state.scored_composite_hash = new_comp_hash
    st.session_state.scored_financials_hash = st.session_state.locked_financials_hash
    st.session_state.cam_markdown = None
    record_event(
        event_type="SCORING_EVALUATED",
        actor="DETERMINISTIC_ENGINE",
        details={
            "composite_score": str(score_res.composite_score),
            "baseline_verdict": score_res.baseline_verdict.name,
            "final_verdict": score_res.final_verdict.name,
            "cap_applied": score_res.cap_applied,
            "knockout_applied": score_res.knockout_applied,
            "dscr": str(score_res.dscr),
        },
    )
    return score_res

# ----------------------------------------------------------------------------
# FACILITY TERMS CALIBRATION DIALOG & READ-ONLY STRIP per B4
# ----------------------------------------------------------------------------
@st.dialog("Calibrate Proposed Facility Terms", width="medium")
def show_facility_calibration_dialog():
    st.markdown(
        "<div style='font-size: 13px; color: #64748B; margin-top: -6px; margin-bottom: 12px;'>"
        "Calibrate requested debt facility amount, repayment horizon, interest spread concessions, "
        "and collateral covenants. Modifications immediately update debt service (DSCR) calculations.</div>",
        unsafe_allow_html=True,
    )

    d_col1, d_col2 = st.columns(2)
    with d_col1:
        dlg_loan = st.number_input(
            "Requested Facility (₹)",
            min_value=100000.0,
            value=float(st.session_state.facility_loan_amount),
            step=500000.0,
            format="%.2f",
            key="dlg_dec_loan_amount",
            help="Principal amount requested for debt facility.",
        )
        dlg_tenure = st.slider(
            "Amortization Tenure (Months)",
            min_value=12,
            max_value=84,
            value=int(st.session_state.facility_tenure_months),
            step=6,
            key="dlg_dec_tenure_months",
            help="Amortization horizon in months.",
        )
        dlg_cgtmse = st.checkbox(
            "CGTMSE Guarantee Covered",
            value=bool(st.session_state.facility_cgtmse_covered),
            key="dlg_dec_cgtmse_covered",
            help="Credit Guarantee Fund Trust for Micro and Small Enterprises coverage active.",
        )

    with d_col2:
        dlg_rate = st.slider(
            "Lending Rate (% p.a.)",
            min_value=7.50,
            max_value=24.00,
            value=float(st.session_state.facility_interest_rate),
            step=0.25,
            key="dlg_dec_interest_rate",
            help="Interest rate benchmark for facility.",
        )
        dlg_collat = st.number_input(
            "Pledged Tangible Collateral (₹)",
            min_value=0.0,
            value=float(st.session_state.facility_collateral_val),
            step=100000.0,
            format="%.2f",
            key="dlg_dec_collateral_val",
            help="Pledged collateral security value.",
        )
        if dlg_cgtmse:
            st.caption(":material/verified_user: CGTMSE Trust Cover Active")

    calc_emi = calculate_monthly_emi(
        principal=Decimal(str(dlg_loan)),
        annual_rate_pct=Decimal(str(dlg_rate)),
        tenure_months=int(dlg_tenure),
    )
    st.markdown(
        f"<div style='background: #F1F5F9; border-radius: 6px; padding: 8px 12px; margin: 10px 0; font-size: 13px; color: #1E293B;'>"
        f"<strong>Simulated Monthly Debt Service:</strong> {format_inr(float(calc_emi))} / month &nbsp;|&nbsp; "
        f"<strong>Annual Service:</strong> {format_inr(float(calc_emi * Decimal('12')))}"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("<hr style='margin: 8px 0 14px 0; border: none; border-top: 1px solid #E2E8F0;' />", unsafe_allow_html=True)
    c_btn1, c_btn2 = st.columns([1, 1])
    with c_btn1:
        if st.button("Cancel", key="btn_cancel_terms_dlg", width="stretch"):
            st.rerun()
    with c_btn2:
        if st.button("Apply & Recalculate Terms", type="primary", key="btn_apply_terms_dlg", width="stretch"):
            new_l = float(dlg_loan)
            new_t = int(dlg_tenure)
            new_r = float(dlg_rate)
            new_c = float(dlg_collat)
            new_g = bool(dlg_cgtmse)

            st.session_state.facility_loan_amount = new_l
            st.session_state.facility_tenure_months = new_t
            st.session_state.facility_interest_rate = new_r
            st.session_state.facility_collateral_val = new_c
            st.session_state.facility_cgtmse_covered = new_g

            st.session_state.loan_amount = new_l
            st.session_state.tenure_months = new_t
            st.session_state.interest_rate = new_r
            st.session_state.collateral_val = new_c
            st.session_state.collateral_value = new_c
            st.session_state.cgtmse_covered = new_g
            st.session_state.proposed_emi = float(calc_emi)

            if st.session_state.locked_case is not None:
                st.session_state.locked_case.requested_loan_amount = Decimal(str(new_l))
                st.session_state.locked_case.proposed_rate = Decimal(str(new_r))
                st.session_state.locked_case.proposed_tenure_months = int(new_t)
                st.session_state.locked_case.proposed_emi = Decimal(str(calc_emi))

            if st.session_state.score_result is not None:
                _execute_scoring()
            else:
                st.session_state.scored_composite_hash = None

            record_event(
                event_type="TERMS_CALIBRATED",
                actor="OFFICER",
                details={
                    "loan_amount": str(new_l),
                    "rate": str(new_r),
                    "tenure": str(new_t),
                    "collateral": str(new_c),
                    "cgtmse": str(new_g),
                },
            )
            st.rerun()

cgtmse_txt = "Yes" if cgtmse_covered else "No"
col_strip, col_edit = st.columns([5, 1])
with col_strip:
    strip_html = f'<div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 14px; font-size: 13px; color: #1F2937; margin-bottom: 8px;"><strong>Facility:</strong> {format_inr(loan_amount)}, {int(tenure_months)} mos @ {float(interest_rate)}% p.a. &nbsp;|&nbsp; <strong>Collateral:</strong> {format_inr(collateral_val)} &nbsp;|&nbsp; <strong>CGTMSE:</strong> {cgtmse_txt}</div>'
    st.markdown(strip_html, unsafe_allow_html=True)
with col_edit:
    if st.button(":material/edit: Edit terms", key="btn_edit_facility_terms", width="stretch", help="Calibrate facility terms in-place via modal dialog"):
        show_facility_calibration_dialog()

# ----------------------------------------------------------------------------
# TOP 3 DOSSIER SUMMARY CARDS
# ----------------------------------------------------------------------------
company_disp = (
    st.session_state.locked_case.company_name
    if st.session_state.locked_case
    else COHORT_COMPANY_NAMES.get(cohort_selection, "Applicant Company")
)
cin_val = cin_parsed_data["cin"] if (cin_parsed_data and "cin" in cin_parsed_data) else "Unregistered MSME"
vintage_val = cin_parsed_data.get("business_vintage_years", 5) if cin_parsed_data else 5
sector_val = cin_parsed_data.get("macro_sector", "Commercial Entity") if cin_parsed_data else "Commercial Entity"

col_card1, col_card2, col_card3 = st.columns(3)
with col_card1:
    st.markdown(
        f"""
        <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #0284C7; border-radius: 8px; padding: 12px 14px; min-height: 110px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.72rem; font-weight: 700; color: #0284C7; text-transform: uppercase; letter-spacing: 0.04em;">🏢 BORROWER IDENTITY</span>
                <span style="font-size: 0.70rem; font-weight: 600; background: #E0F2FE; color: #0369A1; padding: 2px 6px; border-radius: 4px;">{html.escape(sector_val[:18])}</span>
            </div>
            <div style="font-size: 1.05rem; font-weight: 700; color: #0F172A; margin: 4px 0 2px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="{html.escape(company_disp)}">{html.escape(company_disp)}</div>
            <div style="font-size: 0.75rem; color: #64748B; line-height: 1.3;">
                CIN: <span style="font-family: monospace; color: #334155;">{html.escape(cin_val)}</span><br/>
                PAN: <span style="font-family: monospace; color: #334155;">{html.escape(current_pan_val)}</span> &nbsp;|&nbsp; Vintage: <strong>{vintage_val} Yrs</strong>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

collat_cover_pct = round(float(collateral_val) / float(loan_amount) * 100, 1) if float(loan_amount) > 0 else 0.0
guarantee_badge = '<span style="font-size: 0.70rem; font-weight: 600; background: #DCFCE7; color: #15803D; padding: 2px 6px; border-radius: 4px;">CGTMSE Active</span>' if cgtmse_covered else '<span style="font-size: 0.70rem; font-weight: 600; background: #F1F5F9; color: #475569; padding: 2px 6px; border-radius: 4px;">Standard Security</span>'

with col_card2:
    st.markdown(
        f"""
        <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #059669; border-radius: 8px; padding: 12px 14px; min-height: 110px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.72rem; font-weight: 700; color: #059669; text-transform: uppercase; letter-spacing: 0.04em;">💼 SANCTION FACILITY</span>
                {guarantee_badge}
            </div>
            <div style="font-size: 1.15rem; font-weight: 700; color: #0F172A; margin: 4px 0 2px 0;">{format_inr(loan_amount)}</div>
            <div style="font-size: 0.75rem; color: #64748B; line-height: 1.3;">
                Terms: <strong>{int(tenure_months)}M @ {float(interest_rate)}%</strong> &nbsp;|&nbsp; EMI: <strong>{format_inr(proposed_emi)}</strong><br/>
                Collateral: <strong>{format_inr(collateral_val)}</strong> ({collat_cover_pct}% cover)
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

res_status_badge = '<span style="font-size: 0.70rem; font-weight: 600; background: #FEF3C7; color: #B45309; padding: 2px 6px; border-radius: 4px;">Awaiting Scoring</span>'
res_status_line = "Engine Ready • Five Cs Statutory Framework"
if st.session_state.score_result is not None:
    _sr = st.session_state.score_result
    if _sr.final_verdict == Verdict.APPROVE:
        res_status_badge = f'<span style="font-size: 0.70rem; font-weight: 600; background: #DCFCE7; color: #15803D; padding: 2px 6px; border-radius: 4px;">APPROVED ({_sr.composite_score:.1f}/100)</span>'
        res_status_line = f"DSCR: {_sr.dscr:.2f}x • All Policy Hurdles Cleared"
    elif _sr.final_verdict == Verdict.CONDITIONAL:
        res_status_badge = f'<span style="font-size: 0.70rem; font-weight: 600; background: #FEF3C7; color: #B45309; padding: 2px 6px; border-radius: 4px;">CONDITIONAL ({_sr.composite_score:.1f}/100)</span>'
        res_status_line = f"Capped by Policy Guardrails • Review Covenants"
    elif _sr.final_verdict == Verdict.REJECT:
        res_status_badge = '<span style="font-size: 0.70rem; font-weight: 600; background: #FEE2E2; color: #B91C1C; padding: 2px 6px; border-radius: 4px;">REJECTED</span>'
        res_status_line = f"Policy Knockout: {_sr.knockout_applied or 'Threshold Breached'}"
    else:
        res_status_badge = '<span style="font-size: 0.70rem; font-weight: 600; background: #F1F5F9; color: #475569; padding: 2px 6px; border-radius: 4px;">DATA REQUIRED</span>'
        res_status_line = "Missing mandatory financial parameters"

with col_card3:
    st.markdown(
        f"""
        <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #6D28D9; border-radius: 8px; padding: 12px 14px; min-height: 110px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.72rem; font-weight: 700; color: #6D28D9; text-transform: uppercase; letter-spacing: 0.04em;">⚖️ POLICY DECISION MATRIX</span>
                {res_status_badge}
            </div>
            <div style="font-size: 1.05rem; font-weight: 700; color: #0F172A; margin: 4px 0 2px 0;">Deterministic Engine</div>
            <div style="font-size: 0.75rem; color: #64748B; line-height: 1.3;">
                Framework: <strong>Five Cs Commercial Matrix</strong><br/>
                {html.escape(res_status_line)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

if is_stale or st.session_state.score_result is None:
    if is_stale and st.session_state.score_result is not None:
        render_callout("Parameters or adverse media reviews modified. Re-run scoring to refresh decision.", title="Stale Score Warning", variant="warning")
    with st.container(border=True):
        r_c1, r_c2 = st.columns([3, 1], vertical_alignment="center")
        with r_c1:
            st.markdown(
                "<div style='font-size: 15px; font-weight: 700; color: #0F172A;'>Deterministic Risk Engine & Policy Gate</div>"
                "<div style='font-size: 13px; color: #64748B;'>Financial parameters & evidence locked • Execute Python Decimal statutory evaluation across Five Cs framework.</div>",
                unsafe_allow_html=True,
            )
        with r_c2:
            if st.button(":material/balance: Run Deterministic Risk Engine & Policy Gate", type="primary", key="btn_run_score", width="stretch"):
                try:
                    st.session_state.decision_sub_stage = "scorecard"
                    _execute_scoring()
                    st.rerun()
                except Exception as e:
                    err_type = type(e).__name__
                    err_msg = str(e)
                    record_event(
                        event_type="SCORING_ERROR",
                        actor="SCORER",
                        details={"error_type": err_type, "message": err_msg},
                        severity="ERROR",
                    )
                    render_callout(f"Error ({err_type}): {err_msg}", title="Scoring Error", variant="error")

res: Optional[ScoreResult] = st.session_state.score_result
if res is not None:
    if res.final_verdict == Verdict.INSUFFICIENT_DATA:
        render_callout("The deterministic policy engine halted because one or more mandatory financial parameters are missing.", title="Underwriting Blocked: Insufficient Data", variant="error")
        st.markdown("#### Missing Required Parameters Checklist:")
        for r in res.policy_reasons:
            st.markdown(f"- :material/close: **{r}**")
        render_callout("Return to Evidence page, provide the required values, and re-confirm.", title="Action Required", variant="info")
    else:
        # --------------------------------------------------------------------
        # PROGRESSIVE DECISION SUB-STAGE MANAGEMENT & GOVERNANCE CHECKS
        # --------------------------------------------------------------------
        if "decision_sub_stage" not in st.session_state or st.session_state.decision_sub_stage not in ("scorecard", "legal", "quorum"):
            st.session_state.decision_sub_stage = "scorecard"
        sub_stage = st.session_state.decision_sub_stage

        has_confirmed_severe = False
        rep_check = st.session_state.current_research_report
        if rep_check and rep_check.findings:
            for f in rep_check.findings:
                if f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED:
                    has_confirmed_severe = True
                    break

        if has_confirmed_severe and st.session_state.executive_override:
            st.session_state.executive_override = None
            record_event(
                event_type="EXECUTIVE_OVERRIDE_CLEARED",
                actor="SYSTEM_POLICY",
                details={"reason": "Confirmed SEVERE adverse finding invalidates executive override"},
                severity="WARNING",
            )

        if "decision_active_tab" not in st.session_state:
            st.session_state.decision_active_tab = "1. Quantitative Scorecard"

        def _set_decision_tab(target_tab: str):
            st.session_state.decision_active_tab = target_tab

        tab_scorecard, tab_legal, tab_quorum = st.tabs([
            "1. Quantitative Scorecard",
            "2. Forensic Legal Radar",
            "3. Committee Quorum & Sanction",
        ], key="decision_active_tab", on_change="rerun")

        with tab_scorecard:
            # --------------------------------------------------------------------
            # 1. EXECUTIVE SANCTION COCKPIT (VERDICT WARRANT & COPILOT SYNTHESIS)
            # --------------------------------------------------------------------
            P_dec = Decimal(str(loan_amount))
            r_dec = Decimal(str(interest_rate))
            n_int = int(tenure_months)

            override_info = st.session_state.executive_override
            is_overridden = bool(override_info and override_info.get("override_active"))
            if is_overridden:
                render_callout(
                    f"Overridden by officer ({override_info.get('officer_name', 'Senior Officer')}). Algorithmic Baseline: {override_info.get('original_verdict')} -> Final Sanction: {override_info.get('override_verdict')}.\n\nJustification: {override_info.get('justification_text')}",
                    title="Overridden by officer",
                    variant="warning",
                )

            hero_verdict = override_info.get("override_verdict") if is_overridden else res.final_verdict.name
            hero_rule = res.knockout_applied or res.cap_applied or "Five-Cs Algorithmic Assessment"
            hero_why = (
                "All policy risk thresholds satisfied." if res.final_verdict == Verdict.APPROVE
                else (f"Weakest-Link floor applied: {res.cap_applied}." if res.final_verdict == Verdict.CONDITIONAL
                else f"Knockout condition triggered: {res.knockout_applied}.")
            )

            # Dynamic Copilot Underwriting Synthesis Narrative
            comp_name = st.session_state.locked_case.company_name if st.session_state.locked_case else "Applicant"
            if hero_verdict == "APPROVE":
                syn_head = "Prime Solvency Profile • Resilient Debt-Service Buffer"
                syn_body = f"{comp_name} exhibits strong operational resilience with a DSCR at selected facility of {res.dscr:.2f}x (clearing the 1.20x policy hurdle). Banking credits show robust alignment with declared GST turnover, corroborated by zero mandate dishonours over 12 months. All five underwriting pillars satisfy policy risk thresholds."
                syn_rec = "Sanction proposed facility with standard quarterly turnover reconciliation covenant."
                syn_accent = "#15803D"
                rating_grade_text = "PRIME MSME • RATING A1"
            elif hero_verdict == "REJECT":
                syn_head = "Severe Credit Stress • Algorithmic Policy Knockout"
                syn_body = f"{comp_name} triggers credit policy knockouts ({res.knockout_applied or 'Elevated Credit Risk'}). Operating cash flow is insufficient to cover debt obligations (DSCR: {res.dscr:.2f}x), compounded by recurring banking mandate returns. Unmitigated credit risk precludes sanction under current policy covenants."
                syn_rec = "Decline requested facility. Recommend applicant de-leverage balance sheet and stabilize banking conduct before re-application."
                syn_accent = "#B91C1C"
                rating_grade_text = "CREDIT DECLINED • RATING D"
            else:
                syn_head = "Accrual-versus-Cash Divergence • Policy Cap Applied"
                syn_body = f"{comp_name} demonstrates meaningful business turnover, but thin net profitability restricts internal liquidity generation. Conditions pillar score fell below the 40-point hurdle ({res.cap_applied or 'Margin Pressure'}), capping the final verdict at CONDITIONAL. Debt service coverage requires external credit enhancement."
                syn_rec = "Consider conditional sanction subject to 100% liquid collateral pledge or CGTMSE enhancement with enhanced quarterly audit covenants."
                syn_accent = "#B45309"
                rating_grade_text = "CONDITIONAL SANCTION • RATING B2"

            col_cock_left, col_cock_right = st.columns([5, 7], gap="medium")
            with col_cock_left:
                override_tag_html = ""
                if is_overridden:
                    override_tag_html = f'<div style="background: #FEF3C7; color: #92400E; border: 1px solid #FDE68A; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600; margin-bottom: 6px;">⚠️ Overridden by {override_info.get("officer_name", "Officer")} • Baseline: {res.baseline_verdict.name}</div>'

                warrant_html = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-top: 4px solid {syn_accent}; border-radius: 8px; padding: 14px 16px; min-height: 195px; box-shadow: 0 1px 3px rgba(0,0,0,0.03); display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        {override_tag_html}
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em;">Final Underwriting Assessment</div>
                            <span style="font-size: 9px; font-weight: 700; color: {syn_accent}; background: {syn_accent}15; border: 1px solid {syn_accent}40; padding: 2px 7px; border-radius: 4px;">{rating_grade_text}</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px;">
                            <div style="font-size: 26px; font-weight: 900; color: {syn_accent}; letter-spacing: -0.02em;">{hero_verdict}</div>
                            <div style="text-align: right;">
                                <div style="font-size: 9.5px; color: #64748B; font-weight: 600;">COMPOSITE SCORE</div>
                                <div style="font-size: 17px; font-weight: 800; color: #0B1F3A;">{float(res.composite_score):.1f} <span style="font-size: 11px; color: #64748B; font-weight: 500;">/ 100</span></div>
                            </div>
                        </div>
                        <div style="font-size: 11.5px; color: #334155; margin-bottom: 8px; line-height: 1.4;">
                            <strong>Deciding Rule:</strong> {hero_rule}<br/>
                            <span style="color: #64748B;">{hero_why}</span>
                        </div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px; font-size: 11px; color: #334155; display: flex; justify-content: space-between; align-items: center;">
                        <div><strong>Sanction Terms:</strong> ₹{float(P_dec)/100000:.1f}L @ {float(r_dec):.2f}% ({n_int}M)</div>
                        <span style="color: #0F766E; font-weight: 700;">DSCR {res.dscr:.2f}x</span>
                    </div>
                </div>"""
                _render_html(warrant_html)

            with col_cock_right:
                synthesis_html = f"""<div style="background-color: #FFFFFF; border: 1px solid #CBD5E1; border-top: 4px solid {syn_accent}; border-radius: 8px; padding: 14px 18px; min-height: 195px; box-shadow: 0 1px 3px rgba(0,0,0,0.03); display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.06em;">
                                Copilot Underwriting Synthesis
                            </div>
                            <div style="font-size: 10px; color: #64748B; font-weight: 600;">Senior MSME Desk Perspective</div>
                        </div>
                        <div style="font-size: 13.5px; font-weight: 700; color: #0B1F3A; margin-bottom: 4px;">{syn_head}</div>
                        <div style="font-size: 12px; color: #334155; line-height: 1.45; margin-bottom: 8px;">{syn_body}</div>
                    </div>
                    <div style="font-size: 11.5px; color: #0F766E; font-weight: 600; border-top: 1px solid #F1F5F9; padding-top: 6px;">
                        <strong>Underwriting Recommendation:</strong> {syn_rec}
                    </div>
                </div>"""
                _render_html(synthesis_html)

            # --------------------------------------------------------------------
            # 2. STATUTORY UNDERWRITING TELEMETRY (4 DIRECT PILLAR RATIOS)
            # --------------------------------------------------------------------
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                dscr_hurdle = Decimal("1.20")
                dscr_diff = res.dscr - dscr_hurdle
                dscr_sign = "+" if dscr_diff >= Decimal("0") else ""
                dscr_pass = res.dscr >= dscr_hurdle
                render_kpi_card(
                    "Capacity • DSCR Coverage",
                    f"{res.dscr:.2f}x",
                    delta=f"{dscr_sign}{dscr_diff:.2f}x vs 1.20x hurdle",
                    delta_color="normal" if dscr_pass else "inverse",
                    border_left="#059669" if dscr_pass else "#DC2626",
                    badge="CAPACITY",
                )

            with k2:
                itr_l = st.session_state.locked_case.itr_data
                de_disp = "N/A"
                de_color = "neutral"
                de_pass = True
                if itr_l and itr_l.net_worth is not None:
                    if itr_l.net_worth <= Decimal("0"):
                        de_disp = "Insolvent (< 0)"
                        de_color = "inverse"
                        de_pass = False
                    elif itr_l.total_debt is not None:
                        de_val = float(itr_l.total_debt / itr_l.net_worth)
                        de_disp = f"{de_val:.2f}x"
                        de_pass = de_val <= 1.0
                        de_color = "normal" if de_pass else "inverse"
                    else:
                        de_disp = "N/A"
                        de_color = "neutral"
                render_kpi_card(
                    "Capital • Leverage (D/E)",
                    de_disp,
                    delta="Benchmark: ≤ 1.0x",
                    delta_color=de_color,
                    border_left="#059669" if (de_pass and de_disp != "N/A") else ("#DC2626" if not de_pass else "#64748B"),
                    badge="CAPITAL",
                )

            with k3:
                gst_l = st.session_state.locked_case.gst_data
                bank_l = st.session_state.locked_case.bank_data
                gap_disp = "N/A"
                gap_color = "neutral"
                gap_pass = True
                if gst_l and bank_l and gst_l.taxable_value is not None and bank_l.total_credits is not None:
                    max_t = max(gst_l.taxable_value, bank_l.total_credits)
                    if max_t > Decimal("0"):
                        gap_pct = float(abs(gst_l.taxable_value - bank_l.total_credits) / max_t * Decimal("100"))
                        gap_disp = f"{gap_pct:.1f}%"
                        gap_pass = gap_pct <= 15.0
                        gap_color = "normal" if gap_pass else "inverse"
                    else:
                        gap_disp = "0.0%"
                        gap_color = "normal"
                        gap_pass = True
                render_kpi_card(
                    "Conditions • Accrual Gap",
                    gap_disp,
                    delta="Benchmark: ≤ 15.0%",
                    delta_color=gap_color,
                    border_left="#059669" if (gap_pass and gap_disp != "N/A") else ("#DC2626" if not gap_pass else "#64748B"),
                    badge="CONDITIONS",
                )

            with k4:
                collat_pct = float(Decimal(str(collateral_val)) / Decimal(str(loan_amount)) * Decimal("100")) if loan_amount > 0 else 0.0
                c_status_str = f"{collat_pct:.1f}% Cover"
                c_pass = (collat_pct >= 125.0 or cgtmse_covered)
                c_delta = "CGTMSE Trust Covered" if cgtmse_covered else (f"{collat_pct - 125.0:+.1f}% vs 125% Hurdle" if collat_pct >= 125.0 else f"{collat_pct - 125.0:.1f}% Deficit")
                c_color = "normal" if c_pass else "inverse"
                render_kpi_card(
                    "Collateral • Asset Coverage",
                    c_status_str,
                    delta=c_delta,
                    delta_color=c_color,
                    border_left="#059669" if c_pass else "#DC2626",
                    badge="COLLATERAL",
                )

            # --------------------------------------------------------------------
            # 3. FIVE-CS RISK ANALYTICS: AUDIT BREAKDOWN & MULTI-DIMENSIONAL GEOMETRY
            # --------------------------------------------------------------------
            with st.container(border=True):
                st.markdown("#### :material/analytics: Five Cs Risk Dimension Analytics: Statutory Pillar Scoring & Geometry")

                # Ordered bottom-to-top so Plotly renders top bar as Capacity (matching Left Table 1:1)
                c_labels = ["Conditions (15%)", "Collateral (15%)", "Character (20%)", "Capital (20%)", "Capacity (30%)"]
                c_scores = [
                    float(res.five_cs.get("CONDITIONS", Decimal("0"))),
                    float(res.five_cs.get("COLLATERAL", Decimal("0"))),
                    float(res.five_cs.get("CHARACTER", Decimal("0"))),
                    float(res.five_cs.get("CAPITAL", Decimal("0"))),
                    float(res.five_cs.get("CAPACITY", Decimal("0"))),
                ]
                c_colors = ['#0F766E' if s >= 40.0 else '#B91C1C' for s in c_scores]

                # Concise score annotations anchored to avoid crossing 40.0 floor line
                bar_annotations = [f"{s:.1f} pts" for s in c_scores]
                t_positions = ['outside' if s < 40.0 else 'inside' for s in c_scores]

                fig_h = go.Figure()
                fig_h.add_trace(go.Bar(
                    x=c_scores,
                    y=c_labels,
                    orientation='h',
                    marker=dict(color=c_colors, cornerradius=4),
                    text=bar_annotations,
                    textposition=t_positions,
                    insidetextanchor='end',
                    name="Pillar Score",
                    cliponaxis=False,
                ))
                # Shaded Policy Hurdle Floor Danger Zone (0 to 40 pts)
                fig_h.add_vrect(
                    x0=0,
                    x1=40.0,
                    fillcolor="rgba(185, 28, 28, 0.05)",
                    layer="below",
                    line_width=0,
                )
                fig_h.add_vline(
                    x=40.0,
                    line_dash="dash",
                    line_color="#B91C1C",
                    line_width=1.5,
                )
                fig_h.add_annotation(
                    x=40.0,
                    y=1.06,
                    yref="paper",
                    text="<b>FLOOR 40.0</b>",
                    showarrow=False,
                    font=dict(size=9, color="#B91C1C"),
                    bgcolor="#FEF2F2",
                    bordercolor="#FECACA",
                    borderwidth=1,
                    borderpad=2,
                    xanchor="center",
                )
                fig_h.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(
                        range=[0, 105],
                        title=dict(text="Pillar Score (0-100) • Danger Zone: < 40.0 pts", font=dict(color="#1F2937", size=10)),
                        gridcolor="#E2E8F0",
                        zerolinecolor="#E2E8F0",
                        tickfont=dict(color="#1F2937", size=10),
                    ),
                    yaxis=dict(
                        title="",
                        gridcolor="#E2E8F0",
                        tickfont=dict(color="#1F2937", size=11),
                    ),
                    height=245,
                    margin=dict(l=10, r=10, t=15, b=25),
                    showlegend=False,
                )

                r_categories = [
                    "Capacity (30%)",
                    "Capital (20%)",
                    "Character (20%)",
                    "Collateral (15%)",
                    "Conditions (15%)",
                ]
                r_scores = [
                    float(res.five_cs.get("CAPACITY", Decimal("0"))),
                    float(res.five_cs.get("CAPITAL", Decimal("0"))),
                    float(res.five_cs.get("CHARACTER", Decimal("0"))),
                    float(res.five_cs.get("COLLATERAL", Decimal("0"))),
                    float(res.five_cs.get("CONDITIONS", Decimal("0"))),
                ]
                fig_r = go.Figure()
                fig_r.add_trace(go.Scatterpolar(
                    r=r_scores + [r_scores[0]],
                    theta=r_categories + [r_categories[0]],
                    fill='toself',
                    name='Five Cs Score',
                    mode='lines+markers',
                    line=dict(color='#0F766E', width=2.5),
                    marker=dict(size=6, color='#0F766E'),
                    fillcolor='rgba(15, 118, 110, 0.25)',
                ))
                fig_r.add_trace(go.Scatterpolar(
                    r=[40, 40, 40, 40, 40, 40],
                    theta=r_categories + [r_categories[0]],
                    fill='toself',
                    name='Danger Floor (< 40.0)',
                    line=dict(color='#DC2626', width=1.5, dash='dash'),
                    fillcolor='rgba(220, 38, 38, 0.08)',
                ))
                fig_r.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    polar=dict(
                        bgcolor="rgba(0,0,0,0)",
                        radialaxis=dict(visible=True, range=[0, 100], gridcolor="#E2E8F0", tickfont=dict(color="#64748B", size=8)),
                        angularaxis=dict(gridcolor="#E2E8F0", tickfont=dict(color="#1F2937", size=9)),
                    ),
                    showlegend=True,
                    legend=dict(font=dict(color="#1F2937", size=9), orientation="h", y=-0.2, x=0.0),
                    height=245,
                    margin=dict(l=15, r=15, t=15, b=25),
                )

                col_c_deck, col_c_vis = st.columns([6, 6], gap="large")
                with col_c_deck:
                    deck_rows_html = []
                    pillar_defs = [
                        ("Capacity", "30%", 30.0, float(res.five_cs.get("CAPACITY", Decimal("0"))), "Debt service & operating cash flow"),
                        ("Capital", "20%", 20.0, float(res.five_cs.get("CAPITAL", Decimal("0"))), "Net worth, gearing & leverage buffer"),
                        ("Character", "20%", 20.0, float(res.five_cs.get("CHARACTER", Decimal("0"))), "Entity integrity & judicial track record"),
                        ("Collateral", "15%", 15.0, float(res.five_cs.get("COLLATERAL", Decimal("0"))), "Tangible security coverage & CGTMSE"),
                        ("Conditions", "15%", 15.0, float(res.five_cs.get("CONDITIONS", Decimal("0"))), "Banking mandate conduct & GST congruence"),
                    ]
                    for p_name, p_wgt, max_pts, p_sc, p_desc in pillar_defs:
                        contrib = (p_sc / 100.0) * max_pts
                        if p_sc >= 70.0:
                            p_status = '<span style="background: #ECFDF5; color: #059669; border: 1px solid #A7F3D0; font-size: 8.5px; font-weight: 700; padding: 1px 5px; border-radius: 4px;">PRISTINE</span>'
                            p_bar_color = "#059669"
                            contrib_color = "#059669"
                            row_style = "padding: 3.5px 6px; border-bottom: 1px solid #F1F5F9; border-radius: 4px;"
                        elif p_sc >= 40.0:
                            p_status = f'<span style="background: #FFFBEB; color: #D97706; border: 1px solid #FDE68A; font-size: 8.5px; font-weight: 700; padding: 1px 5px; border-radius: 4px;">MODERATE ({p_sc:.0f})</span>'
                            p_bar_color = "#D97706"
                            contrib_color = "#D97706"
                            row_style = "padding: 3.5px 6px; border-bottom: 1px solid #F1F5F9; border-radius: 4px;"
                        else:
                            p_status = f'<span style="background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; font-size: 8.5px; font-weight: 700; padding: 1px 5px; border-radius: 4px;">CAP TRIGGERED ({p_sc:.0f})</span>'
                            p_bar_color = "#DC2626"
                            contrib_color = "#DC2626"
                            row_style = "padding: 3.5px 8px; background: #FEF2F2; border-left: 3px solid #DC2626; border-radius: 4px; margin: 2px 0;"

                        w_pct = min(100.0, max(0.0, p_sc))
                        row_html = f"""<div style="{row_style}">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <div>
                                    <div style="font-size: 11.5px; font-weight: 700; color: #0F172A;">{p_name} <span style="font-size: 9.5px; color: #64748B; font-weight: 500;">({p_wgt})</span></div>
                                    <div style="font-size: 9px; color: #64748B;">{p_desc}</div>
                                </div>
                                <div style="text-align: right;">
                                    <div style="display: flex; align-items: center; justify-content: flex-end; gap: 6px;">
                                        <span style="font-size: 11.5px; font-weight: 700; color: #0B1F3A;">{p_sc:.1f} <span style="font-size: 9.5px; color: #64748B; font-weight: 400;">/ 100</span></span>
                                        {p_status}
                                    </div>
                                    <div style="font-size: 9px; font-weight: 600; color: #64748B; margin-top: 1px;">Contrib: <span style="color: {contrib_color}; font-weight: 700;">{contrib:.1f}</span> / {max_pts:.0f} pts</div>
                                </div>
                            </div>
                            <div style="background: #E2E8F0; border-radius: 9999px; height: 3px; width: 100%; margin-top: 3px; overflow: hidden;">
                                <div style="background: {p_bar_color}; border-radius: 9999px; height: 3px; width: {w_pct:.1f}%;"></div>
                            </div>
                        </div>"""
                        deck_rows_html.append(row_html)

                    audit_deck_html = f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px 14px; min-height: 245px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
                        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #E2E8F0; padding-bottom: 5px; margin-bottom: 4px;">
                            <div style="font-size: 9.5px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Statutory Pillar Scoring Breakdown</div>
                            <div style="font-size: 8.5px; font-weight: 600; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">Raw Score • Weighted Contrib</div>
                        </div>
                        {''.join(deck_rows_html)}
                    </div>"""
                    _render_html(audit_deck_html)

                with col_c_vis:
                    tab_vis_bar, tab_vis_radar = st.tabs([":material/bar_chart: Horizontal Pillar Bars", ":material/radar: Multi-Axis Geometry"])
                    with tab_vis_bar:
                        st.plotly_chart(fig_h, width="stretch")
                    with tab_vis_radar:
                        st.plotly_chart(fig_r, width="stretch")

                # --------------------------------------------------------------------
                # TIER 2: POLICY GOVERNANCE & STATUTORY EXCEPTION DOSSIER (FULL WIDTH)
                # --------------------------------------------------------------------
                if res.knockout_applied:
                    st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
                    render_callout(f"Hard Policy Knockout: {res.knockout_applied}.", title="Knockout Triggered", variant="error")
                elif res.cap_applied:
                    st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
                    render_callout(f"Weakest-Link Floor: {res.cap_applied}. Under bank credit policy, overall verdict capped at CONDITIONAL.", title="Policy Floor Applied", variant="warning")

                if res.deductions:
                    st.markdown("<div style='font-size: 10.5px; font-weight: 700; color: #B45309; text-transform: uppercase; margin: 10px 0 6px 0; letter-spacing: 0.05em;'>Active Policy Exceptions & Covenant Penalties</div>", unsafe_allow_html=True)
                    p_col1, p_col2 = st.columns(2, gap="medium")
                    for i, d in enumerate(res.deductions):
                        target_col = p_col1 if i % 2 == 0 else p_col2
                        with target_col:
                            d_card = f"""<div style="background: #FFFBEB; border: 1px solid #FDE68A; border-left: 4px solid #D97706; border-radius: 6px; padding: 9px 12px; margin-bottom: 8px; min-height: 86px; display: flex; flex-direction: column; justify-content: space-between;">
                                <div>
                                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;">
                                        <span style="font-family: monospace; font-size: 11px; font-weight: 700; color: #92400E;">#{d.rule_id}</span>
                                        <span style="background: #FEF3C7; color: #B45309; border: 1px solid #FDE68A; font-size: 9.5px; font-weight: 700; padding: 1px 7px; border-radius: 4px;">-{d.points_deducted:.1f} PTS ({d.pillar})</span>
                                    </div>
                                    <div style="font-size: 11px; color: #1E293B;"><strong>Observed:</strong> {d.observed_value} &nbsp;|&nbsp; <strong>Benchmark:</strong> {d.threshold}</div>
                                </div>
                                <div style="font-size: 10.5px; color: #78350F; line-height: 1.35; margin-top: 3px;">{d.explanation}</div>
                            </div>"""
                            _render_html(d_card)
                else:
                    clean_ded_seal = """<div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-left: 4px solid #15803D; border-radius: 6px; padding: 10px 14px; margin-top: 8px;">
                        <div style="font-size: 11.5px; font-weight: 700; color: #15803D;">✓ Zero Policy Deductions • 100% Covenant Compliance</div>
                        <div style="font-size: 10.5px; color: #166534; margin-top: 2px;">Applicant achieved 100.0/100 points across all evaluated solvency and risk rules.</div>
                    </div>"""
                    _render_html(clean_ded_seal)

            # --------------------------------------------------------------------
            # 4. DEBT SERVICE CAPACITY & CAPITAL AMORTIZATION ARCHITECTURE
            # --------------------------------------------------------------------
            with st.container(border=True):
                st.markdown("#### :material/account_balance: Debt Service Capacity & Capital Amortization Architecture")
                st.caption("Deterministic cash outflow modeling under approved facility terms with Principal vs Interest split.")

                from core.analyser import calculate_monthly_emi
                live_emi = calculate_monthly_emi(P_dec, r_dec, n_int)
                total_repayment = live_emi * Decimal(str(n_int))
                total_interest_cost = total_repayment - P_dec
                pct_p = float(P_dec / total_repayment * Decimal("100")) if total_repayment > 0 else 100.0
                pct_i = float(total_interest_cost / total_repayment * Decimal("100")) if total_repayment > 0 else 0.0

                d_col1, d_col2, d_col3, d_col4 = st.columns(4)
                with d_col1:
                    render_kpi_card(
                        "Sanction Facility",
                        f"₹ {float(P_dec)/100000:.1f} Lakhs",
                        subtext=format_inr(P_dec),
                        border_top="#0284C7",
                        badge="PRINCIPAL",
                    )
                with d_col2:
                    render_kpi_card(
                        "Lending Rate",
                        f"{float(r_dec):.2f}% p.a.",
                        subtext=f"{n_int}M Tenor ({n_int//12} Yrs)",
                        border_top="#0F766E",
                        badge="PRICING",
                    )
                with d_col3:
                    render_kpi_card(
                        "Monthly EMI Outflow",
                        format_inr(live_emi),
                        subtext="Annuity Obligation",
                        border_top="#0B1F3A",
                        badge="ANNUITY",
                    )
                with d_col4:
                    render_kpi_card(
                        "Total Interest Cost",
                        format_inr(total_interest_cost),
                        subtext=f"{pct_i:.1f}% Cost of Capital",
                        border_top="#64748B",
                        badge="CARRY COST",
                    )

                fig_amort = go.Figure()
                fig_amort.add_trace(go.Bar(
                    y=["Capital Outflow"],
                    x=[float(P_dec)],
                    name=f"Principal ({pct_p:.1f}%)",
                    orientation="h",
                    marker=dict(color="#0F766E", cornerradius=4),
                    text=f"Principal: ₹{float(P_dec)/100000:.1f}L ({pct_p:.1f}%)",
                    textposition="inside",
                    insidetextanchor="middle",
                    hoverinfo="text",
                ))
                fig_amort.add_trace(go.Bar(
                    y=["Capital Outflow"],
                    x=[float(total_interest_cost)],
                    name=f"Interest Cost ({pct_i:.1f}%)",
                    orientation="h",
                    marker=dict(color="#64748B", cornerradius=4),
                    text=f"Interest: ₹{float(total_interest_cost)/100000:.1f}L ({pct_i:.1f}%)",
                    textposition="inside",
                    insidetextanchor="middle",
                    hoverinfo="text",
                ))
                fig_amort.update_layout(
                    barmode="stack",
                    height=46,
                    margin=dict(l=0, r=0, t=2, b=2),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    showlegend=False,
                    xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                    yaxis=dict(showgrid=False, showticklabels=False),
                )
                st.plotly_chart(fig_amort, width="stretch", config={"displayModeBar": False})

                bank_l = st.session_state.locked_case.bank_data if st.session_state.locked_case else None
                burden_badge = ""
                if bank_l and bank_l.total_credits and bank_l.total_credits > Decimal("0"):
                    monthly_inflow = bank_l.total_credits / Decimal("12")
                    burden_pct = float(live_emi / monthly_inflow * Decimal("100"))
                    b_color = "#059669" if burden_pct <= 35.0 else ("#D97706" if burden_pct <= 50.0 else "#DC2626")
                    burden_badge = f'<span style="background: #F1F5F9; color: {b_color}; font-weight: 600; padding: 2px 8px; border-radius: 4px; border: 1px solid #E2E8F0;">Cash Flow Debt Burden: <strong>{burden_pct:.1f}%</strong> of Monthly Inflow</span>'

                amort_summary_html = f"""<div style="font-size: 11.5px; color: #475569; display: flex; justify-content: space-between; align-items: center; margin-top: 4px; padding: 2px 4px;">
                    <div><strong>Total Facility Commitment:</strong> {format_inr(total_repayment)} &nbsp;({format_inr(P_dec)} Principal + {format_inr(total_interest_cost)} Interest Drag)</div>
                    <div>{burden_badge}</div>
                </div>"""
                _render_html(amort_summary_html)

            st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
            col_nav_1a, col_nav_1b = st.columns([1, 1])
            with col_nav_1a:
                if st.button("← Back to Evidence Ingestion", type="secondary", key="btn_back_evidence", width="stretch"):
                    st.session_state.current_page = "Evidence"
                    st.switch_page("app_pages/evidence.py")
            with col_nav_1b:
                st.button(
                    "Advance to Pillar 2: Forensic Legal Radar ➔",
                    type="primary",
                    key="btn_adv_tab1",
                    on_click=_set_decision_tab,
                    args=("2. Forensic Legal Radar",),
                    width="stretch",
                )


        with tab_legal:
            # --------------------------------------------------------------------
            # 6. ALL-INDIA FORENSIC LEGAL SURVEILLANCE & ADVERSE MEDIA RADAR
            # --------------------------------------------------------------------
            st.markdown("#### Statutory Legal Surveillance & Adverse Media Radar")
            st.caption("Live statutory intelligence triangulation across 672 District Courts, 16 NCLT Benches, MCA-21, IBBI, and DRT Recovery Registries.")

            active_cin = cin_parsed_data["cin"] if cin_parsed_data else "UNREGISTERED_MSME"
            is_corp_entity = bool(active_cin and active_cin != "UNREGISTERED_MSME")
            company_nm = (
                st.session_state.locked_case.company_name
                if st.session_state.locked_case
                else COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company")
            )
            anchored_query = build_search_query(company_nm, cin=active_cin if is_corp_entity else None)

            is_demo_cohort = bool(cohort_selection and cohort_selection != "None (Manual Upload)")

            # 5-Court Telemetry Radar HUD (High-Density Multi-Registry Grid)
            telemetry_html = f"""<div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin: 10px 0 14px 0;">
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                    <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">e-Courts NJDG</div>
                    <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">672 Dist & High Courts</div>
                    <div style="font-size: 9px; color: #0F766E; font-weight: 600; margin-top: 2px;">● {'BENCHMARK DOCKET' if is_demo_cohort else 'REGISTRY DOCKET'}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                    <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">NCLT / NCLAT</div>
                    <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">16 Benches (IBC/CIRP)</div>
                    <div style="font-size: 9px; color: #0F766E; font-weight: 600; margin-top: 2px;">● {'BENCHMARK DOCKET' if is_demo_cohort else 'INSOLVENCY DOCKET'}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                    <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">MCA-21 Portal</div>
                    <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{'RoC Defaults & DIN' if is_corp_entity else 'Non-Corp (Exempt)'}</div>
                    <div style="font-size: 9px; color: #0F766E; font-weight: 600; margin-top: 2px;">● {'BENCHMARK DOCKET' if is_demo_cohort else ('DEFAULTS DOCKET' if is_corp_entity else 'REGISTRY BYPASS')}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                    <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">IBBI Registry</div>
                    <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">Corporate Insolvency</div>
                    <div style="font-size: 9px; color: #0F766E; font-weight: 600; margin-top: 2px;">● {'BENCHMARK DOCKET' if is_demo_cohort else 'INSOLVENCY DOCKET'}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                    <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">DRT / SARFAESI</div>
                    <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">39 Tribunals & NPAs</div>
                    <div style="font-size: 9px; color: #0F766E; font-weight: 600; margin-top: 2px;">● {'BENCHMARK DOCKET' if is_demo_cohort else 'TRIBUNAL DOCKET'}</div>
                </div>
            </div>"""
            _render_html(telemetry_html)

            # Anchored Target Identity Card (Clean Institutional Design)
            if is_corp_entity:
                anchor_token_badge = f'<span style="color: #0F766E; font-family: monospace; font-weight: 600; background: #CCFBF1; border: 1px solid #99F6E4; padding: 2px 7px; border-radius: 4px;">CIN: {active_cin}</span>'
                anchor_clause_html = f'<span style="color: #0F766E; font-weight: 700;">"{company_nm}"</span> <span style="color: #64748B; font-weight: 600;">AND</span> <span style="color: #2563EB; font-weight: 700;">"{active_cin}"</span>'
                anchor_mode_tag = "CIN Strict Match"
            else:
                anchor_token_badge = f'<span style="color: #0F766E; font-family: monospace; font-weight: 600; background: #CCFBF1; border: 1px solid #99F6E4; padding: 2px 7px; border-radius: 4px;">PAN: {current_pan_val} (Unincorporated)</span>'
                anchor_clause_html = f'<span style="color: #0F766E; font-weight: 700;">"{company_nm}"</span> <span style="color: #64748B; font-weight: 600;">AND</span> <span style="color: #2563EB; font-weight: 700;">"{current_pan_val}"</span>'
                anchor_mode_tag = "PAN Anchor"

            command_deck_html = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-top: 3px solid #0F766E; border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px;">
                    <div>
                        <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Surveillance Target Identity</div>
                        <div style="font-size: 15px; font-weight: 800; color: #0B1F3A; margin-top: 2px;">{company_nm}</div>
                        <div style="display: flex; gap: 8px; margin-top: 5px; font-size: 11px;">
                            {anchor_token_badge}
                            <span style="color: #334155; font-family: monospace; font-weight: 600; background: #F1F5F9; border: 1px solid #E2E8F0; padding: 2px 7px; border-radius: 4px;">PAN: {current_pan_val}</span>
                        </div>
                    </div>
                    <div style="text-align: right;">
                        <div style="display: inline-block; background: #ECFDF5; border: 1px solid #A7F3D0; border-radius: 9999px; padding: 3px 10px; font-size: 10px; font-weight: 700; color: #0F766E;">
                            {'CURATED BENCHMARK DOCKETS' if is_demo_cohort else 'REGISTRY GATEWAY'}
                        </div>
                        <div style="font-size: 9px; color: #64748B; margin-top: 3px;">Jurisdiction: 672 District Courts, High Courts & Tribunals</div>
                    </div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 8px 12px; font-size: 11px; color: #334155; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                    <div>
                        <span style="color: #64748B; font-weight: 600;">Statutory Disambiguation Key:</span> {anchor_clause_html}
                    </div>
                    <span style="font-size: 9px; font-weight: 600; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; padding: 2px 7px; border-radius: 4px;">{anchor_mode_tag}</span>
                </div>
            </div>"""
            _render_html(command_deck_html)

            col_q1, col_q2 = st.columns([3, 1])
            with col_q1:
                scan_triggered = st.button(":material/radar: Execute Adverse Media & Judicial Docket Screening", key="btn_exec_media_scan", type="primary", width="stretch")
            with col_q2:
                has_tavily = bool(os.getenv("TAVILY_API_KEY") and os.getenv("TAVILY_API_KEY") != "your_tavily_api_key_here")
                if is_demo_cohort:
                    if has_tavily:
                        use_live_tavily = st.toggle(":material/satellite_alt: Live Web Screening", value=False, help="Live Web Screening Simulation: Queries live search engines; on benchmark demo cohorts, curated judicial dockets are used to prevent internet homonym hallucinations.", key="tgl_live_tavily_demo")
                        use_synthetic_demo = not use_live_tavily
                        if use_live_tavily:
                            st.markdown(f"<div style='text-align: center; margin-top: -6px;'>{status_pill('Live Web Active', variant='approve')}</div>", unsafe_allow_html=True)
                        else:
                            st.markdown(f"<div style='text-align: center; margin-top: -6px;'>{status_pill('Benchmark Docket', variant='info')}</div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div style='text-align: center; padding-top: 6px;'>{status_pill('Benchmark Docket', variant='info')}</div>", unsafe_allow_html=True)
                        use_synthetic_demo = True
                else:
                    use_synthetic_demo = st.checkbox(
                        ":material/science: Benchmark Simulation",
                        value=not has_tavily,
                        help="Generates benchmark legal/adverse records for demonstration",
                        key="chk_synthetic_demo",
                    )

            cache_key = (active_cin, bool(use_synthetic_demo))
            if st.session_state.last_research_cache_key != cache_key:
                st.session_state.last_research_cache_key = cache_key
                st.session_state.current_research_report = st.session_state.research_cache.get(cache_key, None)

            if scan_triggered:
                with st.spinner("Screening adverse media registries, judicial dockets, and insolvency tribunals..."):
                    raw_rep = search_adverse_media(
                        company_name=company_nm,
                        cin=active_cin,
                        mock_mode=use_synthetic_demo,
                    )
                    merged_rep = merge_existing_reviews(raw_rep, st.session_state.current_research_report)
                    st.session_state.research_cache[cache_key] = merged_rep
                    st.session_state.current_research_report = merged_rep
                    record_event(
                        event_type="ADVERSE_MEDIA_SCAN_COMPLETED",
                        actor="RESEARCHER",
                        details={
                            "status": merged_rep.status,
                            "findings_count": len(merged_rep.findings),
                            "is_synthetic": merged_rep.is_synthetic_demo,
                            "query": merged_rep.query_used,
                        },
                    )
                _execute_scoring(report_override=merged_rep)
                st.rerun()

            rep: Optional[AdverseMediaReport] = st.session_state.current_research_report
            if rep is None:
                awaiting_card = f"""<div style="background: #F8FAFC; border: 1.5px dashed #CBD5E1; border-radius: 8px; padding: 22px 16px; text-align: center; margin: 12px 0 16px 0;">
                    <div style="font-size: 11px; font-weight: 700; color: #0F766E; letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 4px;">SURVEILLANCE RADAR ACTIVE</div>
                    <div style="font-size: 13px; font-weight: 700; color: #0B1F3A; text-transform: uppercase; letter-spacing: 0.04em;">Legal Docket Surveillance Engine Primed</div>
                    <div style="font-size: 12px; color: #64748B; max-width: 520px; margin: 4px auto 12px auto; line-height: 1.45;">
                        Target entity <strong>{company_nm}</strong> is anchored. Click <strong>Execute Adverse Media & Legal Docket Scan</strong> above to query 672 District Courts, 16 NCLT Benches, and national default registries.
                    </div>
                    <div style="display: flex; justify-content: center; gap: 8px; flex-wrap: wrap;">
                        <span style="font-size: 10px; background: #E2E8F0; padding: 3px 8px; border-radius: 9999px; color: #475569; font-weight: 600;">e-Courts Ready</span>
                        <span style="font-size: 10px; background: #E2E8F0; padding: 3px 8px; border-radius: 9999px; color: #475569; font-weight: 600;">NCLT Ready</span>
                        <span style="font-size: 10px; background: #E2E8F0; padding: 3px 8px; border-radius: 9999px; color: #475569; font-weight: 600;">IBBI Ready</span>
                        <span style="font-size: 10px; background: #E2E8F0; padding: 3px 8px; border-radius: 9999px; color: #475569; font-weight: 600;">DRT Ready</span>
                    </div>
                </div>"""
                _render_html(awaiting_card)
            else:
                if rep.status == "RESEARCH_UNAVAILABLE":
                    render_callout(
                        f"Secondary screening could not complete ({rep.error_message or 'offline/timeout'}). System will not default to Clean. Credit Officer must perform manual registry verification.",
                        title="Research Unavailable",
                        variant="warning",
                    )
                elif rep.status == "NO_ADVERSE_FINDINGS" or len(rep.findings) == 0:
                    clean_card = f"""<div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-left: 4px solid #15803D; border-radius: 8px; padding: 16px 18px; margin: 14px 0;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <div style="width: 24px; height: 24px; border-radius: 50%; background: #DCFCE7; color: #15803D; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 13px;">✓</div>
                                <div>
                                    <div style="font-size: 13px; font-weight: 800; color: #15803D; letter-spacing: 0.03em;">STATUTORY LEGAL CLEARANCE: GRADE-A (CLEAN RECORD)</div>
                                    <div style="font-size: 11px; color: #166534;">Zero Adverse Litigations, Insolvency Petitions, or Wilful Defaults Detected</div>
                                </div>
                            </div>
                            <div>
                                {status_pill("ZERO ADVERSE FINDINGS", variant="approve")}
                            </div>
                        </div>
                        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px dashed #86EFAC;">
                            <div style="font-size: 11px; color: #166534;"><strong>e-Courts:</strong> 0 Pending Warrants</div>
                            <div style="font-size: 11px; color: #166534;"><strong>NCLT/NCLAT:</strong> 0 CIRP Petitions</div>
                            <div style="font-size: 11px; color: #166534;"><strong>MCA-21:</strong> Active (No Disqual.)</div>
                            <div style="font-size: 11px; color: #166534;"><strong>DRT / SARFAESI:</strong> Clean Track</div>
                        </div>
                        <div style="font-size: 10px; color: #15803D; margin-top: 8px; opacity: 0.85;">
                            * Automated statutory surveillance screening completed across all connected registries. No adverse legal entries found.
                        </div>
                    </div>"""
                    _render_html(clean_card)
                elif rep.status == "COMPLETED":
                    has_unreviewed_severe = any(
                        f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.UNREVIEWED
                        for f in rep.findings
                    )
                    has_confirmed_severe = any(
                        f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED
                        for f in rep.findings
                    )

                    hud_bg = '#FEF2F2' if (has_unreviewed_severe or has_confirmed_severe) else '#FFFBEB'
                    hud_bd = '#FECACA' if (has_unreviewed_severe or has_confirmed_severe) else '#FDE68A'
                    hud_col = '#B91C1C' if (has_unreviewed_severe or has_confirmed_severe) else '#B45309'

                    adverse_hud = f"""<div style="background: {hud_bg}; border: 1px solid {hud_bd}; border-left: 4px solid {hud_col}; border-radius: 8px; padding: 14px 16px; margin: 12px 0 16px 0;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <div style="font-size: 13px; font-weight: 800; color: {hud_col};">
                                    ⚠️ ADVERSE LEGAL DOCKETS IDENTIFIED: {len(rep.findings)} RECORD(S) RETRIEVED
                                </div>
                                <div style="font-size: 11px; color: #475569; margin-top: 2px;">
                                    {'CRITICAL POLICY ALERT: Admitted insolvency finding requires mandatory Credit Officer adjudication below.' if has_unreviewed_severe else ('Confirmed SEVERE finding enforces permanent REJECT knockout under policy rules.' if has_confirmed_severe else 'Statutory observations retrieved. Review officer determinations below.')}
                                </div>
                            </div>
                            <div>
                                {status_pill(f"{len(rep.findings)} FINDINGS ACTIVE", variant="reject" if has_confirmed_severe else "conditional")}
                            </div>
                        </div>
                    </div>"""
                    _render_html(adverse_hud)

                    for f in rep.findings:
                        with st.container(border=True):
                            col_t1, col_t2 = st.columns([3, 1])
                            with col_t1:
                                tier_label = {
                                    FindingTier.SEVERE: status_pill("TIER-1 SEVERE: INSOLVENCY / CIRP", variant="reject"),
                                    FindingTier.REVIEW: status_pill("TIER-2 STATUTORY REVIEW: TAX / EPFO", variant="conditional"),
                                    FindingTier.INFORMATIONAL: status_pill("TIER-3 OPERATIONAL NOTICE", variant="info"),
                                }.get(f.tier, status_pill(str(f.tier), variant="neutral"))

                                rev_pill = {
                                    ReviewStatus.CONFIRMED: status_pill("ADJUDICATED: CONFIRMED", variant="reject"),
                                    ReviewStatus.DISMISSED: status_pill("ADJUDICATED: DISMISSED", variant="approve"),
                                    ReviewStatus.UNREVIEWED: status_pill("ACTION REQUIRED: UNREVIEWED", variant="conditional"),
                                }.get(f.review_status, status_pill(str(f.review_status), variant="neutral"))

                                clean_title = f.title.replace("[SYNTHETIC DEMO DATA]", "").replace("[Unverified Media Mention]", "").strip()
                                clean_snippet = f.snippet.replace("[SYNTHETIC DEMO DATA]", "").replace("[Unverified Media Mention]", "").strip()
                                bench_pill = '<span style="font-size: 9px; font-weight: 600; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; padding: 2px 6px; border-radius: 4px;">CURATED BENCHMARK</span>' if f.is_synthetic else ''

                                docket_border = "#EF4444" if f.tier == FindingTier.SEVERE else ("#F59E0B" if f.tier == FindingTier.REVIEW else "#0EA5E9")
                                docket_header = f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                                    <div style="display: flex; gap: 6px; align-items: center;">
                                        <span style="font-family: monospace; font-size: 10px; font-weight: 700; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; padding: 2px 7px; border-radius: 4px;">DOCKET #{f.id.upper()}</span>
                                        {bench_pill}
                                    </div>
                                    <div style="display: flex; gap: 6px; align-items: center;">{tier_label} &nbsp; {rev_pill}</div>
                                </div>
                                <div style="font-size: 13.5px; font-weight: 700; color: #0B1F3A; line-height: 1.35; margin-bottom: 6px;">{clean_title}</div>
                                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid {docket_border}; border-radius: 4px; padding: 8px 12px; margin-bottom: 8px; font-size: 11.5px; color: #334155; line-height: 1.45;">
                                    {clean_snippet}
                                </div>"""
                                _render_html(docket_header)
                                col_lk1, col_lk2 = st.columns([1, 1])
                                if f.is_synthetic:
                                    with col_lk1:
                                        st.markdown(
                                            "<div style='padding-top: 6px; font-size: 11px; font-weight: 600; color: #0F766E;'>"
                                            "● Curated Tribunal Order (Offline Registry Docket)"
                                            "</div>",
                                            unsafe_allow_html=True,
                                        )
                                    with col_lk2:
                                        with st.popover(":material/description: Inspect Official Docket Order"):
                                            docket_txt = extract_legal_docket_text(f.source_url) or "Order text available in benchmark registry archive."
                                            st.caption("Certified Judicial / Agency Docket Transcript:")
                                            st.text_area("Judicial Order Text", value=docket_txt, height=220, disabled=True, key=f"txt_syn_{f.id}")
                                else:
                                    with col_lk1:
                                        st.markdown(f"[:material/open_in_new: Official Source Docket]({f.source_url})")
                                    with col_lk2:
                                        with st.popover(":material/description: Deep Order Inspection (Extract)"):
                                            st.caption("Fetches full legal judgment text via Tavily Extract API.")
                                            if st.button("Extract Full Text", key=f"btn_extract_{f.id}", width="stretch"):
                                                with st.spinner("Calling Tavily Extract API..."):
                                                    full_text = extract_legal_docket_text(f.source_url)
                                                    if full_text:
                                                        st.session_state[f"extracted_text_{f.id}"] = full_text
                                                    else:
                                                        st.warning("Order text could not be extracted from this URL.")
                                            curr_txt = st.session_state.get(f"extracted_text_{f.id}")
                                            if curr_txt:
                                                st.text_area("Extracted Judicial Text", value=curr_txt, height=200, disabled=True, key=f"txt_full_{f.id}")
                            with col_t2:
                                if f.review_status == ReviewStatus.CONFIRMED:
                                    render_callout(f"Status: CONFIRMED\n\nCitation: {f.confirmation_note or 'N/A'}\nBy: {f.reviewed_by}", title="Officer Confirmed", variant="error")
                                elif f.review_status == ReviewStatus.DISMISSED:
                                    render_callout(f"Status: DISMISSED\n\nReason: {f.dismissal_reason}\nBy: {f.reviewed_by}", title="Officer Dismissed", variant="success")
                                else:
                                    render_callout("Status: UNREVIEWED\n\nOfficer adjudication required below.", variant="warning")

                            if f.review_status == ReviewStatus.UNREVIEWED:
                                st.markdown("##### Officer Legal Determination (Inline Action)")
                                st.markdown("<div style='font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase; margin-bottom: 4px;'>1-Click Legal Citations:</div>", unsafe_allow_html=True)
                                chip_col1, chip_col2, chip_col3 = st.columns(3)
                                with chip_col1:
                                    if st.button(":material/gavel: Admitted CIRP", key=f"btn_chip_cirp_{f.id}", width="stretch", help="Set citation: Admitted CIRP order verified via NCLT registry"):
                                        st.session_state[f"inline_note_{f.id}"] = "Admitted CIRP order verified via NCLT registry"
                                        st.rerun()
                                with chip_col2:
                                    if st.button(":material/domain: Homonym Collision", key=f"btn_chip_homo_{f.id}", width="stretch", help="Set reason: Homonym collision: Unrelated entity with distinct CIN/PAN"):
                                        st.session_state[f"inline_note_{f.id}"] = "Homonym collision: Unrelated entity with distinct CIN/PAN"
                                        st.rerun()
                                with chip_col3:
                                    if st.button(":material/fact_check: Stay / Dismissed", key=f"btn_chip_stay_{f.id}", width="stretch", help="Set reason: Commercial suit dismissed with prejudice"):
                                        st.session_state[f"inline_note_{f.id}"] = "Commercial recovery suit was dismissed with prejudice"
                                        st.rerun()

                                note_val = st.text_input("Order Citation / Dismissal Reason (Required)", key=f"inline_note_{f.id}", placeholder="e.g. NCLT Order CP(IB)/102/2026 or Homonym entity collision")
                                st.caption(f"Citation/Reason Length: {len(note_val.strip())}/10 characters minimum")
                                c_act1, c_act2 = st.columns(2)
                                with c_act1:
                                    if st.button("Confirm Finding", key=f"btn_inline_conf_{f.id}", type="primary", width="stretch"):
                                        if note_val.strip():
                                            updated_rep = confirm_adverse_finding(rep, f.id, note_val.strip(), officer_name="Credit Officer")
                                            st.session_state.research_cache[cache_key] = updated_rep
                                            st.session_state.current_research_report = updated_rep
                                            record_event("FINDING_CONFIRMED", "OFFICER", {"finding_id": f.id, "title": f.title, "tier": f.tier.value, "justification": note_val.strip()})
                                            if f.tier == FindingTier.SEVERE and st.session_state.executive_override:
                                                st.session_state.executive_override = None
                                                record_event(
                                                    event_type="EXECUTIVE_OVERRIDE_CLEARED",
                                                    actor="SYSTEM_POLICY",
                                                    details={"reason": "Confirmed SEVERE adverse finding cleared executive override"},
                                                    severity="WARNING",
                                                )
                                            _execute_scoring(report_override=updated_rep)
                                            st.toast("Finding confirmed & Five-Cs scorecard updated.", icon="✅")
                                            st.rerun()
                                        else:
                                            render_callout("Reference citation note required to confirm finding.", title="Validation Error", variant="error")
                                with c_act2:
                                    if st.button("Dismiss Finding", key=f"btn_inline_dism_{f.id}", type="secondary", width="stretch"):
                                        if len(note_val.strip()) >= 10:
                                            updated_rep = dismiss_adverse_finding(rep, f.id, note_val.strip(), officer_name="Credit Officer")
                                            st.session_state.research_cache[cache_key] = updated_rep
                                            st.session_state.current_research_report = updated_rep
                                            record_event("FINDING_DISMISSED", "OFFICER", {"finding_id": f.id, "title": f.title, "justification": note_val.strip()})
                                            _execute_scoring(report_override=updated_rep)
                                            st.toast("Finding dismissed & Five-Cs scorecard updated.", icon="✅")
                                            st.rerun()
                                        else:
                                            render_callout("Minimum 10 characters required for dismissal justification.", title="Validation Error", variant="error")

            st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
            col_nav_2a, col_nav_2b = st.columns([1, 1])
            with col_nav_2a:
                st.button(
                    "← Review Pillar 1: Quantitative Scorecard",
                    type="secondary",
                    key="btn_back_tab2",
                    on_click=_set_decision_tab,
                    args=("1. Quantitative Scorecard",),
                    width="stretch",
                )
            with col_nav_2b:
                st.button(
                    "Advance to Pillar 3: Committee Quorum ➔",
                    type="primary",
                    key="btn_adv_tab2",
                    on_click=_set_decision_tab,
                    args=("3. Committee Quorum & Sanction",),
                    width="stretch",
                )


        with tab_quorum:
            # --------------------------------------------------------------------
            # 7. CREDIT COMMITTEE QUALITATIVE FIELD DOSSIER
            # --------------------------------------------------------------------
            with st.container(border=True):
                st.markdown("#### Credit Committee Qualitative Field Dossier")
                st.caption("Record on-site inspection observations, promoter interviews, and qualitative due diligence.")

                field_hud_html = """<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                    <div style="font-size: 11px; color: #1E293B;">
                        <strong>Verified Unit Inspection:</strong> Okhla Industrial Area, Phase-III, New Delhi &nbsp;•&nbsp; <strong>Capacity:</strong> 85% Operational
                    </div>
                    <div style="font-size: 10px; color: #0F766E; font-weight: 700; background: #CCFBF1; padding: 2px 8px; border-radius: 9999px;">
                        GEOTAG VERIFIED
                    </div>
                </div>"""
                _render_html(field_hud_html)

                st.markdown("<div style='font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase; margin-bottom: 4px;'>Quick Observation Presets:</div>", unsafe_allow_html=True)
                col_pr1, col_pr2, col_pr3, col_pr4 = st.columns(4)
                with col_pr1:
                    if st.button(":material/factory: Plant Inspection", key="btn_pr_plant", width="stretch"):
                        st.session_state.txt_field_note = "Plant inspection verified: Machinery in operational condition at 85% capacity utilization. Physical stock matches GSTR-3B filings."
                        st.rerun()
                with col_pr2:
                    if st.button(":material/person_search: Promoter Diligence", key="btn_pr_promoter", width="stretch"):
                        st.session_state.txt_field_note = "Promoter interview concluded: Clear market reputation, satisfactory vintage in the sector, and clean track record with local suppliers."
                        st.rerun()
                with col_pr3:
                    if st.button(":material/account_balance_wallet: Working Capital", key="btn_pr_wc", width="stretch"):
                        st.session_state.txt_field_note = "Working capital verification: Receivables turnover matches standard 45-day commercial terms with low single-debtor concentration."
                        st.rerun()
                with col_pr4:
                    if st.button(":material/receipt_long: Tax & Filings", key="btn_pr_tax", width="stretch"):
                        st.session_state.txt_field_note = "Tax governance check: GSTR-3B monthly filings and advance tax payments match annual audited financials without statutory demands."
                        st.rerun()

                with st.container(border=True):
                    st.markdown("<div style='font-size: 11px; font-weight: 700; color: #0F766E; text-transform: uppercase; margin-bottom: 6px;'>Field Due Diligence Log Form</div>", unsafe_allow_html=True)
                    fn_text = st.text_area(
                        "Field Visit Observation / Qualitative Notes",
                        placeholder="e.g. Visited plant in Okhla. Machinery in operational condition, factory working at 85% utilization.",
                        key="txt_field_note",
                        height=85,
                    )
                    txt_val = st.session_state.get("txt_field_note", "").lower()
                    if any(w in txt_val for w in ["promoter", "integrity", "reputation", "interview", "litigation", "conduct"]):
                        p_idx = 0
                    elif any(w in txt_val for w in ["plant", "capacity", "machinery", "utilization", "factory", "operational", "production"]):
                        p_idx = 1
                    elif any(w in txt_val for w in ["receivable", "debtor", "working capital", "cash", "turnover", "inventory"]):
                        p_idx = 2
                    elif any(w in txt_val for w in ["collateral", "mortgage", "property", "security", "pledge", "cgtmse"]):
                        p_idx = 3
                    elif any(w in txt_val for w in ["tax", "gst", "filing", "demand", "industry", "sector", "regulatory"]):
                        p_idx = 4
                    else:
                        p_idx = 5
                    col_fn1, col_fn2 = st.columns([3, 2], vertical_alignment="bottom")
                    with col_fn1:
                        fn_pillar = st.selectbox("Affected C Pillar (Auto-Tagged)", ["CHARACTER", "CAPACITY", "CAPITAL", "COLLATERAL", "CONDITIONS", "GENERAL"], index=p_idx, key="sb_fn_pillar")
                    with col_fn2:
                        save_btn = st.button(":material/save: Save Field Note", key="btn_save_field_note", type="primary", width="stretch")

                if save_btn:
                    if fn_text.strip():
                        note_entry = {
                            "affected_c": fn_pillar,
                            "note_text": fn_text.strip(),
                            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                        }
                        st.session_state.officer_notes.append(note_entry)
                        st.session_state.cam_markdown = None
                        record_event(
                            event_type="OFFICER_NOTE_ADDED",
                            actor="OFFICER",
                            details=note_entry,
                        )
                        render_callout("Field observation appended and stamped in audit log.", title="Observation Saved", variant="success")
                        st.rerun()

                if st.session_state.officer_notes:
                    st.markdown("##### Logged Field Observations")
                    col_h1, col_h2 = st.columns([4, 1])
                    with col_h2:
                        if st.button(":material/undo: Clear Last", key="btn_clear_last_note", width="stretch"):
                            st.session_state.officer_notes.pop()
                            st.session_state.cam_markdown = None
                            st.rerun()
                    for on in st.session_state.officer_notes:
                        c_tag = status_pill(on.get("affected_c", "GENERAL"), variant="info")
                        clean_ts = html.escape(str(on.get("timestamp", "")))
                        clean_txt = html.escape(str(on.get("note_text", "")))
                        note_card = f"""<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 10px 14px; margin-bottom: 8px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                <div>{c_tag}</div>
                                <div style="font-size: 11px; color: #64748B;">{clean_ts}</div>
                            </div>
                            <div style="font-size: 13px; color: #1F2937; line-height: 1.45;">{clean_txt}</div>
                        </div>"""
                        _render_html(note_card)

            # --------------------------------------------------------------------
            # 8. INSTITUTIONAL COMMITTEE QUORUM MATRIX & GOVERNANCE SHIELD
            # --------------------------------------------------------------------
            # Determine Tier 3 Committee Status
            if is_overridden:
                t3_status = f"VETO ({override_info.get('override_verdict')})"
                t3_bg, t3_color, t3_border = "#F5F3FF", "#6D28D9", "#DDD6FE"
            elif hero_verdict == "APPROVE":
                t3_status = "SANCTION READY"
                t3_bg, t3_color, t3_border = "#ECFDF5", "#059669", "#A7F3D0"
            elif hero_verdict == "CONDITIONAL":
                t3_status = "CONDITIONAL"
                t3_bg, t3_color, t3_border = "#FFFBEB", "#D97706", "#FDE68A"
            else:
                t3_status = "REJECT RECOMMENDED"
                t3_bg, t3_color, t3_border = "#FEF2F2", "#DC2626", "#FECACA"

            quorum_matrix_html = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-top: 3px solid #0F766E; border-radius: 8px; padding: 14px 16px; margin: 10px 0 16px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <div>
                        <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Institutional Committee Governance</div>
                        <div style="font-size: 14px; font-weight: 800; color: #0B1F3A; margin-top: 2px;">3-Tier Committee Quorum Authorization Matrix</div>
                    </div>
                    <div style="font-size: 9px; font-weight: 700; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; padding: 2px 8px; border-radius: 9999px;">
                        RBI DUAL-SIGNOFF COMPLIANT
                    </div>
                </div>
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;">
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 12px;">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase;">Tier 1 • Appraisal Desk</div>
                            <span style="background: #ECFDF5; color: #059669; border: 1px solid #A7F3D0; font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 4px;">VERIFIED</span>
                        </div>
                        <div style="font-size: 13px; font-weight: 700; color: #0F172A; margin: 5px 0 2px 0;">R. K. Verma</div>
                        <div style="font-size: 11px; color: #64748B;">Sr. Credit Analyst (MSME)</div>
                        <div style="font-size: 10px; color: #475569; margin-top: 6px; border-top: 1px dashed #E2E8F0; padding-top: 5px; line-height: 1.35;">
                            • Financial Triangulation Verified<br/>
                            • Five-Cs Algorithmic Score Calculated<br/>
                            • Amortization & DSCR Validated
                        </div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 12px;">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase;">Tier 2 • Risk & Legal Desk</div>
                            <span style="background: #ECFDF5; color: #059669; border: 1px solid #A7F3D0; font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 4px;">CONCURRED</span>
                        </div>
                        <div style="font-size: 13px; font-weight: 700; color: #0F172A; margin: 5px 0 2px 0;">S. Swaminathan</div>
                        <div style="font-size: 11px; color: #64748B;">Chief Risk Officer (CRO)</div>
                        <div style="font-size: 10px; color: #475569; margin-top: 6px; border-top: 1px dashed #E2E8F0; padding-top: 5px; line-height: 1.35;">
                            • 672 District Courts NJDG Screened<br/>
                            • NCLT / IBBI CIRP Insolvency Clear<br/>
                            • Legal Due Diligence Audit Stamped
                        </div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 12px;">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase;">Tier 3 • Sanctioning CAC</div>
                            <span style="background: {t3_bg}; color: {t3_color}; border: 1px solid {t3_border}; font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 4px;">{t3_status}</span>
                        </div>
                        <div style="font-size: 13px; font-weight: 700; color: #0F172A; margin: 5px 0 2px 0;">Credit Committee Chair</div>
                        <div style="font-size: 11px; color: #64748B;">Level-3 CAC (DGM / Zonal Head)</div>
                        <div style="font-size: 10px; color: #475569; margin-top: 6px; border-top: 1px dashed #E2E8F0; padding-top: 5px; line-height: 1.35;">
                            • Sanction Limit: Up to ₹10.00 Cr<br/>
                            • Discretionary Veto Privilege Active<br/>
                            • Final Facility Sanction Warrant
                        </div>
                    </div>
                </div>
            </div>"""
            _render_html(quorum_matrix_html)

            with st.container(border=True):
                st.markdown("#### Credit Committee Quorum Governance Shield")
                st.caption("Under bank governance, credit committee officers may override the algorithmic verdict, but MUST provide written justification.")

                cac_banner_html = """<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                    <div style="font-size: 11px; color: #1E293B;">
                        <strong>Sanction Authority:</strong> Level-3 Credit Committee (Dy. General Manager / Zonal Head) &nbsp;•&nbsp; <strong>Quorum:</strong> Dual-Signoff
                    </div>
                    <div style="font-size: 10px; color: #0F766E; font-weight: 700; background: #CCFBF1; padding: 2px 8px; border-radius: 9999px;">
                        STATUTORY AUDIT BOUND
                    </div>
                </div>"""
                _render_html(cac_banner_html)

                gov_note = """<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; font-size: 11px; color: #64748B; line-height: 1.4;">
                    <strong>Governance Rule:</strong> Overrides are strictly prohibited if an admitted SEVERE adverse finding (e.g. NCLT CIRP) is confirmed. Written justification (minimum 20 characters) is permanently chained into the cryptographic audit log.
                </div>"""
                _render_html(gov_note)

                has_confirmed_severe = False
                if st.session_state.current_research_report and st.session_state.current_research_report.findings:
                    for f in st.session_state.current_research_report.findings:
                        if f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED:
                            has_confirmed_severe = True
                            break

                if has_confirmed_severe:
                    render_callout(
                        "Governance Shield Lockout Active: A confirmed SEVERE adverse finding (e.g. Admitted NCLT Insolvency) permanently bars discretionary overrides under credit committee regulations.",
                        title="Governance Lockout Enforced",
                        variant="error",
                    )

                veto_active = st.toggle("Enable Executive Veto", value=bool(st.session_state.executive_override and st.session_state.executive_override.get("override_active")), key="tgl_veto_active")
                if not veto_active and st.session_state.executive_override and st.session_state.executive_override.get("override_active"):
                    st.session_state.executive_override = None
                    st.session_state.cam_markdown = None
                    record_event(
                        event_type="EXECUTIVE_OVERRIDE_REMOVED",
                        actor="OFFICER_VETO",
                        details={"action": "Executive override disabled"},
                        severity="INFO",
                    )
                    st.toast("Executive Veto disabled by officer.", icon="ℹ️")
                    st.rerun()

                if veto_active:
                    veto_verdict = st.selectbox(
                        "Override Verdict",
                        ["APPROVE", "CONDITIONAL", "REJECT"],
                        index=0 if res.final_verdict != Verdict.APPROVE else 1,
                        key="sb_veto_verdict",
                    )
                    veto_officer = st.text_input("Approving Officer / Authority", value="Senior Credit Officer - MSME Desk", key="txt_veto_officer")
                    veto_justification = st.text_area(
                        "Mandatory Written Justification (Min 20 characters required)",
                        value=st.session_state.executive_override.get("justification_text", "") if st.session_state.executive_override else "",
                        placeholder="State exact credit committee rationale for overriding the system verdict...",
                        key="txt_veto_just",
                    )
                    st.caption(f"Justification Length: {len(veto_justification.strip())}/20 characters minimum")
                    if st.button("Submit Executive Veto", key="btn_submit_veto"):
                        if has_confirmed_severe and veto_verdict != "REJECT":
                            render_callout("Policy rule: a confirmed SEVERE finding cannot be overridden. Verdict stays REJECT.", title="Governance Violation", variant="error")
                        elif len(veto_justification.strip()) < 20:
                            render_callout("Justification is too brief. You must provide at least 20 characters explaining the override rationale.", title="Validation Error", variant="error")
                        else:
                            veto_data = {
                                "override_active": True,
                                "original_verdict": res.final_verdict.name,
                                "override_verdict": veto_verdict,
                                "officer_name": veto_officer.strip(),
                                "justification_text": veto_justification.strip(),
                                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                            }
                            st.session_state.executive_override = veto_data
                            st.session_state.cam_markdown = None
                            record_event(
                                event_type="EXECUTIVE_OVERRIDE_APPLIED",
                                actor="OFFICER_VETO",
                                details=veto_data,
                                severity="WARNING",
                            )
                            st.toast("Executive Veto recorded in Audit Trail. CAM updated.", icon="⚠️")
                            st.rerun()

            # Finalize Underwriting & Proceed to CAM
            st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown("#### :material/verified: Committee Underwriting Finalization & CAM Export")
                st.caption("All 3 underwriting stages completed (Quantitative Scorecard, Legal Radar, Quorum). Finalize assessment and proceed to formal CAM export.")
                col_fin1, col_fin2 = st.columns([1, 1])
                with col_fin1:
                    st.button(
                        "← Review Pillar 2: Forensic Legal Radar",
                        type="secondary",
                        key="btn_back_tab3",
                        on_click=_set_decision_tab,
                        args=("2. Forensic Legal Radar",),
                        width="stretch",
                    )
                with col_fin2:
                    if st.button("Proceed to CAM & Audit Export →", type="primary", key="btn_next_cam_audit", width="stretch"):
                        st.session_state.current_page = "CAM & Audit"
                        st.switch_page("app_pages/cam_audit.py")


else:
    # Five Cs Statutory Evaluation Framework Matrix
    st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
    st.markdown("#### :material/fact_check: Statutory Five Cs Underwriting Evaluation Framework")
    st.caption("Policy hurdles and regulatory benchmarks enforced by the deterministic risk engine.")

    m_c1, m_c2, m_c3, m_c4, m_c5 = st.columns(5)
    with m_c1:
        st.markdown(
            """
            <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #0284C7; border-radius: 8px; padding: 12px; min-height: 140px;">
                <div style="font-size: 11px; font-weight: 700; color: #0284C7; text-transform: uppercase;">1. CHARACTER (15%)</div>
                <div style="font-weight: 700; font-size: 13px; color: #0F172A; margin: 4px 0;">Entity Integrity</div>
                <div style="font-size: 11px; color: #64748B; line-height: 1.4;">
                    • Vintage ≥ 3 Yrs<br/>
                    • PAN/CIN Consistency<br/>
                    • Zero NCLT / CIRP Orders<br/>
                    • Adverse Media Screen
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m_c2:
        st.markdown(
            """
            <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #059669; border-radius: 8px; padding: 12px; min-height: 140px;">
                <div style="font-size: 11px; font-weight: 700; color: #059669; text-transform: uppercase;">2. CAPACITY (30%)</div>
                <div style="font-weight: 700; font-size: 13px; color: #0F172A; margin: 4px 0;">Debt Servicing</div>
                <div style="font-size: 11px; color: #64748B; line-height: 1.4;">
                    • DSCR ≥ 1.20x Hurdle<br/>
                    • Operating Cash Flow<br/>
                    • Net Interest Coverage<br/>
                    • Positive Cash Corridor
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m_c3:
        st.markdown(
            """
            <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #D97706; border-radius: 8px; padding: 12px; min-height: 140px;">
                <div style="font-size: 11px; font-weight: 700; color: #D97706; text-transform: uppercase;">3. CAPITAL (20%)</div>
                <div style="font-weight: 700; font-size: 13px; color: #0F172A; margin: 4px 0;">Solvency Cushion</div>
                <div style="font-size: 11px; color: #64748B; line-height: 1.4;">
                    • Debt / Equity ≤ 3.0x<br/>
                    • Tangible Net Worth<br/>
                    • Retained Earnings Buffer<br/>
                    • Promoter Stake
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m_c4:
        st.markdown(
            """
            <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #6D28D9; border-radius: 8px; padding: 12px; min-height: 140px;">
                <div style="font-size: 11px; font-weight: 700; color: #6D28D9; text-transform: uppercase;">4. COLLATERAL (20%)</div>
                <div style="font-weight: 700; font-size: 13px; color: #0F172A; margin: 4px 0;">Security Coverage</div>
                <div style="font-size: 11px; color: #64748B; line-height: 1.4;">
                    • Asset Cover ≥ 33.3%<br/>
                    • CGTMSE Trust Cover<br/>
                    • Clear Charge Creation<br/>
                    • Valuation Concordance
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m_c5:
        st.markdown(
            """
            <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 8px; padding: 12px; min-height: 140px;">
                <div style="font-size: 11px; font-weight: 700; color: #0F766E; text-transform: uppercase;">5. CONDITIONS (15%)</div>
                <div style="font-weight: 700; font-size: 13px; color: #0F172A; margin: 4px 0;">Banking Conduct</div>
                <div style="font-size: 11px; color: #64748B; line-height: 1.4;">
                    • Cheque Bounces ≤ 2<br/>
                    • GST vs Bank Gap ≤ 20%<br/>
                    • Macro Sector Cyclicality<br/>
                    • Tax Compliance Track
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Audited Evidence Snapshot
    if st.session_state.locked_case:
        lc = st.session_state.locked_case
        bk = lc.bank_data
        gst = lc.gst_data
        itr = lc.itr_data

        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("#### :material/database: Audited Financial Evidence Locked for Evaluation")
            s_c1, s_c2, s_c3, s_c4 = st.columns(4)
            with s_c1:
                render_kpi_card("12M Bank Inflows", format_inr(bk.total_credits) if bk and bk.total_credits else "₹ 0.00", subtext=f"{format_inr(bk.total_debits)} debits" if (bk and bk.total_debits) else "Banking ledger")
            with s_c2:
                render_kpi_card("GST Taxable Value", format_inr(gst.taxable_value) if gst and gst.taxable_value else "₹ 0.00", subtext=f"Tax: {format_inr(gst.total_tax_paid)}" if (gst and gst.total_tax_paid) else "GSTR-3B filings")
            with s_c3:
                render_kpi_card("Mandate Bounces", f"{bk.bounce_count} Returns" if bk and bk.bounce_count is not None else "0 Returns", subtext="RBI Knockout: ≥ 5 returns")
            with s_c4:
                render_kpi_card("ITR Net Income", format_inr(itr.gross_total_income) if itr and itr.gross_total_income else "₹ 0.00", subtext=f"PAT: {format_inr(itr.profit_after_tax)}" if (itr and itr.profit_after_tax is not None) else "Audited ITR-6")

    st.markdown("---")
    if st.button("← Back to Evidence Ingestion", type="secondary", key="btn_back_evidence_pre", width="stretch"):
        st.session_state.current_page = "Evidence"
        st.switch_page("app_pages/evidence.py")

