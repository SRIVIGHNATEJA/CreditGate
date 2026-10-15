import os
import re
import json
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any, List, Optional

import streamlit as st

from core.onboarding import (
    parse_cin,
    validate_cin_syntax,
    validate_pan_syntax,
    check_entity_consistency,
    generate_application_id,
    NIC_SECTOR_TAXONOMY,
)
from core.analyser import calculate_monthly_emi
from core.formatting import format_inr
from core.audit_logger import AuditLogger, verify_chain
from core.policy import Verdict
from ui.components import render_kpi_card, render_callout, status_pill, _render_html, _clean_html

COHORT_COMPANY_NAMES = {
    "Cohort 1: Prime MSME (Apex Synthetic Demo)": "Apex Synthetic Holdings (Demo)",
    "Cohort 2: Distressed (Kaveri Fictitious Demo)": "Kaveri Fictitious Freight (Demo)",
    "Cohort 3: Mismatch Trap (Bharat Mock Demo)": "Bharat Mock Steels & Infra (Demo)",
    "None (Manual Upload)": "Manual Applicant Company"
}

COHORT_DEFAULTS = {
    "Cohort 1: Prime MSME (Apex Synthetic Demo)": {
        "cin": "U29253DL2018PTC334120",
        "pan": "AAACA1234B",
        "company_name": "Apex Synthetic Holdings (Demo)",
    },
    "Cohort 2: Distressed (Kaveri Fictitious Demo)": {
        "cin": "U60200KA2019PTC120481",
        "pan": "AAACK5678M",
        "company_name": "Kaveri Fictitious Freight (Demo)",
    },
    "Cohort 3: Mismatch Trap (Bharat Mock Demo)": {
        "cin": "U24101MH2020PTC349120",
        "pan": "AABFB8910N",
        "company_name": "Bharat Mock Steels & Infra (Demo)",
    },
    "None (Manual Upload)": {
        "cin": "UNREGISTERED_MSME",
        "pan": "",
        "company_name": "Manual Applicant Company",
    },
}


def select_and_reset_cohort(cohort_name: str):
    """Resets all underwriting session state and syncs CIN, PAN, and app_id to the new cohort."""
    init_session_state()
    defaults = COHORT_DEFAULTS.get(cohort_name, COHORT_DEFAULTS["None (Manual Upload)"])

    # If widgets are already instantiated in current script run, defer setting to start of next run
    try:
        st.session_state.sb_cohort_selection = cohort_name
        st.session_state.sb_cin_input = defaults["cin"]
        st.session_state.sb_pan_input = defaults["pan"]
        st.session_state.case_cohort_select = cohort_name
        st.session_state.case_cin_input = defaults["cin"]
        st.session_state.case_pan_input = defaults["pan"]
    except Exception:
        st.session_state["_pending_cohort_reset"] = cohort_name
        st.rerun()
        return

    st.session_state.active_cohort_selection = cohort_name
    st.session_state.active_cin_selection = defaults["cin"]
    st.session_state.active_pan_input = defaults["pan"].strip().upper()
    st.session_state.case_pan_input = defaults["pan"]
    st.session_state.case_cin_input = defaults["cin"]
    st.session_state.active_pan_mismatch = False
    if cohort_name != "None (Manual Upload)":
        st.session_state.active_is_corporate = True
        st.session_state.case_is_corporate = True
        st.session_state["case_is_corporate"] = True

    # B-07: Regenerate app_id on cohort change to prevent audit log pollution
    st.session_state.app_id = generate_application_id(defaults["company_name"])
    st.session_state.audit_trail = []

    if cohort_name != "None (Manual Upload)":
        st.session_state.uploaded_bank_bytes = None
        st.session_state.uploaded_gst_bytes = None
        st.session_state.uploaded_itr_bytes = None
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
    st.session_state.lock_cleared_message = "Lock cleared because cohort changed."


def init_session_state():
    """Initializes all required session-state variables if not present."""
    if "lock_cleared_message" not in st.session_state:
        st.session_state.lock_cleared_message = None
    if "session_started_at" not in st.session_state:
        st.session_state.session_started_at = datetime.now(timezone.utc).strftime("%H:%M UTC")
    if "app_id" not in st.session_state:
        st.session_state.app_id = generate_application_id("Apex Synthetic Holdings (Demo)")
    if "audit_trail" not in st.session_state:
        st.session_state.audit_trail = []
    if "extracted_case" not in st.session_state:
        st.session_state.extracted_case = None
    if "extraction_meta" not in st.session_state:
        st.session_state.extraction_meta = None
    if "hitl_confirmed" not in st.session_state:
        st.session_state.hitl_confirmed = False
    if "editable_df" not in st.session_state:
        st.session_state.editable_df = None
    if "original_df" not in st.session_state:
        st.session_state.original_df = None
    if "locked_case" not in st.session_state:
        st.session_state.locked_case = None
    if "locked_financials_hash" not in st.session_state:
        st.session_state.locked_financials_hash = None
    if "scored_financials_hash" not in st.session_state:
        st.session_state.scored_financials_hash = None
    if "scored_composite_hash" not in st.session_state:
        st.session_state.scored_composite_hash = None
    if "score_result" not in st.session_state:
        st.session_state.score_result = None
    if "officer_notes" not in st.session_state:
        st.session_state.officer_notes = []
    if "executive_override" not in st.session_state:
        st.session_state.executive_override = None
    if "cam_markdown" not in st.session_state:
        st.session_state.cam_markdown = None
    if "research_cache" not in st.session_state:
        st.session_state.research_cache = {}
    if "current_research_report" not in st.session_state:
        st.session_state.current_research_report = None
    if "last_research_cin" not in st.session_state:
        st.session_state.last_research_cin = None
    if "last_research_cache_key" not in st.session_state:
        st.session_state.last_research_cache_key = None
    # Cohort and Borrower defaults
    if "active_cohort_selection" not in st.session_state:
        st.session_state.active_cohort_selection = "Cohort 1: Prime MSME (Apex Synthetic Demo)"
    if "sb_cohort_selection" not in st.session_state:
        st.session_state.sb_cohort_selection = "Cohort 1: Prime MSME (Apex Synthetic Demo)"
    if "active_is_corporate" not in st.session_state:
        st.session_state.active_is_corporate = True
    if "active_cin_selection" not in st.session_state:
        st.session_state.active_cin_selection = "U29253DL2018PTC334120"
    if "sb_cin_input" not in st.session_state:
        st.session_state.sb_cin_input = "U29253DL2018PTC334120"
    if "active_pan_input" not in st.session_state:
        st.session_state.active_pan_input = "AAACA1234B"
    if "sb_pan_input" not in st.session_state:
        st.session_state.sb_pan_input = "AAACA1234B"
    if "active_pan_mismatch" not in st.session_state:
        st.session_state.active_pan_mismatch = False

    # Facility defaults (B2: plain session_state keys)
    if "facility_loan_amount" not in st.session_state:
        st.session_state.facility_loan_amount = 5000000.0
    if "facility_tenure_months" not in st.session_state:
        st.session_state.facility_tenure_months = 36
    if "facility_interest_rate" not in st.session_state:
        st.session_state.facility_interest_rate = 11.5
    if "facility_collateral_val" not in st.session_state:
        st.session_state.facility_collateral_val = 0.0
    if "facility_cgtmse_covered" not in st.session_state:
        st.session_state.facility_cgtmse_covered = True

    # Synced plain aliases for direct access
    if "loan_amount" not in st.session_state:
        st.session_state.loan_amount = st.session_state.facility_loan_amount
    if "tenure_months" not in st.session_state:
        st.session_state.tenure_months = st.session_state.facility_tenure_months
    if "interest_rate" not in st.session_state:
        st.session_state.interest_rate = st.session_state.facility_interest_rate
    if "collateral_value" not in st.session_state:
        st.session_state.collateral_value = st.session_state.facility_collateral_val
    if "cgtmse_covered" not in st.session_state:
        st.session_state.cgtmse_covered = st.session_state.facility_cgtmse_covered
    if "proposed_emi" not in st.session_state:
        st.session_state.proposed_emi = float(calculate_monthly_emi(
            principal=Decimal(str(st.session_state.facility_loan_amount)),
            annual_rate_pct=Decimal(str(st.session_state.facility_interest_rate)),
            tenure_months=int(st.session_state.facility_tenure_months),
        ))

    # In-memory document bytes
    if "uploaded_bank_bytes" not in st.session_state:
        st.session_state.uploaded_bank_bytes = None
    if "uploaded_gst_bytes" not in st.session_state:
        st.session_state.uploaded_gst_bytes = None
    if "uploaded_itr_bytes" not in st.session_state:
        st.session_state.uploaded_itr_bytes = None


def record_event(event_type: str, actor: str, details: Dict[str, Any], severity: str = "INFO"):
    """Helper to record event both to disk JSONL and in-memory session state."""
    init_session_state()
    audit = AuditLogger(st.session_state.app_id)
    evt = audit.log(event_type, actor, details, severity)
    st.session_state.audit_trail.append(evt)


def compute_composite_hash(
    locked_financials_hash: Optional[str],
    loan_amount: float,
    tenure_months: int,
    interest_rate: float,
    proposed_emi: float,
    collateral_val: float,
    cgtmse_covered: bool,
    cin: str,
    vintage: int,
    pan: str,
    pan_mismatch: bool,
    research_findings: List[Any],
) -> str:
    """Computes a deterministic hash over all inputs affecting underwriting scoring."""
    research_snapshot = [
        (f.id, f.review_status.value, f.dismissal_reason, getattr(f, "confirmation_note", None))
        for f in (research_findings or [])
    ]
    composite_payload = {
        "financials_hash": locked_financials_hash,
        "loan_amount": float(loan_amount),
        "tenure_months": int(tenure_months),
        "interest_rate": float(interest_rate),
        "proposed_emi": float(proposed_emi),
        "collateral_val": float(collateral_val),
        "cgtmse_covered": bool(cgtmse_covered),
        "cin": cin,
        "vintage": vintage,
        "pan": pan,
        "pan_mismatch": pan_mismatch,
        "research": research_snapshot,
    }
    return hashlib.sha256(
        json.dumps(composite_payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def render_sidebar() -> Dict[str, Any]:
    """Renders the slimmed shared sidebar per B3:
    - App ID
    - Step status (Case -> Evidence -> Decision -> CAM)
    - Reset case button
    - Audit chain status via verify_chain()
    Nothing else in sidebar.
    """
    init_session_state()

    if "_pending_cohort_reset" in st.session_state:
        pending_cohort = st.session_state.pop("_pending_cohort_reset")
        select_and_reset_cohort(pending_cohort)

    with st.sidebar:
        # 0. Context variables
        cur_cohort = st.session_state.get("active_cohort_selection", "Cohort 1: Prime MSME (Apex Synthetic Demo)")
        is_corporate = st.session_state.get("active_is_corporate", True)
        company_name = COHORT_COMPANY_NAMES.get(cur_cohort, "Manual Applicant Company")
        if not is_corporate:
            company_name = "Unincorporated MSME / Sole Proprietor"

        app_id = st.session_state.get("app_id", "APP-202610-0001")
        loan_amt = float(st.session_state.get("facility_loan_amount", 5000000.0))
        rate_val = float(st.session_state.get("facility_interest_rate", 10.25))
        tenure_val = int(st.session_state.get("facility_tenure_months", 36))
        collat_val = float(st.session_state.get("facility_collateral_val", 0.0))
        cgtmse_cov = bool(st.session_state.get("facility_cgtmse_covered", True))

        emi_dec = calculate_monthly_emi(Decimal(str(loan_amt)), Decimal(str(rate_val)), tenure_val)
        cov_pct = (collat_val / loan_amt * 100.0) if loan_amt > 0 else 0.0

        # Current page detection for timeline highlighting
        curr_page = str(st.session_state.get("current_page", "Case")).lower()
        is_p1_active = "case" in curr_page
        is_p2_active = "evidence" in curr_page
        is_p3_active = "decision" in curr_page
        is_p4_active = ("cam" in curr_page or "audit" in curr_page)

        # 1. Executive Brand & Command Identity Card (Light Institutional Design)
        brand_card = f"""<div style="background: #FFFFFF; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04); border: 1px solid #E2E8F0; border-top: 3px solid #0F766E;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <div style="display: flex; align-items: center; gap: 7px;">
                    <div style="background: #0F766E; width: 22px; height: 22px; border-radius: 5px; display: flex; align-items: center; justify-content: center; font-size: 11px; color: #FFFFFF; box-shadow: 0 2px 4px rgba(15, 118, 110, 0.25);">
                        🛡️
                    </div>
                    <div>
                        <div style="font-size: 13px; font-weight: 800; color: #0B1F3A; letter-spacing: 0.03em; line-height: 1.1;">CREDITGATE</div>
                        <div style="font-size: 9px; font-weight: 600; color: #64748B; letter-spacing: 0.05em; text-transform: uppercase;">v3.0 ENTERPRISE</div>
                    </div>
                </div>
                <div style="background: #ECFDF5; border: 1px solid #A7F3D0; border-radius: 9999px; padding: 2px 7px; display: flex; align-items: center; gap: 4px;">
                    <span style="display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: #10B981;"></span>
                    <span style="font-size: 9px; font-weight: 700; color: #059669; letter-spacing: 0.04em; text-transform: uppercase;">ONLINE</span>
                </div>
            </div>
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 10px; margin-top: 6px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em;">Active Dossier</span>
                    <span style="font-size: 10px; font-weight: 600; color: #0F766E; font-family: monospace; background: #CCFBF1; padding: 1px 5px; border-radius: 3px;">{app_id}</span>
                </div>
                <div style="font-size: 12px; font-weight: 700; color: #0B1F3A; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="{company_name}">
                    {company_name}
                </div>
                <div style="font-size: 10px; color: #64748B; margin-top: 4px; display: flex; justify-content: space-between;">
                    <span>Sanction: <strong style="color: #0B1F3A;">₹{loan_amt/100000:.1f}L</strong></span>
                    <span>Rate: <strong style="color: #0F766E;">{rate_val:.2f}%</strong></span>
                    <span>Tenor: <strong style="color: #0B1F3A;">{tenure_val}M</strong></span>
                </div>
            </div>
        </div>"""
        _render_html(brand_card)

        # 2. Live Underwriting Risk Scorecard HUD
        score_res = st.session_state.get("score_result")
        if score_res is not None and score_res.final_verdict != Verdict.INSUFFICIENT_DATA:
            override_info = st.session_state.get("executive_override")
            is_overridden = bool(override_info and override_info.get("override_active"))
            eff_verdict = override_info.get("override_verdict") if is_overridden else score_res.final_verdict.name

            c_score = float(score_res.composite_score)
            score_col = "#10B981" if c_score >= 70.0 else ("#F59E0B" if c_score >= 50.0 else "#EF4444")
            v_variant = "approve" if eff_verdict == "APPROVE" else ("conditional" if eff_verdict == "CONDITIONAL" else "reject")
            v_pill = status_pill(eff_verdict, variant=v_variant)

            dscr_val = float(score_res.dscr)
            dscr_col = "#0F766E" if dscr_val >= 1.20 else "#EF4444"
            total_ded = sum(float(d.points_deducted) for d in score_res.deductions)

            with st.container(border=True):
                score_hud = f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <span style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Live Scorecard HUD</span>
                    {v_pill}
                </div>
                <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px;">
                    <div>
                        <span style="font-size: 18px; font-weight: 800; color: #0B1F3A;">{c_score:.1f}</span>
                        <span style="font-size: 10px; font-weight: 600; color: #64748B;">/ 100 pts</span>
                    </div>
                    <span style="font-size: 9.5px; font-weight: 700; color: {score_col}; text-transform: uppercase;">{'PRIME' if c_score >= 70 else ('MODERATE' if c_score >= 50 else 'STRESSED')}</span>
                </div>
                <div style="background: #E2E8F0; border-radius: 9999px; height: 5px; width: 100%; overflow: hidden; margin-bottom: 6px;">
                    <div style="background: {score_col}; width: {min(100.0, max(0.0, c_score))}%; height: 100%; border-radius: 9999px;"></div>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 4px;">
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 4px; padding: 4px 6px;">
                        <div style="font-size: 8.5px; color: #64748B; font-weight: 600; text-transform: uppercase;">DSCR Cover</div>
                        <div style="font-size: 11px; font-weight: 700; color: {dscr_col};">{dscr_val:.2f}x</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 4px; padding: 4px 6px;">
                        <div style="font-size: 8.5px; color: #64748B; font-weight: 600; text-transform: uppercase;">Deductions</div>
                        <div style="font-size: 11px; font-weight: 700; color: {'#EF4444' if total_ded > 0 else '#0F766E'};">-{total_ded:.1f} pts</div>
                    </div>
                </div>"""
                _render_html(score_hud)
        else:
            with st.container(border=True):
                radar_idle = f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                    <span style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Underwriting Radar</span>
                    {status_pill("Intake Primed", variant="info")}
                </div>
                <div style="font-size: 11px; color: #64748B; line-height: 1.4; margin-top: 3px;">
                    Awaiting verification. Lock borrower evidence to evaluate Five-Cs score & dynamic pricing.
                </div>"""
                _render_html(radar_idle)

        # 3. Connected Workflow Progression Pipeline
        is_extracted = (st.session_state.get("locked_case") is not None or st.session_state.get("extracted_case") is not None)
        is_locked = (st.session_state.get("locked_case") is not None)
        is_scored = (st.session_state.get("score_result") is not None)
        is_cam = (st.session_state.get("cam_markdown") is not None)

        if is_cam:
            prog_pct = 100
            prog_val = 1.0
        elif is_scored:
            prog_pct = 75
            prog_val = 0.75
        elif is_locked or is_extracted:
            prog_pct = 50
            prog_val = 0.50
        else:
            prog_pct = 25
            prog_val = 0.25

        p_case = status_pill("Configured", variant="approve")
        if is_locked:
            p_ev = status_pill("Locked", variant="approve")
        elif is_extracted:
            p_ev = status_pill("Extracted", variant="info")
        else:
            p_ev = status_pill("Pending", variant="neutral")

        p_dec = status_pill("Scored", variant="approve") if is_scored else status_pill("Pending", variant="neutral")
        p_cam = status_pill("Generated", variant="approve") if is_cam else status_pill("Pending", variant="neutral")

        with st.container(border=True):
            header_pipe = f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <span style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Pipeline Progression</span>
                <span style="font-size: 10px; font-weight: 700; color: #0B1F3A;">{prog_pct}%</span>
            </div>"""
            _render_html(header_pipe)
            st.progress(prog_val)

            stage_bg1 = "background: #F0FDFA; border-left: 3px solid #0F766E; padding: 3px 6px;" if is_p1_active else "padding: 3px 6px;"
            stage_bg2 = "background: #F0FDFA; border-left: 3px solid #0F766E; padding: 3px 6px;" if is_p2_active else "padding: 3px 6px;"
            stage_bg3 = "background: #F0FDFA; border-left: 3px solid #0F766E; padding: 3px 6px;" if is_p3_active else "padding: 3px 6px;"
            stage_bg4 = "background: #F0FDFA; border-left: 3px solid #0F766E; padding: 3px 6px;" if is_p4_active else "padding: 3px 6px;"

            stages_html = f"""<div style="margin-top: 8px; display: flex; flex-direction: column; gap: 4px;">
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11px; {stage_bg1} border-radius: 4px;">
                    <span style="font-weight: {'700' if is_p1_active else '500'}; color: {'#0F766E' if is_p1_active else '#334155'};">1. Case & Terms</span>
                    {p_case}
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11px; {stage_bg2} border-radius: 4px;">
                    <span style="font-weight: {'700' if is_p2_active else '500'}; color: {'#0F766E' if is_p2_active else '#334155'};">2. Evidence Data</span>
                    {p_ev}
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11px; {stage_bg3} border-radius: 4px;">
                    <span style="font-weight: {'700' if is_p3_active else '500'}; color: {'#0F766E' if is_p3_active else '#334155'};">3. Risk & Scoring</span>
                    {p_dec}
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11px; {stage_bg4} border-radius: 4px;">
                    <span style="font-weight: {'700' if is_p4_active else '500'}; color: {'#0F766E' if is_p4_active else '#334155'};">4. Committee CAM</span>
                    {p_cam}
                </div>
            </div>"""
            _render_html(stages_html)

        # 4. Live Sanction Envelope HUD & 1-Click Ticket Simulator
        ent_mode = "Corporate" if is_corporate else "Unincorporated"
        ent_badge = status_pill(ent_mode, variant="info" if is_corporate else "neutral")
        collat_chip = "CGTMSE" if cgtmse_cov else f"{cov_pct:.0f}%"
        annual_debt_serv = emi_dec * Decimal("12")

        with st.container(border=True):
            terms_html = f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <span style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Sanction Terms</span>
                {ent_badge}
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px; margin-bottom: 6px;">
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 5px 8px;">
                    <div style="font-size: 9px; font-weight: 600; color: #94A3B8; text-transform: uppercase;">Facility</div>
                    <div style="font-size: 12px; font-weight: 700; color: #0B1F3A;">₹ {loan_amt/100000:.1f}L</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 5px 8px;">
                    <div style="font-size: 9px; font-weight: 600; color: #94A3B8; text-transform: uppercase;">Rate p.a.</div>
                    <div style="font-size: 12px; font-weight: 700; color: #0F766E;">{rate_val:.2f}%</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 5px 8px;">
                    <div style="font-size: 9px; font-weight: 600; color: #94A3B8; text-transform: uppercase;">Monthly EMI</div>
                    <div style="font-size: 11px; font-weight: 700; color: #0B1F3A;">{format_inr(emi_dec)}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 5px 8px;">
                    <div style="font-size: 9px; font-weight: 600; color: #94A3B8; text-transform: uppercase;">Security</div>
                    <div style="font-size: 11px; font-weight: 700; color: #0F766E;">{collat_chip}</div>
                </div>
            </div>
            <div style="background: #F8FAFC; border: 1px dashed #CBD5E1; border-radius: 4px; padding: 3px 6px; font-size: 10px; color: #64748B; display: flex; justify-content: space-between; margin-bottom: 6px;">
                <span>Annual Outflow:</span>
                <strong style="color: #0B1F3A;">{format_inr(annual_debt_serv)}</strong>
            </div>"""
            _render_html(terms_html)

            # Ticket Size Simulator Chips (Strictly st.button to maintain positional index compatibility)
            st.markdown("<div style='font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase; margin-bottom: 3px;'>Simulate Ticket:</div>", unsafe_allow_html=True)
            col_sb1, col_sb2, col_sb3, col_sb4 = st.columns(4)
            with col_sb1:
                if st.button("25L", key="sb_btn_pre_25l", width="stretch", help="Set facility to ₹25 Lakhs"):
                    st.session_state.facility_loan_amount = 2500000.0
                    st.session_state.loan_amount = 2500000.0
                    st.rerun()
            with col_sb2:
                if st.button("50L", key="sb_btn_pre_50l", width="stretch", help="Set facility to ₹50 Lakhs"):
                    st.session_state.facility_loan_amount = 5000000.0
                    st.session_state.loan_amount = 5000000.0
                    st.rerun()
            with col_sb3:
                if st.button("75L", key="sb_btn_pre_75l", width="stretch", help="Set facility to ₹75 Lakhs"):
                    st.session_state.facility_loan_amount = 7500000.0
                    st.session_state.loan_amount = 7500000.0
                    st.rerun()
            with col_sb4:
                if st.button("1Cr", key="sb_btn_pre_1cr", width="stretch", help="Set facility to ₹1.0 Crore"):
                    st.session_state.facility_loan_amount = 10000000.0
                    st.session_state.loan_amount = 10000000.0
                    st.rerun()

        # 5. Cryptographic Audit Seal Block
        is_chain_valid, chain_err = verify_chain(st.session_state.get("audit_trail", []))
        evt_count = len(st.session_state.get("audit_trail", []))
        chain_status_pill = status_pill("Verified Chain", variant="approve") if is_chain_valid else status_pill("Tampered", variant="reject")
        gen_hash = st.session_state["audit_trail"][0].get("current_hash", "")[:8] if (evt_count > 0 and "audit_trail" in st.session_state) else "e3b0c442"
        s_time = st.session_state.get("session_started_at", "Active")

        with st.container(border=True):
            seal_html = f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <span style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Security Seal</span>
                {chain_status_pill}
            </div>
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 8px; font-size: 11px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: #64748B; font-size: 10px;">Audit Blocks:</span>
                    <strong style="color: #0B1F3A; font-size: 10px;">{evt_count} Chained</strong>
                </div>
                <div style="margin-top: 3px; padding-top: 3px; border-top: 1px dashed #CBD5E1; font-size: 9px; color: #64748B; font-family: monospace;">
                    Genesis: <span style="color: #0F766E;">{gen_hash}...</span>
                </div>
            </div>
            <div style="margin-top: 5px; font-size: 9px; color: #94A3B8; text-align: right;">
                Session: {s_time}
            </div>"""
            _render_html(seal_html)

        # 6. Reset Case Button
        if st.button(":material/restart_alt: Reset Active Dossier", key="sb_btn_reset_case", width="stretch", help="Resets locks, extraction caches, and overrides while keeping the selected cohort"):
            cur_cohort = st.session_state.get("active_cohort_selection", "Cohort 1: Prime MSME (Apex Synthetic Demo)")
            select_and_reset_cohort(cur_cohort)
            st.rerun()

    return get_app_context()


def get_app_context() -> Dict[str, Any]:
    """Retrieves the current application context, resolving from plain session_state keys."""
    init_session_state()

    cohort_selection = st.session_state.get("active_cohort_selection", "Cohort 1: Prime MSME (Apex Synthetic Demo)")
    is_corporate = st.session_state.get("active_is_corporate", True)
    cin_input = st.session_state.get("active_cin_selection", "U29253DL2018PTC334120")
    pan_input = st.session_state.get("active_pan_input", "AAACA1234B")
    current_cin_val = cin_input if is_corporate else "UNREGISTERED_MSME"
    current_pan_val = pan_input.strip().upper()

    # If non-corporate mode, ensure 4th character reflects individual/proprietor ('P' or 'F').
    # Auto-translate corporate 'C' to 'P' so a spurious Consistency Alert never triggers on toggle OFF.
    if not is_corporate and len(current_pan_val) == 10 and current_pan_val[3] == "C":
        current_pan_val = current_pan_val[:3] + "P" + current_pan_val[4:]
        st.session_state.active_pan_input = current_pan_val
        st.session_state.case_pan_input = current_pan_val
        st.session_state["case_pan_input"] = current_pan_val

    cin_parsed_data = None
    if is_corporate:
        is_cin_valid, _ = validate_cin_syntax(current_cin_val)
        if is_cin_valid:
            cin_parsed_data = parse_cin(current_cin_val)
    else:
        manual_sector = st.session_state.get("case_manual_sector", st.session_state.get("manual_sector", list(NIC_SECTOR_TAXONOMY.values())[0]))
        manual_vintage = st.session_state.get("case_manual_vintage", st.session_state.get("manual_vintage", 7))
        cin_parsed_data = {
            "cin": "UNREGISTERED_MSME",
            "listing_status": "Unlisted",
            "macro_sector": manual_sector,
            "business_vintage_years": int(manual_vintage),
            "low_vintage_risk_flag": int(manual_vintage) < 3,
            "entity_code": "PROPRIETORSHIP",
            "state_name": "Pan-India",
        }

    pan_check = check_entity_consistency(
        current_pan_val,
        is_corporate=is_corporate,
        cin_entity_code=cin_parsed_data.get("entity_code") if cin_parsed_data else None,
    )
    current_pan_mismatch = bool(pan_check.get("mismatch_detected", False))

    loan_amount = float(st.session_state.get("facility_loan_amount", 5000000.0))
    tenure_months = int(st.session_state.get("facility_tenure_months", 36))
    interest_rate = float(st.session_state.get("facility_interest_rate", 11.5))
    collateral_val = float(st.session_state.get("facility_collateral_val", 0.0))
    cgtmse_covered = bool(st.session_state.get("facility_cgtmse_covered", True))

    computed_monthly_emi = calculate_monthly_emi(
        principal=Decimal(str(loan_amount)),
        annual_rate_pct=Decimal(str(interest_rate)),
        tenure_months=int(tenure_months),
    )
    proposed_emi = float(computed_monthly_emi)

    # Document bytes
    base_synthetic = Path(__file__).resolve().parent.parent / "data" / "synthetic"
    bank_bytes, gst_bytes, itr_bytes = None, None, None
    cohort_name = "Custom Upload"

    if cohort_selection and cohort_selection != "None (Manual Upload)":
        cohort_folder = "cohort_1_prime"
        if "Cohort 2" in cohort_selection:
            cohort_folder = "cohort_2_distressed"
        elif "Cohort 3" in cohort_selection:
            cohort_folder = "cohort_3_mismatch"

        cohort_dir = base_synthetic / cohort_folder
        if cohort_dir.exists():
            bank_p = cohort_dir / "bank_statement.pdf"
            gst_p = cohort_dir / "gstr3b.pdf"
            itr_p = cohort_dir / "itr_acknowledgement.pdf"
            if bank_p.exists() and gst_p.exists() and itr_p.exists():
                bank_bytes = bank_p.read_bytes()
                gst_bytes = gst_p.read_bytes()
                itr_bytes = itr_p.read_bytes()
                cohort_name = cohort_folder
    else:
        bank_bytes = st.session_state.get("uploaded_bank_bytes")
        gst_bytes = st.session_state.get("uploaded_gst_bytes")
        itr_bytes = st.session_state.get("uploaded_itr_bytes")

    has_all_docs = (bank_bytes is not None) and (gst_bytes is not None) and (itr_bytes is not None)

    ctx = {
        "cohort_selection": cohort_selection,
        "cohort_name": cohort_name,
        "company_name": COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company"),
        "is_corporate": is_corporate,
        "cin_input": cin_input,
        "cin_parsed_data": cin_parsed_data,
        "pan_input": pan_input,
        "pan_check": pan_check,
        "current_cin_val": current_cin_val,
        "current_pan_val": current_pan_val,
        "current_pan_mismatch": current_pan_mismatch,
        "loan_amount": loan_amount,
        "tenure_months": tenure_months,
        "interest_rate": interest_rate,
        "computed_monthly_emi": computed_monthly_emi,
        "proposed_emi": proposed_emi,
        "collateral_val": collateral_val,
        "cgtmse_covered": cgtmse_covered,
        "bank_bytes": bank_bytes,
        "gst_bytes": gst_bytes,
        "itr_bytes": itr_bytes,
        "has_all_docs": has_all_docs,
    }
    st.session_state.app_context = ctx
    return ctx


def render_document_gate():
    """Renders the Hold card when mandatory document trinity is missing."""
    gate_html = """<div class="gate-card-hold">
        <h3 style="color: #B91C1C; margin-top: 0; font-size: 16px;">STATUS: ON HOLD (Pending Mandatory Documents)</h3>
        <p style="color: #475569; font-size: 13px; max-width: 650px; margin: 0 0 10px 0;">
            The Underwriting Pipeline is gated. Under MSME credit policy, financial scoring cannot commence until all three documents in the <strong>Mandatory Document Trinity</strong> are uploaded or loaded.
        </p>
        <div style="font-weight: 600; font-size: 13px; color: #1F2937;">
            Required documents: Bank Statement, GSTR-3B Return, and ITR Form.
        </div>
        <div style="margin-top: 8px; font-size: 12px; color: #64748B;">
            Select a Pre-Generated Synthetic Cohort on the Case page to load verified sample documents.
        </div>
    </div>"""
    lines = [line.strip() for line in gate_html.splitlines() if line.strip()]
    st.markdown("\n".join(lines), unsafe_allow_html=True)
