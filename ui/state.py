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
from core.audit_logger import AuditLogger
from ui.components import render_kpi_card, render_callout

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
    except Exception:
        st.session_state["_pending_cohort_reset"] = cohort_name
        st.rerun()
        return

    st.session_state.active_cohort_selection = cohort_name
    st.session_state.active_cin_selection = defaults["cin"]
    st.session_state.active_pan_input = defaults["pan"].strip().upper()
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
    # Facility defaults
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
    """Renders the shared sidebar with 3 bordered groups (Case, Borrower, Facility)."""
    init_session_state()

    if "_pending_cohort_reset" in st.session_state:
        pending_cohort = st.session_state.pop("_pending_cohort_reset")
        select_and_reset_cohort(pending_cohort)

    with st.sidebar:
        st.markdown("### :material/shield: CreditGate v3.0")
        st.caption("AI-Powered MSME Credit Underwriting Copilot")
        st.markdown(f"**Application ID:** `{st.session_state.app_id}`")
        st.markdown("---")

        # --------------------------------------------------------------------
        # BORDERED GROUP 1: Case (Cohort)
        # --------------------------------------------------------------------
        with st.container(border=True):
            st.markdown("#### :material/workspaces: Case")
            cohort_options = [
                "None (Manual Upload)",
                "Cohort 1: Prime MSME (Apex Synthetic Demo)",
                "Cohort 2: Distressed (Kaveri Fictitious Demo)",
                "Cohort 3: Mismatch Trap (Bharat Mock Demo)",
            ]
            default_idx = None if "sb_cohort_selection" in st.session_state else 1
            cohort_selection = st.selectbox(
                "Quick-Load Test Cohort",
                cohort_options,
                index=default_idx,
                key="sb_cohort_selection",
            )
            cohort_selection = cohort_selection or "None (Manual Upload)"

        if "active_cohort_selection" not in st.session_state:
            st.session_state.active_cohort_selection = cohort_selection

        # 1. Full reset on cohort change (Must run BEFORE sb_cin_input / sb_pan_input widgets are instantiated)
        if st.session_state.active_cohort_selection != cohort_selection:
            defaults = COHORT_DEFAULTS.get(cohort_selection, COHORT_DEFAULTS["None (Manual Upload)"])
            st.session_state.sb_cin_input = defaults["cin"]
            st.session_state.sb_pan_input = defaults["pan"]
            st.session_state.active_cohort_selection = cohort_selection
            st.session_state.active_cin_selection = defaults["cin"]
            st.session_state.active_pan_input = defaults["pan"].strip().upper()
            st.session_state.active_pan_mismatch = False
            st.session_state.app_id = generate_application_id(defaults["company_name"])
            st.session_state.audit_trail = []

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
            st.rerun()

        # --------------------------------------------------------------------
        # BORDERED GROUP 2: Borrower (CIN, PAN)
        # --------------------------------------------------------------------
        with st.container(border=True):
            st.markdown("#### :material/domain: Borrower")
            is_corporate = st.toggle("Registered Corporate? (MCA CIN)", value=True, key="sb_is_corporate")

            cin_parsed_data = None
            if is_corporate:
                default_cin = "U29253DL2018PTC334120"
                if cohort_selection and "Cohort 2" in cohort_selection:
                    default_cin = "U60200KA2019PTC120481"
                elif cohort_selection and "Cohort 3" in cohort_selection:
                    default_cin = "U24101MH2020PTC349120"

                cin_val = None if "sb_cin_input" in st.session_state else default_cin
                cin_input = st.text_input("Corporate ID Number (CIN)", value=cin_val, key="sb_cin_input")
                is_cin_valid, cin_err = validate_cin_syntax(cin_input)
                if not is_cin_valid:
                    render_callout(cin_err, title="Invalid CIN Format", variant="error")
                else:
                    cin_parsed_data = parse_cin(cin_input)
                    render_callout(
                        f"MCA Valid: {cin_parsed_data['state_name']} | Vintage: {cin_parsed_data['business_vintage_years']} Years",
                        title="MCA Validated",
                        variant="success",
                    )
                    if cin_parsed_data["low_vintage_risk_flag"]:
                        render_callout(
                            "Business vintage < 3 years indicates elevated early-stage default risk.",
                            title="Vintage Risk Warning",
                            variant="warning",
                        )
            else:
                render_callout("Unregistered MSME / Proprietorship mode active.", title="Entity Mode", variant="info")
                manual_sector = st.selectbox("Industry Macro Sector", list(NIC_SECTOR_TAXONOMY.values()), index=0, key="sb_manual_sector")
                manual_vintage = st.number_input("Business Vintage (Years)", min_value=1, max_value=60, value=7, key="sb_manual_vintage")
                cin_input = "UNREGISTERED_MSME"
                cin_parsed_data = {
                    "cin": "UNREGISTERED_MSME",
                    "listing_status": "Unlisted",
                    "macro_sector": manual_sector,
                    "business_vintage_years": manual_vintage,
                    "low_vintage_risk_flag": manual_vintage < 3,
                    "entity_code": "PROPRIETORSHIP",
                    "state_name": "Pan-India",
                }

            # PAN Verification
            default_pan = "AAACA1234B"
            if cohort_selection and "Cohort 2" in cohort_selection:
                default_pan = "AAACK5678M"
            elif cohort_selection and "Cohort 3" in cohort_selection:
                default_pan = "AABFB8910N"

            pan_val = None if "sb_pan_input" in st.session_state else default_pan
            pan_input = st.text_input("Permanent Account Number (PAN)", value=pan_val, key="sb_pan_input")
            pan_check = check_entity_consistency(
                pan_input,
                is_corporate=is_corporate,
                cin_entity_code=cin_parsed_data.get("entity_code") if cin_parsed_data else None,
            )
            if not pan_check["is_valid"]:
                render_callout(pan_check["error"], title="Invalid PAN Format", variant="error")
            elif pan_check["mismatch_detected"]:
                render_callout(pan_check["warning_message"], title="Entity Mismatch Detected", variant="warning")

        # Detect CIN, PAN, or entity-type change and wipe/invalidate downstream session state
        current_cin_val = cin_input if is_corporate else "UNREGISTERED_MSME"
        current_pan_val = pan_input.strip().upper()
        current_pan_mismatch = bool(pan_check.get("mismatch_detected", False))

        if "active_is_corporate" not in st.session_state:
            st.session_state.active_is_corporate = is_corporate
        if "active_cin_selection" not in st.session_state:
            st.session_state.active_cin_selection = current_cin_val
        if "active_pan_input" not in st.session_state:
            st.session_state.active_pan_input = current_pan_val
        if "active_pan_mismatch" not in st.session_state:
            st.session_state.active_pan_mismatch = current_pan_mismatch

        # Reset on entity-type change
        if st.session_state.active_is_corporate != is_corporate:
            st.session_state.active_is_corporate = is_corporate
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

        # Full reset on CIN change
        if st.session_state.active_cin_selection != current_cin_val:
            st.session_state.active_cin_selection = current_cin_val
            st.session_state.active_pan_input = current_pan_val
            st.session_state.active_pan_mismatch = current_pan_mismatch
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

        # Reset on PAN change
        if (st.session_state.active_pan_input != current_pan_val) or (st.session_state.active_pan_mismatch != current_pan_mismatch):
            st.session_state.active_pan_input = current_pan_val
            st.session_state.active_pan_mismatch = current_pan_mismatch
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

        # --------------------------------------------------------------------
        # BORDERED GROUP 3: Facility Terms (st.form)
        # --------------------------------------------------------------------
        with st.container(border=True):
            st.markdown("#### :material/payments: Facility")
            with st.form(key="facility_form"):
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
                submitted = st.form_submit_button(":material/save: Apply Facility Terms", type="primary")
                if submitted:
                    st.session_state.facility_loan_amount = float(form_loan)
                    st.session_state.facility_tenure_months = int(form_tenure)
                    st.session_state.facility_interest_rate = float(form_rate)
                    st.session_state.facility_collateral_val = float(form_collat)
                    st.session_state.facility_cgtmse_covered = bool(form_cgtmse)
                    st.rerun()

            loan_amount = st.session_state.facility_loan_amount
            tenure_months = st.session_state.facility_tenure_months
            interest_rate = st.session_state.facility_interest_rate
            collateral_val = st.session_state.facility_collateral_val
            cgtmse_covered = st.session_state.facility_cgtmse_covered

            # Backend-computed Annuity EMI
            computed_monthly_emi = calculate_monthly_emi(
                principal=Decimal(str(loan_amount)),
                annual_rate_pct=Decimal(str(interest_rate)),
                tenure_months=int(tenure_months),
            )
            render_kpi_card(
                "Computed Monthly EMI",
                format_inr(computed_monthly_emi),
                subtext="Banking Annuity Formula",
                tooltip="P*r*(1+r)^n / ((1+r)^n - 1)",
            )
            proposed_emi = float(computed_monthly_emi)

    # Resolve document bytes (Cohort vs In-Memory Uploads)
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


def get_app_context() -> Dict[str, Any]:
    """Retrieves the current application context, initializing if needed."""
    if "app_context" not in st.session_state:
        init_session_state()
        return render_sidebar()
    return st.session_state.app_context


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
