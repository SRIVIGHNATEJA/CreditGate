import os
import re
import time
import json
import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any, List, Optional

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from core.config import MODEL_EXTRACTION, MODEL_RESEARCH
from core.data_models import (
    CreditCase,
    BankExtraction,
    GSTExtraction,
    ITRExtraction,
    coerce_currency,
    ReviewStatus,
    FindingTier,
    AdverseMediaFinding,
    AdverseMediaReport,
)
from core.researcher import (
    build_search_query,
    search_adverse_media,
    confirm_adverse_finding,
    dismiss_adverse_finding,
    extract_policy_flags,
)
from core.onboarding import (
    parse_cin,
    validate_cin_syntax,
    validate_pan_syntax,
    check_entity_consistency,
    generate_application_id,
    NIC_SECTOR_TAXONOMY,
)
from core.parser import (
    extract_all_documents,
    verify_bank_reconciliation,
)
from core.policy import (
    Verdict,
    WEAKEST_LINK_THRESHOLD,
)
from core.scorer import (
    score_credit_case,
    ScoreResult,
)
from core.formatting import (
    format_inr,
)
from core.cam_generator import (
    generate_cam_markdown,
    CamGenerationError,
)
from core.audit_logger import (
    AuditLogger,
    log_event,
    mask_identifier,
)

# ----------------------------------------------------------------------------
# 1. PAGE CONFIGURATION & ENTERPRISE STYLING
# ----------------------------------------------------------------------------

st.set_page_config(
    page_title="CreditGate | AI Underwriting Copilot",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for dark-mode financial dashboard aesthetics
st.markdown("""
<style>
    .main-title {
        font-size: 26px;
        font-weight: 800;
        color: #1e3a8a;
        margin-bottom: 2px;
    }
    .sub-title {
        font-size: 13px;
        color: #64748b;
        margin-bottom: 20px;
    }
    .badge-corporate {
        background-color: #eff6ff;
        color: #1d4ed8;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 11px;
    }
    .badge-risk {
        background-color: #fef2f2;
        color: #dc2626;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 11px;
    }
    .gate-card-hold {
        background-color: #fff1f2;
        border: 2px dashed #e11d48;
        border-radius: 8px;
        padding: 24px;
        text-align: center;
        margin-top: 20px;
    }
    .gate-card-ready {
        background-color: #f0fdf4;
        border: 1px solid #86efac;
        border-radius: 6px;
        padding: 12px 16px;
        margin-bottom: 16px;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# 2. SESSION STATE INITIALIZATION (RERUN GUARDS & CACHE KEYS)
# ----------------------------------------------------------------------------

if "app_id" not in st.session_state:
    st.session_state.app_id = generate_application_id("APEX_PRECISION_TOOLS")
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

audit = AuditLogger(st.session_state.app_id)


def record_event(event_type: str, actor: str, details: Dict[str, Any], severity: str = "INFO"):
    """Helper to record event both to disk JSONL and in-memory session state."""
    evt = audit.log(event_type, actor, details, severity)
    st.session_state.audit_trail.append(evt)


# ----------------------------------------------------------------------------
# 3. SIDEBAR: ONBOARDING METADATA, TOGGLES & DOCUMENT GATE
# ----------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### 🛡️ CreditGate v3.0")
    st.caption("AI-Powered MSME Credit Underwriting Copilot")
    st.markdown(f"**Application ID:** `{st.session_state.app_id}`")
    st.markdown("---")

    # Demo Quick-Loader
    st.markdown("#### ⚡ Quick-Load Test Cohort")
    cohort_selection = st.selectbox(
        "Load Pre-Generated Synthetic Cohort",
        ["None (Manual Upload)", "Cohort 1: Prime MSME (Apex Tools)", "Cohort 2: Distressed (Kaveri Freight)", "Cohort 3: Mismatch Trap (Bharat Steels)"],
        index=1
    )

    st.markdown("---")
    st.markdown("#### 🏢 Entity Classification")
    is_corporate = st.toggle("Registered Corporate? (Has MCA CIN)", value=True)

    cin_parsed_data = None
    if is_corporate:
        default_cin = "U29253DL2018PTC334120"
        if "Cohort 2" in cohort_selection:
            default_cin = "U60200KA2019PTC120481"
        elif "Cohort 3" in cohort_selection:
            default_cin = "U24101MH2020PTC349120"

        cin_input = st.text_input("Corporate Identification Number (CIN)", value=default_cin)
        is_cin_valid, cin_err = validate_cin_syntax(cin_input)
        if not is_cin_valid:
            st.error(f"⚠️ {cin_err}")
        else:
            cin_parsed_data = parse_cin(cin_input)
            st.success(f"✅ MCA Valid: {cin_parsed_data['state_name']} | Vintage: {cin_parsed_data['business_vintage_years']} Years")
            if cin_parsed_data["low_vintage_risk_flag"]:
                st.warning("⚠️ High Default Risk: Business vintage < 3 years")
    else:
        st.info("Unregistered MSME / Proprietorship mode active.")
        manual_sector = st.selectbox("Industry Macro Sector", list(NIC_SECTOR_TAXONOMY.values()), index=0)
        manual_vintage = st.number_input("Business Vintage (Years)", min_value=1, max_value=60, value=7)
        cin_parsed_data = {
            "cin": "UNREGISTERED_MSME",
            "listing_status": "Unlisted",
            "macro_sector": manual_sector,
            "business_vintage_years": manual_vintage,
            "low_vintage_risk_flag": manual_vintage < 3,
            "entity_code": "PROPRIETORSHIP",
            "state_name": "Pan-India"
        }

    # PAN Verification
    default_pan = "AAACA1234B"
    if "Cohort 2" in cohort_selection:
        default_pan = "AAACK5678M"
    elif "Cohort 3" in cohort_selection:
        default_pan = "AABFB8910N"

    pan_input = st.text_input("Permanent Account Number (PAN)", value=default_pan)
    pan_check = check_entity_consistency(
        pan_input,
        is_corporate=is_corporate,
        cin_entity_code=cin_parsed_data.get("entity_code") if cin_parsed_data else None
    )
    if not pan_check["is_valid"]:
        st.error(f"⚠️ {pan_check['error']}")
    elif pan_check["mismatch_detected"]:
        st.warning(pan_check["warning_message"])

    st.markdown("---")
    st.markdown("#### 💰 Facility Specifications")
    loan_amount = st.number_input("Requested Facility (₹)", value=5000000.0, step=500000.0, format="%.2f")
    tenure_months = st.slider("Tenure (Months)", min_value=12, max_value=84, value=36, step=6)
    interest_rate = st.slider("Indicative Rate (%)", min_value=7.5, max_value=24.0, value=11.5, step=0.25)
    proposed_emi = st.number_input("Estimated Monthly EMI (₹)", value=164900.0, step=5000.0, format="%.2f")

    st.markdown("---")
    st.markdown("#### 📂 Mandatory Document Gate")
    st.caption("Document Trinity required for credit assessment:")
    uploaded_bank = st.file_uploader("1. Bank Statement (PDF)", type=["pdf"])
    uploaded_gst = st.file_uploader("2. GSTR-3B Summary (PDF)", type=["pdf"])
    uploaded_itr = st.file_uploader("3. ITR / Audited Financials (PDF)", type=["pdf"])

# ----------------------------------------------------------------------------
# 4. RESOLVE DOCUMENT BYTES (COHORT QUICK-LOAD vs MANUAL UPLOAD)
# ----------------------------------------------------------------------------

base_synthetic = Path(__file__).resolve().parent / "data" / "synthetic"
bank_bytes, gst_bytes, itr_bytes = None, None, None
cohort_name = "Custom Upload"

if cohort_selection != "None (Manual Upload)":
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
    if uploaded_bank:
        bank_bytes = uploaded_bank.read()
    if uploaded_gst:
        gst_bytes = uploaded_gst.read()
    if uploaded_itr:
        itr_bytes = uploaded_itr.read()

# ----------------------------------------------------------------------------
# 5. DOCUMENT GATE CHECK (HOLD STATUS ENFORCEMENT)
# ----------------------------------------------------------------------------

has_all_docs = (bank_bytes is not None) and (gst_bytes is not None) and (itr_bytes is not None)

st.markdown('<div class="main-title">CreditGate: AI MSME Underwriting Copilot</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Stage 1: Structured Onboarding &nbsp;|&nbsp; Stage 2: Parallel Extraction &nbsp;|&nbsp; Stage 3: Human-in-the-Loop Verification</div>', unsafe_allow_html=True)

if not has_all_docs:
    st.markdown("""
    <div class="gate-card-hold">
        <h3 style="color: #e11d48; margin-top: 0;">🚨 STATUS: ON HOLD (Pending Mandatory Documents)</h3>
        <p style="color: #475569; font-size: 14px; max-width: 600px; margin: 0 auto;">
            The Underwriting Pipeline is gated. Under Indian lending compliance, no ratio calculation or credit appraisal can commence until all three documents in the <strong>Mandatory Document Trinity</strong> are received.
        </p>
        <div style="margin-top: 16px; font-weight: 700; color: #334155;">
            Missing documents: Bank Statement, GSTR-3B Return, and ITR Form.
        </div>
        <div style="margin-top: 8px; font-size: 12px; color: #64748b;">
            💡 <em>Tip: Select a Pre-Generated Synthetic Cohort from the left sidebar to bypass manual file upload and run an instant end-to-end evaluation.</em>
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ----------------------------------------------------------------------------
# 6. PIPELINE WORKFLOW TABS
# ----------------------------------------------------------------------------

tab_onboard, tab_hitl, tab_scoring, tab_cam, tab_audit = st.tabs([
    "📋 1. Onboarding & Entity Intelligence",
    "🤖 2. Multimodal Extraction & HITL Verification",
    "⚖️ 3. Five Cs Risk Engine & Policy Gate",
    "📑 4. Executive CAM Report Export",
    "📜 5. Live Audit Trail & Provenance"
])

# ============================================================================
# TAB 1: ONBOARDING & ENTITY INTELLIGENCE
# ============================================================================

with tab_onboard:
    st.markdown("### Corporate Entity & Identification Profile")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Entity Status", "Active Corporate" if is_corporate else "Proprietorship")
    with col2:
        st.metric("Macro Industry Sector", cin_parsed_data["macro_sector"][:24] + "..." if len(cin_parsed_data["macro_sector"]) > 24 else cin_parsed_data["macro_sector"])
    with col3:
        st.metric("Operating Vintage", f"{cin_parsed_data['business_vintage_years']} Years")
    with col4:
        st.metric("Jurisdiction State", cin_parsed_data["state_name"])

    st.markdown("#### Entity Identification Consistency Check")
    if pan_check["mismatch_detected"]:
        st.error(f"🚨 **CONSISTENCY ALERT:** {pan_check['warning_message']}")
    else:
        st.success(f"✅ **CONSISTENT:** PAN 4th character '{pan_check['pan_entity_char']}' aligns with {pan_check['pan_entity_type']}.")

    st.markdown("#### Facility Parameters Summary")
    fcol1, fcol2, fcol3 = st.columns(3)
    fcol1.metric("Requested Facility", f"₹ {loan_amount:,.2f}")
    fcol2.metric("Tenure & Rate", f"{tenure_months}M @ {interest_rate}% p.a.")
    fcol3.metric("Proposed Monthly Debt Service", f"₹ {proposed_emi:,.2f}")

    if st.button("Log Onboarding Verification Event", key="btn_log_onboarding"):
        record_event(
            event_type="ONBOARDING_VERIFIED",
            actor="OFFICER",
            details={
                "cin": cin_parsed_data["cin"],
                "pan": pan_input,
                "sector": cin_parsed_data["macro_sector"],
                "vintage_years": cin_parsed_data["business_vintage_years"],
                "loan_amount": str(loan_amount)
            }
        )
        st.toast("Onboarding verified and stamped in Audit Trail.")

# ============================================================================
# TAB 2: MULTIMODAL EXTRACTION & HITL VERIFICATION
# ============================================================================

with tab_hitl:
    st.markdown("### Multimodal AI Extraction & Human Verification Grid")
    st.caption("Gemini 3.5 Flash Lite extracts financial fields in parallel. The loan officer verifies data before passing it to the deterministic risk scorer.")

    if st.session_state.extracted_case is None:
        if st.button("🚀 Run Multimodal Extraction (3 Parallel Threads)", type="primary"):
            with st.spinner("Slicing pages with PyMuPDF, scrubbing PII, and extracting JSON schema in parallel..."):
                t_start = time.perf_counter()
                credit_case, meta = extract_all_documents(
                    bank_bytes=bank_bytes,
                    gst_bytes=gst_bytes,
                    itr_bytes=itr_bytes,
                    company_name=cohort_selection.replace("Cohort ", "").replace("None (Manual Upload)", "Manual Applicant"),
                    cin_or_pan=cin_parsed_data["cin"],
                    requested_loan_amount=Decimal(str(loan_amount)),
                    proposed_emi=Decimal(str(proposed_emi)),
                    proposed_rate=Decimal(str(interest_rate)),
                    proposed_tenure_months=int(tenure_months)
                )
                t_elapsed = time.perf_counter() - t_start

                st.session_state.extracted_case = credit_case
                st.session_state.extraction_meta = meta

                # Prepare initial HITL Dataframe
                b = credit_case.bank_data
                g = credit_case.gst_data
                it = credit_case.itr_data

                hitl_records = [
                    {"Parameter": "Annual Gross Revenue", "AI_Extracted_Amount": float(it.gross_total_income), "Source_Document": "ITR / Audited P&L", "Page": "Page 1", "Confidence": it.confidence, "Status": "✅ OK"},
                    {"Parameter": "Total Bank Inward Credits", "AI_Extracted_Amount": float(b.total_credits), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": "✅ OK"},
                    {"Parameter": "Total Outward Debits", "AI_Extracted_Amount": float(b.total_debits), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": "✅ OK"},
                    {"Parameter": "Bank Opening Balance", "AI_Extracted_Amount": float(b.opening_balance), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": "✅ OK"},
                    {"Parameter": "Bank Closing Balance", "AI_Extracted_Amount": float(b.closing_balance), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": "✅ OK"},
                    {"Parameter": "Cheque / Mandate Bounces", "AI_Extracted_Amount": float(b.bounce_count), "Source_Document": "Bank Statement", "Page": "Page 1-2", "Confidence": b.confidence, "Status": "✅ OK" if b.bounce_count == 0 else "⚠️ CHECK (Bounce Risk)"},
                    {"Parameter": "GST Taxable Turnover", "AI_Extracted_Amount": float(g.taxable_value), "Source_Document": "GSTR-3B Summary", "Page": "Page 1", "Confidence": g.confidence, "Status": "✅ OK"},
                    {"Parameter": "Total GST Paid", "AI_Extracted_Amount": float(g.total_tax_paid), "Source_Document": "GSTR-3B Summary", "Page": "Page 1", "Confidence": g.confidence, "Status": "✅ OK"},
                    {"Parameter": "Profit After Tax (PAT)", "AI_Extracted_Amount": float(it.profit_after_tax), "Source_Document": "ITR / P&L", "Page": "Page 1", "Confidence": it.confidence, "Status": "✅ OK"},
                    {"Parameter": "Depreciation Expense", "AI_Extracted_Amount": float(it.depreciation), "Source_Document": "ITR / P&L", "Page": "Page 1", "Confidence": it.confidence, "Status": "✅ OK"},
                    {"Parameter": "Finance Costs / Interest", "AI_Extracted_Amount": float(it.interest_paid), "Source_Document": "ITR / P&L", "Page": "Page 1", "Confidence": it.confidence, "Status": "✅ OK"},
                    {"Parameter": "Tangible Net Worth", "AI_Extracted_Amount": float(it.net_worth), "Source_Document": "ITR / Balance Sheet", "Page": "Page 1", "Confidence": it.confidence, "Status": "✅ OK"},
                    {"Parameter": "Existing Long-Term Debt", "AI_Extracted_Amount": float(it.total_debt), "Source_Document": "ITR / Balance Sheet", "Page": "Page 1", "Confidence": it.confidence, "Status": "✅ OK"},
                    {"Parameter": "Current Annual Debt Service", "AI_Extracted_Amount": float(it.existing_emi_obligations), "Source_Document": "ITR / Financials", "Page": "Page 1", "Confidence": it.confidence, "Status": "✅ OK"},
                ]

                # Cross-Document Triangulation: GST vs Bank Inward discrepancy check
                gst_val = float(g.taxable_value)
                bank_cr = float(b.total_credits)
                if abs(gst_val - bank_cr) / max(gst_val, bank_cr, 1.0) > 0.20:
                    for rec in hitl_records:
                        if rec["Parameter"] in ["GST Taxable Turnover", "Total Bank Inward Credits"]:
                            rec["Status"] = "⚠️ CHECK (Accrual vs Cash Gap > 20%)"

                df = pd.DataFrame(hitl_records)
                st.session_state.editable_df = df
                st.session_state.original_df = df.copy()

                record_event(
                    event_type="EXTRACTION_COMPLETED",
                    actor="AI_GEMINI",
                    details={
                        "latency_sec": round(t_elapsed, 3),
                        "model": MODEL_EXTRACTION,
                        "bank_balanced": meta["bank_reconciled"],
                        "reconciliation_diff": meta["bank_reconciliation_difference"]
                    }
                )
                st.rerun()

    if st.session_state.editable_df is not None:
        meta = st.session_state.extraction_meta
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Measured API Latency", f"{meta['execution_latency_seconds']}s", delta="Parallel 3 Threads", delta_color="normal")
        col_m2.metric("Ledger Equation Check", "BALANCED ✅" if meta["bank_reconciled"] else "DISCREPANCY ❌", help="Open + Credits - Debits == Close")
        col_m3.metric("Document Trinity Status", "3/3 Extracted", delta="100% Complete")

        st.markdown("#### Human-in-the-Loop Verification Editor")
        st.info("💡 **Officer Review:** Inspect the extracted values. If a number requires adjustment, double click and edit it directly. The audit log will record your modifications.")

        edited_df = st.data_editor(
            st.session_state.editable_df,
            column_config={
                "AI_Extracted_Amount": st.column_config.NumberColumn(
                    "Verified Amount (₹)",
                    format="₹ %.2f",
                    step=1000.0,
                    required=True
                ),
                "Status": st.column_config.TextColumn("System Validation Status"),
                "Confidence": st.column_config.TextColumn("AI Confidence"),
                "Source_Document": st.column_config.TextColumn("Source Origin")
            },
            disabled=["Parameter", "Source_Document", "Page", "Confidence", "Status"],
            use_container_width=True,
            num_rows="fixed",
            key="hitl_data_editor"
        )

        # Detect and log officer modifications
        orig_df = st.session_state.original_df
        changes = []
        for idx in range(len(orig_df)):
            orig_val = orig_df.iloc[idx]["AI_Extracted_Amount"]
            new_val = edited_df.iloc[idx]["AI_Extracted_Amount"]
            param = orig_df.iloc[idx]["Parameter"]
            if orig_val != new_val:
                changes.append((param, orig_val, new_val))

        if changes:
            st.warning(f"⚠️ {len(changes)} field(s) modified by officer. These corrections will be logged with officer attribution.")
            for p, o, n in changes:
                st.write(f"• **{p}:** Changed from `₹ {o:,.2f}` to `₹ {n:,.2f}`")

        c1, c2 = st.columns([1, 4])
        with c1:
            if st.button("✅ Confirm & Lock Financials", type="primary"):
                # Log modifications if any
                for p, o, n in changes:
                    record_event(
                        event_type="HITL_FIELD_CORRECTED",
                        actor="OFFICER",
                        details={
                            "parameter": p,
                            "original_amount": str(o),
                            "corrected_amount": str(n)
                        },
                        severity="WARNING"
                    )

                record_event(
                    event_type="HITL_CONFIRMED",
                    actor="OFFICER",
                    details={"total_fields_reviewed": len(edited_df), "modifications_made": len(changes)}
                )

                # Reconstruct canonical CreditCase from verified table
                params_map = {}
                for idx in range(len(edited_df)):
                    p_name = edited_df.iloc[idx]["Parameter"]
                    val = edited_df.iloc[idx]["AI_Extracted_Amount"]
                    params_map[p_name] = val

                def to_dec(val):
                    if pd.isna(val) or val is None:
                        return None
                    return Decimal(str(val))

                b_data = BankExtraction(
                    total_credits=to_dec(params_map.get("Total Bank Inward Credits")),
                    total_debits=to_dec(params_map.get("Total Outward Debits")),
                    opening_balance=to_dec(params_map.get("Bank Opening Balance")),
                    closing_balance=to_dec(params_map.get("Bank Closing Balance")),
                    bounce_count=int(params_map.get("Cheque / Mandate Bounces", 0)) if not pd.isna(params_map.get("Cheque / Mandate Bounces")) else None,
                    confidence="HIGH"
                )

                g_data = GSTExtraction(
                    taxable_value=to_dec(params_map.get("GST Taxable Turnover")),
                    total_tax_paid=to_dec(params_map.get("Total GST Paid")),
                    confidence="HIGH"
                )

                itr_data = ITRExtraction(
                    gross_total_income=to_dec(params_map.get("Annual Gross Revenue")),
                    profit_after_tax=to_dec(params_map.get("Profit After Tax (PAT)")),
                    depreciation=to_dec(params_map.get("Depreciation Expense")),
                    interest_paid=to_dec(params_map.get("Finance Costs / Interest")),
                    net_worth=to_dec(params_map.get("Tangible Net Worth")),
                    total_debt=to_dec(params_map.get("Existing Long-Term Debt")),
                    existing_emi_obligations=to_dec(params_map.get("Current Annual Debt Service")),
                    confidence="HIGH"
                )

                locked_case = CreditCase(
                    company_name=cohort_selection.replace("Cohort ", "").replace("None (Manual Upload)", "Manual Applicant"),
                    cin_or_pan=cin_parsed_data["cin"],
                    requested_loan_amount=Decimal(str(loan_amount)),
                    proposed_emi=Decimal(str(proposed_emi)),
                    proposed_rate=Decimal(str(interest_rate)),
                    proposed_tenure_months=int(tenure_months),
                    bank_data=b_data,
                    gst_data=g_data,
                    itr_data=itr_data
                )

                # Compute financial hash for state invalidation (Review Item 4)
                new_hash = hashlib.sha256(
                    json.dumps(locked_case.model_dump(), sort_keys=True, default=str).encode()
                ).hexdigest()[:16]

                if st.session_state.scored_financials_hash != new_hash:
                    st.session_state.score_result = None
                    st.session_state.cam_markdown = None

                st.session_state.locked_case = locked_case
                st.session_state.locked_financials_hash = new_hash
                st.session_state.hitl_confirmed = True
                st.session_state.editable_df = edited_df
                st.session_state.original_df = edited_df.copy()
                st.success("Financial data locked and hash-verified! Ready for Five Cs Scoring.")
                st.rerun()

        with c2:
            if st.button("🔄 Reset Extraction"):
                st.session_state.extracted_case = None
                st.session_state.editable_df = None
                st.session_state.original_df = None
                st.session_state.locked_case = None
                st.session_state.locked_financials_hash = None
                st.session_state.scored_financials_hash = None
                st.session_state.score_result = None
                st.session_state.cam_markdown = None
                st.session_state.hitl_confirmed = False
                st.rerun()

# ============================================================================
# TAB 3: FIVE Cs RISK ENGINE & POLICY GATE
# ============================================================================

with tab_scoring:
    st.markdown("### Deterministic Five Cs Risk Scorecard & Policy Gate")
    st.caption("Evaluated entirely in Python Decimal arithmetic. Zero LLM hallucinations.")

    # ------------------------------------------------------------------------
    # ADVERSE MEDIA SCREENING & LEGAL DUE DILIGENCE
    # ------------------------------------------------------------------------
    st.markdown("#### 🔍 Adverse Media Screening & Legal Due Diligence")
    st.caption("Secondary structured search ingestion with keyword/pattern matching. Zero LLM hallucinations.")

    active_cin = cin_parsed_data["cin"] if cin_parsed_data else "UNREGISTERED_MSME"
    # Disambiguation cache check: Invalidate if applicant CIN changed
    if st.session_state.last_research_cin != active_cin:
        st.session_state.last_research_cin = active_cin
        st.session_state.current_research_report = st.session_state.research_cache.get(active_cin, None)

    company_nm = (
        st.session_state.locked_case.company_name
        if st.session_state.locked_case
        else cohort_selection.replace("Cohort ", "").replace("None (Manual Upload)", "Manual Applicant")
    )
    anchored_query = build_search_query(company_nm, cin=active_cin)

    col_q1, col_q2 = st.columns([3, 1])
    with col_q1:
        st.code(f"Anchored Identity Query: {anchored_query}", language="sql")
    with col_q2:
        use_synthetic_demo = st.checkbox(
            "🧪 Synthetic Demo Mode",
            value=not bool(os.getenv("TAVILY_API_KEY")),
            help="Generates simulated legal/adverse records watermarked with [SYNTHETIC DEMO DATA]"
        )

    if st.button("🔎 Execute Adverse Media & Legal Docket Scan"):
        # Check rerun cache keyed by CIN
        if active_cin in st.session_state.research_cache:
            st.session_state.current_research_report = st.session_state.research_cache[active_cin]
        else:
            with st.spinner("Screening adverse media registries and dockets..."):
                rep = search_adverse_media(
                    company_name=company_nm,
                    cin=active_cin,
                    mock_mode=use_synthetic_demo
                )
                st.session_state.research_cache[active_cin] = rep
                st.session_state.current_research_report = rep
                record_event(
                    event_type="ADVERSE_MEDIA_SCAN_COMPLETED",
                    actor="RESEARCHER",
                    details={
                        "status": rep.status,
                        "findings_count": len(rep.findings),
                        "is_synthetic": rep.is_synthetic_demo,
                        "query": rep.query_used
                    }
                )
        st.session_state.score_result = None
        st.session_state.cam_markdown = None
        st.rerun()

    rep: Optional[AdverseMediaReport] = st.session_state.current_research_report
    if rep is not None:
        if rep.status == "RESEARCH_UNAVAILABLE":
            st.warning(
                f"⚠️ **RESEARCH_UNAVAILABLE:** Secondary screening could not complete ({rep.error_message or 'offline/timeout'}). "
                "System will NOT default to 'Clean'. Credit Officer must perform manual registry verification."
            )
        elif rep.status == "NO_ADVERSE_FINDINGS":
            st.success("✅ **CLEAN (No Adverse Findings):** Zero adverse media, statutory summons, or insolvency petitions detected.")
        elif rep.status == "COMPLETED":
            # Check for UNREVIEWED severe findings
            has_unreviewed_severe = any(
                f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.UNREVIEWED
                for f in rep.findings
            )
            if has_unreviewed_severe:
                st.error(
                    "🚨 **OFFICER REVIEW REQUIRED:** Admitted insolvency or severe finding detected! "
                    "Web search findings carry ZERO effect on the policy gate until explicitly confirmed by the Credit Officer."
                )

            for f in rep.findings:
                with st.container(border=True):
                    col_t1, col_t2 = st.columns([3, 1])
                    with col_t1:
                        tier_label = {
                            FindingTier.SEVERE: "🔴 Severe (Admitted Insolvency)",
                            FindingTier.REVIEW: "🟡 Review (Statutory Inquiry)",
                            FindingTier.INFORMATIONAL: "🔵 Informational (General Media)"
                        }.get(f.tier, str(f.tier))
                        st.markdown(f"**{f.title}** &nbsp; `{tier_label}`")
                        st.caption(f.snippet)
                        st.markdown(f"🔗 [Direct Source Registry]({f.source_url})")
                    with col_t2:
                        if f.review_status == ReviewStatus.UNREVIEWED:
                            st.warning("⏳ Status: UNREVIEWED")
                            if st.button("✅ Confirm", key=f"conf_{f.id}", help="Confirms finding as authentic. Forwards risk flag to policy gate."):
                                updated_rep = confirm_adverse_finding(rep, f.id, officer_name="Credit Officer")
                                st.session_state.research_cache[active_cin] = updated_rep
                                st.session_state.current_research_report = updated_rep
                                record_event("FINDING_CONFIRMED", "OFFICER", {"finding_id": f.id, "title": f.title, "tier": f.tier.value})
                                st.session_state.score_result = None
                                st.session_state.cam_markdown = None
                                st.rerun()
                            with st.popover("❌ Dismiss"):
                                just = st.text_input("Justification (min 10 chars)", key=f"just_{f.id}")
                                if st.button("Submit Dismissal", key=f"btn_dism_{f.id}"):
                                    if len(just.strip()) >= 10:
                                        updated_rep = dismiss_adverse_finding(rep, f.id, just.strip(), officer_name="Credit Officer")
                                        st.session_state.research_cache[active_cin] = updated_rep
                                        st.session_state.current_research_report = updated_rep
                                        record_event("FINDING_DISMISSED", "OFFICER", {"finding_id": f.id, "title": f.title, "justification": just.strip()})
                                        st.session_state.score_result = None
                                        st.session_state.cam_markdown = None
                                        st.rerun()
                                    else:
                                        st.error("Min 10 characters required.")
                        elif f.review_status == ReviewStatus.CONFIRMED:
                            st.error(f"🚨 Status: CONFIRMED\n\n*By: {f.reviewed_by}*")
                        elif f.review_status == ReviewStatus.DISMISSED:
                            st.success(f"🛡️ Status: DISMISSED\n\n*Reason: {f.dismissal_reason}*")

    st.markdown("---")

    if not st.session_state.hitl_confirmed or st.session_state.locked_case is None:
        st.info("⚠️ **Financials Not Locked:** Please review and click 'Confirm & Lock Financials' in Tab 2 before running the Risk Scorer.")
    else:
        current_hash = st.session_state.locked_financials_hash
        is_stale = (st.session_state.scored_financials_hash != current_hash)

        if is_stale or st.session_state.score_result is None:
            if is_stale and st.session_state.score_result is not None:
                st.warning("⚠️ **Financials Modified:** Locked financials have changed since last evaluation. Re-run scoring to update.")
            if st.button("⚖️ Run Deterministic Risk Engine & Policy Gate", type="primary"):
                sev_flags, rev_flags = extract_policy_flags(st.session_state.current_research_report)
                score_res = score_credit_case(
                    credit_case=st.session_state.locked_case,
                    business_vintage_years=cin_parsed_data.get("business_vintage_years", 5),
                    pan_entity_mismatch=pan_check.get("mismatch_detected", False),
                    cgtmse_covered=True,
                    severe_flags=sev_flags,
                    review_flags=rev_flags
                )
                st.session_state.score_result = score_res
                st.session_state.scored_financials_hash = current_hash
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
                        "dscr": str(score_res.dscr)
                    }
                )
                st.rerun()

        res: Optional[ScoreResult] = st.session_state.score_result
        if res is not None:
            # 1. Handle Verdict.INSUFFICIENT_DATA (Review Item 3: no radar, list missing fields, block CAM)
            if res.final_verdict == Verdict.INSUFFICIENT_DATA:
                st.error("🚨 **UNDERWRITING BLOCKED: INSUFFICIENT FINANCIAL DATA**")
                st.markdown("""
                The deterministic policy engine halted because one or more mandatory financial parameters are missing.
                Under banking regulations and policy covenants, credit ratios cannot be evaluated with missing parameters.
                """)
                st.markdown("#### Missing Required Parameters Checklist:")
                for r in res.policy_reasons:
                    st.markdown(f"- ❌ **{r}**")
                st.info("💡 **Action Required:** Return to **Tab 2 (HITL Verification)**, provide the required values, and re-confirm.")
            else:
                # 2. Key Ratio Metric Bar
                k1, k2, k3, k4, k5 = st.columns(5)
                k1.metric("Operating DSCR", f"{res.dscr:.2f}x", delta="Hurdle: 1.20x", delta_color="normal" if res.dscr >= Decimal("1.20") else "inverse")

                itr_l = st.session_state.locked_case.itr_data
                de_disp = "N/A"
                if itr_l and itr_l.net_worth and itr_l.net_worth > Decimal("0"):
                    de_disp = f"{(itr_l.total_debt / itr_l.net_worth):.2f}x"
                k2.metric("Leverage (D/E)", de_disp, delta="Benchmark: 1.0x", delta_color="normal" if de_disp != "N/A" and float(de_disp.replace("x","")) <= 1.0 else "off")

                gst_l = st.session_state.locked_case.gst_data
                bank_l = st.session_state.locked_case.bank_data
                gap_disp = "0.0%"
                if gst_l and bank_l:
                    max_t = max(gst_l.taxable_value or Decimal("0"), bank_l.total_credits or Decimal("0"))
                    if max_t > Decimal("0"):
                        gap_pct = (abs((gst_l.taxable_value or Decimal("0")) - (bank_l.total_credits or Decimal("0"))) / max_t * Decimal("100"))
                        gap_disp = f"{gap_pct:.1f}%"
                k3.metric("Accrual vs Cash Gap", gap_disp, delta="<= 15% Normal", delta_color="normal" if float(gap_disp.replace("%","")) <= 15 else "inverse")

                k4.metric("Tangible Net Worth", format_inr(itr_l.net_worth if itr_l else Decimal("0")))
                k5.metric("Composite Score", f"{res.composite_score:.1f} / 100", delta=res.baseline_verdict.name)

                # 3. Policy Gate Verdict Banner
                st.markdown("---")
                if res.final_verdict == Verdict.APPROVE:
                    st.success(f"🟢 **FINAL SYSTEM VERDICT: APPROVE** &nbsp;|&nbsp; Baseline: {res.baseline_verdict.name} &nbsp;|&nbsp; Composite: {res.composite_score:.1f}/100.0 (Prism risk thresholds fully satisfied)")
                elif res.final_verdict == Verdict.CONDITIONAL:
                    st.warning(f"🟡 **FINAL SYSTEM VERDICT: CONDITIONAL** &nbsp;|&nbsp; {res.cap_applied or 'Review Required'} &nbsp;|&nbsp; (One or more operational pillars constrained by Weakest-Link Floor)")
                else:
                    st.error(f"🔴 **FINAL SYSTEM VERDICT: REJECT** &nbsp;|&nbsp; {res.knockout_applied or 'Hard Knockout Violated'}")

                # 4. Radar Chart & Policy Deliberation Grid
                col_chart, col_reasons = st.columns([1, 1])

                with col_chart:
                    st.markdown("#### Five Cs Balance Radar")
                    categories = ["Character", "Capacity", "Capital", "Collateral", "Conditions"]
                    scores = [
                        float(res.five_cs.get("CHARACTER", Decimal("0"))),
                        float(res.five_cs.get("CAPACITY", Decimal("0"))),
                        float(res.five_cs.get("CAPITAL", Decimal("0"))),
                        float(res.five_cs.get("COLLATERAL", Decimal("0"))),
                        float(res.five_cs.get("CONDITIONS", Decimal("0"))),
                    ]
                    fig = go.Figure()
                    fig.add_trace(go.Scatterpolar(
                        r=scores + [scores[0]],
                        theta=categories + [categories[0]],
                        fill='toself',
                        name='Five Cs Score',
                        line=dict(color='#2563eb', width=2),
                        fillcolor='rgba(37, 99, 235, 0.2)'
                    ))
                    fig.add_trace(go.Scatterpolar(
                        r=[40, 40, 40, 40, 40, 40],
                        theta=categories + [categories[0]],
                        name='Weakest-Link Floor (40.0)',
                        line=dict(color='#dc2626', width=1.5, dash='dash')
                    ))
                    fig.update_layout(
                        polar=dict(
                            radialaxis=dict(visible=True, range=[0, 100])
                        ),
                        showlegend=True,
                        height=360,
                        margin=dict(l=30, r=30, t=20, b=20)
                    )
                    st.plotly_chart(fig, use_container_width=True)

                with col_reasons:
                    st.markdown("#### Policy Gate Findings & Deliberations")
                    for reason in res.policy_reasons:
                        st.markdown(f"• {reason}")
                    if res.cap_applied:
                        st.info(f"🛡️ **Weakest-Link Protection:** {res.cap_applied}. In regulated credit underwriting, a high average score cannot mask a severe operational failure.")
                    if res.knockout_applied:
                        st.error(f"🚨 **Knockout Enforced:** {res.knockout_applied}.")

                # 5. Auditable Deductions Trail
                st.markdown("#### Forensic Score Deduction Trail")
                if res.deductions:
                    deduct_records = [
                        {
                            "Rule ID": d.rule_id,
                            "Pillar": d.pillar,
                            "Deduction": f"-{d.points_deducted:.1f} pts",
                            "Observed Value": str(d.observed_value),
                            "Benchmark": str(d.threshold),
                            "Forensic Explanation": d.explanation
                        }
                        for d in res.deductions
                    ]
                    st.dataframe(pd.DataFrame(deduct_records), use_container_width=True)
                else:
                    st.success("Applicant achieved 100% score across all evaluated parameters with zero deductions.")

                # 6. Officer Field Notes & Executive Veto Section
                st.markdown("---")
                st.markdown("#### 📝 Credit Officer Field Observations & Executive Veto")

                with st.expander("➕ Append Field Visit Observation Note", expanded=False):
                    fn_text = st.text_area("Field Visit Observation / Qualitative Notes", placeholder="e.g. Visited plant in Okhla. Machinery in operational condition, factory working at 85% utilization.")
                    fn_pillar = st.selectbox("Affected C Pillar", ["CHARACTER", "CAPACITY", "CAPITAL", "COLLATERAL", "CONDITIONS", "GENERAL"])
                    fn_adj = st.slider("Officer Score Adjustment (pts)", min_value=-25, max_value=10, value=0, step=1, help="Adjustments are capped at -40 globally under bank policy")
                    if st.button("Save Field Note"):
                        if fn_text.strip():
                            note_entry = {
                                "affected_c": fn_pillar,
                                "note_text": fn_text.strip(),
                                "adjustment": fn_adj,
                                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                            }
                            st.session_state.officer_notes.append(note_entry)
                            st.session_state.cam_markdown = None
                            record_event(
                                event_type="OFFICER_NOTE_ADDED",
                                actor="OFFICER",
                                details=note_entry
                            )
                            st.success("Field observation appended and stamped in audit log.")
                            st.rerun()

                with st.expander("⚠️ Executive Veto (Credit Committee Override)", expanded=False):
                    st.caption("Under bank governance, credit committee officers may override the algorithmic verdict, but MUST provide written justification.")
                    veto_active = st.toggle("Enable Executive Veto", value=bool(st.session_state.executive_override and st.session_state.executive_override.get("override_active")))
                    if veto_active:
                        veto_verdict = st.selectbox(
                            "Override Verdict",
                            ["APPROVE", "CONDITIONAL", "REJECT"],
                            index=0 if res.final_verdict != Verdict.APPROVE else 1
                        )
                        veto_officer = st.text_input("Approving Officer / Authority", value="Senior Credit Officer - MSME Desk")
                        veto_justification = st.text_area(
                            "Mandatory Written Justification (Min 20 characters required)",
                            value=st.session_state.executive_override.get("justification_text", "") if st.session_state.executive_override else "",
                            placeholder="State exact credit committee rationale for overriding the system verdict..."
                        )
                        if st.button("Submit & Seal Executive Veto in Audit Trail"):
                            if len(veto_justification.strip()) < 20:
                                st.error("⚠️ Justification is too brief. You must provide at least 20 characters explaining the override rationale.")
                            else:
                                veto_data = {
                                    "override_active": True,
                                    "original_verdict": res.final_verdict.name,
                                    "override_verdict": veto_verdict,
                                    "officer_name": veto_officer.strip(),
                                    "justification_text": veto_justification.strip(),
                                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                                }
                                st.session_state.executive_override = veto_data
                                st.session_state.cam_markdown = None
                                record_event(
                                    event_type="EXECUTIVE_OVERRIDE_APPLIED",
                                    actor="OFFICER_VETO",
                                    details=veto_data,
                                    severity="WARNING"
                                )
                                st.success("Executive Veto sealed in Audit Trail! CAM will reflect override.")
                                st.rerun()

# ============================================================================
# TAB 4: EXECUTIVE CAM REPORT EXPORT
# ============================================================================

with tab_cam:
    st.markdown("### Credit Appraisal Memorandum (CAM) Export")
    st.caption("Standardized Banking Credit Committee Memorandum generated deterministically with full audit provenance.")

    if st.session_state.score_result is None or st.session_state.locked_case is None:
        st.info("⚠️ Please complete Financial Verification (Tab 2) and Scoring (Tab 3) before generating the CAM.")
    elif st.session_state.score_result.final_verdict == Verdict.INSUFFICIENT_DATA:
        st.error("🚨 **EXPORT BLOCKED: INSUFFICIENT FINANCIAL DATA**")
        st.markdown("Under regulatory credit compliance, a formal Credit Appraisal Memorandum cannot be exported while mandatory financial parameters are missing.")
        for r in st.session_state.score_result.policy_reasons:
            st.markdown(f"- ❌ {r}")
    else:
        if st.session_state.cam_markdown is None:
            try:
                st.session_state.cam_markdown = generate_cam_markdown(
                    credit_case=st.session_state.locked_case,
                    score_result=st.session_state.score_result,
                    cin_parsed_data=cin_parsed_data,
                    officer_notes=st.session_state.officer_notes,
                    executive_override=st.session_state.executive_override,
                    audit_events=st.session_state.audit_trail,
                    adverse_media_report=st.session_state.current_research_report,
                    app_id=st.session_state.app_id
                )
            except CamGenerationError as e:
                st.error(f"CAM Generation Failed: {e}")

        if st.session_state.cam_markdown:
            col_d1, col_d2 = st.columns([1, 1])
            with col_d1:
                st.download_button(
                    label="📥 Download Formal CAM (.md)",
                    data=st.session_state.cam_markdown,
                    file_name=f"CAM_{st.session_state.app_id}.md",
                    mime="text/markdown"
                )
            with col_d2:
                html_cam = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CAM - {st.session_state.app_id}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
h1 {{ color: #1e3a8a; border-bottom: 2px solid #2563eb; padding-bottom: 8px; }}
h2 {{ color: #0f766e; margin-top: 24px; border-bottom: 1px solid #cbd5e1; padding-bottom: 4px; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
th, td {{ border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left; }}
th {{ background-color: #f1f5f9; color: #0f172a; }}
code {{ background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 2px 4px; border-radius: 4px; }}
</style>
</head>
<body>
<pre style="white-space: pre-wrap; font-family: inherit;">{st.session_state.cam_markdown}</pre>
</body>
</html>"""
                st.download_button(
                    label="📥 Download Formal CAM (.html)",
                    data=html_cam,
                    file_name=f"CAM_{st.session_state.app_id}.html",
                    mime="text/html"
                )

            st.markdown("---")
            st.markdown("#### Live Memorandum Preview")
            with st.container(border=True):
                st.markdown(st.session_state.cam_markdown)

# ============================================================================
# TAB 5: LIVE AUDIT TRAIL & PROVENANCE
# ============================================================================

with tab_audit:
    st.markdown("### Forensic Audit Trail & Event Stream")
    st.caption("Immutable append-only JSONL event stream capturing every onboarding, AI extraction, and human correction.")

    events = st.session_state.audit_trail
    if not events:
        st.info("No audit events recorded for this session yet.")
    else:
        st.markdown(f"**Total Chronological Events:** `{len(events)}`")

        for evt in reversed(events):
            sev = evt.get("severity", "INFO")
            color = "#16a34a" if sev == "INFO" else "#d97706" if sev == "WARNING" else "#dc2626"
            with st.expander(f"{evt['timestamp'][:19]} | {evt['event_type']} (Actor: {evt['actor']})", expanded=False):
                st.markdown(f"**Severity:** <span style='color:{color}; font-weight:700;'>{sev}</span>", unsafe_allow_html=True)
                st.json(evt["details"])

        # Download raw JSONL button
        jsonl_content = "\n".join(json.dumps(e) for e in events)
        st.download_button(
            label="📥 Export Full Audit Log (.jsonl)",
            data=jsonl_content,
            file_name=f"audit_trail_{st.session_state.app_id}.jsonl",
            mime="application/jsonlines"
        )
