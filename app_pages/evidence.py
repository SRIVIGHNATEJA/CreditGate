import os
import io
import time
import json
import zipfile
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import streamlit as st
import pandas as pd

from core.config import MODEL_EXTRACTION
from core.data_models import (
    CreditCase,
    BankExtraction,
    GSTExtraction,
    ITRExtraction,
)
from core.parser import extract_all_documents
from core.formatting import format_inr
from ui.state import get_app_context, record_event, COHORT_COMPANY_NAMES, select_and_reset_cohort
from ui.components import render_kpi_card, render_callout, status_pill

st.session_state.current_page = "Evidence"
ctx = get_app_context()

cohort_selection = ctx["cohort_selection"]
bank_bytes = ctx["bank_bytes"]
gst_bytes = ctx["gst_bytes"]
itr_bytes = ctx["itr_bytes"]
cin_parsed_data = ctx["cin_parsed_data"]
current_pan_val = ctx["current_pan_val"]
loan_amount = ctx["loan_amount"]
tenure_months = ctx["tenure_months"]
interest_rate = ctx["interest_rate"]
proposed_emi = ctx["proposed_emi"]
has_all_docs = ctx["has_all_docs"]

st.markdown("### Document Ingestion & Verification")

is_locked = bool(st.session_state.hitl_confirmed and st.session_state.locked_case is not None)
is_extracted = bool(st.session_state.extracted_case is not None)

chip_docs = status_pill("Documents ready", variant="approve" if has_all_docs else "neutral")
chip_ext = status_pill("Extracted", variant="approve" if is_extracted else "neutral")
chip_lock = status_pill("Locked", variant="approve" if is_locked else "neutral")
st.markdown(f"**Status:** &nbsp; {chip_docs} &nbsp;|&nbsp; {chip_ext} &nbsp;|&nbsp; {chip_lock}", unsafe_allow_html=True)

if st.session_state.get("lock_cleared_message"):
    render_callout(st.session_state.lock_cleared_message, title="Lock Status Notice", variant="warning")

# ----------------------------------------------------------------------------
# 1. INTAKE: DRAG-AND-DROP OR SAMPLE CASE BADGE
# ----------------------------------------------------------------------------
@st.cache_data
def create_sample_zip() -> bytes:
    buf = io.BytesIO()
    base_syn = Path(__file__).resolve().parent.parent / "data" / "synthetic"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for cohort_folder in ["cohort_1_prime", "cohort_2_distressed", "cohort_3_mismatch"]:
            cdir = base_syn / cohort_folder
            if cdir.exists():
                for pdf_name in ["bank_statement.pdf", "gstr3b.pdf", "itr_acknowledgement.pdf"]:
                    pdf_path = cdir / pdf_name
                    if pdf_path.exists():
                        z.writestr(f"{cohort_folder}/{pdf_name}", pdf_path.read_bytes())
    return buf.getvalue()

up_b, up_g, up_it = None, None, None
is_sample_active = bool(cohort_selection and cohort_selection != "None (Manual Upload)")

if is_locked:
    render_callout(
        f"Financials locked: Bank, GST, ITR verified for {ctx['company_name']}",
        title="Financials Locked",
        variant="success",
    )
elif is_sample_active:
    render_callout(
        f"Ready to extract: Bank, GST, ITR for {ctx['company_name']}",
        title="Ready to Extract",
        variant="info",
    )

if is_sample_active:
    with st.expander(":material/swap_horiz: Re-upload or Switch Sample Documents", expanded=False):
        col_u1, col_u2, col_u3 = st.columns(3)
        with col_u1:
            up_b = st.file_uploader("1. Bank Statement (PDF)", type=["pdf"], key="ev_up_bank")
        with col_u2:
            up_g = st.file_uploader("2. GSTR-3B Summary (PDF)", type=["pdf"], key="ev_up_gst")
        with col_u3:
            up_it = st.file_uploader("3. ITR / Financials (PDF)", type=["pdf"], key="ev_up_itr")

        st.download_button(
            label=":material/download: Download Sample PDFs Package (.zip)",
            data=create_sample_zip(),
            file_name="CreditGate_Synthetic_Sample_PDFs.zip",
            mime="application/zip",
            key="btn_download_sample_pdfs",
            width="stretch",
        )

        st.markdown("##### Quick-Load Sample Cohorts")
        c_sc1, c_sc2, c_sc3 = st.columns(3)
        with c_sc1:
            with st.container(border=True):
                st.markdown("**Cohort 1: Prime MSME**")
                st.caption("Apex Synthetic Holdings (Demo)\n0 bounces, clean turnover")
                st.button(
                    "Load Cohort 1",
                    key="btn_load_c1",
                    on_click=select_and_reset_cohort,
                    args=("Cohort 1: Prime MSME (Apex Synthetic Demo)",),
                    width="stretch",
                )
        with c_sc2:
            with st.container(border=True):
                st.markdown("**Cohort 2: Distressed**")
                st.caption("Kaveri Fictitious Freight (Demo)\n5 mandate bounces, debt stress")
                st.button(
                    "Load Cohort 2",
                    key="btn_load_c2",
                    on_click=select_and_reset_cohort,
                    args=("Cohort 2: Distressed (Kaveri Fictitious Demo)",),
                    width="stretch",
                )
        with c_sc3:
            with st.container(border=True):
                st.markdown("**Cohort 3: Mismatch Trap**")
                st.caption("Bharat Mock Steels & Infra (Demo)\nTurnover/credit gap, thin PAT")
                st.button(
                    "Load Cohort 3",
                    key="btn_load_c3",
                    on_click=select_and_reset_cohort,
                    args=("Cohort 3: Mismatch Trap (Bharat Mock Demo)",),
                    width="stretch",
                )
else:
    st.markdown("#### :material/upload_file: Primary Intake: Document Upload")
    st.caption("Demo accepts the sample PDFs in data/synthetic/; other formats may return incomplete extraction.")
    st.download_button(
        label=":material/download: Download Sample PDFs Package (.zip)",
        data=create_sample_zip(),
        file_name="CreditGate_Synthetic_Sample_PDFs.zip",
        mime="application/zip",
        key="btn_download_sample_pdfs",
        width="stretch",
    )
    col_u1, col_u2, col_u3 = st.columns(3)
    with col_u1:
        up_b = st.file_uploader("1. Bank Statement (PDF)", type=["pdf"], key="ev_up_bank")
    with col_u2:
        up_g = st.file_uploader("2. GSTR-3B Summary (PDF)", type=["pdf"], key="ev_up_gst")
    with col_u3:
        up_it = st.file_uploader("3. ITR / Financials (PDF)", type=["pdf"], key="ev_up_itr")

    st.markdown("#### :material/bolt: Secondary Intake: Load Sample Case")
    c_sc1, c_sc2, c_sc3 = st.columns(3)
    with c_sc1:
        with st.container(border=True):
            st.markdown("**Cohort 1: Prime MSME**")
            st.caption("Apex Synthetic Holdings (Demo)\n0 bounces, clean turnover")
            st.button(
                "Load Cohort 1",
                key="btn_load_c1",
                on_click=select_and_reset_cohort,
                args=("Cohort 1: Prime MSME (Apex Synthetic Demo)",),
                width="stretch",
            )
    with c_sc2:
        with st.container(border=True):
            st.markdown("**Cohort 2: Distressed**")
            st.caption("Kaveri Fictitious Freight (Demo)\n5 mandate bounces, debt stress")
            st.button(
                "Load Cohort 2",
                key="btn_load_c2",
                on_click=select_and_reset_cohort,
                args=("Cohort 2: Distressed (Kaveri Fictitious Demo)",),
                width="stretch",
            )
    with c_sc3:
        with st.container(border=True):
            st.markdown("**Cohort 3: Mismatch Trap**")
            st.caption("Bharat Mock Steels & Infra (Demo)\nTurnover/credit gap, thin PAT")
            st.button(
                "Load Cohort 3",
                key="btn_load_c3",
                on_click=select_and_reset_cohort,
                args=("Cohort 3: Mismatch Trap (Bharat Mock Demo)",),
                width="stretch",
            )

# Hold uploaded files in memory only; never save to disk
files_changed = False
if up_b is not None:
    b_val = up_b.getvalue()
    if st.session_state.get("uploaded_bank_bytes") != b_val:
        st.session_state.uploaded_bank_bytes = b_val
        files_changed = True
if up_g is not None:
    g_val = up_g.getvalue()
    if st.session_state.get("uploaded_gst_bytes") != g_val:
        st.session_state.uploaded_gst_bytes = g_val
        files_changed = True
if up_it is not None:
    it_val = up_it.getvalue()
    if st.session_state.get("uploaded_itr_bytes") != it_val:
        st.session_state.uploaded_itr_bytes = it_val
        files_changed = True

if files_changed:
    select_and_reset_cohort("None (Manual Upload)")
    st.rerun()

st.markdown("---")

# ----------------------------------------------------------------------------
# 3. EXTRACTION TRIGGER & STATUS CARDS
# ----------------------------------------------------------------------------
if not has_all_docs:
    render_callout(
        "Mandatory Document Trinity incomplete. Please upload Bank Statement, GSTR-3B, and ITR Form above, or choose a Sample Case.",
        title="Documents Pending",
        variant="warning",
    )
else:
    if st.session_state.extracted_case is None:
        with st.container(border=True):
            col_e1, col_e2 = st.columns([2, 1])
            with col_e1:
                engine_choice = st.radio(
                    "Ingestion Engine Pipeline",
                    [
                        "Deterministic Native (PyMuPDF Offline)",
                        "Multimodal AI (Gemini 3.5 Flash Lite)"
                    ],
                    index=0,
                    horizontal=True,
                    help="Deterministic Native (PyMuPDF): Zero network latency, regex & tabular parser for standard PDF filings. Multimodal AI (Gemini 3.5 Flash Lite): Vision OCR for non-standard or scanned layouts.",
                    key="sel_extraction_engine",
                )
                use_gemini = "Gemini" in engine_choice
            with col_e2:
                key_configured = bool(os.getenv("GEMINI_API_KEY") and os.getenv("GEMINI_API_KEY") != "your_gemini_api_key_here")
                if use_gemini:
                    if key_configured:
                        st.markdown(f"Status: &nbsp; {status_pill('AI Connected', variant='info')}", unsafe_allow_html=True)
                        st.caption("Active model: `gemini-3.5-flash-lite`")
                    else:
                        st.markdown(f"Status: &nbsp; {status_pill('Offline Mode (Key Unset)', variant='conditional')}", unsafe_allow_html=True)
                        st.caption("Auto-engaging deterministic fallback")
                else:
                    st.markdown(f"Status: &nbsp; {status_pill('Deterministic Active', variant='info')}", unsafe_allow_html=True)
                    st.caption("Local OCR & Regex, zero network latency")

        if st.button(":material/bolt: Run Extraction (3 Parallel Threads)", type="primary", key="btn_run_extraction_main"):
            with st.status("Extracting and verifying documents...", expanded=True) as status:
                t_start = time.perf_counter()
                try:
                    active_gemini_key = os.getenv("GEMINI_API_KEY") if use_gemini else None
                    if active_gemini_key == "your_gemini_api_key_here":
                        active_gemini_key = None
                    credit_case, meta = extract_all_documents(
                        bank_bytes=bank_bytes,
                        gst_bytes=gst_bytes,
                        itr_bytes=itr_bytes,
                        company_name=COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company"),
                        cin_or_pan=(cin_parsed_data["cin"] if cin_parsed_data else (ctx.get("cin_input") or current_pan_val or "UNKNOWN")),
                        requested_loan_amount=Decimal(str(loan_amount)),
                        proposed_emi=Decimal(str(proposed_emi)),
                        proposed_rate=Decimal(str(interest_rate)),
                        proposed_tenure_months=int(tenure_months),
                        gemini_api_key=active_gemini_key if use_gemini else None,
                        force_deterministic=not use_gemini,
                    )
                    t_elapsed = time.perf_counter() - t_start

                    # Prepare initial HITL Dataframe
                    b = credit_case.bank_data
                    g = credit_case.gst_data
                    it = credit_case.itr_data

                    def to_float_or_none(val: Any) -> Optional[float]:
                        if val is None or pd.isna(val):
                            return None
                        try:
                            return float(val)
                        except (ValueError, TypeError):
                            return None

                    def determine_status(val: Any, conf: str, default_ok: str = "OK") -> str:
                        if val is None:
                            return "MISSING (Blank)"
                        if conf == "LOW":
                            return "FLAG: LOW CONFIDENCE"
                        return default_ok

                    hitl_records = [
                        {"Parameter": "Annual Gross Revenue", "AI_Extracted_Amount": to_float_or_none(it.gross_total_income), "Source_Document": "ITR / Audited P&L", "Page": "Page 1", "Confidence": it.confidence, "Status": determine_status(it.gross_total_income, it.confidence)},
                        {"Parameter": "Total Bank Inward Credits", "AI_Extracted_Amount": to_float_or_none(b.total_credits), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": determine_status(b.total_credits, b.confidence)},
                        {"Parameter": "Total Outward Debits", "AI_Extracted_Amount": to_float_or_none(b.total_debits), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": determine_status(b.total_debits, b.confidence)},
                        {"Parameter": "Bank Opening Balance", "AI_Extracted_Amount": to_float_or_none(b.opening_balance), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": determine_status(b.opening_balance, b.confidence)},
                        {"Parameter": "Bank Closing Balance", "AI_Extracted_Amount": to_float_or_none(b.closing_balance), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": determine_status(b.closing_balance, b.confidence)},
                        {"Parameter": "Cheque / Mandate Bounces", "AI_Extracted_Amount": to_float_or_none(b.bounce_count), "Source_Document": "Bank Statement", "Page": "Page 1-2", "Confidence": b.confidence, "Status": "MISSING (Blank)" if b.bounce_count is None else ("FLAG: LOW CONFIDENCE" if b.confidence == "LOW" else ("OK" if b.bounce_count == 0 else "CHECK (Bounce Risk)"))},
                        {"Parameter": "GST Taxable Turnover", "AI_Extracted_Amount": to_float_or_none(g.taxable_value), "Source_Document": "GSTR-3B Summary", "Page": "Page 1", "Confidence": g.confidence, "Status": determine_status(g.taxable_value, g.confidence)},
                        {"Parameter": "Total GST Paid", "AI_Extracted_Amount": to_float_or_none(g.total_tax_paid), "Source_Document": "GSTR-3B Summary", "Page": "Page 1", "Confidence": g.confidence, "Status": determine_status(g.total_tax_paid, g.confidence)},
                        {"Parameter": "Profit After Tax (PAT)", "AI_Extracted_Amount": to_float_or_none(it.profit_after_tax), "Source_Document": "ITR / P&L", "Page": "Page 1", "Confidence": it.confidence, "Status": determine_status(it.profit_after_tax, it.confidence)},
                        {"Parameter": "Depreciation Expense", "AI_Extracted_Amount": to_float_or_none(it.depreciation), "Source_Document": "ITR / P&L", "Page": "Page 1", "Confidence": it.confidence, "Status": determine_status(it.depreciation, it.confidence)},
                        {"Parameter": "Finance Costs / Interest", "AI_Extracted_Amount": to_float_or_none(it.interest_paid), "Source_Document": "ITR / P&L", "Page": "Page 1", "Confidence": it.confidence, "Status": determine_status(it.interest_paid, it.confidence)},
                        {"Parameter": "Tangible Net Worth", "AI_Extracted_Amount": to_float_or_none(it.net_worth), "Source_Document": "ITR / Balance Sheet", "Page": "Page 1", "Confidence": it.confidence, "Status": determine_status(it.net_worth, it.confidence)},
                        {"Parameter": "Existing Long-Term Debt", "AI_Extracted_Amount": to_float_or_none(it.total_debt), "Source_Document": "ITR / Balance Sheet", "Page": "Page 1", "Confidence": it.confidence, "Status": determine_status(it.total_debt, it.confidence)},
                        {"Parameter": "Current Annual Debt Service", "AI_Extracted_Amount": to_float_or_none(it.existing_emi_obligations), "Source_Document": "ITR / Financials", "Page": "Page 1", "Confidence": it.confidence, "Status": determine_status(it.existing_emi_obligations, it.confidence)},
                    ]

                    # Cross-Document Triangulation: GST vs Bank Inward discrepancy check
                    if g.taxable_value is not None and b.total_credits is not None:
                        gst_val = float(g.taxable_value)
                        bank_cr = float(b.total_credits)
                        if abs(gst_val - bank_cr) / max(gst_val, bank_cr, 1.0) > 0.20:
                            for rec in hitl_records:
                                if rec["Parameter"] in ["GST Taxable Turnover", "Total Bank Inward Credits"] and rec["AI_Extracted_Amount"] is not None:
                                    rec["Status"] = "CHECK (Accrual vs Cash Gap > 20%)"

                    df = pd.DataFrame(hitl_records)
                    st.session_state.editable_df = df
                    st.session_state.original_df = df.copy()
                    st.session_state.extracted_case = credit_case
                    st.session_state.extraction_meta = meta

                    extraction_engine = meta.get("extraction_engine", "DETERMINISTIC_FALLBACK")
                    total_fields = len(hitl_records)
                    fields_found = sum(1 for r in hitl_records if r.get("AI_Extracted_Amount") is not None)
                    ledger_status = "Balanced" if meta.get("bank_reconciled") else f"Discrepancy (₹{float(meta.get('bank_reconciliation_difference') or 0):,.2f})"
                    bounces_found = credit_case.bank_data.bounce_count if credit_case.bank_data and credit_case.bank_data.bounce_count is not None else 0

                    st.write(f"1. Engine: Extracted documents via `{extraction_engine}` ({t_elapsed:.2f}s).")
                    st.write(f"2. Fields & Ledger: Mapped {fields_found}/{total_fields} parameters; ledger {ledger_status}.")
                    st.write(f"3. Risk Indicators: Detected {bounces_found} cheque/mandate bounce(s).")
                    status.update(label="Extraction completed", state="complete", expanded=False)

                    record_event(
                        event_type="EXTRACTION_COMPLETED",
                        actor=extraction_engine,
                        details={
                            "latency_sec": round(t_elapsed, 3),
                            "model": meta.get("extraction_model", MODEL_EXTRACTION),
                            "extraction_engine": extraction_engine,
                            "bank_balanced": meta["bank_reconciled"],
                            "reconciliation_diff": meta["bank_reconciliation_difference"],
                            "fields_found": fields_found,
                            "bounces": bounces_found,
                        },
                    )
                    st.rerun()
                except Exception as e:
                    err_type = type(e).__name__
                    err_msg = str(e)
                    record_event(
                        event_type="EXTRACTION_ERROR",
                        actor="PARSER",
                        details={"error_type": err_type, "message": err_msg},
                        severity="ERROR",
                    )
                    render_callout(f"Error ({err_type}): {err_msg}", title="Extraction Error", variant="error")

    if st.session_state.extracted_case is not None and st.session_state.extraction_meta is not None:
        p_meta = st.session_state.extraction_meta
        p_case = st.session_state.extracted_case
        p_eng = p_meta.get("extraction_engine", "DETERMINISTIC_FALLBACK")
        p_recon = "Ledger Balanced" if p_meta.get("bank_reconciled") else f"Ledger Discrepancy (₹{float(p_meta.get('bank_reconciliation_difference') or 0):,.2f})"
        p_bounces = p_case.bank_data.bounce_count if p_case.bank_data and p_case.bank_data.bounce_count is not None else 0
        p_df = st.session_state.editable_df
        p_found = sum(1 for _, r in p_df.iterrows() if r.get("AI_Extracted_Amount") is not None) if p_df is not None else 14
        p_total = len(p_df) if p_df is not None else 14
        render_callout(
            f"Extraction Results: Extracted {p_found}/{p_total} fields via {p_eng}. {p_recon} | Cheque/mandate bounces: {p_bounces}.",
            title="Extraction Complete",
            variant="success" if p_meta.get("bank_reconciled") else "warning",
        )

    if st.session_state.editable_df is not None:
        meta = st.session_state.extraction_meta
        case = st.session_state.extracted_case

        # 3 Document Status Cards
        st.markdown("#### :material/description: Ingestion Status Cards")
        c_st1, c_st2, c_st3 = st.columns(3)
        with c_st1:
            with st.container(border=True):
                st.markdown("**Bank Statement** &nbsp; " + status_pill("2 Pages • 84 KB", variant="neutral"), unsafe_allow_html=True)
                b_eng = meta.get("extraction_engine", "DETERMINISTIC_FALLBACK")
                st.caption(f"Engine: `{b_eng}`")
                st.markdown(f"Confidence: **{case.bank_data.confidence}**")
                recon_diff_num = float(meta.get("bank_reconciliation_difference") or 0.0)
                recon_diff_formatted = format_inr(Decimal(str(recon_diff_num)))
                recon_txt = "BALANCED" if meta.get("bank_reconciled") else f"DISCREPANCY ({recon_diff_formatted})"
                recon_var = "success" if meta.get("bank_reconciled") else "error"
                render_callout(f"Reconciliation: {recon_txt}", title="Ledger Math", variant=recon_var)

        with c_st2:
            with st.container(border=True):
                st.markdown("**GSTR-3B Summary** &nbsp; " + status_pill("1 Page • 32 KB", variant="neutral"), unsafe_allow_html=True)
                g_eng = meta.get("extraction_engine", "DETERMINISTIC_FALLBACK")
                st.caption(f"Engine: `{g_eng}`")
                st.markdown(f"Confidence: **{case.gst_data.confidence}**")
                render_callout("Taxable value & tax paid extracted", title="GST Verification", variant="success" if case.gst_data.confidence == "HIGH" else "warning")

        with c_st3:
            with st.container(border=True):
                st.markdown("**ITR / Audited Financials** &nbsp; " + status_pill("1 Page • 48 KB", variant="neutral"), unsafe_allow_html=True)
                it_eng = meta.get("extraction_engine", "DETERMINISTIC_FALLBACK")
                st.caption(f"Engine: `{it_eng}`")
                st.markdown(f"Confidence: **{case.itr_data.confidence}**")
                render_callout("P&L, Net Worth & obligations mapped", title="Financial Statements", variant="success" if case.itr_data.confidence == "HIGH" else "warning")

        # Cross-Document Triangulation Horizon (3-Way Revenue & Cash Inflow Cross-Validation)
        bank_c = case.bank_data.total_credits if case.bank_data else None
        gst_t = case.gst_data.taxable_value if case.gst_data else None
        itr_g = case.itr_data.gross_total_income if case.itr_data else None

        gap_pill = status_pill("TRIANGULATION PENDING", variant="neutral")
        analysis_txt = "Awaiting extracted parameters across banking and statutory documents."
        border_accent = "#0F766E"
        velocity_subtext = ""

        if bank_c is not None and gst_t is not None:
            max_val = max(bank_c, gst_t)
            if max_val > Decimal("0"):
                gap_val = float(abs(gst_t - bank_c) / max_val * Decimal("100"))
                gap_str = f"{gap_val:.1f}%"
                vel_pct = float(bank_c / gst_t * Decimal("100")) if gst_t > Decimal("0") else 0.0
                velocity_subtext = f"Cash Realization Velocity: <strong>{vel_pct:.1f}%</strong> (Banking Receipts / GST Turnover)"
                if gap_val <= 10.0:
                    gap_pill = status_pill(f"CONGRUENT ({gap_str} VARIANCE)", variant="approve")
                    analysis_txt = f"Operating banking deposits ({format_inr(bank_c)}) strongly corroborate declared GSTR-3B taxable turnover ({format_inr(gst_t)}), staying within normal commercial variance (<= 10%)."
                    border_accent = "#15803D"
                elif gap_val <= 20.0:
                    gap_pill = status_pill(f"MODERATE GAP ({gap_str} VARIANCE)", variant="conditional")
                    analysis_txt = f"Moderate variance detected: Declared GST turnover ({format_inr(gst_t)}) diverges from actual banking receipts ({format_inr(bank_c)}) by {gap_str} (10–20% band). Underwriter inquiry recommended."
                    border_accent = "#B45309"
                else:
                    gap_pill = status_pill(f"SEVERE DIVERGENCE ({gap_str} VARIANCE)", variant="reject")
                    analysis_txt = f"Severe variance detected: Declared GST turnover ({format_inr(gst_t)}) diverges from actual banking receipts ({format_inr(bank_c)}) by {gap_str} (> 20%). Signals potential unbilled receipts, circular movement, or debtor stretch."
                    border_accent = "#B91C1C"

        vel_block = f'<div style="font-size: 11px; color: #0F766E; margin-top: 4px;">{velocity_subtext}</div>' if velocity_subtext else ''
        triangulation_html = f"""<div style="background-color: #FFFFFF; border: 1px solid #E2E8F0; border-left: 4px solid {border_accent}; border-radius: 8px; padding: 14px 18px; margin: 12px 0 16px 0;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;">
                <div>
                    <div style="font-size: 11px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.06em;">
                        Cross-Document Triangulation Horizon
                    </div>
                    <div style="font-size: 13px; font-weight: 700; color: #0B1F3A;">Three-Source Revenue & Cash Inflow Cross-Validation</div>
                    {vel_block}
                </div>
                <div>{gap_pill}</div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 10px;">
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 12px;">
                    <div style="font-size: 11px; font-weight: 600; color: #64748B; text-transform: uppercase;">1. Banking Credits (Cash Flow)</div>
                    <div style="font-size: 16px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{format_inr(bank_c)}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 12px;">
                    <div style="font-size: 11px; font-weight: 600; color: #64748B; text-transform: uppercase;">2. GSTR-3B Taxable (Declared Sales)</div>
                    <div style="font-size: 16px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{format_inr(gst_t)}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 12px;">
                    <div style="font-size: 11px; font-weight: 600; color: #64748B; text-transform: uppercase;">3. ITR Gross Income (Direct Tax)</div>
                    <div style="font-size: 16px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{format_inr(itr_g)}</div>
                </div>
            </div>
            <div style="font-size: 12px; color: #475569; line-height: 1.45; border-top: 1px solid #F1F5F9; padding-top: 8px;">
                <strong>Triangulation Assessment:</strong> {analysis_txt}
            </div>
        </div>"""
        st.markdown("\n".join(line.strip() for line in triangulation_html.splitlines() if line.strip()), unsafe_allow_html=True)

        # --------------------------------------------------------------------
        # 4. HITL VERIFICATION GRID IN BORDERED CONTAINER
        # --------------------------------------------------------------------
        with st.container(border=True):
            st.markdown("#### Human-in-the-Loop Financial Verification Grid")
            btn_lock_top = False
            if not is_locked:
                st.markdown("**Extracted - review below, then lock to continue**")
                btn_lock_top = st.button(":material/lock: Confirm & Lock Financials", type="primary", key="btn_confirm_lock_top", width="stretch")

            has_low_conf = any(row.get("Confidence") == "LOW" for _, row in st.session_state.editable_df.iterrows())
            if has_low_conf:
                render_callout("Visual Flag: One or more financial parameters exhibit LOW AI extraction confidence. Carefully verify values before locking.", title="Low Confidence Flag", variant="warning")

            render_callout("Inspect extracted values. Double-click any cell to adjust. All manual edits are logged with officer attribution.", title="Officer Review Guidelines", variant="info")

            def format_hitl_value(param_name: str, val: Any) -> str:
                if pd.isna(val) or val is None or str(val).strip() == "":
                    return "BLANK"
                try:
                    f_val = float(val)
                    if "Bounces" in param_name or "bounce" in param_name.lower():
                        return f"{int(f_val)}"
                    return f"₹ {f_val:,.2f}"
                except (ValueError, TypeError):
                    return str(val)

            edited_df = st.data_editor(
                st.session_state.editable_df,
                column_config={
                    "AI_Extracted_Amount": st.column_config.NumberColumn(
                        "Verified Value (₹ / Count)",
                        format="%.2f",
                        help="Values in INR (₹) except Cheque/Mandate Bounces (integer count)",
                        required=False,
                    ),
                    "Status": st.column_config.TextColumn("System Validation Status"),
                    "Confidence": st.column_config.TextColumn("AI Confidence"),
                    "Source_Document": st.column_config.TextColumn("Source Origin"),
                },
                disabled=["Parameter", "Source_Document", "Page", "Confidence", "Status"],
                width="stretch",
                num_rows="fixed",
                key="hitl_data_editor",
            )

        # Detect modifications and blanks
        orig_df = st.session_state.original_df
        changes = []
        blank_fields = []
        for idx in range(len(orig_df)):
            orig_val = orig_df.iloc[idx]["AI_Extracted_Amount"]
            new_val = edited_df.iloc[idx]["AI_Extracted_Amount"]
            param = orig_df.iloc[idx]["Parameter"]

            orig_is_empty = pd.isna(orig_val) or orig_val is None or str(orig_val).strip() == ""
            new_is_empty = pd.isna(new_val) or new_val is None or str(new_val).strip() == ""

            if new_is_empty:
                blank_fields.append(param)

            is_different = False
            if orig_is_empty != new_is_empty:
                is_different = True
            elif not orig_is_empty and not new_is_empty:
                try:
                    is_different = (float(orig_val) != float(new_val))
                except (ValueError, TypeError):
                    is_different = (str(orig_val).strip() != str(new_val).strip())

            if is_different:
                changes.append((param, orig_val, new_val))

        if changes:
            render_callout(f"{len(changes)} field(s) modified by officer. These corrections will be logged with officer attribution.", title="Officer Corrections Detected", variant="warning")
            for p, o, n in changes:
                o_str = format_hitl_value(p, o)
                n_str = format_hitl_value(p, n)
                diff_note = ""
                try:
                    delta_amt = float(n) - float(o)
                    d_sign = "+" if delta_amt >= 0 else ""
                    if "Bounces" not in p and "bounce" not in p.lower():
                        diff_note = f" ({d_sign}₹ {abs(delta_amt):,.2f})"
                    else:
                        diff_note = f" ({d_sign}{int(delta_amt)})"
                except (ValueError, TypeError):
                    pass
                st.write(f"• **{p}:** Changed from `{o_str}` to `{n_str}`{diff_note}")

        ack_blanks = False
        if blank_fields:
            render_callout(f"Missing/Blank Field(s) Detected ({len(blank_fields)}). Fill values or acknowledge blanks before confirming.", title="Missing Fields Alert", variant="warning")
            pills_html = " ".join(status_pill(f, variant="reject") for f in blank_fields)
            st.markdown(pills_html, unsafe_allow_html=True)
            st.caption("Under underwriting policy, all mandatory financial fields should be populated. You can fill them in above, or check below to proceed with missing data (routes assessment to **INSUFFICIENT_DATA**).")
            ack_blanks = st.checkbox("Explicitly acknowledge missing/blank field(s) (routes assessment to INSUFFICIENT_DATA)", key="ack_blank_fields")

        recon_failed = not meta.get("bank_reconciled", True)
        recon_reason = ""
        if recon_failed:
            diff_num = float(meta.get("bank_reconciliation_difference") or 0.0)
            recon_diff_f = format_inr(Decimal(str(diff_num)))
            render_callout(f"Opening + Inward - Outward != Closing (Discrepancy: {recon_diff_f}). An explicit officer justification is required to lock financials.", title="Ledger Equation Mismatch", variant="error")
            recon_reason = st.text_input("Mandatory Officer Reconciliation Exception Reason (Min 10 characters):", key="officer_recon_reason")
            st.caption(f"Reason Length: {len(recon_reason.strip())}/10 characters minimum")

        # Confirm & Lock and Reset in one balanced row
        b_col1, b_col2 = st.columns(2)
        with b_col1:
            lock_btn_label = ":material/lock_reset: Confirm & Re-Lock Financials" if is_locked else ":material/lock: Confirm & Lock Financials"
            lock_btn_type = "secondary" if is_locked else "primary"
            btn_lock_bottom = st.button(lock_btn_label, type=lock_btn_type, key="btn_confirm_lock", width="stretch")

        if btn_lock_top or btn_lock_bottom:
            can_lock = True
            if blank_fields and not ack_blanks:
                render_callout("Cannot lock financials: Missing/blank fields detected. Fill values or check 'Explicitly acknowledge missing/blank field(s)'.", title="Lock Precondition Failed", variant="error")
                can_lock = False
            if recon_failed and (not recon_reason or len(recon_reason.strip()) < 10):
                render_callout("Cannot lock financials: Ledger reconciliation failed. You must provide an explicit officer exception reason (at least 10 characters).", title="Lock Precondition Failed", variant="error")
                can_lock = False

            if can_lock:
                    if recon_failed:
                        record_event(
                            event_type="RECONCILIATION_EXCEPTION_LOGGED",
                            actor="OFFICER",
                            details={
                                "reason": recon_reason.strip(),
                                "reconciliation_diff": str(meta.get("bank_reconciliation_difference", 0.0)),
                            },
                            severity="WARNING",
                        )

                    for p, o, n in changes:
                        record_event(
                            event_type="HITL_FIELD_CORRECTED",
                            actor="OFFICER",
                            details={
                                "parameter": p,
                                "original_amount": str(o) if (not pd.isna(o) and o is not None) else "BLANK",
                                "corrected_amount": str(n) if (not pd.isna(n) and n is not None) else "BLANK",
                            },
                            severity="WARNING",
                        )

                    record_event(
                        event_type="HITL_CONFIRMED",
                        actor="OFFICER",
                        details={
                            "total_fields_reviewed": len(edited_df),
                            "modifications_made": len(changes),
                            "officer_verified": True,
                            "had_blank_fields": len(blank_fields) > 0,
                            "blank_fields": blank_fields,
                        },
                    )

                    params_map = {}
                    for idx in range(len(edited_df)):
                        p_name = edited_df.iloc[idx]["Parameter"]
                        val = edited_df.iloc[idx]["AI_Extracted_Amount"]
                        params_map[p_name] = val

                    def to_dec(val):
                        if pd.isna(val) or val is None or str(val).strip() == "":
                            return None
                        try:
                            return Decimal(str(val))
                        except (InvalidOperation, ValueError):
                            return None

                    orig_b = st.session_state.extracted_case.bank_data if st.session_state.extracted_case else None
                    orig_g = st.session_state.extracted_case.gst_data if st.session_state.extracted_case else None
                    orig_it = st.session_state.extracted_case.itr_data if st.session_state.extracted_case else None

                    bounce_raw = params_map.get("Cheque / Mandate Bounces")
                    bounce_val = None
                    if bounce_raw is not None and not pd.isna(bounce_raw) and str(bounce_raw).strip() != "":
                        try:
                            bounce_val = int(float(bounce_raw))
                        except (ValueError, TypeError):
                            bounce_val = None

                    b_data = BankExtraction(
                        total_credits=to_dec(params_map.get("Total Bank Inward Credits")),
                        total_debits=to_dec(params_map.get("Total Outward Debits")),
                        opening_balance=to_dec(params_map.get("Bank Opening Balance")),
                        closing_balance=to_dec(params_map.get("Bank Closing Balance")),
                        bounce_count=bounce_val,
                        confidence=orig_b.confidence if orig_b else "HIGH",
                        extraction_engine=orig_b.extraction_engine if orig_b else None,
                        officer_verified=True,
                    )

                    g_data = GSTExtraction(
                        taxable_value=to_dec(params_map.get("GST Taxable Turnover")),
                        total_tax_paid=to_dec(params_map.get("Total GST Paid")),
                        confidence=orig_g.confidence if orig_g else "HIGH",
                        extraction_engine=orig_g.extraction_engine if orig_g else None,
                        officer_verified=True,
                    )

                    itr_data = ITRExtraction(
                        gross_total_income=to_dec(params_map.get("Annual Gross Revenue")),
                        profit_after_tax=to_dec(params_map.get("Profit After Tax (PAT)")),
                        depreciation=to_dec(params_map.get("Depreciation Expense")),
                        interest_paid=to_dec(params_map.get("Finance Costs / Interest")),
                        net_worth=to_dec(params_map.get("Tangible Net Worth")),
                        total_debt=to_dec(params_map.get("Existing Long-Term Debt")),
                        existing_emi_obligations=to_dec(params_map.get("Current Annual Debt Service")),
                        confidence=orig_it.confidence if orig_it else "HIGH",
                        extraction_engine=orig_it.extraction_engine if orig_it else None,
                        officer_verified=True,
                    )

                    locked_case = CreditCase(
                        company_name=COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company"),
                        cin_or_pan=(cin_parsed_data["cin"] if cin_parsed_data else (ctx.get("cin_input") or current_pan_val or "UNKNOWN")),
                        pan=current_pan_val,
                        requested_loan_amount=Decimal(str(loan_amount)),
                        proposed_emi=Decimal(str(proposed_emi)),
                        proposed_rate=Decimal(str(interest_rate)),
                        proposed_tenure_months=int(tenure_months),
                        officer_verified=True,
                        bank_data=b_data,
                        gst_data=g_data,
                        itr_data=itr_data,
                    )

                    new_hash = hashlib.sha256(
                        json.dumps(locked_case.model_dump(), sort_keys=True, default=str).encode()
                    ).hexdigest()[:16]

                    if st.session_state.scored_financials_hash != new_hash:
                        st.session_state.score_result = None
                        st.session_state.cam_markdown = None

                    st.session_state.locked_case = locked_case
                    st.session_state.locked_financials_hash = new_hash
                    st.session_state.financials_locked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    st.session_state.hitl_confirmed = True
                    st.session_state.editable_df = edited_df
                    st.session_state.original_df = edited_df.copy()
                    st.session_state.lock_cleared_message = None
                    st.toast("Financial data locked! Ready for Five Cs Scoring.", icon="🔒")
                    st.rerun()

        with b_col2:
            if st.button(":material/restart_alt: Reset Extraction", type="secondary", key="btn_reset_extraction", width="stretch"):
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

    # Bidirectional Page Navigation (Proceed enabled ONLY when financials are locked)
    st.markdown("---")
    if st.session_state.hitl_confirmed and st.session_state.locked_case is not None:
        nav_c1, nav_c2 = st.columns(2)
        with nav_c1:
            if st.button("← Back to Case Profile", type="secondary", key="btn_back_case", width="stretch"):
                st.session_state.current_page = "Case"
                st.switch_page("app_pages/case.py")
        with nav_c2:
            if st.button("Proceed to Decision Scorecard →", type="primary", key="btn_next_decision", width="stretch"):
                st.session_state.current_page = "Decision"
                st.switch_page("app_pages/decision.py")
    else:
        render_callout(
            "Financials are not locked yet. Review the verified parameters in the grid above and click 'Confirm & Lock Financials' to unlock the Five Cs Decision Scorecard.",
            title="Next Step Locked",
            variant="info",
        )
        if st.button("← Back to Case Profile", type="secondary", key="btn_back_case", width="stretch"):
            st.session_state.current_page = "Case"
            st.switch_page("app_pages/case.py")
