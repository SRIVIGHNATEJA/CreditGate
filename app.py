import os
import re
import time
import json
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any, List

import streamlit as st
import pandas as pd

from core.config import MODEL_EXTRACTION, MODEL_RESEARCH
from core.data_models import (
    CreditCase,
    BankExtraction,
    GSTExtraction,
    ITRExtraction,
    coerce_currency,
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
# 2. SESSION STATE INITIALIZATION (RERUN GUARDS)
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

tab_onboard, tab_hitl, tab_audit = st.tabs([
    "📋 1. Onboarding & Entity Intelligence",
    "🤖 2. Multimodal Extraction & HITL Verification",
    "📜 3. Live Audit Trail & Provenance"
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
                st.session_state.hitl_confirmed = True
                st.session_state.editable_df = edited_df
                st.session_state.original_df = edited_df.copy()
                st.success("Financial data locked! Provenance stamped in Audit Trail.")
                st.toast("Ready for Phase 4: Five Cs Scoring & Policy Gate.")

        with c2:
            if st.button("🔄 Reset Extraction"):
                st.session_state.extracted_case = None
                st.session_state.editable_df = None
                st.session_state.original_df = None
                st.session_state.hitl_confirmed = False
                st.rerun()

# ============================================================================
# TAB 3: LIVE AUDIT TRAIL & PROVENANCE
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
