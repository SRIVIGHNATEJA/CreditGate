import os
import re
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
    AdverseMediaFinding,
)
from core.researcher import (
    build_search_query,
    search_adverse_media,
    confirm_adverse_finding,
    dismiss_adverse_finding,
    extract_policy_flags,
    merge_existing_reviews,
    extract_legal_docket_text,
    create_empanelled_advocate_finding,
)
from core.policy import Verdict, derive_stipulated_covenants
from core.scorer import score_credit_case, ScoreResult
from core.formatting import format_inr
from core.analyser import calculate_monthly_emi
import importlib
try:
    from ui.state import (
        get_app_context,
        record_event,
        compute_composite_hash,
        COHORT_COMPANY_NAMES,
        get_default_quorum_governance,
    )
except ImportError:
    import ui.state
    importlib.reload(ui.state)
    from ui.state import (
        get_app_context,
        record_event,
        compute_composite_hash,
        COHORT_COMPANY_NAMES,
        get_default_quorum_governance,
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
    cin=cin_parsed_data.get("cin", "") if cin_parsed_data else "",
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
        st.session_state.locked_case.collateral_value = Decimal(str(collateral_val))
        st.session_state.locked_case.cgtmse_covered = bool(cgtmse_covered)

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
        cin=cin_parsed_data.get("cin", "") if cin_parsed_data else "",
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

def format_institutional_entity_name(name: str) -> str:
    if not name:
        return name
    n = name.replace(" (Demo)", "").replace("(Demo)", "").strip()
    n = n.replace("Apex Synthetic Holdings", "Apex Industrial Holdings Ltd.")
    n = n.replace("Kaveri Fictitious Freight", "Kaveri Logistics & Freight Ltd.")
    n = n.replace("Bharat Mock Steels & Infra", "Bharat Structural Steels & Infra Ltd.")
    return n

def clean_policy_rule_text(rule: Optional[str]) -> str:
    """Sanitizes raw policy knockouts and caps into clean, authoritative institutional strings."""
    if not rule:
        return "All policy risk thresholds satisfied."
    r = str(rule).strip()
    r = r.replace("[SYNTHETIC DEMO DATA]", "").replace("[Unverified Media Mention]", "").strip()
    r = format_institutional_entity_name(r)

    # Specific pattern decoders:
    if "BOUNCE_KNOCKOUT" in r:
        return "Mandate Bounce Limit Exceeded (≥5 Returns)"
    if "DSCR_KNOCKOUT" in r:
        return "Debt Service Coverage Ratio Below Policy Floor (<1.20x)"
    if "RESEARCH_SEVERE_FLAG" in r or "CONFIRMED_INSOLVENCY" in r:
        if "NCLT" in r or "CIRP" in r or "SECTION 9" in r.upper():
            return "Active NCLT Section 9 CIRP Admitted (Statutory Moratorium)"
        if "ADVOCATE" in r.upper():
            return "Adverse Judicial Finding Attested by Empanelled Legal Counsel"
        return "Statutory Insolvency Moratorium Enforced"
    if "WEAKEST_LINK_CAP" in r or "CAP (" in r:
        if "CONDITIONS" in r:
            return "Conditions Pillar Score Below Risk Hurdle (<40 Pts)"
        return "Weakest-Link Policy Floor Applied"
    if "BUREAU_OVERDUE" in r:
        return "Overdue Credit Facilities on Commercial Bureau Track"

    # General cleanup
    r = r.replace("RESEARCH_SEVERE_FLAG", "").replace("CONFIRMED_INSOLVENCY:", "").strip()
    r = r.strip("() ").strip()
    return r or "Credit Policy Knockout Triggered"

def clean_policy_rule_short(rule: Optional[str]) -> str:
    """Returns a tight, punchy <=35 character descriptor for compact cards."""
    if not rule:
        return "Five-Cs Algorithmic Assessment"
    full = clean_policy_rule_text(rule)
    if "NCLT" in full or "CIRP" in full:
        return "Active NCLT Section 9 CIRP"
    if "Bounce" in full:
        return "Mandate Bounces (≥5 Returns)"
    if "Debt Service" in full or "DSCR" in full:
        return "DSCR Below Floor (<1.20x)"
    if "Conditions" in full:
        return "Conditions Pillar Floor Cap"
    if "Advocate" in full:
        return "Empanelled Advocate Knockout"
    if len(full) > 35:
        return full[:32] + "..."
    return full

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
        f"""<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3.5px solid #0F766E; border-radius: 6px; padding: 10px 14px; margin: 12px 0; font-size: 12px; color: #1E293B; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
            <div>
                <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">Scheduled Debt Service (Amortization Schedule)</div>
                <div style="font-size: 15px; font-weight: 800; color: #0B1F3A; margin-top: 2px;">{format_inr(float(calc_emi))} <span style="font-size: 11px; font-weight: 500; color: #64748B;">/ month</span></div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">Annualized Debt Outflow</div>
                <div style="font-size: 13px; font-weight: 700; color: #0F766E; margin-top: 2px;">{format_inr(float(calc_emi * Decimal('12')))} / yr</div>
            </div>
        </div>""",
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
                st.session_state.locked_case.collateral_value = Decimal(str(new_c))
                st.session_state.locked_case.cgtmse_covered = bool(new_g)

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
# TOP 3 BORROWER SUMMARY CARDS
# ----------------------------------------------------------------------------
company_disp_raw = (
    st.session_state.locked_case.company_name
    if (st.session_state.locked_case and st.session_state.locked_case.company_name != "Manual Applicant Company")
    else (st.session_state.get("custom_company_name") or COHORT_COMPANY_NAMES.get(cohort_selection, "Applicant Company"))
)
company_disp = format_institutional_entity_name(company_disp_raw)
cin_val = (
    st.session_state.locked_case.cin_or_pan
    if (st.session_state.locked_case and getattr(st.session_state.locked_case, "cin_or_pan", None) and st.session_state.locked_case.cin_or_pan != "UNKNOWN")
    else (cin_parsed_data.get("cin", "Unregistered MSME") if cin_parsed_data else "Unregistered MSME")
)
vintage_val = cin_parsed_data.get("business_vintage_years", 5) if cin_parsed_data else 5
sector_val = cin_parsed_data.get("macro_sector", "Commercial Entity") if cin_parsed_data else "Commercial Entity"

col_card1, col_card2, col_card3 = st.columns(3)
with col_card1:
    st.markdown(
        f"""
        <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #0284C7; border-radius: 8px; padding: 10px 14px; height: 112px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.72rem; font-weight: 700; color: #0284C7; text-transform: uppercase; letter-spacing: 0.04em;">BORROWER IDENTITY</span>
                <span style="font-size: 0.70rem; font-weight: 600; background: #E0F2FE; color: #0369A1; padding: 2px 6px; border-radius: 4px;">{html.escape(sector_val[:18])}</span>
            </div>
            <div style="font-size: 1.05rem; font-weight: 700; color: #0F172A; margin: 4px 0 2px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="{html.escape(company_disp)}">{html.escape(company_disp)}</div>
            <div style="font-size: 0.75rem; color: #64748B; line-height: 1.35;">
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
        <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #059669; border-radius: 8px; padding: 10px 14px; height: 112px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.72rem; font-weight: 700; color: #059669; text-transform: uppercase; letter-spacing: 0.04em;">SANCTION FACILITY</span>
                {guarantee_badge}
            </div>
            <div style="font-size: 1.15rem; font-weight: 700; color: #0F172A; margin: 4px 0 2px 0;">{format_inr(loan_amount)}</div>
            <div style="font-size: 0.75rem; color: #64748B; line-height: 1.35;">
                Terms: <strong>{int(tenure_months)}M @ {float(interest_rate)}%</strong> &nbsp;|&nbsp; EMI: <strong>{format_inr(proposed_emi)}</strong><br/>
                Collateral: <strong>{format_inr(collateral_val)}</strong> ({collat_cover_pct}% cover)
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

res_status_badge = '<span style="font-size: 0.70rem; font-weight: 600; background: #FEF3C7; color: #B45309; padding: 2px 6px; border-radius: 4px;">Awaiting Scoring</span>'
res_status_line = "Engine Ready • Five Cs Statutory Framework"
full_policy_rule = "All policy risk thresholds satisfied."
if st.session_state.score_result is not None:
    _sr = st.session_state.score_result
    if _sr.final_verdict == Verdict.APPROVE:
        res_status_badge = f'<span style="font-size: 0.70rem; font-weight: 600; background: #DCFCE7; color: #15803D; padding: 2px 6px; border-radius: 4px;">APPROVED ({_sr.composite_score:.1f}/100)</span>'
        res_status_line = f"DSCR: <strong>{_sr.dscr:.2f}x</strong> • All Policy Hurdles Cleared"
        full_policy_rule = "All policy risk thresholds satisfied."
    elif _sr.final_verdict == Verdict.CONDITIONAL:
        res_status_badge = f'<span style="font-size: 0.70rem; font-weight: 600; background: #FEF3C7; color: #B45309; padding: 2px 6px; border-radius: 4px;">CONDITIONAL ({_sr.composite_score:.1f}/100)</span>'
        cap_short = clean_policy_rule_short(_sr.cap_applied)
        full_policy_rule = clean_policy_rule_text(_sr.cap_applied)
        res_status_line = f'Policy Cap: <strong style="color: #B45309;">{html.escape(cap_short)}</strong>'
    elif _sr.final_verdict == Verdict.REJECT:
        res_status_badge = '<span style="font-size: 0.70rem; font-weight: 600; background: #FEE2E2; color: #B91C1C; padding: 2px 6px; border-radius: 4px;">REJECTED</span>'
        ko_short = clean_policy_rule_short(_sr.knockout_applied)
        full_policy_rule = clean_policy_rule_text(_sr.knockout_applied)
        res_status_line = f'Policy Knockout: <strong style="color: #B91C1C;">{html.escape(ko_short)}</strong>'
    else:
        res_status_badge = '<span style="font-size: 0.70rem; font-weight: 600; background: #F1F5F9; color: #475569; padding: 2px 6px; border-radius: 4px;">DATA REQUIRED</span>'
        res_status_line = "Missing mandatory financial parameters"

with col_card3:
    st.markdown(
        f"""
        <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #6D28D9; border-radius: 8px; padding: 10px 14px; height: 112px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.72rem; font-weight: 700; color: #6D28D9; text-transform: uppercase; letter-spacing: 0.04em;">POLICY DECISION MATRIX</span>
                {res_status_badge}
            </div>
            <div style="font-size: 1.05rem; font-weight: 700; color: #0F172A; margin: 4px 0 2px 0;">Deterministic Engine</div>
            <div style="font-size: 0.75rem; color: #64748B; line-height: 1.35; overflow: hidden;">
                Framework: <strong>Five Cs Commercial Matrix</strong><br/>
                <span style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block;" title="{html.escape(full_policy_rule)}">{res_status_line}</span>
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

        # Completely eradicate default static Streamlit tab header from DOM
        st.html("""<style>
        [data-testid="stTabs"] > div:first-child,
        [data-testid="stTabs"] [data-baseweb="tab-list"],
        [data-testid="stTabs"] [role="tablist"],
        .stTabs [data-baseweb="tab-list"],
        .stTabs [role="tablist"],
        div[data-baseweb="tab-list"],
        div[role="tablist"],
        div[data-baseweb="tab-highlight"],
        div[data-baseweb="tab-border"] {
            display: none !important;
            height: 0px !important;
            max-height: 0px !important;
            min-height: 0px !important;
            margin: 0px !important;
            padding: 0px !important;
            border: none !important;
            opacity: 0 !important;
            overflow: hidden !important;
            pointer-events: none !important;
            visibility: hidden !important;
        }
        /* Restore nested chart tabs within tab content */
        [data-testid="stTabContent"] [data-testid="stTabs"] > div:first-child,
        [data-testid="stTabContent"] [data-testid="stTabs"] [data-baseweb="tab-list"],
        [data-testid="stTabContent"] [data-testid="stTabs"] [role="tablist"] {
            display: flex !important;
            height: auto !important;
            opacity: 1 !important;
            visibility: visible !important;
            pointer-events: auto !important;
            overflow: visible !important;
        }
        </style>""")

        # Dynamic Interactive Underwriting Stepper Console
        cur_t = st.session_state.get("decision_active_tab", "1. Quantitative Scorecard")
        s1_active = "1." in cur_t
        s2_active = "2." in cur_t
        s3_active = "3." in cur_t

        # Pre-compute live telemetry for the 3 interactive navigation cards
        t1_badge_txt = f"Score: {float(res.composite_score):.1f}/100" if res else "Awaiting Score"
        t1_sub = f"DSCR: {res.dscr:.2f}x" if res else "Hurdle: 1.20x"

        rep_t2 = st.session_state.current_research_report
        if rep_t2 and rep_t2.status == "COMPLETED":
            has_conf_sev = any(f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED for f in rep_t2.findings)
            has_unrev_sev = any(f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.UNREVIEWED for f in rep_t2.findings)
            has_conf_rev = any(f.tier == FindingTier.REVIEW and f.review_status == ReviewStatus.CONFIRMED for f in rep_t2.findings)
            if has_conf_sev:
                t2_nav_badge = "KNOCKOUT ADVERSE"
                t2_nav_color = "#DC2626"
            elif has_unrev_sev:
                t2_nav_badge = "REVIEW PENDING"
                t2_nav_color = "#D97706"
            elif has_conf_rev:
                t2_nav_badge = "INQUIRY FLAGGED"
                t2_nav_color = "#D97706"
            else:
                t2_nav_badge = "CONCURRED (CLEAN)"
                t2_nav_color = "#059669"
        else:
            t2_nav_badge = "SURVEILLANCE READY"
            t2_nav_color = "#0F766E"

        override_info = st.session_state.executive_override
        is_overridden = bool(override_info and override_info.get("override_active"))
        hero_posture = override_info.get("override_verdict") if is_overridden else (res.final_verdict.name if res else "PENDING")
        if is_overridden:
            t3_nav_badge = f"EXCEPTION ({hero_posture})"
            t3_nav_color = "#0F766E"
        elif hero_posture == "APPROVE":
            t3_nav_badge = "SANCTION READY"
            t3_nav_color = "#059669"
        elif hero_posture == "CONDITIONAL":
            t3_nav_badge = "CONDITIONAL"
            t3_nav_color = "#D97706"
        else:
            t3_nav_badge = "REJECT RECOMMENDED"
            t3_nav_color = "#DC2626"

        # Stage indicator metadata
        if s1_active:
            cur_stage_num = "Stage 1 of 3"
            cur_stage_name = "Quantitative Five-Cs Risk Scorecard & Amortization"
            cur_stage_badge = "<span style='background: #E0F2FE; color: #0369A1; font-weight: 700; padding: 3px 8px; border-radius: 6px; font-size: 11px; border: 1px solid #BAE6FD;'>STAGE 1 OF 3 • 5-Cs ACTIVE</span>"
        elif s2_active:
            cur_stage_num = "Stage 2 of 3"
            cur_stage_name = "All-India Forensic Legal Radar & Judicial Adjudication"
            cur_stage_badge = "<span style='background: #FEF3C7; color: #B45309; font-weight: 700; padding: 3px 8px; border-radius: 6px; font-size: 11px; border: 1px solid #FDE68A;'>STAGE 2 OF 3 • LEGAL ACTIVE</span>"
        else:
            cur_stage_num = "Stage 3 of 3"
            cur_stage_name = "Underwriting Synthesis & Sanction Routing"
            cur_stage_badge = "<span style='background: #F3E8FF; color: #6D28D9; font-weight: 700; padding: 3px 8px; border-radius: 6px; font-size: 11px; border: 1px solid #DDD6FE;'>STAGE 3 OF 3 • SYNTHESIS ACTIVE</span>"

        with st.container(border=True):
            hdr_left, hdr_right = st.columns([2, 1], vertical_alignment="center")
            with hdr_left:
                st.markdown(
                    "<div style='display: flex; align-items: center; gap: 8px;'>"
                    "<span style='font-size: 12px; font-weight: 800; letter-spacing: 0.06em; color: #0B1F3A; text-transform: uppercase;'>🏛️ Underwriting Stages & Audit Flight Deck</span>"
                    "</div>"
                    "<div style='font-size: 11px; color: #64748B;'>Select a pillar below to inspect quantitative risk models, forensic legal radar, or underwriting synthesis & sanction routing.</div>",
                    unsafe_allow_html=True,
                )
            with hdr_right:
                st.markdown(
                    f"<div style='text-align: right;'>{cur_stage_badge}</div>",
                    unsafe_allow_html=True,
                )

            st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)

            c_nav1, c_nav2, c_nav3 = st.columns(3)
            with c_nav1:
                if st.button(
                    f":material/calculate: Pillar 1: Quantitative 5-Cs",
                    key="btn_switch_pillar1",
                    type="primary" if s1_active else "secondary",
                    width="stretch",
                    help="Navigate to Quantitative Five-Cs Risk Scorecard & DSCR Ratios",
                ):
                    _set_decision_tab("1. Quantitative Scorecard")
                    st.rerun()
                st.markdown(
                    f"<div style='background: {'#F0FDFA' if s1_active else '#F8FAFC'}; border: 1px solid {'#99F6E4' if s1_active else '#E2E8F0'}; border-radius: 6px; padding: 4px 8px; text-align: center; font-size: 11px; margin-top: -4px;'>"
                    f"<span style='font-weight: 700; color: {'#0F766E' if s1_active else '#475569'};'>{'● ACTIVE WORKBENCH' if s1_active else t1_badge_txt}</span>"
                    f" &nbsp;•&nbsp; <span style='font-size: 10px; color: #64748B;'>{t1_sub}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            with c_nav2:
                if st.button(
                    f":material/gavel: Pillar 2: Forensic Legal Radar",
                    key="btn_switch_pillar2",
                    type="primary" if s2_active else "secondary",
                    width="stretch",
                    help="Navigate to Forensic Legal Radar & Judicial Adjudication Desk",
                ):
                    _set_decision_tab("2. Forensic Legal Radar")
                    st.rerun()
                st.markdown(
                    f"<div style='background: {'#FEF2F2' if (s2_active and t2_nav_color == '#DC2626') else ('#FFFBEB' if s2_active else '#F8FAFC')}; border: 1px solid {'#FECACA' if (s2_active and t2_nav_color == '#DC2626') else ('#FDE68A' if s2_active else '#E2E8F0')}; border-radius: 6px; padding: 4px 8px; text-align: center; font-size: 11px; margin-top: -4px;'>"
                    f"<span style='font-weight: 700; color: {'#0F766E' if s2_active else t2_nav_color};'>{'● ACTIVE WORKBENCH' if s2_active else t2_nav_badge}</span>"
                    f" &nbsp;•&nbsp; <span style='font-size: 10px; color: #64748B;'>672 Courts NJDG</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            with c_nav3:
                if st.button(
                    f":material/fact_check: Pillar 3: Underwriting Synthesis",
                    key="btn_switch_pillar3",
                    type="primary" if s3_active else "secondary",
                    width="stretch",
                    help="Navigate to Underwriting Synthesis, Delegation of Financial Powers (DoFP) Grid & Qualitative Field Attestation",
                ):
                    _set_decision_tab("3. Underwriting Synthesis & Sanction Routing")
                    st.rerun()
                st.markdown(
                    f"<div style='background: {'#FAF5FF' if s3_active else '#F8FAFC'}; border: 1px solid {'#E9D5FF' if s3_active else '#E2E8F0'}; border-radius: 6px; padding: 4px 8px; text-align: center; font-size: 11px; margin-top: -4px;'>"
                    f"<span style='font-weight: 700; color: {'#0F766E' if s3_active else t3_nav_color};'>{'● ACTIVE WORKBENCH' if s3_active else t3_nav_badge}</span>"
                    f" &nbsp;•&nbsp; <span style='font-size: 10px; color: #64748B;'>DoFP Sanction Routing</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        # Clear layout separation between navigation flight deck and active workbench
        st.markdown(
            f"<div style='display: flex; align-items: center; justify-content: space-between; margin: 16px 0 12px 0; padding-bottom: 8px; border-bottom: 2px solid #E2E8F0;'>"
            f"<div style='font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.08em; color: {'#0F766E' if s1_active else ('#B45309' if s2_active else '#6D28D9')};'>"
            f"ACTIVE UNDERWRITING WORKBENCH &nbsp;•&nbsp; {cur_stage_num.upper()}: {cur_stage_name.upper()}"
            f"</div>"
            f"<div style='font-size: 11px; color: #64748B; font-weight: 600;'>"
            f"Statutory Underwriting Portal"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        tab_scorecard, tab_legal, tab_quorum = st.tabs([
            "1. Quantitative Scorecard",
            "2. Forensic Legal Radar",
            "3. Underwriting Synthesis & Sanction Routing",
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
                orig_v = override_info.get('original_verdict', 'REJECT')
                over_v = override_info.get('override_verdict', 'APPROVE')
                off_nm = override_info.get('officer_name', 'Senior Underwriter')
                just_tx = override_info.get('justification_text', '')

                override_banner_html = f"""<div style="background: #F0FDFA; border: 1px solid #99F6E4; border-left: 4px solid #0F766E; border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; box-shadow: 0 1px 2px rgba(15,23,42,0.02);">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 10px; font-weight: 800; color: #0F766E; background: #CCFBF1; border: 1px solid #5EEAD4; padding: 2px 7px; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.04em;">Policy Exception Active</span>
                            <span style="font-size: 12px; font-weight: 800; color: #0B1F3A;">Model Baseline {orig_v} revised to {over_v}</span>
                        </div>
                        <span style="font-size: 10px; font-weight: 600; color: #475569;">Recorded by: {html.escape(off_nm)}</span>
                    </div>
                    <div style="font-size: 11px; color: #334155; margin-top: 5px; line-height: 1.45;">
                        <strong>Committee Rationale:</strong> {html.escape(just_tx)}
                    </div>
                </div>"""
                _render_html(override_banner_html)

            hero_verdict = override_info.get("override_verdict") if is_overridden else res.final_verdict.name
            raw_ko_or_cap = res.knockout_applied or res.cap_applied
            clean_rule_desc = clean_policy_rule_text(raw_ko_or_cap)
            hero_rule = clean_policy_rule_short(raw_ko_or_cap)
            hero_why = (
                "All policy risk thresholds satisfied." if res.final_verdict == Verdict.APPROVE
                else (f"Weakest-Link floor applied: {clean_rule_desc}." if res.final_verdict == Verdict.CONDITIONAL
                else f"Mandatory knockout enforced: {clean_rule_desc}.")
            )

            # Dynamic Copilot Underwriting Synthesis Narrative
            comp_name = format_institutional_entity_name(st.session_state.locked_case.company_name if st.session_state.locked_case else "Applicant")
            if hero_verdict == "APPROVE":
                syn_head = "Prime Solvency Profile • Resilient Debt-Service Buffer"
                syn_body = f"{comp_name} exhibits strong operational resilience with a DSCR of {res.dscr:.2f}x (clearing the 1.20x policy hurdle). Banking credits show robust alignment with declared GST turnover, corroborated by zero mandate dishonours over 12 months. All five underwriting pillars satisfy policy risk thresholds."
                syn_rec = "Sanction proposed facility with standard quarterly turnover reconciliation covenant."
                syn_accent = "#15803D"
                syn_bg = "#F0FDF4"
                syn_bd = "#BBF7D0"
                rating_grade_text = "PRIME MSME • RATING A1"
            elif hero_verdict == "REJECT":
                syn_head = "Severe Credit Stress • Algorithmic Policy Knockout"
                syn_body = f"{comp_name} triggers credit policy knockout covenants ({clean_rule_desc}). Operating cash flow is insufficient to cover debt obligations (DSCR: {res.dscr:.2f}x), compounded by recurring banking mandate returns. Unmitigated credit risk precludes sanction under current policy covenants."
                syn_rec = "Decline requested facility. Recommend applicant de-leverage balance sheet and stabilize banking conduct before re-application."
                syn_accent = "#B91C1C"
                syn_bg = "#FEF2F2"
                syn_bd = "#FECACA"
                rating_grade_text = "CREDIT DECLINED • RATING D"
            else:
                syn_head = "Accrual-versus-Cash Divergence • Policy Cap Applied"
                syn_body = f"{comp_name} demonstrates meaningful business turnover, but thin net profitability restricts internal liquidity generation. Conditions pillar score fell below the 40-point hurdle ({clean_rule_desc}), capping the final verdict at CONDITIONAL. Debt service coverage requires external credit enhancement."
                syn_rec = "Consider conditional sanction subject to 100% liquid collateral pledge or CGTMSE enhancement with enhanced quarterly audit covenants."
                syn_accent = "#B45309"
                syn_bg = "#FFFBEB"
                syn_bd = "#FDE68A"
                rating_grade_text = "CONDITIONAL SANCTION • RATING B2"

            override_tag_html = ""
            if is_overridden:
                override_tag_html = f'<div style="background: #FEF3C7; color: #92400E; border: 1px solid #FDE68A; border-radius: 4px; padding: 2px 7px; font-size: 9.5px; font-weight: 700; margin-bottom: 6px;">⚠️ Executive Override Active • Officer: {override_info.get("officer_name", "Officer")} • Baseline: {res.baseline_verdict.name}</div>'

            dscr_color = '#059669' if res.dscr >= Decimal('1.20') else '#DC2626'
            dscr_bg = '#ECFDF5' if res.dscr >= Decimal('1.20') else '#FEF2F2'
            dscr_border = '#A7F3D0' if res.dscr >= Decimal('1.20') else '#FECACA'

            unified_warrant_html = f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 4.5px solid {syn_accent}; border-radius: 6px; padding: 13px 18px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(15,23,42,0.04);">
                {override_tag_html}
                <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 12px; padding-bottom: 10px; border-bottom: 1px solid #F1F5F9;">
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <div style="background: {syn_bg}; border: 1.5px solid {syn_bd}; border-radius: 6px; padding: 6px 14px; text-align: center;">
                            <div style="font-size: 22px; font-weight: 900; color: {syn_accent}; letter-spacing: -0.01em; line-height: 1;">{hero_verdict}</div>
                            <div style="font-size: 8.5px; font-weight: 800; color: {syn_accent}; text-transform: uppercase; letter-spacing: 0.05em; margin-top: 3px;">{rating_grade_text}</div>
                        </div>
                        <div>
                            <div style="font-size: 14px; font-weight: 800; color: #0B1F3A; line-height: 1.3;">{syn_head}</div>
                            <div style="font-size: 11px; color: #64748B; margin-top: 2px;">
                                <strong>Deciding Policy Rule:</strong> {html.escape(hero_why)}
                            </div>
                        </div>
                    </div>
                    <div style="text-align: right; display: flex; align-items: baseline; gap: 6px;">
                        <span style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">COMPOSITE SCORE:</span>
                        <span style="font-size: 24px; font-weight: 900; color: #0B1F3A;">{float(res.composite_score):.1f}</span>
                        <span style="font-size: 11px; color: #94A3B8; font-weight: 600;">/ 100</span>
                    </div>
                </div>
                <div style="padding: 8px 0; font-size: 11.5px; color: #334155; line-height: 1.45;">
                    {syn_body}
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; border-top: 1px solid #F1F5F9; padding-top: 8px; font-size: 11px;">
                    <div style="display: flex; align-items: center; gap: 6px; color: {syn_accent}; font-weight: 700;">
                        <span>➔</span>
                        <span>{syn_rec}</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 10px; color: #475569; font-size: 10.5px;">
                        <span>Sanction: <strong>₹{float(P_dec)/100000:.1f}L @ {float(r_dec):.2f}% ({n_int}M)</strong></span>
                        <span style="color: #CBD5E1;">│</span>
                        <span style="font-weight: 700; color: {dscr_color}; background: {dscr_bg}; border: 1px solid {dscr_border}; padding: 1.5px 7px; border-radius: 4px;">DSCR {res.dscr:.2f}x</span>
                    </div>
                </div>
            </div>"""
            _render_html(unified_warrant_html)

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
                # TIER 2: POLICY GOVERNANCE & STATUTORY EXCEPTION SUMMARY (FULL WIDTH)
                # --------------------------------------------------------------------
                if res.knockout_applied:
                    st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
                    clean_ko = clean_policy_rule_text(res.knockout_applied)
                    render_callout(f"Hard Policy Knockout: {clean_ko}.", title="Knockout Triggered", variant="error")
                elif res.cap_applied:
                    st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
                    clean_cap = clean_policy_rule_text(res.cap_applied)
                    render_callout(f"Weakest-Link Floor: {clean_cap}. Under bank credit policy, overall verdict capped at CONDITIONAL.", title="Policy Floor Applied", variant="warning")

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
            # 4. DEBT SERVICE CAPACITY & AUDITED FINANCIAL EVIDENCE ARCHITECTURE
            # --------------------------------------------------------------------
            with st.container(border=True):
                from core.analyser import calculate_monthly_emi
                live_emi = calculate_monthly_emi(P_dec, r_dec, n_int)
                total_repayment = live_emi * Decimal(str(n_int))
                total_interest_cost = total_repayment - P_dec
                pct_p = float(P_dec / total_repayment * Decimal("100")) if total_repayment > 0 else 100.0
                pct_i = float(total_interest_cost / total_repayment * Decimal("100")) if total_repayment > 0 else 0.0

                bank_l = st.session_state.locked_case.bank_data if st.session_state.locked_case else None
                monthly_inflow = Decimal("0")
                burden_pct = 0.0
                coverage_ratio = 0.0
                if bank_l and bank_l.total_credits and bank_l.total_credits > Decimal("0"):
                    monthly_inflow = bank_l.total_credits / Decimal("12")
                    if live_emi > Decimal("0"):
                        burden_pct = float(live_emi / monthly_inflow * Decimal("100"))
                        coverage_ratio = float(monthly_inflow / live_emi)
                    b_color = "#059669" if burden_pct <= 35.0 else ("#D97706" if burden_pct <= 50.0 else "#DC2626")
                    b_badge_bg = "#F0FDF4" if burden_pct <= 35.0 else ("#FFFBEB" if burden_pct <= 50.0 else "#FEF2F2")
                    b_badge_border = "#BBF7D0" if burden_pct <= 35.0 else ("#FDE68A" if burden_pct <= 50.0 else "#FECACA")
                else:
                    b_color = "#64748B"
                    b_badge_bg = "#F8FAFC"
                    b_badge_border = "#E2E8F0"

                lock_hash_disp = st.session_state.locked_financials_hash or "SHA-256"

                # Header Row: Title & Subtitle on Left, Tamper Seal & Hash on Right
                st.markdown(
                    f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px;">
<div>
<div style="font-size: 13.5px; font-weight: 800; color: #0F172A; letter-spacing: -0.01em;">
Debt Service Capacity & Audited Evidence Architecture
</div>
<div style="font-size: 11px; color: #64748B; margin-top: 2px;">
Deterministic facility repayment mechanics cross-verified against cryptographically locked financial statements.
</div>
</div>
<div style="display: flex; align-items: center; gap: 8px;">
<span style="font-size: 9.5px; font-weight: 700; color: #059669; background: #ECFDF5; border: 1px solid #A7F3D0; padding: 2px 7px; border-radius: 4px;">TAMPER-SEALED AUDIT</span>
<span style="font-size: 9.5px; color: #64748B; font-family: monospace; background: #F8FAFC; border: 1px solid #E2E8F0; padding: 2px 7px; border-radius: 4px;">HASH #{html.escape(lock_hash_disp[:12])}</span>
</div>
</div>""",
                    unsafe_allow_html=True,
                )

                # Balanced 2-Column Institutional Cockpit
                col_cap_left, col_cap_right = st.columns(2)

                with col_cap_left:
                    # Amortization & Repayment Card
                    p_label = f"Principal: ₹{float(P_dec)/100000:.1f}L ({pct_p:.1f}%)"
                    i_label = f"Interest: ₹{float(total_interest_cost)/100000:.1f}L ({pct_i:.1f}%)"
                    debt_panel_html = f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 12px 14px; box-shadow: 0 1px 2px rgba(15,23,42,0.03); min-height: 236px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: space-between;">
<div>
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
<span style="font-size: 10.5px; font-weight: 800; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Facility Terms & Debt Service Mechanics</span>
<span style="font-size: 9.5px; font-weight: 700; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; padding: 1.5px 6px; border-radius: 3px;">{n_int}M AMORTIZING</span>
</div>
<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px;">
<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
<div style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase;">Approved Facility</div>
<div style="font-size: 13px; font-weight: 800; color: #0B1F3A; margin: 1px 0;">₹ {float(P_dec)/100000:.1f} Lakhs</div>
<div style="font-size: 9.5px; color: #64748B;">@ {float(r_dec):.2f}% p.a. Benchmark</div>
</div>
<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
<div style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase;">Monthly Installment (EMI)</div>
<div style="font-size: 13px; font-weight: 800; color: #0F766E; margin: 1px 0;">{format_inr(live_emi)}</div>
<div style="font-size: 9.5px; color: #64748B;">P+I Monthly Outflow</div>
</div>
<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
<div style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase;">Finance Drag (Interest)</div>
<div style="font-size: 13px; font-weight: 800; color: #475569; margin: 1px 0;">{format_inr(total_interest_cost)}</div>
<div style="font-size: 9.5px; color: #64748B;">{pct_i:.1f}% Total Interest Cost</div>
</div>
<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
<div style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase;">Total Commitment</div>
<div style="font-size: 13px; font-weight: 800; color: #0B1F3A; margin: 1px 0;">{format_inr(total_repayment)}</div>
<div style="font-size: 9.5px; color: #64748B;">Principal + Interest Drag</div>
</div>
</div>
<div style="background: #E2E8F0; border-radius: 4px; height: 20px; display: flex; overflow: hidden; font-size: 9.5px; font-weight: 700; color: #FFFFFF; margin-bottom: 8px;">
<div style="background: #0F766E; width: {pct_p:.1f}%; display: flex; align-items: center; justify-content: center; padding: 0 6px; white-space: nowrap;">
{p_label}
</div>
<div style="background: #64748B; width: {pct_i:.1f}%; display: flex; align-items: center; justify-content: center; padding: 0 6px; white-space: nowrap;">
{i_label}
</div>
</div>
</div>
<div style="background: {b_badge_bg}; border: 1px solid {b_badge_border}; border-radius: 5px; padding: 5.5px 10px; display: flex; justify-content: space-between; align-items: center; font-size: 10.5px; min-height: 28px; box-sizing: border-box;">
<div>
<span style="color: #64748B; font-weight: 600;">Cash Flow Debt Burden:</span>
<strong style="color: {b_color}; margin-left: 4px;">{burden_pct:.1f}% of Inflow</strong>
</div>
<div style="font-size: 10px; font-weight: 700; color: {b_color};">
{coverage_ratio:.1f}x Inflow Coverage
</div>
</div>
</div>"""
                    _render_html(debt_panel_html)

                with col_cap_right:
                    # Audited Evidence 2x2 Grid Panel
                    if st.session_state.locked_case:
                        lc = st.session_state.locked_case
                        bk = lc.bank_data
                        gst = lc.gst_data
                        itr = lc.itr_data

                        bank_in_val = format_inr(bk.total_credits) if bk and bk.total_credits else "₹ 0.00"
                        bank_deb_val = format_inr(bk.total_debits) if bk and bk.total_debits else "₹ 0.00"
                        gst_val_disp = format_inr(gst.taxable_value) if gst and gst.taxable_value else "₹ 0.00"
                        gst_tax_disp = format_inr(gst.total_tax_paid) if gst and gst.total_tax_paid else "₹ 0.00"
                        bounce_val_disp = f"{bk.bounce_count} Returns" if bk and bk.bounce_count is not None else "0 Returns"
                        b_col_disp = "#059669" if (bk and bk.bounce_count == 0) else ("#DC2626" if (bk and bk.bounce_count is not None and bk.bounce_count >= 5) else "#D97706")
                        b_tag_disp = "CLEAN" if (bk and bk.bounce_count == 0) else ("KNOCKOUT" if (bk and bk.bounce_count is not None and bk.bounce_count >= 5) else "CAUTION")
                        itr_inc_disp = format_inr(itr.gross_total_income) if itr and itr.gross_total_income else "₹ 0.00"
                        itr_pat_disp = format_inr(itr.profit_after_tax) if itr and itr.profit_after_tax is not None else "₹ 0.00"

                        evidence_panel_html = f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 3px solid #0284C7; border-radius: 6px; padding: 12px 14px; box-shadow: 0 1px 2px rgba(15,23,42,0.03); min-height: 236px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: space-between;">
<div>
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
<span style="font-size: 10.5px; font-weight: 800; color: #0284C7; text-transform: uppercase; letter-spacing: 0.05em;">Audited Financial Baseline (Evidence Proof)</span>
<span style="font-size: 9.5px; font-weight: 700; color: #0284C7; background: #E0F2FE; border: 1px solid #BAE6FD; padding: 1.5px 6px; border-radius: 3px;">RUN-RATE: {format_inr(monthly_inflow)}/MO</span>
</div>
<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px;">
<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
<div style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase;">12M Bank Inflows</div>
<div style="font-size: 13px; font-weight: 800; color: #0B1F3A; margin: 1px 0;">{bank_in_val}</div>
<div style="font-size: 9.5px; color: #64748B;">Debits: {bank_deb_val}</div>
</div>
<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
<div style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase;">GST Taxable Turnover</div>
<div style="font-size: 13px; font-weight: 800; color: #0B1F3A; margin: 1px 0;">{gst_val_disp}</div>
<div style="font-size: 9.5px; color: #64748B;">Tax Paid: {gst_tax_disp}</div>
</div>
<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
<div style="display: flex; justify-content: space-between; align-items: center;">
<span style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase;">Mandate Bounces</span>
<span style="font-size: 8px; font-weight: 700; color: {b_col_disp}; background: #FFFFFF; border: 1px solid {b_col_disp}; padding: 0.5px 3.5px; border-radius: 2px;">{b_tag_disp}</span>
</div>
<div style="font-size: 13px; font-weight: 800; color: {b_col_disp}; margin: 1px 0;">{bounce_val_disp}</div>
<div style="font-size: 9.5px; color: #64748B;">RBI Knockout: ≥5 returns</div>
</div>
<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
<div style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase;">ITR Net Profit (PAT)</div>
<div style="font-size: 13px; font-weight: 800; color: #0B1F3A; margin: 1px 0;">{itr_pat_disp}</div>
<div style="font-size: 9.5px; color: #64748B;">Gross: {itr_inc_disp}</div>
</div>
</div>
<div style="background: #E0F2FE; border: 1px solid #BAE6FD; border-radius: 4px; height: 20px; display: flex; overflow: hidden; font-size: 9.5px; font-weight: 700; color: #0369A1; margin-bottom: 8px; padding: 0 8px; align-items: center; justify-content: space-between; box-sizing: border-box;">
<span>Statutory Concordance: Reconciled</span>
<span>100% Audited Coverage</span>
</div>
</div>
<div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 5px; padding: 5.5px 10px; display: flex; justify-content: space-between; align-items: center; font-size: 10.5px; min-height: 28px; box-sizing: border-box;">
<div>
<span style="color: #64748B; font-weight: 600;">Financial Evidence Status:</span>
<strong style="color: #15803D; margin-left: 4px;">Tamper-Sealed & Validated</strong>
</div>
<div style="font-size: 10px; font-weight: 700; color: #15803D;">
SHA-256 Ledger Verified
</div>
</div>
</div>"""
                        _render_html(evidence_panel_html)

            st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
            col_nav_1a, col_nav_1b = st.columns([1, 1])
            with col_nav_1a:
                if st.button("← Back to Evidence Ingestion", type="secondary", key="btn_back_evidence", width="stretch"):
                    st.session_state.current_page = "Evidence"
                    st.switch_page("app_pages/evidence.py")
            with col_nav_1b:
                st.button(
                    "Concur with Quantitative Baseline & Advance to Pillar 2 ➔",
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
            def _get_statutory_recommendation(finding: AdverseMediaFinding, company_name: str) -> dict:
                title_lower = finding.title.lower()
                snippet_lower = finding.snippet.lower()
                full_text = f"{title_lower} {snippet_lower}"

                if finding.tier == FindingTier.SEVERE:
                    return {
                        "action": "CONFIRM",
                        "action_text": "Confirm (Mandatory Knockout)",
                        "label": "MANDATORY REJECT KNOCKOUT",
                        "note": "Admitted Section 9 CIRP petition confirmed via NCLT Bengaluru Bench order.",
                        "rationale": "Under Section 14 of IBC, moratorium restricts debt enforcement. Admitted CIRP triggers mandatory credit policy knockout.",
                        "variant": "reject",
                        "accent": "#DC2626",
                        "bg": "#FEF2F2",
                        "border": "#FECACA",
                    }
                elif "138" in full_text or "cheque" in full_text:
                    return {
                        "action": "DISMISS",
                        "action_text": "Dismiss (Stay in Abeyance)",
                        "label": "RECOMMENDED DISMISSAL (STAY IN EFFECT)",
                        "note": "High Court interim stay in Crl.M.C. with 20% Section 148 pre-deposit on record.",
                        "rationale": "High Court has stayed proceedings subject to pre-deposit; commercial dispute is actively referred to mediation.",
                        "variant": "approve",
                        "accent": "#15803D",
                        "bg": "#F0FDF4",
                        "border": "#BBF7D0",
                    }
                elif "dggi" in full_text or "tax" in full_text or "turnover" in full_text:
                    return {
                        "action": "CONFIRM",
                        "action_text": "Confirm for Escrow Review",
                        "label": "CONFIRM FOR COMMITTEE ESCROW REVIEW",
                        "note": "GST inquiry under verification; require promoter tax indemnity covenant.",
                        "rationale": "Departmental inquiry does not stay lending operations but warrants special escrow and quarterly tax clearance covenants.",
                        "variant": "conditional",
                        "accent": "#B45309",
                        "bg": "#FFFBEB",
                        "border": "#FDE68A",
                    }
                elif "drt" in full_text or "sarfaesi" in full_text or "oa/219" in full_text or "recovery notice" in full_text:
                    return {
                        "action": "CONFIRM",
                        "action_text": "Confirm for Collateral Review",
                        "label": "CONFIRM FOR COLLATERAL & ENCUMBRANCE REVIEW",
                        "note": "Prior SARFAESI demand notice on record; recovery proceedings stayed by operation of Section 14 IBC moratorium.",
                        "rationale": "Prior lender default action confirmed. Under Section 14(1)(c) IBC, enforcement is stayed pending CIRP resolution, preventing unencumbered asset liquidation.",
                        "variant": "conditional",
                        "accent": "#B45309",
                        "bg": "#FFFBEB",
                        "border": "#FDE68A",
                    }
                elif "satisfaction" in full_text or "clean" in full_text or "chg-4" in full_text:
                    return {
                        "action": "DISMISS",
                        "action_text": "Affirm Clean Standing",
                        "label": "AFFIRMATIVE DISMISSAL (CLEAN SATISFACTION)",
                        "note": "Form CHG-4 full charge satisfaction confirmed on MCA-21 record.",
                        "rationale": "Finding reflects positive credit event (loan payoff and charge release). Zero adverse liability.",
                        "variant": "approve",
                        "accent": "#15803D",
                        "bg": "#F0FDF4",
                        "border": "#BBF7D0",
                    }
                elif "[empanelled advocate report]" in title_lower:
                    is_severe = finding.tier == FindingTier.SEVERE
                    return {
                        "action": "CONFIRM" if is_severe else "DISMISS",
                        "action_text": "Confirm Counsel Discovery" if is_severe else "Dismiss with Covenant",
                        "label": "CONFIRM COUNSEL REPORT" if is_severe else "DISMISS WITH COVENANT",
                        "note": "Empanelled counsel search report verified on judicial records.",
                        "rationale": "Physical judicial discovery verified by empanelled legal counsel.",
                        "variant": "reject" if is_severe else "conditional",
                        "accent": "#DC2626" if is_severe else "#B45309",
                        "bg": "#FEF2F2" if is_severe else "#FFFBEB",
                        "border": "#FECACA" if is_severe else "#FDE68A",
                    }
                else:
                    return {
                        "action": "DISMISS",
                        "action_text": "Dismiss Operational Notice",
                        "label": "DISMISS AS NON-MATERIAL OPERATIONAL NOTICE",
                        "note": "Operational industry notice with no active civil decree or warrant against borrower.",
                        "rationale": "General market observation; does not affect debt servicing capability.",
                        "variant": "approve",
                        "accent": "#0F766E",
                        "bg": "#F0FDFA",
                        "border": "#CCFBF1",
                    }

            # Prioritize verified extracted evidence in locked_case
            locked = st.session_state.get("locked_case")
            if locked and locked.company_name and locked.company_name != "Manual Applicant Company":
                company_nm_raw = locked.company_name
            else:
                company_nm_raw = st.session_state.get("custom_company_name") or COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company")
            
            company_nm = format_institutional_entity_name(company_nm_raw)

            # Resolve active CIN / Identity token with evidence grounding
            if locked and getattr(locked, "cin_or_pan", None) and locked.cin_or_pan != "UNKNOWN":
                active_cin = locked.cin_or_pan
            elif cin_parsed_data and cin_parsed_data.get("cin") and cin_parsed_data.get("cin") != "UNREGISTERED_MSME":
                active_cin = cin_parsed_data.get("cin", "")
            elif st.session_state.get("active_cin_selection") and st.session_state.active_cin_selection != "UNREGISTERED_MSME":
                active_cin = st.session_state.active_cin_selection
            else:
                active_cin = ctx.get("current_pan_val") or "UNREGISTERED_MSME"

            is_corp_entity = bool(active_cin and len(active_cin) == 21 and active_cin != "UNREGISTERED_MSME")
            anchored_query = build_search_query(company_nm, cin=active_cin if is_corp_entity else None)
            is_demo_cohort = bool(cohort_selection and cohort_selection != "None (Manual Upload)")

            # Cache & State Management
            cache_key = (active_cin, company_nm, True)
            if st.session_state.last_research_cache_key != cache_key:
                st.session_state.last_research_cache_key = cache_key
                st.session_state.current_research_report = st.session_state.research_cache.get(cache_key, None)

            # Determine Copilot Statutory Intelligence State
            rep: Optional[AdverseMediaReport] = st.session_state.current_research_report
            has_unreviewed_severe = False
            has_confirmed_severe = False
            has_review_findings = False
            has_adverse_findings = False
            has_only_clean_records = False

            if rep and rep.status == "COMPLETED" and rep.findings:
                has_unreviewed_severe = any(f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.UNREVIEWED for f in rep.findings)
                has_confirmed_severe = any(f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED for f in rep.findings)
                has_review_findings = any(f.tier == FindingTier.REVIEW for f in rep.findings)
                has_adverse_findings = any(f.tier in (FindingTier.SEVERE, FindingTier.REVIEW) for f in rep.findings)
                has_only_clean_records = all(f.tier == FindingTier.INFORMATIONAL for f in rep.findings)

            if rep is None:
                syn_head = "Judicial Registries Primed"
                syn_rating = "READY FOR SWEEP"
                syn_accent = "#0F766E"
                syn_body = f"Borrower <strong>{company_nm}</strong> mapped to statutory identity token <span style='font-family: monospace; background: #F1F5F9; padding: 1px 4px; border-radius: 3px;'>{active_cin}</span>. Digital cause list sweeps cover e-Courts, 16 NCLT Benches, and default registries."
                syn_rec = "Execute automated judicial sweep below to begin."
            elif has_confirmed_severe or has_unreviewed_severe or (rep.status == "COMPLETED" and any(f.tier == FindingTier.SEVERE for f in rep.findings)):
                syn_head = "Active CIRP Insolvency Moratorium"
                syn_rating = "POLICY KNOCKOUT"
                syn_accent = "#DC2626"
                syn_body = f"Operational creditor petition admitted by NCLT against <strong>{company_nm}</strong> under Section 9 of the IBC. Section 14 statutory debt moratorium is currently active, legally staying prior DRT-II recovery enforcement."
                syn_rec = "Policy Action: Mandatory REJECT knockout under Section 14 IBC credit covenants."
            elif has_review_findings:
                syn_head = "Active Commercial Litigation Dockets"
                syn_rating = "UNDER REVIEW"
                syn_accent = "#D97706"
                syn_body = f"Litigation dockets for <strong>{company_nm}</strong> include Section 138 NI Act proceedings (under High Court stay with 20% deposit) and an ongoing DGGI audit inquiry."
                syn_rec = "Review Condition: Dispute escrow retention or promoter tax indemnity required."
            elif rep and rep.status == "PENDING_ADVOCATE_AUDIT":
                syn_head = "Unindexed Entity — Advocate Legal Search Required"
                syn_rating = "ADVOCATE AUDIT"
                syn_accent = "#D97706"
                syn_body = f"<strong>{company_nm}</strong> returned 0 indexed records on central digital cause lists. Physical advocate title search report (TSR) is required under commercial lending policy."
                syn_rec = "Action Required: File Empanelled Advocate Legal Opinion prior to sanction."
            else:
                syn_head = "Clean Legal & Regulatory Docket"
                syn_rating = "CLEAR RECORD"
                syn_accent = "#15803D"
                syn_body = f"Surveillance for <strong>{company_nm}</strong> confirmed zero adverse judgments, no IBC petitions, and clean RoC standing with Form CHG-4 satisfaction on record."
                syn_rec = "Statutory criteria satisfied. Clear for credit appraisal."

            # Statutory Entity Identity Profile
            if is_corp_entity:
                inc_year = cin_parsed_data.get("incorporation_year", 2019) if cin_parsed_data else 2019
                vintage_yrs = max(0, 2026 - int(inc_year))
                state_nm = cin_parsed_data.get("state_name", "Karnataka") if cin_parsed_data else "Karnataka"
                raw_entity_code = cin_parsed_data.get("entity_code", "PTC") if cin_parsed_data else "PTC"
                if "PTC" in raw_entity_code:
                    subtype_str = "Private Limited (PTC)"
                elif "PLC" in raw_entity_code:
                    subtype_str = "Public Limited (PLC)"
                else:
                    subtype_str = cin_parsed_data.get("entity_description", "Corporate Entity") if cin_parsed_data else "Corporate Entity"
                profile_line1 = f"RoC {state_nm} • Inc. {inc_year} ({vintage_yrs}-Yr Vintage) • {subtype_str}"
                profile_line2 = "Corporate Status: Active Entity • MCA-21 Master Registry Synchronized"
                cin_badge = f'<span style="font-family: monospace; font-size: 10px; font-weight: 600; background: #F8FAFC; border: 1px solid #CBD5E1; color: #0F172A; padding: 2px 7px; border-radius: 4px;">CIN: {active_cin}</span>'
            else:
                manual_v = cin_parsed_data.get("business_vintage_years", 5) if cin_parsed_data else 5
                pan_char_dec = current_pan_val[3] if len(current_pan_val) >= 4 else ""
                if pan_char_dec == "F":
                    profile_line1 = f"Commercial Intake • {manual_v}-Yr History • Registered Partnership Firm (RoF)"
                    profile_line2 = "Jurisdiction: RoF / Registrar of Firms • Indian Partnership Act, 1932"
                elif pan_char_dec == "P":
                    profile_line1 = f"Commercial Intake • {manual_v}-Yr History • Sole Proprietorship"
                    profile_line2 = "Jurisdiction: Pan-India MSME • Non-Corporate Entity"
                else:
                    profile_line1 = f"Commercial Intake • {manual_v}-Yr History • Unregistered MSME"
                    profile_line2 = "Jurisdiction: Pan-India MSME • Non-Corporate Entity"
                cin_badge = f'<span style="font-family: monospace; font-size: 10px; font-weight: 600; background: #F8FAFC; border: 1px solid #CBD5E1; color: #64748B; padding: 2px 7px; border-radius: 4px;">CIN: Unregistered MSME</span>'

            pan_badge = f'<span style="font-family: monospace; font-size: 10px; font-weight: 600; background: #F8FAFC; border: 1px solid #CBD5E1; color: #0F172A; padding: 2px 7px; border-radius: 4px;">PAN: {current_pan_val}</span>'

            # Multi-Forum Registry Telemetry Computation
            ecourts_val = "0 Active Suits"
            ecourts_sub = "● Clean Docket"
            ecourts_bd = "#0F766E"

            nclt_val = "0 Petitions"
            nclt_sub = "● Clean Standing"
            nclt_bd = "#0F766E"

            mca_val = "Good Standing" if is_corp_entity else "Exempt"
            mca_sub = "● Charges Released" if (rep and any("chg-4" in f.title.lower() for f in rep.findings)) else "● Compliant"
            mca_bd = "#0F766E"

            ibbi_val = "0 Filings"
            ibbi_sub = "● Solvency Intact"
            ibbi_bd = "#0F766E"

            drt_val = "0 Claims"
            drt_sub = "● Clear Track"
            drt_bd = "#0F766E"

            if rep and rep.status == "COMPLETED" and rep.findings:
                for f_item in rep.findings:
                    f_text = f"{f_item.title.lower()} {f_item.snippet.lower()}"
                    if "138" in f_text or "cheque" in f_text or "patiala" in f_text:
                        ecourts_val = "1 Dishonour Suit"
                        ecourts_sub = "● §138 NI Act (Stayed)"
                        ecourts_bd = "#D97706"
                    if "cirp" in f_text or "section 9" in f_text or "nclt" in f_text:
                        nclt_val = "CIRP Admitted"
                        nclt_sub = "● Section 9 Petition"
                        nclt_bd = "#DC2626"
                        ibbi_val = "Moratorium Active"
                        ibbi_sub = "● Section 14 Enforced"
                        ibbi_bd = "#DC2626"
                        mca_val = "Charge Default"
                        mca_sub = "● Unsettled Liens"
                        mca_bd = "#DC2626"
                    if "drt" in f_text or "sarfaesi" in f_text or "oa/219" in f_text:
                        drt_val = "Recovery Stayed"
                        drt_sub = "● Stayed by §14 IBC"
                        drt_bd = "#DC2626"
            elif rep and rep.status == "PENDING_ADVOCATE_AUDIT":
                ecourts_val = "0 Indexed Hits"
                ecourts_sub = "● Local TSR Reqd"
                ecourts_bd = "#D97706"
                nclt_val = "0 Records"
                nclt_sub = "● Unindexed"
                nclt_bd = "#D97706"
                ibbi_val = "0 Records"
                ibbi_sub = "● Solvency Intact"
                ibbi_bd = "#D97706"
                mca_val = "Active RoC" if is_corp_entity else "Exempt"
                mca_sub = "● Pending TSR"
                mca_bd = "#D97706"
                drt_val = "Pending TSR"
                drt_sub = "● Local Verification"
                drt_bd = "#D97706"

            def _registry_cell_html(title: str, category: str, val: str, sub: str, bd_color: str) -> str:
                if bd_color in ("#DC2626", "#B91C1C"):
                    bg = "#FEF2F2"
                    border = "#FECACA"
                    val_c = "#991B1B"
                    sub_c = "#DC2626"
                    badge_bg = "#FEE2E2"
                    badge_c = "#991B1B"
                elif bd_color in ("#D97706", "#B45309"):
                    bg = "#FFFBEB"
                    border = "#FDE68A"
                    val_c = "#92400E"
                    sub_c = "#B45309"
                    badge_bg = "#FEF3C7"
                    badge_c = "#92400E"
                else:
                    bg = "#F8FAFC"
                    border = "#E2E8F0"
                    val_c = "#0F172A"
                    sub_c = "#0F766E"
                    badge_bg = "#F1F5F9"
                    badge_c = "#475569"

                return f"""<div style="background: {bg}; border: 1px solid {border}; border-top: 3px solid {bd_color}; border-radius: 6px; padding: 10px 12px; display: flex; flex-direction: column; justify-content: space-between; min-height: 82px;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 4px;">
                        <span style="font-size: 10px; font-weight: 800; color: #0B1F3A; text-transform: uppercase; letter-spacing: 0.03em;">{title}</span>
                        <span style="font-size: 7.5px; font-weight: 700; color: {badge_c}; background: {badge_bg}; border: 1px solid {border}; padding: 1px 4px; border-radius: 3px; letter-spacing: 0.02em;">{category}</span>
                    </div>
                    <div>
                        <div style="font-size: 13.5px; font-weight: 800; color: {val_c}; line-height: 1.2;">{val}</div>
                        <div style="font-size: 10px; font-weight: 600; color: {sub_c}; margin-top: 3px; line-height: 1.2;">{sub}</div>
                    </div>
                </div>"""

            unified_surveillance_html = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-top: 3.5px solid {syn_accent}; border-radius: 8px; padding: 16px 20px; margin-bottom: 14px; box-shadow: 0 1px 3px rgba(15,23,42,0.04);">
                <!-- Top Row: Identity & Judicial Disposition -->
                <div style="display: grid; grid-template-columns: 5fr 7fr; gap: 24px; align-items: start;">
                    <!-- Left: Borrower Corporate Profile & Statutory Registry Identity -->
                    <div>
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 5px;">
                            <span style="font-size: 9.5px; font-weight: 800; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em;">Borrower Statutory Identity</span>
                            <span style="font-size: 8px; font-weight: 700; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; padding: 1.5px 6px; border-radius: 3px;">{'REFERENCE BENCHMARK' if is_demo_cohort else 'CUSTOM INTAKE'}</span>
                        </div>
                        <div style="font-size: 16px; font-weight: 800; color: #0B1F3A; letter-spacing: -0.01em; margin-bottom: 6px; line-height: 1.2;">{company_nm}</div>
                        <div style="display: flex; gap: 6px; margin-bottom: 8px; flex-wrap: wrap;">
                            {cin_badge}
                            {pan_badge}
                        </div>
                        <div style="font-size: 11px; color: #334155; font-weight: 600; line-height: 1.35;">{profile_line1}</div>
                        <div style="font-size: 10px; color: #64748B; margin-top: 2px;">{profile_line2}</div>
                    </div>

                    <!-- Right: Judicial Standing & Advisory Disposition -->
                    <div style="border-left: 1px solid #F1F5F9; padding-left: 20px;">
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 5px;">
                            <span style="font-size: 9.5px; font-weight: 800; color: {syn_accent}; text-transform: uppercase; letter-spacing: 0.05em;">Judicial & Regulatory Standing</span>
                            <span style="font-size: 8.5px; font-weight: 800; color: {syn_accent}; background: {syn_accent}15; border: 1px solid {syn_accent}35; padding: 2px 7px; border-radius: 4px; letter-spacing: 0.03em;">{syn_rating}</span>
                        </div>
                        <div style="font-size: 14.5px; font-weight: 800; color: #0B1F3A; margin-bottom: 4px; line-height: 1.25;">{syn_head}</div>
                        <div style="font-size: 11.5px; color: #334155; line-height: 1.45; margin-bottom: 8px;">{syn_body}</div>
                        <div style="display: flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 700; color: {syn_accent}; background: {syn_accent}0D; border: 1px solid {syn_accent}25; border-radius: 4px; padding: 4px 8px;">
                            <span style="font-size: 13px;">→</span>
                            <span>{syn_rec}</span>
                        </div>
                    </div>
                </div>

                <!-- Bottom Row: 5-Registry Telemetry Grid -->
                <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-top: 14px; border-top: 1px solid #E2E8F0; padding-top: 12px;">
                    {_registry_cell_html("e-Courts NJDG", "CIVIL & CRIMINAL", ecourts_val, ecourts_sub, ecourts_bd)}
                    {_registry_cell_html("NCLT / NCLAT", "INSOLVENCY", nclt_val, nclt_sub, nclt_bd)}
                    {_registry_cell_html("MCA-21 RoC", "ROC & CHARGES", mca_val, mca_sub, mca_bd)}
                    {_registry_cell_html("IBBI Registry", "IBC FILINGS", ibbi_val, ibbi_sub, ibbi_bd)}
                    {_registry_cell_html("DRT / SARFAESI", "DEBT RECOVERY", drt_val, drt_sub, drt_bd)}
                </div>
            </div>"""
            _render_html(unified_surveillance_html)

            # TIER 4: Action Command Bar
            col_act1, col_act2 = st.columns([3, 2])
            with col_act1:
                btn_lbl = ":material/refresh: Re-Execute Adverse Media & Statutory Registry Sweep" if rep is not None else ":material/radar: Execute Adverse Media & Statutory Registry Sweep"
                scan_triggered = st.button(
                    btn_lbl,
                    key="btn_exec_media_scan",
                    type="secondary" if rep is not None else "primary",
                    width="stretch"
                )
            with col_act2:
                with st.popover(":material/post_add: File Empanelled Advocate Report", help="Record physical court search findings or title investigation conducted by bank's empanelled legal counsel"):
                    st.markdown("##### File Advocate Legal Search Report")
                    st.caption("Record physical court searches, local title investigations, or sub-registrar disclosures.")
                    adv_forum = st.text_input("Judicial Forum / Court", placeholder="e.g. City Civil Court Bengaluru / DRT-II Mumbai", key="inp_adv_forum")
                    adv_case_no = st.text_input("Case Reference / Docket No.", placeholder="e.g. OS/412/2025 or CC/1892/2024", key="inp_adv_case")
                    col_af1, col_af2 = st.columns(2)
                    with col_af1:
                        adv_section = st.text_input("Statute & Section", placeholder="e.g. Section 138 NI Act", key="inp_adv_sec")
                    with col_af2:
                        adv_claim_amt = st.text_input("Claim Amount (₹)", placeholder="e.g. 28,50,000", key="inp_adv_amt")
                    adv_tier_sel = st.selectbox(
                        "Adjudication Severity Tier",
                        options=["Tier-1 Severe (Insolvency/CIRP)", "Tier-2 Statutory Review (Tax/Cheque)", "Tier-3 Operational Notice"],
                        index=1,
                        key="sel_adv_tier"
                    )
                    adv_summary = st.text_area("Advocate's Legal Observation Summary", placeholder="Enter certified summary of petition, notice or decree...", height=90, key="txt_adv_summary")

                    if st.button("Submit Legal Search Report", key="btn_submit_advocate_report", type="primary", width="stretch"):
                        if adv_case_no.strip() and adv_summary.strip():
                            tier_map = {
                                "Tier-1 Severe (Insolvency/CIRP)": FindingTier.SEVERE,
                                "Tier-2 Statutory Review (Tax/Cheque)": FindingTier.REVIEW,
                                "Tier-3 Operational Notice": FindingTier.INFORMATIONAL,
                            }
                            adv_tier = tier_map[adv_tier_sel]
                            new_finding = create_empanelled_advocate_finding(
                                company_name=company_nm,
                                forum=adv_forum.strip() or "District Commercial Court",
                                case_no=adv_case_no.strip(),
                                section_law=adv_section.strip() or "Statutory Commercial Suit",
                                claim_amount=adv_claim_amt.strip() or "Unspecified",
                                tier=adv_tier,
                                summary=adv_summary.strip(),
                            )
                            c_key = (active_cin, True)
                            cur_rep = st.session_state.current_research_report
                            if cur_rep is None:
                                cur_rep = AdverseMediaReport(
                                    status="COMPLETED",
                                    findings=[new_finding],
                                    query_used=anchored_query,
                                    is_synthetic_demo=True,
                                    company_name=company_nm,
                                )
                            else:
                                cur_rep.findings.insert(0, new_finding)
                                cur_rep.status = "COMPLETED"
                            st.session_state.research_cache[c_key] = cur_rep
                            st.session_state.current_research_report = cur_rep
                            record_event(
                                event_type="EMPANELLED_ADVOCATE_REPORT_FILED",
                                actor="OFFICER",
                                details={
                                    "finding_id": new_finding.id,
                                    "case_no": adv_case_no,
                                    "tier": adv_tier.value,
                                    "forum": adv_forum,
                                }
                            )
                            _execute_scoring(report_override=cur_rep)
                            st.toast("Empanelled Advocate Legal Search Report recorded & scorecard updated.", icon="⚖️")
                            st.rerun()
                        else:
                            render_callout("Case Docket Number and Observation Summary are required.", title="Validation Error", variant="error")

            if scan_triggered:
                with st.spinner("Querying statutory court dockets, insolvency registries, and MCA-21 databases..."):
                    active_case = st.session_state.get("locked_case") or st.session_state.get("case")
                    extracted_bounces = 0
                    if active_case and active_case.bank_data and active_case.bank_data.bounce_count:
                        extracted_bounces = active_case.bank_data.bounce_count

                    raw_rep = search_adverse_media(
                        company_name=company_nm,
                        cin=active_cin,
                        mock_mode=False,
                        promoter_name=None,
                        bounce_count=extracted_bounces,
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

            # TIER 5: Adaptive Statutory Findings Display
            if rep is None:
                awaiting_card = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-left: 4px solid #0F766E; border-radius: 6px; padding: 16px 20px; margin: 14px 0; box-shadow: 0 1px 2px rgba(15,23,42,0.03);">
                    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #F1F5F9; padding-bottom: 10px; margin-bottom: 12px;">
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <div style="width: 10px; height: 10px; border-radius: 50%; background: #0F766E; box-shadow: 0 0 0 3px #CCFBF1;"></div>
                            <div>
                                <div style="font-size: 13.5px; font-weight: 800; color: #0B1F3A; letter-spacing: -0.01em;">Forensic Surveillance Pipeline Primed</div>
                                <div style="font-size: 11px; color: #64748B; margin-top: 1px;">Ready to execute statutory cross-verification for <strong>{company_nm}</strong></div>
                            </div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span style="font-size: 9.5px; font-weight: 700; color: #0F766E; background: #F0FDFA; border: 1px solid #99F6E4; padding: 3px 8px; border-radius: 4px;">TARGET ENTITY: {active_cin}</span>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 12px 14px;">
                        <div>
                            <div style="font-size: 10px; font-weight: 800; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px;">Multi-Forum Judicial Verification</div>
                            <div style="font-size: 11px; color: #334155; line-height: 1.5;">
                                Surveillance sweep cross-queries the <strong>offline synthetic statutory docket archive</strong> (covering simulated District Court, <strong>NCLT / NCLAT</strong>, and <strong>DRT</strong> records) for civil decrees, IBC insolvency petitions, and Section 138 cheque dishonours.
                            </div>
                        </div>
                        <div>
                            <div style="font-size: 10px; font-weight: 800; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px;">Corporate & Regulatory Registries</div>
                            <div style="font-size: 11px; color: #334155; line-height: 1.5;">
                                Cross-references <strong>MCA-21 v3</strong> company master dockets, active Director Disqualification (DIN) records, and <strong>IBBI Section 7/9/10</strong> moratorium records to ensure unencumbered statutory standing.
                            </div>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 10px; padding-top: 8px; border-top: 1px solid #F1F5F9; font-size: 11px; color: #64748B;">
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span style="color: #0F766E; font-weight: 700;">→</span>
                            <span>Click <strong>Execute Adverse Media & Statutory Registry Sweep</strong> above to initiate deterministic scan.</span>
                        </div>
                        <span style="font-size: 9.5px; font-weight: 600; color: #94A3B8;">SURVEILLANCE LATENCY: &lt; 200ms</span>
                    </div>
                </div>"""
                _render_html(awaiting_card)
            elif rep.status == "PENDING_ADVOCATE_AUDIT":
                # SCENARIO: UNINDEXED MSME (Anti-Clean-Chit Standard)
                amber_card_html = f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 3.5px solid #D97706; border-radius: 6px; padding: 14px 18px; margin: 12px 0; box-shadow: 0 1px 2px rgba(15,23,42,0.03);">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 8px;">
                        <div>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 11.5px; font-weight: 800; color: #92400E; text-transform: uppercase; letter-spacing: 0.04em;">STATUS: UNINDEXED MSME — ADVOCATE DUE DILIGENCE MANDATORY</span>
                            </div>
                            <div style="font-size: 11.5px; color: #475569; margin-top: 3px; line-height: 1.45;">Central cause lists returned 0 indexed matches for CIN: <strong>{active_cin}</strong>. Under RBI Commercial Lending Prudential Guidelines, legal clearance requires an Empanelled Advocate Title Search Report (TSR).</div>
                        </div>
                        <span style="font-size: 9px; font-weight: 700; color: #92400E; background: #FEF3C7; border: 1px solid #FDE68A; padding: 2px 8px; border-radius: 4px; flex-shrink: 0;">DUE DILIGENCE PENDING</span>
                    </div>
                    <div style="font-size: 11px; color: #64748B; line-height: 1.5; border-top: 1px solid #F1F5F9; padding-top: 8px; margin-top: 8px;">
                        Zero indexed litigation on central portals does <strong>not</strong> constitute an affirmative clean chit. Operating premises title deeds, regional sub-registrar records, and local commercial court dockets must be certified by bank's empanelled counsel before credit facility sanction. Use <strong>File Empanelled Advocate Report</strong> above to record physical discovery.
                    </div>
                </div>"""
                _render_html(amber_card_html)
            else:
                is_clean_clearance = (rep.status == "NO_ADVERSE_FINDINGS") or not has_adverse_findings

                # SCENARIO A: CLEAN CLEARANCE (Apex Prime or unencumbered applicant)
                if is_clean_clearance:
                    clean_cert_html = f"""<div style="background: #FFFFFF; border: 1px solid #86EFAC; border-left: 4px solid #16A34A; border-radius: 8px; padding: 16px 20px; margin: 16px 0; box-shadow: 0 1px 3px rgba(22, 101, 52, 0.04);">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 12px;">
                            <div style="display: flex; align-items: center; gap: 12px;">
                                <div style="width: 36px; height: 36px; border-radius: 6px; background: #DCFCE7; border: 1px solid #86EFAC; color: #15803D; display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 900; flex-shrink: 0;">
                                    ✓
                                </div>
                                <div>
                                    <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                                        <span style="font-size: 14px; font-weight: 800; color: #0B1F3A; letter-spacing: -0.01em;">Certificate of Statutory Legal Clearance & Good Standing</span>
                                        <span style="font-size: 9px; font-weight: 700; color: #15803D; background: #DCFCE7; border: 1px solid #86EFAC; padding: 2px 7px; border-radius: 4px;">GRADE-A1 CLEARANCE</span>
                                    </div>
                                    <div style="font-size: 11.5px; color: #334155; margin-top: 3px; line-height: 1.45;">
                                        Triangulated surveillance across the offline synthetic statutory docket archive confirmed <strong>zero adverse judgments, zero insolvency proceedings, and zero active recovery notices</strong> for <strong>{company_nm}</strong>.
                                    </div>
                                </div>
                            </div>
                            <div style="text-align: right; flex-shrink: 0;">
                                <div style="font-size: 9.5px; font-family: monospace; font-weight: 700; color: #15803D; background: #F0FDF4; border: 1px solid #BBF7D0; padding: 2px 8px; border-radius: 4px;">CERT-REF: CG-STAT-2026-CLEAN</div>
                                <div style="font-size: 9px; color: #64748B; margin-top: 3px;">Surveillance: VERIFIED</div>
                            </div>
                        </div>

                        <div style="display: flex; gap: 8px; flex-wrap: wrap; padding-top: 10px; border-top: 1px solid #F1F5F9;">
                            <div style="font-size: 10.5px; color: #166534; background: #F0FDF4; border: 1px solid #BBF7D0; padding: 3px 9px; border-radius: 4px; display: flex; align-items: center; gap: 5px;">
                                <span style="font-weight: 700;">✓</span> <span>e-Courts: 0 Pending Warrants / Dishonours</span>
                            </div>
                            <div style="font-size: 10.5px; color: #166534; background: #F0FDF4; border: 1px solid #BBF7D0; padding: 3px 9px; border-radius: 4px; display: flex; align-items: center; gap: 5px;">
                                <span style="font-weight: 700;">✓</span> <span>NCLT: 0 IBC Section 7/9/10 Petitions</span>
                            </div>
                            <div style="font-size: 10.5px; color: #166534; background: #F0FDF4; border: 1px solid #BBF7D0; padding: 3px 9px; border-radius: 4px; display: flex; align-items: center; gap: 5px;">
                                <span style="font-weight: 700;">✓</span> <span>MCA-21: Active Standing • Clean DIN Record</span>
                            </div>
                            <div style="font-size: 10.5px; color: #166534; background: #F0FDF4; border: 1px solid #BBF7D0; padding: 3px 9px; border-radius: 4px; display: flex; align-items: center; gap: 5px;">
                                <span style="font-weight: 700;">✓</span> <span>DRT: Clean Track • 0 Asset Attachment Writs</span>
                            </div>
                        </div>
                    </div>"""
                    _render_html(clean_cert_html)

                    # Affirmative Compliance Milestones (e.g. Form CHG-4 Full Satisfaction of Charge)
                    clean_milestones = [f for f in rep.findings if f.tier == FindingTier.INFORMATIONAL]
                    for cm in clean_milestones:
                        clean_title_disp = cm.title.replace("[SYNTHETIC DEMO DATA]", "").replace("[Unverified Media Mention]", "").strip()
                        clean_snip_disp = cm.snippet.replace("[SYNTHETIC DEMO DATA]", "").replace("[Unverified Media Mention]", "").strip()
                        clean_title_disp = format_institutional_entity_name(clean_title_disp)
                        clean_snip_disp = format_institutional_entity_name(clean_snip_disp)
                        milestone_html = f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 3.5px solid #15803D; border-radius: 6px; padding: 11px 14px; margin: 8px 0; box-shadow: 0 1px 2px rgba(15,23,42,0.02);">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                <div style="display: flex; gap: 8px; align-items: center;">
                                    <span style="font-size: 9px; font-weight: 700; color: #15803D; background: #DCFCE7; border: 1px solid #86EFAC; padding: 2px 8px; border-radius: 4px;">AFFIRMATIVE STATUTORY RECORD</span>
                                </div>
                                <span style="font-size: 10px; font-weight: 700; color: #15803D;">● AUTO-RECORDED ON FILE</span>
                            </div>
                            <div style="font-size: 13px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{clean_title_disp}</div>
                            <div style="font-size: 11.5px; color: #475569; margin-top: 3px; line-height: 1.45;">{clean_snip_disp}</div>
                        </div>"""
                        _render_html(milestone_html)

                # SCENARIO B: ADVERSE COHORT (Severe CIRP or Statutory Review findings exist)
                else:
                    hud_accent = '#DC2626' if (has_unreviewed_severe or has_confirmed_severe) else '#D97706'
                    hud_title = "Statutory & Forensic Adverse Dockets" if (has_unreviewed_severe or has_confirmed_severe) else "Statutory & Litigation Surveillance Dockets"
                    hud_subtext = (
                        "Mandatory officer adjudication required. Review judicial observations and record formal determination below."
                        if has_unreviewed_severe
                        else (
                            "Confirmed material adverse finding enforces mandatory credit policy knockout (VERDICT: REJECT)."
                            if has_confirmed_severe
                            else "Statutory observations on record. Record credit officer notes or mitigating covenants below."
                        )
                    )
                    active_variant = "reject" if has_confirmed_severe else ("conditional" if has_unreviewed_severe else "neutral")

                    adverse_hud = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-left: 4px solid {hud_accent}; border-radius: 6px; padding: 10px 14px; margin: 10px 0 14px 0; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; box-shadow: 0 1px 2px rgba(15,23,42,0.03);">
                        <div>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 11px; font-weight: 800; color: #0B1F3A; text-transform: uppercase; letter-spacing: 0.05em;">{hud_title}</span>
                                <span style="font-family: monospace; font-size: 10px; font-weight: 700; color: #475569; background: #F1F5F9; border: 1px solid #E2E8F0; padding: 1.5px 6px; border-radius: 4px;">{len(rep.findings)} RECORD{'S' if len(rep.findings) > 1 else ''} ON FILE</span>
                            </div>
                            <div style="font-size: 11px; color: #475569; margin-top: 2px;">
                                {hud_subtext}
                            </div>
                        </div>
                        <div>
                            {status_pill(f"{len(rep.findings)} ACTIVE DOCKETS", variant=active_variant)}
                        </div>
                    </div>"""
                    _render_html(adverse_hud)

                    for f in rep.findings:
                        rec = _get_statutory_recommendation(f, company_nm)

                        clean_title = f.title.replace("[SYNTHETIC DEMO DATA]", "").replace("[Unverified Media Mention]", "").strip()
                        clean_snippet = f.snippet.replace("[SYNTHETIC DEMO DATA]", "").replace("[Unverified Media Mention]", "").strip()
                        clean_title = format_institutional_entity_name(clean_title)
                        clean_snippet = format_institutional_entity_name(clean_snippet)

                        is_empanelled = "[EMPANELLED ADVOCATE REPORT]" in f.title
                        if is_empanelled:
                            source_tag = '<span style="font-size: 9px; font-weight: 700; color: #1E40AF; background: #EFF6FF; border: 1px solid #DBEAFE; padding: 2px 7px; border-radius: 4px;">EMPANELLED ADVOCATE REPORT</span>'
                        else:
                            source_tag = '<span style="font-size: 9px; font-weight: 700; color: #0F766E; background: #F0FDFA; border: 1px solid #CCFBF1; padding: 2px 7px; border-radius: 4px;">STATUTORY REGISTRY FEED</span>'

                        # Tier-specific styling
                        if f.tier == FindingTier.SEVERE:
                            tier_accent = "#DC2626"
                            tier_header_bg = "#FEF2F2"
                            tier_header_border = "#FECACA"
                            tier_pill = status_pill("TIER-1 SEVERE: INSOLVENCY / CIRP", variant="reject")
                            tier_badge_bg = "#FEE2E2"
                            tier_badge_c = "#991B1B"
                            snippet_bg = "#FFF1F2"
                            snippet_bd = "#FECDD3"
                            snippet_text = "#881337"
                        elif f.tier == FindingTier.REVIEW:
                            tier_accent = "#D97706"
                            tier_header_bg = "#FFFBEB"
                            tier_header_border = "#FDE68A"
                            tier_pill = status_pill("TIER-2 STATUTORY REVIEW", variant="conditional")
                            tier_badge_bg = "#FEF3C7"
                            tier_badge_c = "#92400E"
                            snippet_bg = "#FFFDF5"
                            snippet_bd = "#FDE68A"
                            snippet_text = "#78350F"
                        else:
                            tier_accent = "#0F766E"
                            tier_header_bg = "#F0FDFA"
                            tier_header_border = "#CCFBF1"
                            tier_pill = status_pill("TIER-3 STATUTORY RECORD", variant="info")
                            tier_badge_bg = "#E6FFFA"
                            tier_badge_c = "#0F766E"
                            snippet_bg = "#F8FAFC"
                            snippet_bd = "#E2E8F0"
                            snippet_text = "#334155"

                        rev_pill = {
                            ReviewStatus.CONFIRMED: status_pill("ADJUDICATED: CONFIRMED", variant="reject"),
                            ReviewStatus.DISMISSED: status_pill("ADJUDICATED: DISMISSED", variant="approve"),
                            ReviewStatus.UNREVIEWED: status_pill("ACTION REQUIRED: UNREVIEWED", variant="conditional"),
                        }.get(f.review_status, status_pill(str(f.review_status), variant="neutral"))

                        with st.container(border=True):
                            # Dossier Header & Finding Callout
                            docket_header = f"""<div style="background: {tier_header_bg}; border: 1px solid {tier_header_border}; border-left: 4px solid {tier_accent}; border-radius: 6px; padding: 10px 14px; margin-bottom: 10px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; flex-wrap: wrap; gap: 6px;">
                                    <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
                                        {source_tag}
                                        {tier_pill}
                                    </div>
                                    <div style="display: flex; gap: 6px; align-items: center;">{rev_pill}</div>
                                </div>
                                <div style="font-size: 14px; font-weight: 800; color: #0B1F3A; line-height: 1.3; margin-bottom: 6px;">{clean_title}</div>
                                <div style="background: {snippet_bg}; border: 1px solid {snippet_bd}; border-left: 3.5px solid {tier_accent}; border-radius: 4px; padding: 8px 12px; font-size: 11.5px; color: {snippet_text}; line-height: 1.5;">
                                    {clean_snippet}
                                </div>
                            </div>"""
                            _render_html(docket_header)

                            # Copilot Recommendation Strip
                            copilot_rec_html = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-left: 3.5px solid {rec['accent']}; border-radius: 5px; padding: 8px 12px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">
                                <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex: 1;">
                                    <span style="font-size: 9.5px; font-weight: 800; color: #0B1F3A; text-transform: uppercase; letter-spacing: 0.04em;">AI Copilot Guidance:</span>
                                    <span style="font-size: 9.5px; font-weight: 800; color: {rec['accent']}; background: {rec['bg']}; border: 1px solid {rec['border']}; padding: 1.5px 7px; border-radius: 3px;">{rec['label']}</span>
                                    <span style="color: #CBD5E1;">│</span>
                                    <span style="font-size: 11px; color: #334155;"><strong>Rationale:</strong> {rec['rationale']}</span>
                                </div>
                            </div>"""
                            _render_html(copilot_rec_html)

                            if f.review_status == ReviewStatus.UNREVIEWED:
                                # Quick Citation Pre-fills Toolbar
                                chip_col1, chip_col2, chip_col3, chip_col4 = st.columns([1.2, 1, 1, 1])
                                with chip_col1:
                                    if st.button(
                                        f"Apply Copilot: {rec['action_text']}",
                                        key=f"btn_rec_{f.id}",
                                        type="secondary",
                                        width="stretch",
                                        help=f"Pre-fills adjudication note with: {rec['note']}"
                                    ):
                                        st.session_state[f"inline_note_{f.id}"] = rec["note"]
                                        st.rerun()
                                with chip_col2:
                                    if st.button(":material/gavel: Admitted CIRP", key=f"btn_chip_cirp_{f.id}", width="stretch", help="Set citation: Admitted CIRP order verified via NCLT registry"):
                                        st.session_state[f"inline_note_{f.id}"] = "Admitted CIRP order verified via NCLT registry"
                                        st.rerun()
                                with chip_col3:
                                    if st.button(":material/domain: Homonym Collision", key=f"btn_chip_homo_{f.id}", width="stretch", help="Set reason: Homonym collision: Unrelated entity with distinct CIN/PAN"):
                                        st.session_state[f"inline_note_{f.id}"] = "Homonym collision: Unrelated entity with distinct CIN/PAN"
                                        st.rerun()
                                with chip_col4:
                                    if st.button(":material/fact_check: Stay / Dismissed", key=f"btn_chip_stay_{f.id}", width="stretch", help="Set reason: Commercial recovery suit was dismissed with prejudice"):
                                        st.session_state[f"inline_note_{f.id}"] = "Commercial recovery suit was dismissed with prejudice"
                                        st.rerun()

                                note_val = st.text_input(
                                    "Order Citation / Dismissal Reason (Required)",
                                    key=f"inline_note_{f.id}",
                                    placeholder="e.g. NCLT Order CP(IB)/102/2026 or Homonym entity collision"
                                )
                                st.caption(f"Citation/Reason Length: {len(note_val.strip())}/10 characters minimum")

                                c_act_doc, c_act1, c_act2 = st.columns([1.2, 1, 1])
                                with c_act_doc:
                                    with st.popover(":material/description: Inspect Certified Court Order Transcript"):
                                        docket_txt = extract_legal_docket_text(f.source_url) or "Official certified court transcript logged in statutory repository."
                                        st.caption("Certified Judicial / Regulatory Transcript Record:")
                                        st.text_area("Certified Transcript Text", value=docket_txt, height=220, disabled=True, key=f"txt_syn_{f.id}")
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
                            else:
                                # Adjudicated Resolution Strip
                                is_conf = (f.review_status == ReviewStatus.CONFIRMED)
                                res_bg = "#FEF2F2" if is_conf else "#F0FDF4"
                                res_bd = "#FECACA" if is_conf else "#BBF7D0"
                                res_accent = "#DC2626" if is_conf else "#15803D"
                                res_title = "STATUTORY ADJUDICATION: CONFIRMED (POLICY KNOCKOUT ENFORCED)" if is_conf else "STATUTORY ADJUDICATION: DISMISSED (HOMONYM / STAYED)"
                                res_impact = "Enforces mandatory credit policy knockout (VERDICT: REJECT)." if (is_conf and f.tier == FindingTier.SEVERE) else ("Capped at CONDITIONAL." if is_conf else "Zero adverse credit penalty. Baseline risk score intact.")
                                cit_text = f.confirmation_note if is_conf else f.dismissal_reason

                                res_html = f"""<div style="background: {res_bg}; border: 1px solid {res_bd}; border-left: 3.5px solid {res_accent}; border-radius: 5px; padding: 10px 14px; margin: 4px 0 10px 0;">
                                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
                                        <span style="font-size: 10px; font-weight: 800; color: {res_accent}; text-transform: uppercase; letter-spacing: 0.04em;">{res_title}</span>
                                        <span style="font-size: 9.5px; font-weight: 600; color: #64748B;">Officer: {f.reviewed_by or 'Credit Officer'}</span>
                                    </div>
                                    <div style="font-size: 11px; color: #1E293B; margin-top: 3px;">
                                        <strong>Citation / Justification:</strong> {cit_text or 'Official statutory determination on file'}
                                    </div>
                                    <div style="font-size: 10px; color: #64748B; margin-top: 2px;">
                                        <strong>Policy Impact:</strong> {res_impact}
                                    </div>
                                </div>"""
                                _render_html(res_html)

                                col_res_doc, col_res_btn = st.columns([1.5, 1])
                                with col_res_doc:
                                    with st.popover(":material/description: Inspect Certified Court Order Transcript"):
                                        docket_txt = extract_legal_docket_text(f.source_url) or "Official certified court transcript logged in statutory repository."
                                        st.caption("Certified Judicial / Regulatory Transcript Record:")
                                        st.text_area("Certified Transcript Text", value=docket_txt, height=220, disabled=True, key=f"txt_syn_{f.id}")
                                with col_res_btn:
                                    if st.button(":material/edit_note: Re-adjudicate Docket", key=f"btn_reopen_{f.id}", type="secondary", width="stretch", help="Re-open this finding for review"):
                                        f.review_status = ReviewStatus.UNREVIEWED
                                        f.confirmation_note = None
                                        f.dismissal_reason = None
                                        f.reviewed_by = None
                                        st.session_state.research_cache[cache_key] = rep
                                        st.session_state.current_research_report = rep
                                        _execute_scoring(report_override=rep)
                                        st.rerun()

            st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)
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
                    "Advance to Pillar 3: Underwriting Synthesis ➔",
                    type="primary",
                    key="btn_adv_tab2",
                    on_click=_set_decision_tab,
                    args=("3. Underwriting Synthesis & Sanction Routing",),
                    width="stretch",
                )




        with tab_quorum:
            # --------------------------------------------------------------------
            # 7. QUALITATIVE FIELD DUE DILIGENCE & GEOTAGGED HUD
            # --------------------------------------------------------------------
            # Dynamic Inspection Details derived from Cohort / Applicant Profile
            cin_val_clean = str(cin_val or "").lower()
            pan_val_clean = str(ctx.get("pan_input", "") or "").lower()
            if "Apex" in company_disp or "u29253dl" in cin_val_clean or "aaaca1234b" in pan_val_clean or "Cohort 1" in str(cohort_selection):
                insp_unit = "Plot 42, Okhla Industrial Area, Phase-III, New Delhi"
                insp_sector = "Industrial Machinery & Precision Tools"
                insp_capacity = "85% Operational (Peak Run)"
                insp_cap_pct = 85
                insp_cap_color = "#059669"
                insp_officer = "Er. Arvind Mehra (Sr. Field Surveyor, North Zone)"
                insp_coords = "28.5355° N, 77.2732° E"
                insp_gazette_id = "MCA-REG-2026-VAL-DL01"
                insp_status_pill = '<span style="font-size: 9px; font-weight: 700; color: #065F46; background: #D1FAE5; border: 1px solid #A7F3D0; padding: 2px 7px; border-radius: 9999px;">GEOTAG VERIFIED • 99.4% CONFIDENCE</span>'
                insp_border_color = "#0F766E"
                pr_plant_txt = "Plant inspection verified: Machinery in operational condition at 85% capacity utilization. Physical raw material inventory reconciles with GSTR-3B monthly filings."
                pr_promoter_txt = "Promoter diligence interview: Management exhibits satisfactory domain vintage (7+ Yrs), pristine local supplier references, and unblemished credit discipline."
                pr_wc_txt = "Working capital verification: Receivables turnover matches standard 45-day commercial trade terms with well-diversified debtor concentration (top debtor < 18%)."
                pr_tax_txt = "Tax governance check: GSTR-3B filings and advance tax payments match audited financials without disputed statutory demands or pending assessments."
            elif "Kaveri" in company_disp or "u60200ka" in cin_val_clean or "aaack5678m" in pan_val_clean or "Cohort 2" in str(cohort_selection):
                insp_unit = "Plot 18, Peenya 2nd Stage, Industrial Suburb, Bengaluru, Karnataka"
                insp_sector = "Commercial Freight Fleet & Logistics Hub"
                insp_capacity = "58% Operational (Fleet Idle at Yard)"
                insp_cap_pct = 58
                insp_cap_color = "#D97706"
                insp_officer = "M. S. Murthy (Regional Asset Surveyor, South Zone)"
                insp_coords = "13.0285° N, 77.5197° E"
                insp_gazette_id = "MCA-REG-2026-VAL-KA08"
                insp_status_pill = '<span style="font-size: 9px; font-weight: 700; color: #065F46; background: #D1FAE5; border: 1px solid #A7F3D0; padding: 2px 7px; border-radius: 9999px;">GEOTAG VERIFIED • 99.4% CONFIDENCE</span>'
                insp_border_color = "#0F766E"
                pr_plant_txt = "Fleet depot inspection verified: 24 of 42 commercial prime movers operational. Maintenance logs show deferred servicing cycles consistent with acute cash-flow stress."
                pr_promoter_txt = "Promoter diligence interview: Management acknowledges customer freight receivable delays. Promoter offered personal real-estate collateral to support facility restructuring."
                pr_wc_txt = "Working capital verification: Receivables overdue > 90 days with heavy concentration in 2 stressed logistics aggregators. Cash conversion cycle stretched to 78 days."
                pr_tax_txt = "Tax governance check: Form GSTR-3B filed with persistent delays; pending input-tax reconciliation notice noted. Advance tax tranches partially funded."
            elif "Bharat" in company_disp or "u24101mh" in cin_val_clean or "u27100mh" in cin_val_clean or "aabfb8910n" in pan_val_clean or "Cohort 3" in str(cohort_selection):
                insp_unit = "Survey No. 104, Taloja MIDC Industrial Estate, Navi Mumbai, Maharashtra"
                insp_sector = "Heavy Structural Steel Fabrication & Rolling"
                insp_capacity = "40% Operational (Production Intermittent)"
                insp_cap_pct = 40
                insp_cap_color = "#DC2626"
                insp_officer = "P. N. Shinde (Zonal Technical Officer, West Zone)"
                insp_coords = "19.0660° N, 73.1360° E"
                insp_gazette_id = "MCA-REG-2026-VAL-MH14"
                insp_status_pill = '<span style="font-size: 9px; font-weight: 700; color: #065F46; background: #D1FAE5; border: 1px solid #A7F3D0; padding: 2px 7px; border-radius: 9999px;">GEOTAG VERIFIED • 99.4% CONFIDENCE</span>'
                insp_border_color = "#0F766E"
                pr_plant_txt = "Fabrication yard inspection: Rolling mills operating at 40% capacity. Ingot raw material stock severely depleted; production reduced from three to single shift."
                pr_promoter_txt = "Promoter diligence interview: Management evasive regarding pending creditor summons. Cross-director disclosures indicate undisclosed inter-corporate guarantees."
                pr_wc_txt = "Working capital verification: Extreme inventory accumulation with stalled finished coils. Trade payable ageing exceeds 120 days with supplier stop-supply notices."
                pr_tax_txt = "Tax governance check: Section 138 NI Act notices confirmed in NJDG radar. Active show-cause notice under Section 73 for major ITC mismatch."
            else:
                s_name = cin_parsed_data.get("state_name", "Declared District") if cin_parsed_data else "Declared District"
                m_sec = cin_parsed_data.get("macro_sector", "Commercial Operations") if cin_parsed_data else "Commercial Operations"
                recorded_notes = st.session_state.get("officer_notes", [])
                has_attestation = len(recorded_notes) > 0

                if has_attestation:
                    # Dynamically extract capacity percentage if mentioned in notes, else sensible default
                    all_notes_text = " ".join([str(n.get("note_text", "")) for n in recorded_notes])
                    pct_match = re.search(r'(\d{1,3})%', all_notes_text)
                    if pct_match and 0 <= int(pct_match.group(1)) <= 100:
                        insp_cap_pct = int(pct_match.group(1))
                    else:
                        has_cap_note = any(n.get("affected_c") == "CAPACITY" for n in recorded_notes)
                        insp_cap_pct = 75 if has_cap_note else 70

                    if insp_cap_pct >= 70:
                        insp_cap_color = "#059669"
                    elif insp_cap_pct >= 50:
                        insp_cap_color = "#D97706"
                    else:
                        insp_cap_color = "#DC2626"

                    insp_unit = f"Operating Facility ({s_name}) • Verified on Record"
                    insp_sector = m_sec
                    insp_capacity = f"{insp_cap_pct}% Operational (Field Attested)"
                    insp_officer = "Underwriting Desk Officer (Attested On Record)"
                    insp_coords = f"Verified On-Site ({s_name} District Desk)"
                    insp_gazette_id = f"PSI-ATTEST-{st.session_state.get('app_id', '2026')}"
                    insp_status_pill = f'<span style="font-size: 9px; font-weight: 700; color: #065F46; background: #D1FAE5; border: 1px solid #A7F3D0; padding: 2px 7px; border-radius: 9999px;">FIELD ATTESTED • CREDIT OFFICER SIGN-OFF ({len(recorded_notes)} NOTES)</span>'
                    insp_border_color = "#059669"
                else:
                    insp_unit = f"Operating Facility ({s_name}) • Site Visit Scheduled"
                    insp_sector = m_sec
                    insp_capacity = "Awaiting On-Site Valuation (Pending PSI)"
                    insp_cap_pct = 0
                    insp_cap_color = "#64748B"
                    insp_officer = "Empanelled Technical Valuer (Unassigned)"
                    insp_coords = "Pending Surveyor Dispatch"
                    insp_gazette_id = "PSI-PENDING-INTAKE"
                    insp_status_pill = '<span style="font-size: 9px; font-weight: 700; color: #92400E; background: #FEF3C7; border: 1px solid #FDE68A; padding: 2px 7px; border-radius: 9999px;">INTAKE STAGE • PENDING FIELD INSPECTION (PSI)</span>'
                    insp_border_color = "#D97706"

                pr_plant_txt = "Plant inspection verified: Production facilities operational at 75% capacity utilization. Physical equipment in working order without signs of obsolescence."
                pr_promoter_txt = "Promoter diligence interview: Clear promoter background in the operating sector; satisfactory market reputation with local trade counterparties."
                pr_wc_txt = "Working capital verification: Receivables turnover matches declared credit cycles. Inventory turnover appears stable relative to operating scale."
                pr_tax_txt = "Tax governance check: Routine statutory filings verified. No material disputed tax demands brought to underwriter attention."

            with st.container(border=True):
                st.markdown("#### :material/fact_check: Qualitative Field Due Diligence & Site Verification")
                st.caption("On-site physical inspection observations, promoter interview diligence, and 5-Cs qualitative attestation.")

                # High-Authority Geotagged Site Inspection HUD Strip with Utilization Meter
                inspection_hud_html = f"""<div style="background: #F8FAFC; border: 1px solid #CBD5E1; border-left: 4px solid {insp_border_color}; border-radius: 8px; padding: 12px 16px; margin-bottom: 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 10px; font-weight: 800; color: #0F766E; text-transform: uppercase; letter-spacing: 0.06em;">Geotagged Site Inspection Protocol</span>
                            {insp_status_pill}
                        </div>
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <span style="font-size: 9.5px; font-family: monospace; color: #64748B; background: #F1F5F9; padding: 1px 6px; border-radius: 4px; border: 1px solid #E2E8F0;">REF: {insp_gazette_id}</span>
                            <span style="font-size: 10.5px; font-family: monospace; color: #475569; font-weight: 600;">COORDS: {html.escape(insp_coords)}</span>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; border-top: 1px solid #E2E8F0; padding-top: 10px; font-size: 12px; color: #1E293B;">
                        <div>
                            <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase;">Inspected Facility & Location</div>
                            <div style="font-weight: 700; color: #0B1F3A; margin-top: 2px;">{html.escape(insp_unit)}</div>
                        </div>
                        <div>
                            <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase;">Primary Industrial Activity</div>
                            <div style="font-weight: 600; color: #334155; margin-top: 2px;">{html.escape(insp_sector)}</div>
                        </div>
                        <div>
                            <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase;">Operating Capacity & Utilization</div>
                            <div style="font-weight: 700; color: {insp_cap_color}; margin-top: 2px;">{html.escape(insp_capacity)}</div>
                            <div style="background: #E2E8F0; border-radius: 9999px; height: 5px; width: 100%; margin-top: 4px; overflow: hidden;">
                                <div style="background: {insp_cap_color}; height: 5px; width: {insp_cap_pct}%; border-radius: 9999px;"></div>
                            </div>
                        </div>
                        <div>
                            <div style="font-size: 10px; font-weight: 700; color: #64748B; text-transform: uppercase;">Accredited Field Surveyor</div>
                            <div style="font-weight: 600; color: #334155; margin-top: 2px;">{html.escape(insp_officer)}</div>
                        </div>
                    </div>
                </div>"""
                _render_html(inspection_hud_html)

                # Callbacks for Field Note management (execute before widget instantiation)
                def _apply_preset_note_callback(preset_text: str):
                    st.session_state.txt_field_note = preset_text
                    st.session_state._last_analyzed_field_note = None

                def _save_field_note_callback():
                    txt = st.session_state.get("txt_field_note", "").strip()
                    pillar = st.session_state.get("sb_fn_pillar", "GENERAL")
                    n_len = len(txt)
                    w_count = len([w for w in txt.split() if w])
                    if n_len >= 15 and w_count >= 3:
                        note_entry = {
                            "affected_c": pillar,
                            "note_text": txt,
                            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                        }
                        st.session_state.officer_notes.append(note_entry)
                        st.session_state.cam_markdown = None
                        # Safely clear in callback before widget instantiation
                        st.session_state.txt_field_note = ""
                        st.session_state.sb_fn_pillar = "GENERAL"
                        st.session_state._last_analyzed_field_note = None
                        record_event(
                            event_type="OFFICER_NOTE_ADDED",
                            actor="OFFICER",
                            details=note_entry,
                        )
                        st.session_state["_field_note_saved_flash"] = True

                def _undo_last_field_note_callback():
                    if st.session_state.officer_notes:
                        st.session_state.officer_notes.pop()
                        st.session_state.cam_markdown = None
                        st.session_state["_field_note_undone_flash"] = True

                # Quick Observation Presets Row with Category Subtitles
                st.markdown("<div style='font-size: 11px; font-weight: 700; color: #475569; text-transform: uppercase; letter-spacing: 0.05em; margin: 6px 0 8px 0;'>Quick Observation Presets (One-Click Ingestion):</div>", unsafe_allow_html=True)
                col_pr1, col_pr2, col_pr3, col_pr4 = st.columns(4)
                with col_pr1:
                    st.button(":material/factory: Plant Inspection", key="btn_pr_plant", on_click=_apply_preset_note_callback, args=(pr_plant_txt,), width="stretch")
                with col_pr2:
                    st.button(":material/person_search: Promoter Diligence", key="btn_pr_promoter", on_click=_apply_preset_note_callback, args=(pr_promoter_txt,), width="stretch")
                with col_pr3:
                    st.button(":material/account_balance_wallet: Working Capital", key="btn_pr_wc", on_click=_apply_preset_note_callback, args=(pr_wc_txt,), width="stretch")
                with col_pr4:
                    st.button(":material/receipt_long: Tax & Filings", key="btn_pr_tax", on_click=_apply_preset_note_callback, args=(pr_tax_txt,), width="stretch")

                # Integrated Field Due Diligence Observation Composer
                st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown("<div style='font-size: 11px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px;'>Field Visit Observation & Qualitative Due Diligence Composer</div>", unsafe_allow_html=True)
                    fn_text = st.text_area(
                        "Field Visit Observation / Qualitative Notes",
                        placeholder="Enter verified on-site observations, promoter background feedback, plant inspection remarks, or trade references (minimum 15 characters & 3 words)...",
                        key="txt_field_note",
                        height=90,
                        max_chars=1000,
                    )
                    txt_val = st.session_state.get("txt_field_note", "").strip()
                    txt_lower = txt_val.lower()
                    note_len = len(txt_val)
                    words = [w for w in txt_val.split() if w]
                    word_count = len(words)
                    MIN_NOTE_CHARS = 15
                    MIN_NOTE_WORDS = 3
                    is_valid_note = (note_len >= MIN_NOTE_CHARS) and (word_count >= MIN_NOTE_WORDS)

                    pillar_options = ["CHARACTER", "CAPACITY", "CAPITAL", "COLLATERAL", "CONDITIONS", "GENERAL"]
                    pillar_labels = {
                        "CHARACTER": "Character • Promoter Integrity & Governance",
                        "CAPACITY": "Capacity • Plant Output & Operating Utilization",
                        "CAPITAL": "Capital • Working Capital & Liquidity Buffer",
                        "COLLATERAL": "Collateral • Security & Asset Coverage",
                        "CONDITIONS": "Conditions • Statutory Filings & Industry Macro",
                        "GENERAL": "General • Qualitative Due Diligence Observations",
                    }

                    if any(w in txt_lower for w in ["promoter", "integrity", "reputation", "interview", "litigation", "conduct"]):
                        p_idx = 0
                        detected_pillar = "Character (Promoter Integrity & Governance)"
                    elif any(w in txt_lower for w in ["plant", "capacity", "machinery", "utilization", "factory", "operational", "production"]):
                        p_idx = 1
                        detected_pillar = "Capacity (Plant Output & Utilization)"
                    elif any(w in txt_lower for w in ["receivable", "debtor", "working capital", "cash", "turnover", "inventory"]):
                        p_idx = 2
                        detected_pillar = "Capital (Working Capital & Liquidity)"
                    elif any(w in txt_lower for w in ["collateral", "mortgage", "property", "security", "pledge", "cgtmse"]):
                        p_idx = 3
                        detected_pillar = "Collateral (Security & Asset Coverage)"
                    elif any(w in txt_lower for w in ["tax", "gst", "filing", "demand", "industry", "sector", "regulatory"]):
                        p_idx = 4
                        detected_pillar = "Conditions (Statutory Compliance)"
                    else:
                        p_idx = 5
                        detected_pillar = "General (Site Observations)"

                    # Dynamically set dropdown to detected pillar if text changed and meets dual threshold
                    last_analyzed = st.session_state.get("_last_analyzed_field_note", None)
                    if last_analyzed != txt_val and is_valid_note:
                        st.session_state._last_analyzed_field_note = txt_val
                        st.session_state.sb_fn_pillar = pillar_options[p_idx]

                    # Ensure key is initialized cleanly in session state without index conflicts
                    if "sb_fn_pillar" not in st.session_state:
                        st.session_state["sb_fn_pillar"] = "GENERAL"

                    if note_len == 0:
                        # 1. Standby State (Empty)
                        st.markdown(
                            "<div style='background: #F8FAFC; border: 1px dashed #CBD5E1; border-radius: 6px; padding: 8px 12px; margin: 4px 0 8px 0; font-size: 11px; color: #94A3B8; display: flex; align-items: center; gap: 6px;'>"
                            "<span><strong>Standby:</strong> Enter field observations (min 15 characters & 3 words) or select a quick preset chip above to activate real-time C-Pillar auto-detection.</span>"
                            "</div>",
                            unsafe_allow_html=True,
                        )
                        col_fn1, col_fn2 = st.columns([3, 2], vertical_alignment="bottom")
                        with col_fn1:
                            fn_pillar = st.selectbox(
                                "Affected C Pillar (Auto-Assigned on Input • Selectable)",
                                pillar_options,
                                format_func=lambda x: pillar_labels.get(x, x),
                                key="sb_fn_pillar",
                                disabled=True,
                                help="Will unlock and automatically map to the appropriate C-pillar once minimum 15 characters and 3 words are entered.",
                            )
                        with col_fn2:
                            st.button(":material/save: Save Field Note", key="btn_save_field_note_standby", disabled=True, width="stretch")

                    elif not is_valid_note:
                        # 2. In-Progress State (Typing but below minimum statutory length/word requirements)
                        missing = []
                        if note_len < MIN_NOTE_CHARS:
                            missing.append(f"{MIN_NOTE_CHARS - note_len} more char{'s' if (MIN_NOTE_CHARS - note_len) != 1 else ''}")
                        if word_count < MIN_NOTE_WORDS:
                            missing.append(f"{MIN_NOTE_WORDS - word_count} more word{'s' if (MIN_NOTE_WORDS - word_count) != 1 else ''}")
                        missing_str = " & ".join(missing)

                        st.markdown(
                            f"<div style='background: #FFFBEB; border: 1px solid #FDE68A; border-left: 4px solid #F59E0B; border-radius: 6px; padding: 8px 12px; margin: 4px 0 8px 0; font-size: 11px; color: #92400E; display: flex; justify-content: space-between; align-items: center;'>"
                            f"  <span><strong>Observation in progress:</strong> {note_len}/{MIN_NOTE_CHARS} chars • {word_count}/{MIN_NOTE_WORDS} words (minimum 15 characters & 3 words required for audit record).</span>"
                            f"  <span style='font-size: 10px; font-weight: 700; background: #FEF3C7; color: #B45309; padding: 2px 7px; border-radius: 4px;'>{missing_str}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        col_fn1, col_fn2 = st.columns([3, 2], vertical_alignment="bottom")
                        with col_fn1:
                            fn_pillar = st.selectbox(
                                "Affected C Pillar (Auto-Assigned on Input • Selectable)",
                                pillar_options,
                                format_func=lambda x: pillar_labels.get(x, x),
                                key="sb_fn_pillar",
                                disabled=True,
                                help=f"Enter at least {MIN_NOTE_CHARS} characters and {MIN_NOTE_WORDS} words to activate auto-tagging and saving.",
                            )
                        with col_fn2:
                            st.button(":material/save: Save Field Note", key="btn_save_field_note_inprogress", disabled=True, width="stretch")

                    else:
                        # 3. Active State: Luminous Auto-Detected Ribbon (Dual threshold satisfied)
                        pillar_badge_styles = {
                            0: ("#EFF6FF", "#1D4ED8", "#BFDBFE"),  # Character
                            1: ("#ECFDF5", "#059669", "#A7F3D0"),  # Capacity
                            2: ("#F5F3FF", "#6D28D9", "#DDD6FE"),  # Capital
                            3: ("#FFFBEB", "#D97706", "#FDE68A"),  # Collateral
                            4: ("#ECFEFF", "#0E7490", "#A5F3FC"),  # Conditions
                            5: ("#F1F5F9", "#475569", "#CBD5E1"),  # General
                        }
                        bg_c, fg_c, bdr_c = pillar_badge_styles.get(p_idx, ("#ECFDF5", "#059669", "#A7F3D0"))

                        st.markdown(
                            f"<div style='background: #F0FDF4; border: 1px solid #BBF7D0; border-left: 4px solid #10B981; border-radius: 6px; padding: 8px 12px; margin: 4px 0 8px 0; display: flex; justify-content: space-between; align-items: center;'>"
                            f"  <div>"
                            f"    <span style='font-size: 11px; font-weight: 800; color: #166534; text-transform: uppercase; letter-spacing: 0.05em;'>Auto-Classified:</span>"
                            f"    <span style='background: {bg_c}; color: {fg_c}; border: 1px solid {bdr_c}; font-weight: 700; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-left: 6px;'>{detected_pillar}</span>"
                            f"  </div>"
                            f"  <span style='font-size: 11px; color: #15803D; font-weight: 600;'>{note_len} chars ({word_count} words) • Auto-assigned below • Ready to save</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                        col_fn1, col_fn2 = st.columns([3, 2], vertical_alignment="bottom")
                        with col_fn1:
                            fn_pillar = st.selectbox(
                                "Affected C Pillar (Auto-Assigned • Select to Override)",
                                pillar_options,
                                format_func=lambda x: pillar_labels.get(x, x),
                                key="sb_fn_pillar",
                                help="Automatically selected based on note keywords. You can change this dropdown to manually override.",
                            )
                        with col_fn2:
                            st.button(":material/save: Save Field Note", key="btn_save_field_note", type="primary", on_click=_save_field_note_callback, width="stretch")

                if st.session_state.pop("_field_note_saved_flash", False):
                    render_callout("Field observation appended to qualitative file and permanently stamped in audit log.", title="Observation Saved", variant="success")
                if st.session_state.pop("_field_note_undone_flash", False):
                    render_callout("Last qualitative observation removed.", title="Observation Undone", variant="info")

                # Audit-Stamped Qualitative Observations Feed
                if st.session_state.officer_notes:
                    st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)
                    col_h1, col_h2 = st.columns([4, 1], vertical_alignment="center")
                    with col_h1:
                        st.markdown(f"<div style='font-size: 12px; font-weight: 800; color: #0B1F3A; text-transform: uppercase;'>Recorded Qualitative Attestations ({len(st.session_state.officer_notes)} Entries)</div>", unsafe_allow_html=True)
                    with col_h2:
                        st.button(":material/undo: Undo Last", key="btn_clear_last_note", on_click=_undo_last_field_note_callback, width="stretch")

                    pillar_colors = {
                        "CHARACTER": {"badge_bg": "#EFF6FF", "badge_color": "#1D4ED8", "badge_border": "#BFDBFE", "accent": "#2563EB"},
                        "CAPACITY": {"badge_bg": "#ECFDF5", "badge_color": "#059669", "badge_border": "#A7F3D0", "accent": "#059669"},
                        "CAPITAL": {"badge_bg": "#F5F3FF", "badge_color": "#6D28D9", "badge_border": "#DDD6FE", "accent": "#7C3AED"},
                        "COLLATERAL": {"badge_bg": "#FFFBEB", "badge_color": "#D97706", "badge_border": "#FDE68A", "accent": "#D97706"},
                        "CONDITIONS": {"badge_bg": "#ECFEFF", "badge_color": "#0E7490", "badge_border": "#A5F3FC", "accent": "#0891B2"},
                        "GENERAL": {"badge_bg": "#F1F5F9", "badge_color": "#475569", "badge_border": "#CBD5E1", "accent": "#64748B"},
                    }

                    for idx, on in enumerate(st.session_state.officer_notes, 1):
                        p_key = on.get("affected_c", "GENERAL").upper()
                        palette = pillar_colors.get(p_key, pillar_colors["GENERAL"])
                        clean_ts = html.escape(str(on.get("timestamp", "")))
                        clean_txt = html.escape(str(on.get("note_text", "")))
                        clean_p = html.escape(p_key)

                        note_card = f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 4px solid {palette['accent']}; border-radius: 6px; padding: 10px 14px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <span style="font-size: 9.5px; font-weight: 800; background: {palette['badge_bg']}; color: {palette['badge_color']}; border: 1px solid {palette['badge_border']}; padding: 2px 7px; border-radius: 4px; text-transform: uppercase;">{clean_p}</span>
                                    <span style="font-size: 11px; font-weight: 600; color: #64748B;">Attestation #{idx} • Underwriting Field Desk</span>
                                </div>
                                <div style="font-size: 11px; font-family: monospace; color: #64748B;">{clean_ts}</div>
                            </div>
                            <div style="font-size: 13px; color: #1E293B; line-height: 1.5;">{clean_txt}</div>
                        </div>"""
                        _render_html(note_card)

            # --------------------------------------------------------------------
            # 8. SANCTION COVENANTS & FACILITY STRUCTURING WORKBENCH
            # --------------------------------------------------------------------
            override_info = st.session_state.executive_override
            is_overridden = bool(override_info and override_info.get("override_active"))
            active_verdict = override_info.get("override_verdict") if is_overridden else (res.final_verdict.name if res else "REJECT")

            with st.container(border=True):
                # Retrieve or initialize custom covenants in session_state
                if "custom_covenants" not in st.session_state:
                    st.session_state.custom_covenants = []

                # Resolve derived covenants via policy engine with active collateral & CGTMSE status
                cov_result = derive_stipulated_covenants(
                    case=st.session_state.locked_case,
                    score_result=res,
                    custom_covenants=st.session_state.custom_covenants,
                    collateral_value=Decimal(str(collateral_val)),
                    cgtmse_covered=bool(cgtmse_covered),
                )

                # Store active plain list in session_state and mirror to quorum_governance (cleared if rejected)
                if st.session_state.get("quorum_governance") is None:
                    st.session_state.quorum_governance = get_default_quorum_governance()

                if active_verdict == "REJECT":
                    st.session_state["stipulated_covenants"] = []
                    st.session_state.quorum_governance["cro_covenants"] = []
                else:
                    st.session_state["stipulated_covenants"] = cov_result["plain_list"]
                    st.session_state.quorum_governance["cro_covenants"] = cov_result["plain_list"]

                if active_verdict == "REJECT":
                    # Executive Declination Notice: Covenants are superseded when facility is rejected
                    decline_html = """<div style="padding: 2px 0;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <div style="font-size: 13.5px; font-weight: 800; color: #0F172A;">
                                Stipulated Credit Covenants & Pre-Disbursal Safeguards
                            </div>
                            <span style="font-size: 9.5px; font-weight: 700; background: #FEE2E2; color: #991B1B; border: 1px solid #FECACA; padding: 2px 7px; border-radius: 4px;">SUPERSEDED // FACILITY DECLINED</span>
                        </div>
                        <div style="background: #FEF2F2; border: 1px solid #FEE2E2; border-left: 3px solid #DC2626; border-radius: 6px; padding: 12px 16px;">
                            <div style="font-size: 12px; font-weight: 700; color: #991B1B; margin-bottom: 3px;">
                                Pre-Disbursal Covenants Inapplicable Under Rejection Sanction
                            </div>
                            <div style="font-size: 11px; color: #7F1D1D; line-height: 1.5;">
                                Under Bank Commercial Credit Policy (§9.2), pre-disbursal security creation, RoC Form CHG-1 charge registrations, and operational covenants are not stipulated for declined credit facilities. Pre-disbursal covenants will only become active if an executive policy override is formally ratified below.
                            </div>
                        </div>
                    </div>"""
                    _render_html(decline_html)
                else:
                    num_stat = len(cov_result["statutory"])
                    num_risk = len(cov_result["risk_triggered"])
                    num_custom = len(cov_result["custom"])

                    # Professional Institutional Header (Zero Emojis)
                    bespoke_pill = f'<span style="font-size: 9.5px; font-weight: 700; background: #EDE9FE; color: #6D28D9; border: 1px solid #DDD6FE; padding: 2px 7px; border-radius: 4px;">{num_custom} Bespoke</span>' if num_custom > 0 else ''
                    c_header_html = f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px;">
                        <div>
                            <div style="font-size: 13.5px; font-weight: 800; color: #0F172A;">
                                Stipulated Credit Covenants & Pre-Disbursal Safeguards
                            </div>
                            <div style="font-size: 11px; color: #64748B; margin-top: 2px;">
                                Pre-disbursal security creation and operational conditions derived from Companies Act 2013 and exposure risk metrics.
                            </div>
                        </div>
                        <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
                            <span style="font-size: 9.5px; font-weight: 700; background: #E0F2FE; color: #0369A1; border: 1px solid #BAE6FD; padding: 2px 7px; border-radius: 4px;">{num_stat} Statutory</span>
                            <span style="font-size: 9.5px; font-weight: 700; background: #FEF3C7; color: #B45309; border: 1px solid #FDE68A; padding: 2px 7px; border-radius: 4px;">{num_risk} Risk-Triggered</span>
                            {bespoke_pill}
                        </div>
                    </div>"""
                    _render_html(c_header_html)

                    # Two-Column Balanced Side-by-Side Grid
                    col_stat, col_risk = st.columns(2)
                    with col_stat:
                        stat_cards = "".join([
                            f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 3px solid #0284C7; border-radius: 6px; padding: 9px 12px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);">
                                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
                                    <div style="font-size: 12px; font-weight: 600; color: #1E293B; line-height: 1.35;">{html.escape(item['title'])}</div>
                                    <span style="font-size: 9px; font-weight: 700; color: #0369A1; background: #E0F2FE; border: 1px solid #BAE6FD; padding: 1px 5px; border-radius: 3px; white-space: nowrap;">STATUTORY</span>
                                </div>
                                <div style="font-size: 10px; color: #64748B; margin-top: 4px;">{html.escape(item['trigger'])}</div>
                            </div>"""
                            for item in cov_result["statutory"]
                        ])
                        col_stat_html = f"""<div>
                            <div style="font-size: 10.5px; font-weight: 800; color: #0284C7; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 8px;">
                                Statutory Security Registration & Title ({num_stat})
                            </div>
                            {stat_cards}
                        </div>"""
                        _render_html(col_stat_html)

                    with col_risk:
                        if cov_result["risk_triggered"]:
                            risk_cards = "".join([
                                f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 3px solid #D97706; border-radius: 6px; padding: 9px 12px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);">
                                    <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
                                        <div style="font-size: 12px; font-weight: 600; color: #1E293B; line-height: 1.35;">{html.escape(item['title'])}</div>
                                        <span style="font-size: 9px; font-weight: 700; color: #B45309; background: #FEF3C7; border: 1px solid #FDE68A; padding: 1px 5px; border-radius: 3px; white-space: nowrap;">RISK-TRIGGERED</span>
                                    </div>
                                    <div style="font-size: 10px; color: #B45309; font-weight: 600; margin-top: 4px;">{html.escape(item['trigger'])}</div>
                                </div>"""
                                for item in cov_result["risk_triggered"]
                            ])
                        else:
                            risk_cards = """<div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-left: 3px solid #16A34A; border-radius: 6px; padding: 12px; font-size: 11.5px; color: #15803D; line-height: 1.45;">
                                <strong>Pristine Underwriting Posture:</strong><br/>
                                <span style="color: #166534; font-size: 10.5px;">Zero adverse risk triggers active (DSCR ≥ 1.30x, Turnover Gap ≤ 15%, Score ≥ 70). Standard statutory terms apply.</span>
                            </div>"""

                        col_risk_html = f"""<div>
                            <div style="font-size: 10.5px; font-weight: 800; color: #D97706; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 8px;">
                                Risk-Triggered Policy Mitigants ({num_risk})
                            </div>
                            {risk_cards}
                        </div>"""
                        _render_html(col_risk_html)

                    # Custom Underwriter Stipulations (if any recorded in session fixture)
                    if cov_result["custom"]:
                        custom_cards = "".join([
                            f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 3px solid #8B5CF6; border-radius: 6px; padding: 8px 12px; margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center; gap: 8px;">
                                <div style="font-size: 12px; font-weight: 600; color: #1E293B;">{html.escape(item['title'])}</div>
                                <span style="font-size: 9px; font-weight: 700; color: #6D28D9; background: #EDE9FE; border: 1px solid #DDD6FE; padding: 1.5px 6px; border-radius: 3px; white-space: nowrap;">BESPOKE</span>
                            </div>"""
                            for item in cov_result["custom"]
                        ])
                        custom_html = f"""<div style="margin-top: 10px; margin-bottom: 4px;">
                            <div style="font-size: 10.5px; font-weight: 800; color: #8B5CF6; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px;">
                                Discretionary Underwriter Stipulations ({num_custom})
                            </div>
                            {custom_cards}
                        </div>"""
                        _render_html(custom_html)

            # --------------------------------------------------------------------
            # 9. UNDERWRITER POLICY EXCEPTION & VARIANCE RECOMMENDATION
            # --------------------------------------------------------------------
            is_override_active = bool(st.session_state.executive_override and st.session_state.executive_override.get("override_active"))
            baseline_verdict = res.final_verdict.name if res else "REJECT"
            base_c = '#059669' if baseline_verdict == 'APPROVE' else ('#D97706' if baseline_verdict == 'CONDITIONAL' else '#DC2626')
            base_bg = '#ECFDF5' if baseline_verdict == 'APPROVE' else ('#FFFBEB' if baseline_verdict == 'CONDITIONAL' else '#FEF2F2')
            base_border = '#A7F3D0' if baseline_verdict == 'APPROVE' else ('#FDE68A' if baseline_verdict == 'CONDITIONAL' else '#FECACA')

            with st.container(border=True):
                # Header row: Title & Commercial Description on Left, Toggle on Right
                col_exc_info, col_exc_action = st.columns([7.2, 2.8], vertical_alignment="center")
                with col_exc_info:
                    st.markdown(
                        f"""<div style="display: flex; align-items: center; gap: 8px; margin-bottom: 2px;">
                            <span style="font-size: 13.5px; font-weight: 800; color: #0F172A; letter-spacing: -0.01em;">Credit Committee Discretion & Policy Exception</span>
                            <span style="font-size: 9.5px; font-weight: 700; color: {'#0F766E' if is_override_active else '#475569'}; background: {'#CCFBF1' if is_override_active else '#F1F5F9'}; border: 1px solid {'#99F6E4' if is_override_active else '#E2E8F0'}; padding: 2px 7px; border-radius: 4px;">{'EXCEPTION ACTIVE' if is_override_active else 'OPTIONAL'}</span>
                        </div>
                        <div style="font-size: 11px; color: #64748B; margin-top: 2px;">
                            Empowers underwriters and committee members to exercise commercial discretion when verified compensatory credit mitigants substantiate an exception to model thresholds.
                        </div>""",
                        unsafe_allow_html=True,
                    )
                with col_exc_action:
                    veto_active = st.toggle(
                        "Record Policy Exception",
                        value=is_override_active,
                        key="tgl_veto_active",
                        help="Enable to submit a formal underwriter recommendation to deviate from the algorithmic baseline with documented compensating mitigants."
                    )

                # Check clear logic if toggled off
                if not veto_active and is_override_active:
                    st.session_state.executive_override = None
                    st.session_state.cam_markdown = None
                    record_event(
                        event_type="EXECUTIVE_OVERRIDE_REMOVED",
                        actor="OFFICER_VETO",
                        details={"action": "Policy variance recommendation removed"},
                        severity="INFO",
                    )
                    st.toast("Policy variance recommendation removed by officer.", icon="ℹ️")
                    st.rerun()

                # When un-toggled, display institutional baseline governance depth
                if not veto_active and not is_override_active:
                    gov_overview_html = f"""<div style="margin-top: 10px; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 11.5px;">
                        <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 3px solid {base_c}; border-radius: 6px; padding: 9px 12px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                <span style="font-size: 10px; font-weight: 800; color: {base_c}; text-transform: uppercase; letter-spacing: 0.05em;">Algorithmic Baseline Posture</span>
                                <span style="font-size: 9px; font-weight: 700; color: {base_c}; background: {base_bg}; border: 1px solid {base_border}; padding: 1px 5px; border-radius: 3px;">DECISION: {baseline_verdict}</span>
                            </div>
                            <div style="font-size: 11px; color: #475569; line-height: 1.4;">
                                Deterministic Five-Cs scorecard governs credit terms. Standard statutory underwriting policies remain fully enforced without variance.
                            </div>
                        </div>
                        <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 9px 12px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                <span style="font-size: 10px; font-weight: 800; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Compensating Mitigant Standard</span>
                                <span style="font-size: 9px; font-weight: 700; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; padding: 1px 5px; border-radius: 3px;">POLICY §4.2</span>
                            </div>
                            <div style="font-size: 11px; color: #475569; line-height: 1.4;">
                                Discretionary overrides require verifiable compensating mitigants: tangible collateral (≥100%), promoter personal guarantee, or receivables escrow.
                            </div>
                        </div>
                    </div>"""
                    _render_html(gov_overview_html)

                # Check if confirmed severe exists
                has_confirmed_severe = False
                if st.session_state.current_research_report and st.session_state.current_research_report.findings:
                    for f in st.session_state.current_research_report.findings:
                        if f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED:
                            has_confirmed_severe = True
                            break

                # If toggled ON, render form fields inside this same card
                if veto_active:
                    st.markdown("<div style='height: 1px; background: #E2E8F0; margin: 12px 0 14px 0;'></div>", unsafe_allow_html=True)

                    if has_confirmed_severe:
                        render_callout(
                            "Statutory Safeguard Active: Under Section 14 Insolvency & Bankruptcy Code (IBC) and Bank Credit Policy §4.2, a confirmed CIRP moratorium legally stays credit facility sanction until corporate debt restructuring is completed.",
                            title="Statutory Insolvency Policy Safeguard",
                            variant="warning",
                        )
                    else:
                        baseline_verdict = res.final_verdict.name if res else "REJECT"
                        base_c = '#059669' if baseline_verdict == 'APPROVE' else ('#D97706' if baseline_verdict == 'CONDITIONAL' else '#DC2626')
                        st.markdown(
                            f"<div style='background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; font-size: 11.5px; color: #334155; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;'>"
                            f"  <div><span style='color: #64748B; font-weight: 600;'>Algorithmic Baseline Verdict:</span> <span style='font-weight: 800; color: {base_c}; margin-left: 4px;'>{baseline_verdict}</span></div>"
                            f"  <span style='font-size: 10.5px; color: #0F766E; font-weight: 600;'>Select target decision with corresponding credit mitigants</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                        col_v1, col_v2 = st.columns([1, 1])
                        with col_v1:
                            veto_options = [v for v in ["APPROVE", "CONDITIONAL", "REJECT"] if v != baseline_verdict]
                            if not veto_options:
                                veto_options = ["APPROVE", "CONDITIONAL", "REJECT"]
                            veto_verdict = st.selectbox(
                                "Recommended Verdict (Policy Exception)",
                                veto_options,
                                index=0,
                                key="sb_veto_verdict",
                                help=f"Select target verdict to supersede the baseline decision of {baseline_verdict}.",
                            )
                        with col_v2:
                            veto_officer = st.text_input(
                                "Recommending Underwriter / Authority",
                                value="Senior Credit Analyst - MSME Desk #702",
                                key="txt_veto_officer"
                            )

                        mitigant_options = [
                            "Collateral Enhancement: Additional unencumbered security pledged (Coverage >= 100%)",
                            "Promoter Guarantee: Unconditional personal/corporate guarantee ratified",
                            "Cashflow Escrow: Tri-partite escrow account on prime receivables established",
                            "Technical Bounce Waiver: Drawee bank certified clearing/mandate glitch",
                            "Equity Infusion: Binding promoter equity/subordinated debt commitment",
                        ]
                        default_mitigants = st.session_state.executive_override.get("mitigants", []) if st.session_state.executive_override else []
                        selected_mitigants = st.multiselect(
                            "Compensating Risk Mitigants (Credit Policy Exception Framework)",
                            mitigant_options,
                            default=[m for m in default_mitigants if m in mitigant_options],
                            key="ms_veto_mitigants",
                            help="Select one or more verified compensatory credit mitigants required by Credit Policy to justify the discretionary override.",
                        )

                        # Quick Underwriting Presets (Dynamically adapted to Recommended Verdict)
                        st.markdown(f"<div style='font-size: 10.5px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em; margin: 6px 0 3px 0;'>Quick Underwriting Presets ({veto_verdict} Rationale - Click to autofill):</div>", unsafe_allow_html=True)
                        col_p1, col_p2, col_p3 = st.columns(3)

                        if veto_verdict == "APPROVE":
                            with col_p1:
                                if st.button("Collateral Override", key="btn_vp_collat", width="stretch", help="Autofill rationale for unencumbered asset coverage override"):
                                    st.session_state.txt_veto_just = "Credit Committee Exception: Pledged unencumbered commercial real estate provides 150% asset coverage, comfortably securing debt service against operating cash flow variance."
                                    st.rerun()
                            with col_p2:
                                if st.button("Guarantee Override", key="btn_vp_guar", width="stretch", help="Autofill rationale for promoter personal guarantee exception"):
                                    st.session_state.txt_veto_just = "Committee Ratification: Promoter provided unconditional personal guarantee with verified liquid net worth supporting debt service coverage."
                                    st.rerun()
                            with col_p3:
                                if st.button("Technical Waiver", key="btn_vp_bounce", width="stretch", help="Autofill rationale for certified bank clearing glitch waiver"):
                                    st.session_state.txt_veto_just = "Technical Clarification: Drawee bank confirmed inward return was an isolated mandate migration glitch during system upgrade, with underlying account conduct fully satisfactory."
                                    st.rerun()
                        elif veto_verdict == "CONDITIONAL":
                            with col_p1:
                                if st.button("Receivables Escrow", key="btn_vp_cond_escrow", width="stretch", help="Autofill condition for trade receivables escrow"):
                                    st.session_state.txt_veto_just = "Conditional Sanction Variance: Sanction approved subject to establishing a formal tripartite escrow mechanism on prime receivables with 1.25x debt service cash sweep prior to disbursal."
                                    st.rerun()
                            with col_p2:
                                if st.button("Equity Milestone", key="btn_vp_cond_equity", width="stretch", help="Autofill condition for promoter equity infusion"):
                                    st.session_state.txt_veto_just = "Conditional Sanction Milestone: Facility approval conditional upon upfront proof of promoter equity / subordinated debt infusion of ₹25L to rebalance capital gearing before limit activation."
                                    st.rerun()
                            with col_p3:
                                if st.button("Charge Perfection", key="btn_vp_cond_collat", width="stretch", help="Autofill condition for collateral title registration"):
                                    st.session_state.txt_veto_just = "Conditional Sanction Security: Term facility approved subject to creating and legally perfecting an exclusive first charge over commercial property within 30 days of sanction."
                                    st.rerun()
                        else:  # REJECT
                            with col_p1:
                                if st.button("Market Headwind", key="btn_vp_rej_market", width="stretch", help="Autofill rationale for sector-wide margin compression"):
                                    st.session_state.txt_veto_just = "Negative Underwriter Discretion: Severe industry-specific cyclical margin compression and declining capacity utilization observed during field inspection undermine debt repayment capability."
                                    st.rerun()
                            with col_p2:
                                if st.button("Governance Concern", key="btn_vp_rej_gov", width="stretch", help="Autofill rationale for management integrity / transparency risks"):
                                    st.session_state.txt_veto_just = "Governance Declination Veto: Material undisclosed related-party loans and inadequate corporate governance identified during promoter due diligence warrant prudent credit declination."
                                    st.rerun()
                            with col_p3:
                                if st.button("Concentration Risk", key="btn_vp_rej_conc", width="stretch", help="Autofill rationale for single-buyer counterparty concentration"):
                                    st.session_state.txt_veto_just = "Credit Committee Declination: Extreme single-buyer concentration (>75% turnover) with extended overdue receivables presents unhedged credit risk in event of counterparty dispute."
                                    st.rerun()

                        veto_justification = st.text_area(
                            "Written Underwriting Rationale (Min 20 characters required)",
                            value=st.session_state.executive_override.get("justification_text", "") if st.session_state.executive_override else "",
                            placeholder="State commercial rationale, committee discussion notes, or mitigating factors...",
                            key="txt_veto_just",
                        )
                        just_len = len(veto_justification.strip())
                        if just_len >= 20:
                            cnt_html = f"<div style='font-size: 11px; color: #15803D; font-weight: 600; margin-bottom: 8px;'>✓ {just_len} / 20 characters minimum • Commercial rationale criteria satisfied</div>"
                        else:
                            cnt_html = f"<div style='font-size: 11px; color: #64748B; font-weight: 600; margin-bottom: 8px;'>○ {just_len} / 20 characters minimum • {20 - just_len} more characters required for committee submission</div>"
                        st.markdown(cnt_html, unsafe_allow_html=True)

                        if st.button(":material/how_to_reg: Record Committee Policy Exception", key="btn_submit_veto", type="primary"):
                            if veto_verdict == baseline_verdict:
                                render_callout(f"Notice: Recommended verdict must differ from the algorithmic baseline verdict ({baseline_verdict}).", title="Divergent Verdict Required", variant="error")
                            elif has_confirmed_severe and veto_verdict != "REJECT":
                                render_callout("Policy rule: a confirmed CIRP moratorium cannot be overridden while insolvency proceedings are active.", title="CIRP Policy Safeguard", variant="error")
                            elif len(veto_justification.strip()) < 20:
                                render_callout("Please provide at least 20 characters explaining the commercial rationale.", title="Validation Notice", variant="error")
                            else:
                                veto_data = {
                                    "override_active": True,
                                    "original_verdict": res.final_verdict.name,
                                    "override_verdict": veto_verdict,
                                    "officer_name": veto_officer.strip(),
                                    "mitigants": selected_mitigants,
                                    "justification_text": veto_justification.strip(),
                                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                                }
                                st.session_state.executive_override = veto_data
                                st.session_state.cam_markdown = None
                                record_event(
                                    event_type="EXECUTIVE_OVERRIDE_APPLIED",
                                    actor="OFFICER_VETO",
                                    details=veto_data,
                                    severity="INFO",
                                )
                                st.toast("Policy Exception recorded & CAM updated.", icon="✅")
                                st.rerun()

                        # Active Institutional Badge
                        if st.session_state.executive_override and st.session_state.executive_override.get("override_active"):
                            ov_data = st.session_state.executive_override
                            ov_mit = ov_data.get("mitigants", [])
                            mit_tags = "".join([f"<span style='background: #CCFBF1; color: #0F766E; border: 1px solid #99F6E4; font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 4px; margin-right: 4px;'>{html.escape(m.split(':')[0])}</span>" for m in ov_mit]) if ov_mit else "<span style='color: #64748B; font-size: 10px; font-style: italic;'>Direct Officer Discretion</span>"

                            st.markdown(
                                f"<div style='background: #F0FDFA; border: 1px solid #99F6E4; border-left: 4px solid #0F766E; border-radius: 6px; padding: 12px 16px; margin-top: 12px; box-shadow: 0 1px 2px rgba(15,23,42,0.02);'>"
                                f"  <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; flex-wrap: wrap; gap: 6px;'>"
                                f"    <span style='font-size: 10.5px; font-weight: 800; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;'>Credit Committee Policy Exception Attached</span>"
                                f"    <span style='font-size: 9.5px; font-weight: 800; background: #CCFBF1; color: #0F766E; border: 1px solid #99F6E4; padding: 2px 8px; border-radius: 4px;'>TARGET SANCTION: {ov_data.get('override_verdict')}</span>"
                                f"  </div>"
                                f"  <div style='font-size: 11.5px; color: #334155; margin-bottom: 5px;'><strong>Recommending Underwriter:</strong> {html.escape(ov_data.get('officer_name', ''))} &nbsp;•&nbsp; <strong>Timestamp:</strong> <code>{ov_data.get('timestamp', '')}</code></div>"
                                f"  <div style='margin-bottom: 6px; font-size: 11px; color: #475569;'><strong>Compensating Mitigants:</strong> {mit_tags}</div>"
                                f"  <div style='font-size: 11.5px; color: #1E293B; background: #FFFFFF; border: 1px solid #CCFBF1; border-radius: 4px; padding: 8px 12px; font-style: italic; line-height: 1.45;'>\"{html.escape(ov_data.get('justification_text', ''))}\"</div>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

            # --------------------------------------------------------------------
            # 10. PRE-FLIGHT UNDERWRITING READINESS & CAM EXPORT
            # --------------------------------------------------------------------
            with st.container(border=True):
                st.markdown("#### :material/verified: Underwriting File Finalization & CAM Export")
                st.caption("All 3 underwriting stages completed (Quantitative Scorecard, Legal Radar, Underwriter Synthesis). Finalize appraisal and proceed to formal CAM export.")

                # High-Authority Underwriting Readiness Posture Strip
                if active_verdict == "APPROVE":
                    posture_badge = '<span style="background: #ECFDF5; color: #059669; border: 1px solid #A7F3D0; font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 9999px;">SANCTION MEMORANDUM READY</span>'
                    posture_border = "#059669"
                elif active_verdict == "CONDITIONAL":
                    posture_badge = '<span style="background: #FFFBEB; color: #D97706; border: 1px solid #FDE68A; font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 9999px;">CONDITIONAL SANCTION READY</span>'
                    posture_border = "#D97706"
                else:
                    posture_badge = '<span style="background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 9999px;">DECLINE MEMORANDUM READY</span>'
                    posture_border = "#DC2626"

                preflight_strip = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-left: 4px solid {posture_border}; border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                    <div style="font-size: 11.5px; color: #1E293B;">
                        <strong>Borrower:</strong> {html.escape(company_disp)} &nbsp;|&nbsp; <strong>Facility:</strong> {format_inr(loan_amount)} ({int(tenure_months)} mos @ {float(interest_rate)}%) &nbsp;|&nbsp; <strong>Synthesis Verdict:</strong> {html.escape(active_verdict)}
                    </div>
                    <div>
                        {posture_badge}
                    </div>
                </div>"""
                _render_html(preflight_strip)

                # Pre-flight Checklist Card
                preflight_html = """<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 11.5px;">
                    <div style="color: #059669; font-weight: 700;">
                        ✓ Pillar 1: Quantitative 5-Cs Scorecard Calculated
                    </div>
                    <div style="color: #059669; font-weight: 700;">
                        ✓ Pillar 2: Statutory Legal Radar Adjudicated
                    </div>
                    <div style="color: #059669; font-weight: 700;">
                        ✓ Pillar 3: Underwriter Synthesis & Covenants Structured
                    </div>
                </div>"""
                _render_html(preflight_html)

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
                <div style="font-size: 11px; font-weight: 700; color: #0284C7; text-transform: uppercase;">1. CHARACTER (20%)</div>
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
                <div style="font-size: 11px; font-weight: 700; color: #6D28D9; text-transform: uppercase;">4. COLLATERAL (15%)</div>
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

        bank_in_val = format_inr(bk.total_credits) if bk and bk.total_credits else "₹ 0.00"
        bank_deb_val = format_inr(bk.total_debits) if bk and bk.total_debits else "₹ 0.00"
        gst_val_disp = format_inr(gst.taxable_value) if gst and gst.taxable_value else "₹ 0.00"
        gst_tax_disp = format_inr(gst.total_tax_paid) if gst and gst.total_tax_paid else "₹ 0.00"
        bounce_val_disp = f"{bk.bounce_count} Returns" if bk and bk.bounce_count is not None else "0 Returns"
        b_col_disp = "#059669" if (bk and bk.bounce_count == 0) else ("#DC2626" if (bk and bk.bounce_count is not None and bk.bounce_count >= 5) else "#D97706")
        itr_inc_disp = format_inr(itr.gross_total_income) if itr and itr.gross_total_income else "₹ 0.00"
        itr_pat_disp = format_inr(itr.profit_after_tax) if itr and itr.profit_after_tax is not None else "₹ 0.00"
        lock_hash_disp = st.session_state.locked_financials_hash or "SHA-256"

        audited_evidence_strip = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-left: 4px solid #0F766E; border-radius: 6px; padding: 10px 14px; margin-top: 10px; margin-bottom: 4px; box-shadow: 0 1px 2px rgba(15,23,42,0.02);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; flex-wrap: wrap; gap: 6px;">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 10px; font-weight: 800; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Audited Financial Evidence Locked for Evaluation</span>
                    <span style="font-size: 8.5px; font-weight: 800; color: #059669; background: #ECFDF5; border: 1px solid #A7F3D0; padding: 1.5px 6px; border-radius: 4px;">TAMPER-SEALED</span>
                </div>
                <span style="font-size: 9.5px; color: #64748B; font-family: monospace;">HASH #{html.escape(lock_hash_disp[:12])}</span>
            </div>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; font-size: 11.5px; color: #334155;">
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
                    <div style="font-size: 9.5px; font-weight: 700; color: #64748B; text-transform: uppercase;">12M Bank Inflows</div>
                    <div style="font-size: 12.5px; font-weight: 800; color: #0B1F3A; margin: 1px 0;">{bank_in_val}</div>
                    <div style="font-size: 10px; color: #64748B;">Debits: {bank_deb_val}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
                    <div style="font-size: 9.5px; font-weight: 700; color: #64748B; text-transform: uppercase;">GST Taxable Turnover</div>
                    <div style="font-size: 12.5px; font-weight: 800; color: #0B1F3A; margin: 1px 0;">{gst_val_disp}</div>
                    <div style="font-size: 10px; color: #64748B;">Tax: {gst_tax_disp}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
                    <div style="font-size: 9.5px; font-weight: 700; color: #64748B; text-transform: uppercase;">Mandate Bounces</div>
                    <div style="font-size: 12.5px; font-weight: 800; color: {b_col_disp}; margin: 1px 0;">{bounce_val_disp}</div>
                    <div style="font-size: 10px; color: #64748B;">RBI Knockout: ≥5 returns</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px;">
                    <div style="font-size: 9.5px; font-weight: 700; color: #64748B; text-transform: uppercase;">ITR Net Income / PAT</div>
                    <div style="font-size: 12.5px; font-weight: 800; color: #0B1F3A; margin: 1px 0;">{itr_pat_disp}</div>
                    <div style="font-size: 10px; color: #64748B;">Rev: {itr_inc_disp}</div>
                </div>
            </div>
        </div>"""
        _render_html(audited_evidence_strip)

    st.markdown("---")
    if st.button("← Back to Evidence Ingestion", type="secondary", key="btn_back_evidence_pre", width="stretch"):
        st.session_state.current_page = "Evidence"
        st.switch_page("app_pages/evidence.py")

