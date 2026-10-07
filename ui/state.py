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
from ui.components import render_kpi_card, render_callout, status_pill

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
        st.markdown("### :material/shield: CreditGate v3.0")
        st.caption("AI MSME Underwriting Copilot")
        st.markdown(f"**Application ID:** `{st.session_state.app_id}`")
        st.markdown("---")

        # 1. Step status (Case -> Evidence -> Decision -> CAM)
        with st.container(border=True):
            st.markdown("#### :material/alt_route: Step Status")

            is_extracted = (st.session_state.get("locked_case") is not None or st.session_state.get("extracted_case") is not None)
            is_locked = (st.session_state.get("locked_case") is not None)
            is_scored = (st.session_state.get("score_result") is not None)
            is_cam = (st.session_state.get("cam_markdown") is not None)

            p_case = status_pill("Configured", variant="approve")
            if is_locked:
                p_ev = status_pill("Locked", variant="approve")
            elif is_extracted:
                p_ev = status_pill("Extracted", variant="info")
            else:
                p_ev = status_pill("Pending", variant="neutral")

            p_dec = status_pill("Scored", variant="approve") if is_scored else status_pill("Pending", variant="neutral")
            p_cam = status_pill("Generated", variant="approve") if is_cam else status_pill("Pending", variant="neutral")

            st.markdown(f"**1. Case:** &nbsp; {p_case}", unsafe_allow_html=True)
            st.markdown(f"**2. Evidence:** &nbsp; {p_ev}", unsafe_allow_html=True)
            st.markdown(f"**3. Decision:** &nbsp; {p_dec}", unsafe_allow_html=True)
            st.markdown(f"**4. CAM & Audit:** &nbsp; {p_cam}", unsafe_allow_html=True)

        # 2. Reset Case Button
        if st.button(":material/restart_alt: Reset Case", key="sb_btn_reset_case", width="stretch"):
            cur_cohort = st.session_state.get("active_cohort_selection", "Cohort 1: Prime MSME (Apex Synthetic Demo)")
            select_and_reset_cohort(cur_cohort)
            st.rerun()

        # 3. Audit chain status via verify_chain()
        with st.container(border=True):
            st.markdown("#### :material/verified_user: Audit Trail")
            is_chain_valid, chain_err = verify_chain(st.session_state.get("audit_trail", []))
            if is_chain_valid:
                evt_count = len(st.session_state.get("audit_trail", []))
                st.markdown(f"**Chain:** &nbsp; {status_pill('Verified', variant='approve')}", unsafe_allow_html=True)
                st.caption(f"{evt_count} events in verified hash chain")
            else:
                st.markdown(f"**Chain:** &nbsp; {status_pill('Tampered', variant='reject')}", unsafe_allow_html=True)
                st.caption(f"Error: {chain_err}")

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

    cin_parsed_data = None
    if is_corporate:
        is_cin_valid, _ = validate_cin_syntax(current_cin_val)
        if is_cin_valid:
            cin_parsed_data = parse_cin(current_cin_val)
    else:
        manual_sector = st.session_state.get("manual_sector", list(NIC_SECTOR_TAXONOMY.values())[0])
        manual_vintage = st.session_state.get("manual_vintage", 7)
        cin_parsed_data = {
            "cin": "UNREGISTERED_MSME",
            "listing_status": "Unlisted",
            "macro_sector": manual_sector,
            "business_vintage_years": manual_vintage,
            "low_vintage_risk_flag": manual_vintage < 3,
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
    st.markdown(
        """
    <div class="gate-card-hold">
        <h3 style="color: #B91C1C; margin-top: 0; font-size: 16px;">STATUS: ON HOLD (Pending Mandatory Documents)</h3>
        <p style="color: #475569; font-size: 13px; max-width: 650px; margin: 0 0 10px 0;">
            The Underwriting Pipeline is gated. Under MSME credit policy, financial scoring cannot commence until all three documents in the <strong>Mandatory Document Trinity</strong> are uploaded or loaded.
        </p>
        <div style="font-weight: 600; font-size: 13px; color: #1F2937;">
            Required documents: Bank Statement, GSTR-3B Return, and ITR Form.
        </div>
        <div style="margin-top: 8px; font-size: 12px; color: #64748B;">
            Select a Pre-Generated Synthetic Cohort from the left sidebar to load verified sample documents.
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )
