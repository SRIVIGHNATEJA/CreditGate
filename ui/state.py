import os
import re
import json
import hashlib
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any, List, Optional

IST = ZoneInfo("Asia/Kolkata")

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
from core.config import get_gemini_api_key
from core.policy import Verdict
from ui.components import render_kpi_card, render_callout, status_pill, _render_html, _clean_html

COHORT_COMPANY_NAMES = {
    # Canonical Institutional Benchmark Cohorts
    "Cohort 1: Prime MSME (Apex Industrial Benchmark)": "Apex Industrial Holdings Ltd.",
    "Cohort 2: Distressed (Kaveri Logistics Benchmark)": "Kaveri Logistics & Freight Ltd.",
    "Cohort 3: Mismatch Trap (Bharat Structural Benchmark)": "Bharat Structural Steels & Infra Ltd.",
    "None (Manual Upload)": "Manual Applicant Company",
    # Backward compatibility aliases for test fixtures and external callers
    "Cohort 1: Prime MSME (Apex Synthetic Demo)": "Apex Industrial Holdings Ltd.",
    "Cohort 2: Distressed (Kaveri Fictitious Demo)": "Kaveri Logistics & Freight Ltd.",
    "Cohort 3: Mismatch Trap (Bharat Mock Demo)": "Bharat Structural Steels & Infra Ltd.",
}

COHORT_DEFAULTS = {
    # Canonical Institutional Benchmark Cohorts
    "Cohort 1: Prime MSME (Apex Industrial Benchmark)": {
        "cin": "U29253DL2018PTC334120",
        "pan": "AAACA1234B",
        "company_name": "Apex Industrial Holdings Ltd.",
    },
    "Cohort 2: Distressed (Kaveri Logistics Benchmark)": {
        "cin": "U60200KA2019PTC120481",
        "pan": "AAACK5678M",
        "company_name": "Kaveri Logistics & Freight Ltd.",
    },
    "Cohort 3: Mismatch Trap (Bharat Structural Benchmark)": {
        "cin": "U24101MH2020PTC349120",
        "pan": "AABFB8910N",
        "company_name": "Bharat Structural Steels & Infra Ltd.",
    },
    "None (Manual Upload)": {
        "cin": "UNREGISTERED_MSME",
        "pan": "",
        "company_name": "Manual Applicant Company",
    },
    # Backward compatibility aliases for legacy test suites
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
}


def canonicalize_cohort_name(name: str) -> str:
    """Resolves any cohort name or legacy alias to its canonical institutional benchmark representation."""
    if not name:
        return "None (Manual Upload)"
    if "Cohort 1" in name or "Apex" in name or name == "c1":
        return "Cohort 1: Prime MSME (Apex Industrial Benchmark)"
    if "Cohort 2" in name or "Kaveri" in name or name == "c2":
        return "Cohort 2: Distressed (Kaveri Logistics Benchmark)"
    if "Cohort 3" in name or "Bharat" in name or name == "c3":
        return "Cohort 3: Mismatch Trap (Bharat Structural Benchmark)"
    if "None" in name or "Manual" in name or "custom" in str(name).lower():
        return "None (Manual Upload)"
    return name


def select_and_reset_cohort(cohort_name: str):
    """Resets all underwriting session state and syncs CIN, PAN, and app_id to the new cohort."""
    init_session_state()
    defaults = COHORT_DEFAULTS.get(cohort_name)
    if not defaults:
        canon_name = canonicalize_cohort_name(cohort_name)
        defaults = COHORT_DEFAULTS.get(canon_name, COHORT_DEFAULTS["None (Manual Upload)"])
        cohort_name = canon_name

    cur_cin = defaults["cin"]
    cur_pan = defaults["pan"]
    cur_comp = defaults["company_name"]
    if cohort_name == "None (Manual Upload)" and st.session_state.get("custom_extracted_identifiers"):
        c_ident = st.session_state.custom_extracted_identifiers
        if c_ident.get("cin"):
            cur_cin = c_ident["cin"]
        if c_ident.get("pan"):
            cur_pan = c_ident["pan"]
        if c_ident.get("company_name"):
            cur_comp = c_ident["company_name"]

    # If widgets are already instantiated in current script run, defer setting to start of next run
    try:
        st.session_state.sb_cohort_selection = cohort_name
        st.session_state.sb_cin_input = cur_cin
        st.session_state.sb_pan_input = cur_pan
        st.session_state.case_cohort_select = cohort_name
        st.session_state.case_cin_input = cur_cin
        st.session_state.case_pan_input = cur_pan
    except Exception:
        st.session_state["_pending_cohort_reset"] = cohort_name
        st.rerun()
        return

    st.session_state.active_cohort_selection = cohort_name
    st.session_state.active_cin_selection = cur_cin
    st.session_state.active_pan_input = cur_pan.strip().upper()
    st.session_state.case_pan_input = cur_pan
    st.session_state.case_cin_input = cur_cin
    st.session_state.active_pan_mismatch = False
    if cohort_name != "None (Manual Upload)":
        st.session_state.active_is_corporate = True
        st.session_state.case_is_corporate = True
        st.session_state["case_is_corporate"] = True
    elif st.session_state.get("custom_extracted_identifiers") and st.session_state.custom_extracted_identifiers.get("is_corporate") is not None:
        is_corp = bool(st.session_state.custom_extracted_identifiers["is_corporate"])
        st.session_state.active_is_corporate = is_corp
        st.session_state.case_is_corporate = is_corp
        st.session_state["case_is_corporate"] = is_corp

    # B-07: Regenerate app_id on cohort change to prevent audit log pollution
    st.session_state.app_id = generate_application_id(cur_comp)
    st.session_state.audit_trail = []

    if cohort_name != "None (Manual Upload)":
        st.session_state.uploaded_bank_bytes = None
        st.session_state.uploaded_gst_bytes = None
        st.session_state.uploaded_itr_bytes = None
        st.session_state.custom_company_name = None
        st.session_state.custom_extracted_identifiers = None
        st.session_state.custom_extracted_pan = None
        st.session_state.document_pan_mismatch = None
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
    st.session_state.quorum_governance = None
    st.session_state.current_research_report = None
    st.session_state.last_research_cache_key = None
    st.session_state.lock_cleared_message = "Lock cleared because cohort changed."


def init_session_state():
    """Initializes all required session-state variables if not present."""
    if "_pending_widget_sync" in st.session_state:
        pending = st.session_state.pop("_pending_widget_sync", {})
        for k, v in pending.items():
            try:
                st.session_state[k] = v
            except Exception:
                pass

    if "lock_cleared_message" not in st.session_state:
        st.session_state.lock_cleared_message = None
    if "session_started_at" not in st.session_state or "UTC" in str(st.session_state.get("session_started_at", "")) or ("AM" not in str(st.session_state.get("session_started_at", "")) and "PM" not in str(st.session_state.get("session_started_at", ""))):
        st.session_state.session_started_at = datetime.now(IST).strftime("%I:%M %p IST").lstrip("0")
    if "app_id" not in st.session_state:
        st.session_state.app_id = generate_application_id("Apex Industrial Holdings Ltd.")
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
    if "quorum_governance" not in st.session_state:
        st.session_state.quorum_governance = None
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
        st.session_state.active_cohort_selection = "Cohort 1: Prime MSME (Apex Industrial Benchmark)"
    if "sb_cohort_selection" not in st.session_state:
        st.session_state.sb_cohort_selection = "Cohort 1: Prime MSME (Apex Industrial Benchmark)"
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
        st.session_state.facility_collateral_val = 2500000.0
    if "facility_cgtmse_covered" not in st.session_state:
        st.session_state.facility_cgtmse_covered = False

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


def reset_facility_to_defaults():
    """Resets facility terms and collateral to Policy Standard benchmark (50% coverage, standard baseline)."""
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
    st.session_state.proposed_emi = float(calculate_monthly_emi(
        principal=Decimal("5000000.0"),
        annual_rate_pct=Decimal("11.5"),
        tenure_months=36,
    ))


def get_default_quorum_governance() -> Dict[str, Any]:
    """Returns a pristine Tier-1 Appraisal state awaiting Risk Head and Committee Quorum."""
    return {
        "stage": "DRAFT",
        "analyst_attested": True,
        "analyst_name": "Credit Underwriting Specialist — MSME Division",
        "analyst_timestamp": datetime.now(IST).strftime("%d-%b-%Y | %I:%M %p IST"),
        "cro_concurred": False,
        "cro_name": "Deputy General Manager / Zonal Head (ZCC)",
        "cro_timestamp": None,
        "cro_covenants": [
            "DSRA: 3 Months Principal + Interest Obligation",
            "Quarterly Hypothecated Stock & Receivables Audit",
            "Trust & Retention Escrow Routing for Trade Credits",
        ],
        "committee_resolution": None,
        "committee_name": "Zonal Credit Sanction Committee (ZCC-IV)",
        "committee_resolution_no": None,
        "committee_timestamp": None,
        "sealed": False,
    }


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

    ctx = get_app_context()

    with st.sidebar:
        # 0. Context variables directly from fresh application context
        cur_cohort = ctx.get("cohort_selection", "")
        is_corporate = ctx.get("is_corporate", True)
        company_name = ctx.get("company_name", "Apex Industrial Holdings Ltd.")
        app_id = ctx.get("app_id") or st.session_state.get("app_id") or generate_application_id("Apex Industrial Holdings Ltd.")
        short_app_id = app_id[-8:] if len(app_id) >= 8 else app_id
        pan_mismatch = bool(ctx.get("current_pan_mismatch", False))
        current_pan = str(ctx.get("current_pan_val", "")).strip().upper()

        # 1. Entity type pill and format verification descriptors
        entity_pill = ctx.get("entity_pill", '<span style="font-size: 9.5px; font-weight: 700; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; border-radius: 4px; padding: 2px 7px;">PVT LTD</span>')
        cin_status_text = ctx.get("cin_status_text", "Awaiting Specification")
        cin_badge_col = ctx.get("cin_badge_col", "#64748B")
        pan_status_text = ctx.get("pan_status_text", "Awaiting Document Extraction")
        pan_badge_col = ctx.get("pan_badge_col", "#64748B")

        # 2. Scorecard HUD section (if case scored and financials actively locked)
        score_res = st.session_state.get("score_result")
        is_case_locked = bool(st.session_state.get("locked_case") is not None and st.session_state.get("hitl_confirmed", False))
        score_section_html = ""
        if score_res is not None and score_res.final_verdict != Verdict.INSUFFICIENT_DATA and is_case_locked:
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

            score_section_html = f"""<div style="padding: 10px 14px; border-bottom: 1px solid #E2E8F0; background: #FFFFFF;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <span style="font-size: 9.5px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Scorecard HUD</span>
                    {v_pill}
                </div>
                <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px;">
                    <div>
                        <span style="font-size: 19px; font-weight: 800; color: #0B1F3A;">{c_score:.1f}</span>
                        <span style="font-size: 11px; font-weight: 600; color: #64748B;">/ 100</span>
                    </div>
                    <span style="font-size: 10px; font-weight: 700; color: {score_col}; text-transform: uppercase;">{'PRIME' if c_score >= 70 else ('MODERATE' if c_score >= 50 else 'STRESSED')}</span>
                </div>
                <div style="background: #E2E8F0; border-radius: 9999px; height: 5px; width: 100%; overflow: hidden; margin-bottom: 8px;">
                    <div style="background: {score_col}; width: {min(100.0, max(0.0, c_score))}%; height: 100%; border-radius: 9999px;"></div>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px;">
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 4px; padding: 4px 6px;">
                        <div style="font-size: 8.5px; color: #64748B; font-weight: 600; text-transform: uppercase;">DSCR at facility</div>
                        <div style="font-size: 11.5px; font-weight: 700; color: {dscr_col}; margin-top: 1px;">{dscr_val:.2f}x</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 4px; padding: 4px 6px;">
                        <div style="font-size: 8.5px; color: #64748B; font-weight: 600; text-transform: uppercase;">Deductions</div>
                        <div style="font-size: 11.5px; font-weight: 700; color: {'#EF4444' if total_ded > 0 else '#0F766E'}; margin-top: 1px;">-{total_ded:.1f} pillar pts</div>
                    </div>
                </div>
            </div>"""

        # 3. Gateway Health (State-Derived Vision AI & Legal Radar)
        has_gemini = bool(get_gemini_api_key())
        ext_meta = st.session_state.get("extraction_meta")
        has_active_extraction = bool(st.session_state.get("extracted_case") is not None)
        if ext_meta and isinstance(ext_meta, dict) and "engine" in ext_meta and has_active_extraction:
            engine_raw = str(ext_meta.get("engine", "")).lower()
            if "gemini" in engine_raw:
                engine_text = "Gemini Multimodal AI (Active)"
            else:
                engine_text = "Offline Parser (Active)"
        elif has_gemini:
            engine_text = "Gemini Multimodal (Standby)"
        else:
            engine_text = "Offline Parser (Standby)"

        # 4. Cryptographic Audit Seal Block (with 12-hour AM/PM IST Time)
        is_chain_valid, chain_err = verify_chain(st.session_state.get("audit_trail", []))
        evt_count = len(st.session_state.get("audit_trail", []))
        chain_status_pill = status_pill("Verified Chain", variant="approve") if is_chain_valid else status_pill("Tampered", variant="reject")
        gen_hash = st.session_state["audit_trail"][0].get("current_hash", "")[:8] if (evt_count > 0 and "audit_trail" in st.session_state) else "e3b0c442"
        s_time = st.session_state.get("session_started_at", "Active")
        if "UTC" in str(s_time) or ("AM" not in str(s_time) and "PM" not in str(s_time)):
            s_time = datetime.now(IST).strftime("%I:%M %p IST").lstrip("0")
            st.session_state.session_started_at = s_time

        # Consolidated Single Continuous Stream Card
        telemetry_stream = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 8px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04); overflow: hidden; margin-bottom: 12px;">
            <div style="padding: 12px 14px 10px 14px; border-bottom: 1px solid #E2E8F0; background: #FFFFFF;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 15px; font-weight: 800; color: #0B1F3A; letter-spacing: 0.05em; line-height: 1.1;">CREDITGATE</span>
                    <span style="font-size: 10px; font-weight: 700; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; border-radius: 4px; padding: 2px 7px; font-family: monospace;">{short_app_id}</span>
                </div>
                <div style="font-size: 11px; font-weight: 600; color: #64748B; margin-top: 3px;">Commercial Underwriting Copilot</div>
            </div>

            <div style="padding: 10px 14px; border-bottom: 1px solid #E2E8F0; border-left: 3px solid #0F766E; background: #FAFCFD;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                    <span style="font-size: 9px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.06em;">Active Application</span>
                    {entity_pill}
                </div>
                <div style="font-size: 13px; font-weight: 700; color: #0B1F3A; line-height: 1.3;">{company_name}</div>
                <div style="margin-top: 6px; padding-top: 5px; border-top: 1px dashed #E2E8F0; font-size: 9.5px; color: #64748B; line-height: 1.4;">
                    <div>• CIN: <span style="color: {cin_badge_col}; font-weight: 600;">{cin_status_text}</span></div>
                    <div>• PAN: <span style="color: {pan_badge_col}; font-weight: 600;">{pan_status_text}</span></div>
                </div>
            </div>

            {score_section_html}

            <div style="padding: 10px 14px; border-bottom: 1px solid #E2E8F0; background: #FFFFFF; font-size: 10.5px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <span style="color: #64748B; font-weight: 600;">Vision AI:</span>
                    <span style="color: #0F766E; font-weight: 700;">● {engine_text}</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: #64748B; font-weight: 600;">Statutory Registry Gateway:</span>
                    <span style="color: #0F766E; font-weight: 700;">● Active (MCA-21 / NeSL / e-Courts)</span>
                </div>
            </div>

            <div style="padding: 10px 14px 12px 14px; background: #F8FAFC;">
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11px;">
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="color: #0F766E; font-weight: 700; letter-spacing: 0.05em;">AUDIT SEAL</span>
                        <span style="color: #64748B; font-size: 10.5px; font-weight: 500;">• {evt_count} Blocks</span>
                    </div>
                    <div>{chain_status_pill}</div>
                </div>
                <div style="font-size: 10px; color: #64748B; font-family: monospace; margin-top: 6px; padding-top: 6px; border-top: 1px dashed #E2E8F0; display: flex; justify-content: space-between; align-items: center;">
                    <span>Root: <strong style="color: #0F766E;">{gen_hash}...</strong></span>
                    <span>Session: <strong style="color: #0B1F3A;">{s_time}</strong></span>
                </div>
            </div>
        </div>"""
        _render_html(telemetry_stream)

        # 5. Reset Active Application
        if st.button(":material/restart_alt: Reset Application", key="sb_btn_reset_case", width="stretch", help="Resets locks, extraction caches, and overrides while keeping the selected cohort"):
            cur_cohort = st.session_state.get("active_cohort_selection", "Cohort 1: Prime MSME (Apex Industrial Benchmark)")
            select_and_reset_cohort(cur_cohort)
            st.rerun()

    return get_app_context()


def get_app_context() -> Dict[str, Any]:
    """Retrieves the current application context, resolving from plain session_state keys."""
    init_session_state()

    cohort_selection = st.session_state.get("active_cohort_selection", "Cohort 1: Prime MSME (Apex Industrial Benchmark)")
    c_ident = st.session_state.get("custom_extracted_identifiers") or {}

    # Synchronize widget toggle state first if user flipped it
    if "case_is_corporate" in st.session_state:
        is_corporate = bool(st.session_state["case_is_corporate"])
        st.session_state.active_is_corporate = is_corporate
    elif cohort_selection == "None (Manual Upload)" and c_ident.get("is_corporate") is not None:
        is_corporate = bool(c_ident["is_corporate"])
        st.session_state.active_is_corporate = is_corporate
    else:
        is_corporate = st.session_state.get("active_is_corporate", True)

    cin_input = st.session_state.get("active_cin_selection")
    if not cin_input:
        if cohort_selection == "None (Manual Upload)":
            cin_input = c_ident.get("cin") or ("UNREGISTERED_MSME" if not is_corporate else "")
        else:
            cin_input = "U29253DL2018PTC334120"
        st.session_state.active_cin_selection = cin_input

    pan_input = st.session_state.get("active_pan_input")
    if not pan_input:
        if cohort_selection == "None (Manual Upload)":
            pan_input = c_ident.get("pan") or ""
        else:
            pan_input = "AAACA1234B"
        st.session_state.active_pan_input = pan_input

    current_cin_val = cin_input if is_corporate else "UNREGISTERED_MSME"
    current_pan_val = pan_input.strip().upper()

    cin_parsed_data = None
    if is_corporate:
        is_cin_valid, _ = validate_cin_syntax(current_cin_val)
        if is_cin_valid:
            cin_parsed_data = parse_cin(current_cin_val)
    else:
        manual_sector = st.session_state.get("case_manual_sector", st.session_state.get("manual_sector", list(NIC_SECTOR_TAXONOMY.values())[0]))
        manual_vintage = st.session_state.get("case_manual_vintage", st.session_state.get("manual_vintage", 6))
        ent_code = "PARTNERSHIP" if current_pan_val and len(current_pan_val) >= 4 and current_pan_val[3] == "F" else "PROPRIETORSHIP"
        cin_parsed_data = {
            "cin": "UNREGISTERED_MSME",
            "listing_status": "Unlisted",
            "macro_sector": manual_sector,
            "business_vintage_years": int(manual_vintage),
            "low_vintage_risk_flag": int(manual_vintage) < 3,
            "entity_code": ent_code,
            "state_name": "Maharashtra" if current_pan_val and len(current_pan_val) >= 4 and current_pan_val[3] == "F" else "Pan-India",
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
    collateral_val = float(st.session_state.get("facility_collateral_val", 2500000.0))
    cgtmse_covered = bool(st.session_state.get("facility_cgtmse_covered", False))

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
        if "Cohort 2" in cohort_selection or "Kaveri" in cohort_selection:
            cohort_folder = "cohort_2_distressed"
        elif "Cohort 3" in cohort_selection or "Bharat" in cohort_selection:
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

    active_comp_name = (
        c_ident.get("company_name") or st.session_state.get("custom_company_name") or "Manual Applicant Company"
        if cohort_selection == "None (Manual Upload)"
        else COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company")
    )

    app_id = st.session_state.get("app_id")
    if not app_id or (cohort_selection == "None (Manual Upload)" and c_ident.get("company_name") and "APEX" in app_id):
        app_id = generate_application_id(active_comp_name)
        st.session_state.app_id = app_id
    elif not app_id:
        app_id = generate_application_id(active_comp_name)
        st.session_state.app_id = app_id

    pan_char = pan_check.get("pan_entity_char", "")
    if not pan_char and len(current_pan_val) >= 4:
        pan_char = current_pan_val[3]

    if is_corporate:
        entity_pill = '<span style="font-size: 9.5px; font-weight: 700; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; border-radius: 4px; padding: 2px 7px;">PVT LTD</span>'
    elif pan_char == "F":
        entity_pill = '<span style="font-size: 9.5px; font-weight: 700; color: #0369A1; background: #E0F2FE; border: 1px solid #BAE6FD; border-radius: 4px; padding: 2px 7px;">PARTNERSHIP FIRM</span>'
    else:
        entity_pill = '<span style="font-size: 9.5px; font-weight: 700; color: #64748B; background: #F1F5F9; border: 1px solid #E2E8F0; border-radius: 4px; padding: 2px 7px;">PROPRIETOR</span>'

    cin_input_val = str(cin_input).strip().upper()
    if not is_corporate:
        cin_status_text = "Unregistered MSME"
        cin_badge_col = "#64748B"
    elif not cin_input_val or cin_input_val in ("UNREGISTERED_MSME", ""):
        cin_status_text = "Awaiting Specification"
        cin_badge_col = "#64748B"
    else:
        is_cin_syntax_valid, _ = validate_cin_syntax(cin_input_val)
        if not is_cin_syntax_valid:
            cin_status_text = "Invalid Format"
            cin_badge_col = "#EF4444"
        else:
            cin_status_text = "Valid Format (MCA-21)"
            cin_badge_col = "#0F766E"

    is_pan_valid = pan_check.get("is_valid", False)
    if not current_pan_val or current_pan_val in ("UNREGISTERED_MSME", ""):
        pan_status_text = "Awaiting Document Extraction"
        pan_badge_col = "#64748B"
    elif not is_pan_valid:
        pan_status_text = "Invalid Format"
        pan_badge_col = "#EF4444"
    elif current_pan_mismatch:
        pan_status_text = "Entity Type Mismatch"
        pan_badge_col = "#EF4444"
    else:
        if is_corporate:
            pan_status_text = "Valid Format (Company)"
        elif pan_char == "F":
            pan_status_text = "Valid Format (Partnership Firm)"
        else:
            pan_status_text = "Valid Format (Proprietor)"
        pan_badge_col = "#0F766E"

    ctx = {
        "app_id": app_id,
        "cohort_selection": cohort_selection,
        "cohort_name": cohort_name,
        "company_name": active_comp_name,
        "is_corporate": is_corporate,
        "cin_input": cin_input,
        "cin_parsed_data": cin_parsed_data,
        "pan_input": pan_input,
        "pan_check": pan_check,
        "current_cin_val": current_cin_val,
        "current_pan_val": current_pan_val,
        "current_pan_mismatch": current_pan_mismatch,
        "entity_pill": entity_pill,
        "cin_status_text": cin_status_text,
        "cin_badge_col": cin_badge_col,
        "pan_status_text": pan_status_text,
        "pan_badge_col": pan_badge_col,
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
