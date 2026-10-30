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
from core.config import get_gemini_api_key
from core.parser import extract_all_documents, extract_entity_identifiers
from core.onboarding import generate_application_id
from core.formatting import format_inr
from ui.state import get_app_context, record_event, COHORT_COMPANY_NAMES, select_and_reset_cohort
from ui.components import render_kpi_card, render_callout, status_pill
from ui.upload_modal import show_custom_upload_dialog, sync_extracted_identifiers_to_state

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

# ----------------------------------------------------------------------------
# 1. INTAKE & ACTIVE EVIDENCE INTAKE COCKPIT
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

if "show_custom_upload_slots" not in st.session_state:
    st.session_state.show_custom_upload_slots = False

is_locked = bool(st.session_state.hitl_confirmed and st.session_state.locked_case is not None)
is_extracted = bool(st.session_state.extracted_case is not None)

chip_docs = status_pill("Documents ready", variant="approve" if has_all_docs else "neutral")
chip_ext = status_pill("Extracted", variant="approve" if is_extracted else "neutral")
chip_lock = status_pill("Locked", variant="approve" if is_locked else "neutral")

# A. Benchmark Scenario Selection Modal (@st.dialog)
active_company_name = (
    st.session_state.get("custom_company_name")
    if cohort_selection == "None (Manual Upload)" and st.session_state.get("custom_company_name")
    else ctx["company_name"]
)
collateral_val = ctx.get("collateral_val", 0.0)
loan_amt_float = float(loan_amount)
emi_float = float(proposed_emi)
tenure_int = int(tenure_months)
rate_float = float(interest_rate)
collat_float = float(collateral_val)

annual_debt_service = emi_float * 12.0
min_ebitda_required = annual_debt_service * 1.25
unsecured_exposure = max(0.0, loan_amt_float - collat_float)
cover_pct = (collat_float / loan_amt_float * 100.0) if loan_amt_float > 0 else 0.0

loan_lakhs = loan_amt_float / 100000.0
collat_lakhs = collat_float / 100000.0
unsec_lakhs = unsecured_exposure / 100000.0
annual_emi_lakhs = annual_debt_service / 100000.0
ebitda_lakhs = min_ebitda_required / 100000.0

b_kb = len(bank_bytes) / 1024.0 if bank_bytes else 0.0
g_kb = len(gst_bytes) / 1024.0 if gst_bytes else 0.0
it_kb = len(itr_bytes) / 1024.0 if itr_bytes else 0.0

bank_full_hash = hashlib.sha256(bank_bytes).hexdigest() if bank_bytes else "PENDING"
gst_full_hash = hashlib.sha256(gst_bytes).hexdigest() if gst_bytes else "PENDING"
itr_full_hash = hashlib.sha256(itr_bytes).hexdigest() if itr_bytes else "PENDING"
bank_hash = bank_full_hash[:10].upper() if bank_bytes else "PENDING"
gst_hash = gst_full_hash[:10].upper() if gst_bytes else "PENDING"
itr_hash = itr_full_hash[:10].upper() if itr_bytes else "PENDING"

if is_extracted:
    case = st.session_state.extracted_case
    meta = st.session_state.extraction_meta or {}

    bank_c = case.bank_data.total_credits if (case and case.bank_data and case.bank_data.total_credits is not None) else Decimal("0")
    bank_d = case.bank_data.total_debits if (case and case.bank_data and case.bank_data.total_debits is not None) else Decimal("0")
    net_cashflow = bank_c - bank_d
    bounces_cnt = case.bank_data.bounce_count if (case and case.bank_data and case.bank_data.bounce_count is not None) else 0

    gst_t = case.gst_data.taxable_value if (case and case.gst_data and case.gst_data.taxable_value is not None) else Decimal("0")
    gst_tax_paid = case.gst_data.total_tax_paid if (case and case.gst_data and case.gst_data.total_tax_paid is not None) else Decimal("0")

    itr_g = case.itr_data.gross_total_income if (case and case.itr_data and case.itr_data.gross_total_income is not None) else Decimal("0")
    itr_pat = case.itr_data.profit_after_tax if (case and case.itr_data and case.itr_data.profit_after_tax is not None) else Decimal("0")
    itr_dep = case.itr_data.depreciation if (case and case.itr_data and case.itr_data.depreciation is not None) else Decimal("0")
    itr_int = case.itr_data.interest_paid if (case and case.itr_data and case.itr_data.interest_paid is not None) else Decimal("0")
    itr_nw = case.itr_data.net_worth if (case and case.itr_data and case.itr_data.net_worth is not None) else Decimal("0")
    itr_debt = case.itr_data.total_debt if (case and case.itr_data and case.itr_data.total_debt is not None) else Decimal("0")
    itr_ex_emi = case.itr_data.existing_emi_obligations if (case and case.itr_data and case.itr_data.existing_emi_obligations is not None) else Decimal("0")

    recon_diff_num = float(meta.get("bank_reconciliation_difference") or 0.0)
    recon_diff_formatted = format_inr(Decimal(str(recon_diff_num)))
    recon_balanced = bool(meta.get("bank_reconciled", True))

    max_turnover = max(bank_c, gst_t) if (bank_c and gst_t) else Decimal("0")
    if max_turnover > Decimal("0"):
        gap_val = float(abs(gst_t - bank_c) / max_turnover * Decimal("100"))
        gap_str = f"{gap_val:.1f}%"
        vel_pct = float(bank_c / gst_t * Decimal("100")) if gst_t > Decimal("0") else 0.0
    else:
        gap_val = 0.0
        gap_str = "0.0%"
        vel_pct = 0.0

    ebitda = (itr_pat or Decimal("0")) + (itr_dep or Decimal("0")) + (itr_int or Decimal("0"))
    total_annual_debt = (itr_ex_emi or Decimal("0")) + Decimal(str(annual_debt_service))
    implied_dscr = float(ebitda / total_annual_debt) if total_annual_debt > Decimal("0") else 0.0

@st.dialog("Select Underwriting Benchmark Scenario", width="large")
def show_cohort_selector_dialog():
    st.markdown(
        "<div style='font-size: 11px; color: #475569; margin-top: -6px; margin-bottom: 10px;'>"
        "<span style='font-weight: 700; color: #0F766E; background: #F0FDFA; border: 1px solid #99F6E4; padding: 1.5px 7px; border-radius: 4px; margin-right: 6px;'>SUPERVISED BENCHMARK SCENARIOS</span>"
        "Select an institutional reference cohort to stage verified multi-file document streams for underwriting."
        "</div>",
        unsafe_allow_html=True,
    )
    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        st.markdown("#### Cohort 1: Prime MSME")
        st.caption("**Apex Industrial Holdings**\n0 mandate bounces, clean turnover, 1.82x DSCR.")
        is_c1 = "Apex" in active_company_name or "Prime" in active_company_name
        if st.button("● Active Scenario" if is_c1 else "Load Prime MSME", key="btn_load_c1", width="stretch", type="primary" if is_c1 else "secondary"):
            select_and_reset_cohort("Cohort 1: Prime MSME (Apex Industrial Benchmark)")
            st.rerun()
    with col_m2:
        st.markdown("#### Cohort 2: Distressed")
        st.caption("**Kaveri Logistics & Freight**\n5 mandate bounces, debt stress, thin liquidity.")
        is_c2 = "Kaveri" in active_company_name or "Distressed" in active_company_name
        if st.button("● Active Scenario" if is_c2 else "Load Distressed", key="btn_load_c2", width="stretch", type="primary" if is_c2 else "secondary"):
            select_and_reset_cohort("Cohort 2: Distressed (Kaveri Logistics Benchmark)")
            st.rerun()
    with col_m3:
        st.markdown("#### Cohort 3: Mismatch Trap")
        st.caption("**Bharat Structural Steels**\nGST vs Bank credit variance, circular trading flag.")
        is_c3 = "Bharat" in active_company_name or "Mismatch" in active_company_name
        if st.button("● Active Scenario" if is_c3 else "Load Mismatch Trap", key="btn_load_c3", width="stretch", type="primary" if is_c3 else "secondary"):
            select_and_reset_cohort("Cohort 3: Mismatch Trap (Bharat Structural Benchmark)")
            st.rerun()

# Custom Document Ingestion Dialog is imported from ui.upload_modal

# B. Consolidated 2-Column Header & Sanction Baseline
col_h_left, col_h_right = st.columns([11, 9], vertical_alignment="center")
with col_h_left:
    st.markdown(
        f"<div style='font-size: 1.32rem; font-weight: 800; color: #0B1F3A; letter-spacing: -0.015em; margin-bottom: 2px;'>"
        f"Evidence File: <span style='white-space: nowrap;'>{active_company_name}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
with col_h_right:
    c_btn1, c_btn2, c_btn3 = st.columns([1.1, 1.2, 1.2])
    with c_btn1:
        is_custom_active = not is_sample_active or bool(
            st.session_state.get("uploaded_bank_bytes")
            or st.session_state.get("uploaded_gst_bytes")
            or st.session_state.get("uploaded_itr_bytes")
        )
        btn_upload_label = "✓ Custom Active" if is_custom_active else "Custom Upload"
        if st.button(btn_upload_label, key="btn_toggle_custom_mode", width="stretch", help="Open Commercial Document Ingestion Room to upload custom PDF streams"):
            show_custom_upload_dialog()
    with c_btn2:
        if st.button("⇄ Switch Scenario", key="btn_open_cohort_modal", width="stretch", help="Select benchmark borrower scenario (Apex Prime, Kaveri Distressed, Bharat Mismatch)"):
            show_cohort_selector_dialog()
    with c_btn3:
        st.download_button(
            label="⬇ Benchmark Pack (.zip)",
            data=create_sample_zip(),
            file_name="CreditGate_Benchmark_Files.zip",
            mime="application/zip",
            key="btn_download_sample_pdfs",
            width="stretch",
            help="Download verified benchmark underwriting file package (PDF package) for local validation",
        )

# C. Active Evidence Summary Strip (Tier 1 Cards)
if is_sample_active or bank_bytes or gst_bytes or itr_bytes:
    pill_b = "● Ground Truth Asset" if is_sample_active else ("● Custom Upload" if bank_bytes else "● Pending Upload")
    pill_g = "● Ground Truth Asset" if is_sample_active else ("● Custom Upload" if gst_bytes else "● Pending Upload")
    pill_it = "● Ground Truth Asset" if is_sample_active else ("● Custom Upload" if itr_bytes else "● Pending Upload")

    col_d1, col_d2, col_d3 = st.columns(3)
    if not is_extracted:
        with col_d1:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #0284C7; border-radius: 8px; padding: 12px 14px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04); min-height: 128px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <strong style="font-size: 12.5px; color: #0B1F3A; letter-spacing: 0.01em;">BANK STATEMENT</strong>
                            <span style="font-size: 10px; font-weight: 700; color: #0369A1; background: #E0F2FE; padding: 2px 7px; border-radius: 4px;">{pill_b}</span>
                        </div>
                        <div style="font-size: 12px; font-weight: 600; color: #1E293B; font-family: monospace; margin-bottom: 3px;">📎 bank_statement.pdf <span style="font-size: 11px; font-weight: 400; color: #64748B;">({b_kb:.1f} KB)</span></div>
                        <div style="font-size: 11.5px; color: #475569; line-height: 1.4;">12-Month Operative CA/CC Cash Flow Ledger</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px; margin-top: 6px; display: flex; justify-content: space-between; font-size: 11px; color: #475569;">
                        <span title="SHA-256 Digest: {bank_full_hash}" style="color: #0F766E; font-weight: 600; cursor: help;">Tamper-Sealed Stream</span>
                        <span>Scope: <b>5 Ledger Fields</b></span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_d2:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #059669; border-radius: 8px; padding: 12px 14px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04); min-height: 128px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <strong style="font-size: 12.5px; color: #0F766E; letter-spacing: 0.01em;">GSTR-3B TAX RETURN</strong>
                            <span style="font-size: 10px; font-weight: 700; color: #047857; background: #D1FAE5; padding: 2px 7px; border-radius: 4px;">{pill_g}</span>
                        </div>
                        <div style="font-size: 12px; font-weight: 600; color: #1E293B; font-family: monospace; margin-bottom: 3px;">📎 gstr3b.pdf <span style="font-size: 11px; font-weight: 400; color: #64748B;">({g_kb:.1f} KB)</span></div>
                        <div style="font-size: 11.5px; color: #475569; line-height: 1.4;">Monthly Taxable Outward Supplies & Statutory ITC</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px; margin-top: 6px; display: flex; justify-content: space-between; font-size: 11px; color: #475569;">
                        <span title="SHA-256 Digest: {gst_full_hash}" style="color: #0F766E; font-weight: 600; cursor: help;">Tamper-Sealed Stream</span>
                        <span>Scope: <b>2 Tax Fields</b></span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_d3:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #6D28D9; border-radius: 8px; padding: 12px 14px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04); min-height: 128px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <strong style="font-size: 12.5px; color: #4338CA; letter-spacing: 0.01em;">ITR-V ACKNOWLEDGEMENT</strong>
                            <span style="font-size: 10px; font-weight: 700; color: #6D28D9; background: #EDE9FE; padding: 2px 7px; border-radius: 4px;">{pill_it}</span>
                        </div>
                        <div style="font-size: 12px; font-weight: 600; color: #1E293B; font-family: monospace; margin-bottom: 3px;">📎 itr_acknowledgement.pdf <span style="font-size: 11px; font-weight: 400; color: #64748B;">({it_kb:.1f} KB)</span></div>
                        <div style="font-size: 11.5px; color: #475569; line-height: 1.4;">Audited Balance Sheet, P&L & Net Worth Filing</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px; margin-top: 6px; display: flex; justify-content: space-between; font-size: 11px; color: #475569;">
                        <span title="SHA-256 Digest: {itr_full_hash}" style="color: #0F766E; font-weight: 600; cursor: help;">Tamper-Sealed Stream</span>
                        <span>Scope: <b>7 Solvency Fields</b></span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        recon_badge = (
            '<span style="font-size: 10px; font-weight: 700; color: #047857; background: #D1FAE5; padding: 2px 7px; border-radius: 4px;">● LEDGER BALANCED</span>'
            if recon_balanced else
            f'<span style="font-size: 10px; font-weight: 700; color: #B91C1C; background: #FEE2E2; padding: 2px 7px; border-radius: 4px;">⚠️ DISCREPANCY</span>'
        )
        bounces_metric = (
            '<span style="color: #047857; font-weight: 700;">0 Clean</span>'
            if bounces_cnt == 0 else
            f'<span style="color: #B91C1C; font-weight: 700;">{bounces_cnt} Risk</span>'
        )
        if gap_val <= 10.0:
            gst_badge = f'<span style="font-size: 10px; font-weight: 700; color: #047857; background: #D1FAE5; padding: 2px 7px; border-radius: 4px;">● {gap_str} GAP</span>'
        elif gap_val <= 20.0:
            gst_badge = f'<span style="font-size: 10px; font-weight: 700; color: #B45309; background: #FEF3C7; padding: 2px 7px; border-radius: 4px;">● {gap_str} GAP</span>'
        else:
            gst_badge = f'<span style="font-size: 10px; font-weight: 700; color: #B91C1C; background: #FEE2E2; padding: 2px 7px; border-radius: 4px;">⚠️ {gap_str} DIVERGENCE</span>'
        itr_badge = '<span style="font-size: 10px; font-weight: 700; color: #6D28D9; background: #EDE9FE; padding: 2px 7px; border-radius: 4px;">● AUDITED</span>'

        with col_d1:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #0284C7; border-radius: 8px; padding: 12px 14px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04); min-height: 128px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                            <strong style="font-size: 12.5px; color: #0B1F3A; letter-spacing: 0.01em;">OPERATIVE BANK LEDGER</strong>
                            {recon_badge}
                        </div>
                        <div style="font-size: 11px; color: #64748B; margin-top: 1px;">12M Inward Banking Credits</div>
                        <div style="font-size: 18px; font-weight: 800; color: #0B1F3A; margin: 2px 0 6px 0; font-feature-settings: 'tnum';">{format_inr(bank_c)}</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px; margin-top: 6px; display: flex; justify-content: space-between; font-size: 11px; color: #475569;">
                        <span><b>Net Flow:</b> {format_inr(net_cashflow)}</span>
                        <span><b>Bounces:</b> {bounces_metric}</span>
                        <span title="SHA-256 Digest: {bank_full_hash}" style="color: #0F766E; font-weight: 600; cursor: help;">Tamper-Sealed</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_d2:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #059669; border-radius: 8px; padding: 12px 14px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04); min-height: 128px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                            <strong style="font-size: 12.5px; color: #0F766E; letter-spacing: 0.01em;">GSTR-3B STATUTORY RETURN</strong>
                            {gst_badge}
                        </div>
                        <div style="font-size: 11px; color: #64748B; margin-top: 1px;">Declared Outward Taxable Sales</div>
                        <div style="font-size: 18px; font-weight: 800; color: #0B1F3A; margin: 2px 0 6px 0; font-feature-settings: 'tnum';">{format_inr(gst_t)}</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px; margin-top: 6px; display: flex; justify-content: space-between; font-size: 11px; color: #475569;">
                        <span><b>Tax Paid:</b> {format_inr(gst_tax_paid)}</span>
                        <span><b>Realization:</b> {vel_pct:.1f}%</span>
                        <span title="SHA-256 Digest: {gst_full_hash}" style="color: #0F766E; font-weight: 600; cursor: help;">Tamper-Sealed</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_d3:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid #6D28D9; border-radius: 8px; padding: 12px 14px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04); min-height: 128px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                            <strong style="font-size: 12.5px; color: #4338CA; letter-spacing: 0.01em;">ITR-V AUDITED FINANCIALS</strong>
                            {itr_badge}
                        </div>
                        <div style="font-size: 11px; color: #64748B; margin-top: 1px;">Audited Gross Revenue</div>
                        <div style="font-size: 18px; font-weight: 800; color: #0B1F3A; margin: 2px 0 6px 0; font-feature-settings: 'tnum';">{format_inr(itr_g)}</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 6px 10px; margin-top: 6px; display: flex; justify-content: space-between; font-size: 11px; color: #475569;">
                        <span><b>PAT:</b> {format_inr(itr_pat)}</span>
                        <span><b>Net Worth:</b> {format_inr(itr_nw)}</span>
                        <span title="SHA-256 Digest: {itr_full_hash}" style="color: #0F766E; font-weight: 600; cursor: help;">Tamper-Sealed</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

# Custom Document Ingestion is managed via show_custom_upload_dialog() modal

# ----------------------------------------------------------------------------
# 2. FULL-WIDTH INGESTION PIPELINE RIBBON & 3x2 / 4-PILLAR VERIFICATION GATES
# ----------------------------------------------------------------------------
if not has_all_docs:
    st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)
    card_col1, card_col2, card_col3 = st.columns(3)
    with card_col1:
        st.markdown(
            """<div style="border: 1.5px dashed #CBD5E1; border-radius: 8px; padding: 12px 14px; background: #F8FAFC; text-align: center; min-height: 128px; display: flex; flex-direction: column; justify-content: center; align-items: center;">
                <div style="font-size: 20px; margin-bottom: 4px;">📄</div>
                <div style="font-size: 12px; font-weight: 700; color: #0B1F3A; letter-spacing: 0.01em;">OPERATIVE BANK STATEMENT</div>
                <div style="font-size: 11px; color: #64748B; margin: 2px 0 6px 0;">12-Month Operative CA/CC Cash Flow Ledger</div>
                <span style="font-size: 9.5px; font-weight: 700; color: #64748B; background: #F1F5F9; border: 1px solid #E2E8F0; padding: 2px 8px; border-radius: 4px;">Awaiting Upload</span>
            </div>""",
            unsafe_allow_html=True,
        )
    with card_col2:
        st.markdown(
            """<div style="border: 1.5px dashed #CBD5E1; border-radius: 8px; padding: 12px 14px; background: #F8FAFC; text-align: center; min-height: 128px; display: flex; flex-direction: column; justify-content: center; align-items: center;">
                <div style="font-size: 20px; margin-bottom: 4px;">📑</div>
                <div style="font-size: 12px; font-weight: 700; color: #0B1F3A; letter-spacing: 0.01em;">GSTR-3B TAX RETURN</div>
                <div style="font-size: 11px; color: #64748B; margin: 2px 0 6px 0;">Monthly Taxable Outward Supplies & Statutory ITC</div>
                <span style="font-size: 9.5px; font-weight: 700; color: #64748B; background: #F1F5F9; border: 1px solid #E2E8F0; padding: 2px 8px; border-radius: 4px;">Awaiting Upload</span>
            </div>""",
            unsafe_allow_html=True,
        )
    with card_col3:
        st.markdown(
            """<div style="border: 1.5px dashed #CBD5E1; border-radius: 8px; padding: 12px 14px; background: #F8FAFC; text-align: center; min-height: 128px; display: flex; flex-direction: column; justify-content: center; align-items: center;">
                <div style="font-size: 20px; margin-bottom: 4px;">📑</div>
                <div style="font-size: 12px; font-weight: 700; color: #0B1F3A; letter-spacing: 0.01em;">ITR-V ACKNOWLEDGEMENT</div>
                <div style="font-size: 11px; color: #64748B; margin: 2px 0 6px 0;">Audited Balance Sheet, P&L & Net Worth Filing</div>
                <span style="font-size: 9.5px; font-weight: 700; color: #64748B; background: #F1F5F9; border: 1px solid #E2E8F0; padding: 2px 8px; border-radius: 4px;">Awaiting Upload</span>
            </div>""",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
    c_btn_l, c_btn_mid, c_btn_r = st.columns([1, 2, 1])
    with c_btn_mid:
        if st.button("Open Commercial Document Ingestion Room", key="btn_open_custom_modal_empty", type="primary", width="stretch"):
            show_custom_upload_dialog()
else:
    # Full-Width Horizontal Ingestion Pipeline Ribbon (Clean, Linear, Zero Duplicate Tables)
    st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        col_pipe_left, col_pipe_right = st.columns([3.2, 1.8], vertical_alignment="center")
        with col_pipe_left:
            st.markdown(
                '<div style="font-size: 0.95rem; font-weight: 700; color: #0F172A; margin-bottom: 2px;">'
                'Document Ingestion & Optical Parsing Pipeline</div>',
                unsafe_allow_html=True,
            )
            engine_choice = st.radio(
                "Engine Mode",
                options=["Deterministic Native (Institutional Offline)", "Multimodal AI (Gemini 3.5 Flash Lite)"],
                key="ev_engine_select",
                horizontal=True,
                label_visibility="collapsed",
            )
            use_gemini = "Gemini" in engine_choice
        with col_pipe_right:
            scan_btn = st.button(
                "Re-Run Extraction" if is_extracted else "Run Extraction Pipeline (3 Parallel Threads)",
                key="btn_run_extraction",
                type="secondary" if is_extracted else "primary",
                use_container_width=True,
            )

    # 3. Refined 3x2 Verification Scope Grid (Aligned vertically with the top 3 cards)
    if not is_extracted:
        col_r1_1, col_r1_2, col_r1_3 = st.columns(3)
        with col_r1_1:
            st.markdown(
                """
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03); min-height: 136px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.94rem; font-weight: 700; color: #0F172A;">Arithmetic Cash Reconciliation</span>
                            <span style="background: #E0F2FE; color: #0369A1; font-size: 0.76rem; font-weight: 700; padding: 3px 9px; border-radius: 4px;">± ₹ 1.00 Tolerance</span>
                        </div>
                        <div style="font-size: 0.83rem; color: #475569; margin: 8px 0 12px 0; line-height: 1.5;">Opening + Total Inflows − Debits = Closing Balance</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; display: flex; justify-content: space-between; font-size: 0.76rem; color: #64748B;">
                        <span>Source: <b>Bank Statement</b></span><span>Gate: #1 • Ledger Identity</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_r1_2:
            st.markdown(
                """
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03); min-height: 136px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.94rem; font-weight: 700; color: #0F172A;">Mandate Clearing Track Record</span>
                            <span style="background: #FEE2E2; color: #B91C1C; font-size: 0.76rem; font-weight: 700; padding: 3px 9px; border-radius: 4px;">&lt; 5 Bounces</span>
                        </div>
                        <div style="font-size: 0.83rem; color: #475569; margin: 8px 0 12px 0; line-height: 1.5;">12-month inward returns scanned; ≥ 5 trigger Hard Knockout</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; display: flex; justify-content: space-between; font-size: 0.76rem; color: #64748B;">
                        <span>Source: <b>Bank Statement</b></span><span>Gate: #2 • Clearing Discipline</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_r1_3:
            st.markdown(
                """
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03); min-height: 136px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.94rem; font-weight: 700; color: #0F172A;">Statutory Turnover Concordance</span>
                            <span style="background: #FEF3C7; color: #B45309; font-size: 0.76rem; font-weight: 700; padding: 3px 9px; border-radius: 4px;">≤ 15.0% Variance</span>
                        </div>
                        <div style="font-size: 0.83rem; color: #475569; margin: 8px 0 12px 0; line-height: 1.5;">GSTR-3B declared outward supplies vs banking credit inflows</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; display: flex; justify-content: space-between; font-size: 0.76rem; color: #64748B;">
                        <span>Source: <b>GSTR-3B + Bank</b></span><span>Gate: #3 • 3-Way Triangulation</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        col_r2_1, col_r2_2, col_r2_3 = st.columns(3)
        with col_r2_1:
            st.markdown(
                """
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03); min-height: 136px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.94rem; font-weight: 700; color: #0F172A;">Debt Service Coverage (DSCR)</span>
                            <span style="background: #DCFCE7; color: #15803D; font-size: 0.76rem; font-weight: 700; padding: 3px 9px; border-radius: 4px;">≥ 1.25x Hurdle</span>
                        </div>
                        <div style="font-size: 0.83rem; color: #475569; margin: 8px 0 12px 0; line-height: 1.5;">EBITDA / Proposed Annual EMI required for facility sanction</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; display: flex; justify-content: space-between; font-size: 0.76rem; color: #64748B;">
                        <span>Source: <b>ITR-V Financials</b></span><span>Gate: #4 • Repayment Capacity</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_r2_2:
            st.markdown(
                """
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03); min-height: 136px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.94rem; font-weight: 700; color: #0F172A;">Capital & Solvency Check</span>
                            <span style="background: #EDE9FE; color: #6D28D9; font-size: 0.76rem; font-weight: 700; padding: 3px 9px; border-radius: 4px;">≤ 3.00x D/E Cap</span>
                        </div>
                        <div style="font-size: 0.83rem; color: #475569; margin: 8px 0 12px 0; line-height: 1.5;">Positive Tangible Net Worth and leverage ceiling compliance</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; display: flex; justify-content: space-between; font-size: 0.76rem; color: #64748B;">
                        <span>Source: <b>ITR-V Balance Sheet</b></span><span>Gate: #5 • Capital Structure</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_r2_3:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03); min-height: 136px; display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.94rem; font-weight: 700; color: #0F172A;">Collateral Coverage Ratio</span>
                            <span style="background: #F1F5F9; color: #334155; font-size: 0.76rem; font-weight: 700; padding: 3px 9px; border-radius: 4px;">{cover_pct:.1f}% Covered</span>
                        </div>
                        <div style="font-size: 0.83rem; color: #475569; margin: 8px 0 12px 0; line-height: 1.5;">₹{collat_lakhs:.1f}L security value against ₹{loan_lakhs:.1f}L proposed facility</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; display: flex; justify-content: space-between; font-size: 0.76rem; color: #64748B;">
                        <span>Source: <b>Facility Terms</b></span><span>Gate: #6 • Security Cushion</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        total_kb = b_kb + g_kb + it_kb
        st.markdown(
            f"""
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 4px solid #0284C7; border-radius: 8px; padding: 12px 18px; margin-top: 4px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div style="font-size: 13px; color: #1E293B;">
                    <span style="font-size: 11px; font-weight: 700; color: #0369A1; text-transform: uppercase; letter-spacing: 0.05em;">Staged Ingestion Manifest:</span>
                    &nbsp;<strong>3 / 3 Document Streams Loaded</strong> ({total_kb:.1f} KB Total)
                    &nbsp;•&nbsp; <strong>14 Underwriting Parameters</strong> Targeted across 3 Parallel Threads
                    &nbsp;•&nbsp; <strong>Tamper-Sealed Provenance Chain</strong> Active
                </div>
                <div style="font-size: 11.5px; font-weight: 700; color: #047857; background: #D1FAE5; padding: 3.5px 10px; border-radius: 5px;">
                    ● READY FOR EXTRACTION
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        # --------------------------------------------------------------------
        # POST-EXTRACTION: 4-PILLAR STATUTORY GATE VERIFICATION CARDS
        # --------------------------------------------------------------------
        p1_pass = recon_balanced and (recon_diff_num < 1.0)
        p1_color = "#0284C7" if p1_pass else "#DC2626"
        p1_badge = '<span style="font-size: 10px; font-weight: 700; color: #047857; background: #DCFCE7; padding: 2px 7px; border-radius: 4px;">● PASS</span>' if p1_pass else f'<span style="font-size: 10px; font-weight: 700; color: #B91C1C; background: #FEE2E2; padding: 2px 7px; border-radius: 4px;">⚠️ BREACH</span>'
        p1_val = "₹ 0.00 Diff" if p1_pass else f"{recon_diff_formatted} Diff"

        p2_pass = (bounces_cnt == 0)
        p2_knockout = (bounces_cnt >= 5)
        if p2_pass:
            p2_color = "#059669"
            p2_badge = '<span style="font-size: 10px; font-weight: 700; color: #047857; background: #DCFCE7; padding: 2px 7px; border-radius: 4px;">● PASS</span>'
        elif p2_knockout:
            p2_color = "#DC2626"
            p2_badge = '<span style="font-size: 10px; font-weight: 700; color: #B91C1C; background: #FEE2E2; padding: 2px 7px; border-radius: 4px;">⛔ KNOCKOUT</span>'
        else:
            p2_color = "#D97706"
            p2_badge = '<span style="font-size: 10px; font-weight: 700; color: #B45309; background: #FEF3C7; padding: 2px 7px; border-radius: 4px;">⚠️ WARN</span>'

        p3_pass = (gap_val <= 15.0)
        if p3_pass:
            p3_color = "#059669"
            p3_badge = '<span style="font-size: 10px; font-weight: 700; color: #047857; background: #DCFCE7; padding: 2px 7px; border-radius: 4px;">● CONGRUENT</span>'
        elif gap_val <= 20.0:
            p3_color = "#D97706"
            p3_badge = '<span style="font-size: 10px; font-weight: 700; color: #B45309; background: #FEF3C7; padding: 2px 7px; border-radius: 4px;">⚠️ MODERATE</span>'
        else:
            p3_color = "#DC2626"
            p3_badge = '<span style="font-size: 10px; font-weight: 700; color: #B91C1C; background: #FEE2E2; padding: 2px 7px; border-radius: 4px;">⛔ DIVERGENT</span>'

        p4_pass = (implied_dscr >= 1.25)
        p4_color = "#059669" if p4_pass else "#DC2626"
        p4_badge = '<span style="font-size: 10px; font-weight: 700; color: #047857; background: #DCFCE7; padding: 2px 7px; border-radius: 4px;">● ADEQUATE</span>' if p4_pass else '<span style="font-size: 10px; font-weight: 700; color: #B91C1C; background: #FEE2E2; padding: 2px 7px; border-radius: 4px;">⚠️ STRESSED</span>'

        v_c1, v_c2, v_c3, v_c4 = st.columns(4)
        with v_c1:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid {p1_color}; border-radius: 8px; padding: 14px 16px; min-height: 104px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 0.72rem; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">1. Arithmetic Ledger</span>
                        {p1_badge}
                    </div>
                    <div style="font-size: 1.18rem; font-weight: 700; color: {'#0F172A' if p1_pass else '#DC2626'}; margin: 5px 0 2px 0;">{p1_val}</div>
                    <div style="font-size: 0.74rem; color: #64748B;">Opening + Inflows − Debits = Closing</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with v_c2:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid {p2_color}; border-radius: 8px; padding: 14px 16px; min-height: 104px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 0.72rem; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">2. Mandate Returns</span>
                        {p2_badge}
                    </div>
                    <div style="font-size: 1.18rem; font-weight: 700; color: {'#0F172A' if p2_pass else ('#DC2626' if p2_knockout else '#D97706')}; margin: 5px 0 2px 0;">{bounces_cnt} Bounces</div>
                    <div style="font-size: 0.74rem; color: #64748B;">Cap: &lt; 5 returns (RBI Hard Knockout)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with v_c3:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid {p3_color}; border-radius: 8px; padding: 14px 16px; min-height: 104px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 0.72rem; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">3. 3-Way Concordance</span>
                        {p3_badge}
                    </div>
                    <div style="font-size: 1.18rem; font-weight: 700; color: {'#0F172A' if p3_pass else ('#D97706' if gap_val <= 20.0 else '#DC2626')}; margin: 5px 0 2px 0;">
                        {gap_str} Gap <span style="font-size: 0.78rem; font-weight: 500; color: #64748B;">({vel_pct:.1f}% Realized)</span>
                    </div>
                    <div style="font-size: 0.74rem; color: #64748B;">Banking vs GSTR-3B Taxable Corridor</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with v_c4:
            st.markdown(
                f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-top: 3px solid {p4_color}; border-radius: 8px; padding: 14px 16px; min-height: 104px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 0.72rem; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">4. Implied DSCR Hurdle</span>
                        {p4_badge}
                    </div>
                    <div style="font-size: 1.18rem; font-weight: 700; color: {'#0F172A' if p4_pass else '#DC2626'}; margin: 5px 0 2px 0;">{implied_dscr:.2f}x DSCR</div>
                    <div style="font-size: 0.74rem; color: #64748B;">Policy Hurdle: ≥ 1.25x Debt Service</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("<div style='margin-bottom: 12px;'></div>", unsafe_allow_html=True)

    if scan_btn:
        with st.status("Extracting and verifying documents...", expanded=True) as status:
            t_start = time.perf_counter()
            try:
                active_gemini_key = get_gemini_api_key() if use_gemini else None
                if cohort_selection == "None (Manual Upload)":
                    ident = st.session_state.get("custom_extracted_identifiers")
                    if not ident:
                        ident = extract_entity_identifiers(bank_bytes, gst_bytes, itr_bytes)
                    sync_extracted_identifiers_to_state(ident)

                active_comp = (
                    st.session_state.get("custom_company_name") or "Manual Applicant Company"
                    if cohort_selection == "None (Manual Upload)"
                    else COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company")
                )
                active_cin_pan = (
                    (st.session_state.get("active_cin_selection") or st.session_state.get("active_pan_input") or "UNKNOWN")
                    if cohort_selection == "None (Manual Upload)"
                    else (cin_parsed_data.get("cin") if cin_parsed_data else (ctx.get("cin_input") or current_pan_val or "UNKNOWN"))
                )

                credit_case, meta = extract_all_documents(
                    bank_bytes=bank_bytes,
                    gst_bytes=gst_bytes,
                    itr_bytes=itr_bytes,
                    company_name=active_comp,
                    cin_or_pan=active_cin_pan,
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

                def determine_status(val: Any, conf: str, default_ok: str = "✓ Validated") -> str:
                    if val is None:
                        return "MISSING (Blank)"
                    if conf == "LOW":
                        return "⚠️ Low Confidence"
                    return default_ok

                hitl_records = [
                    {"Parameter": "Annual Gross Revenue", "AI_Extracted_Amount": to_float_or_none(it.gross_total_income), "Source_Document": "ITR / Audited P&L", "Page": "Page 1", "Confidence": it.confidence, "Status": determine_status(it.gross_total_income, it.confidence)},
                    {"Parameter": "Total Bank Inward Credits", "AI_Extracted_Amount": to_float_or_none(b.total_credits), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": determine_status(b.total_credits, b.confidence)},
                    {"Parameter": "Total Outward Debits", "AI_Extracted_Amount": to_float_or_none(b.total_debits), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": determine_status(b.total_debits, b.confidence)},
                    {"Parameter": "Bank Opening Balance", "AI_Extracted_Amount": to_float_or_none(b.opening_balance), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": determine_status(b.opening_balance, b.confidence)},
                    {"Parameter": "Bank Closing Balance", "AI_Extracted_Amount": to_float_or_none(b.closing_balance), "Source_Document": "Bank Statement", "Page": "Page 1", "Confidence": b.confidence, "Status": determine_status(b.closing_balance, b.confidence)},
                    {"Parameter": "Cheque / Mandate Bounces", "AI_Extracted_Amount": to_float_or_none(b.bounce_count), "Source_Document": "Bank Statement", "Page": "Page 1-2", "Confidence": b.confidence, "Status": "MISSING (Blank)" if b.bounce_count is None else ("⚠️ Low Confidence" if b.confidence == "LOW" else ("✓ Validated" if b.bounce_count == 0 else "⚠️ Check (Bounce Risk)"))},
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
                                rec["Status"] = "⚠️ Check (Accrual vs Cash Gap > 20%)"

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

    if st.session_state.editable_df is not None:
        meta = st.session_state.extraction_meta
        case = st.session_state.extracted_case

        # Elevate status strings in active session state
        if "Status" in st.session_state.editable_df.columns:
            st.session_state.editable_df["Status"] = st.session_state.editable_df["Status"].replace({
                "OK": "✓ Validated",
                "CHECK (Bounce Risk)": "⚠️ Check (Bounce Risk)",
                "CHECK (Accrual vs Cash Gap > 20%)": "⚠️ Check (Accrual vs Cash Gap > 20%)",
                "FLAG: LOW CONFIDENCE": "⚠️ Low Confidence",
            })

        # --------------------------------------------------------------------
        # 4. HITL VERIFICATION GRID IN BORDERED CONTAINER
        # --------------------------------------------------------------------
        if st.session_state.get("document_pan_mismatch"):
            intake_p, doc_p = st.session_state.document_pan_mismatch
            render_callout(
                f"⚠️ Entity Inconsistency Detected: Declared Intake PAN ({intake_p}) differs from Extracted Document PAN ({doc_p}). "
                f"Cross-document entity triangulation discrepancy recorded.",
                title="Cross-Document Entity Inconsistency Alert",
                variant="warning",
            )

        with st.container(border=True):
            st.markdown(
                """
                <div style="font-size: 1.05rem; font-weight: 700; color: #0F172A; margin-bottom: 2px;">Human-in-the-Loop Financial Verification Grid</div>
                <div style="font-size: 0.78rem; color: #64748B; margin-bottom: 12px;">Inspect AI-extracted parameters. Double-click any numeric cell to edit. All manual modifications are logged with officer attribution.</div>
                """,
                unsafe_allow_html=True,
            )

            has_low_conf = any(row.get("Confidence") == "LOW" for _, row in st.session_state.editable_df.iterrows())
            if has_low_conf:
                render_callout("Visual Flag: One or more financial parameters exhibit LOW AI extraction confidence. Carefully verify values before locking.", title="Low Confidence Flag", variant="warning")

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
                    "Parameter": st.column_config.TextColumn("Financial Line Item"),
                    "AI_Extracted_Amount": st.column_config.NumberColumn(
                        "Verified Value (₹ / Count)",
                        format="%.2f",
                        help="Values in INR (₹) except Cheque/Mandate Bounces (integer count)",
                        required=False,
                    ),
                    "Source_Document": st.column_config.TextColumn("Source Origin"),
                    "Page": st.column_config.TextColumn("Page"),
                    "Confidence": st.column_config.TextColumn("AI Confidence"),
                    "Status": st.column_config.TextColumn("System Validation Status"),
                },
                disabled=["Parameter", "Source_Document", "Page", "Confidence", "Status"],
                hide_index=True,
                height=min(495, (len(st.session_state.editable_df) + 1) * 35 + 10),
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

        # Live dynamic ledger reconciliation calculation from active edited table
        live_params = {
            edited_df.iloc[i]["Parameter"]: edited_df.iloc[i]["AI_Extracted_Amount"]
            for i in range(len(edited_df))
        }

        def _to_float(v):
            if v is None or pd.isna(v) or str(v).strip() == "":
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        live_open = _to_float(live_params.get("Bank Opening Balance"))
        live_inward = _to_float(live_params.get("Total Bank Inward Credits"))
        live_outward = _to_float(live_params.get("Total Outward Debits"))
        live_close = _to_float(live_params.get("Bank Closing Balance"))

        orig_recon_failed = not meta.get("bank_reconciled", True)
        recon_reason = ""

        if live_open is not None and live_inward is not None and live_outward is not None and live_close is not None:
            expected_close = live_open + live_inward - live_outward
            live_diff = abs(expected_close - live_close)
            recon_failed = (live_diff >= 1.0)
            diff_num = live_diff
        else:
            recon_failed = orig_recon_failed
            diff_num = float(meta.get("bank_reconciliation_difference") or 0.0)

        if recon_failed:
            recon_diff_f = format_inr(Decimal(str(diff_num)))
            render_callout(f"Opening + Inward - Outward != Closing (Discrepancy: {recon_diff_f}). An explicit officer justification is required to lock financials, or correct the line items above.", title="Ledger Equation Mismatch", variant="error")
            recon_reason = st.text_input("Mandatory Officer Reconciliation Exception Reason (Min 10 characters):", key="officer_recon_reason")
            st.caption(f"Reason Length: {len(recon_reason.strip())}/10 characters minimum")
        elif orig_recon_failed and not recon_failed:
            # Reconciled via human correction!
            render_callout(f"Ledger Equation Reconciled via Officer Corrections: Opening ({format_inr(Decimal(str(live_open)))}) + Inward ({format_inr(Decimal(str(live_inward)))}) - Outward ({format_inr(Decimal(str(live_outward)))}) = Closing ({format_inr(Decimal(str(live_close)))}). Math variance is ₹0.00.", title="✓ Ledger Math Balanced", variant="success")

        # --------------------------------------------------------------------
        # 5. BOTTOM COMMAND DOCK & ATTESTATION BAR
        # --------------------------------------------------------------------
        btn_lock_bottom = False
        btn_reset_bottom = False

        with st.container(border=True):
            if is_locked:
                col_status, col_reset, col_relock, col_proceed = st.columns(
                    [3.3, 1.1, 1.5, 2.1],
                    vertical_alignment="center",
                )
                with col_status:
                    st.markdown(
                        """
                        <div style="display: flex; align-items: center; gap: 8px; line-height: 1.25;">
                            <span style="display: inline-flex; align-items: center; gap: 4px; background: #ECFDF5; color: #047857; border: 1px solid #A7F3D0; padding: 3px 8px; border-radius: 6px; font-size: 0.76rem; font-weight: 700; letter-spacing: 0.03em; text-transform: uppercase;">
                                Sealed
                            </span>
                            <div style="display: flex; flex-direction: column;">
                                <span style="font-size: 0.82rem; font-weight: 600; color: #0F172A;">Ledger Cryptographically Locked</span>
                                <span style="font-size: 0.72rem; color: #64748B;">Audit hash active • Cleared for Stage 3 Five-Cs Risk Scoring</span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with col_reset:
                    btn_reset_bottom = st.button("↺ Reset Extraction", type="secondary", key="btn_reset_extraction", width="stretch")
                with col_relock:
                    btn_lock_bottom = st.button("Confirm & Re-Lock Financials", type="secondary", key="btn_confirm_lock", width="stretch")
                with col_proceed:
                    if st.button("Proceed to Decision Scorecard →", type="primary", key="btn_next_decision", width="stretch"):
                        st.session_state.decision_sub_stage = "scorecard"
                        st.session_state.current_page = "Decision"
                        st.switch_page("app_pages/decision.py")
            else:
                col_status, col_reset, col_lock = st.columns(
                    [3.6, 1.2, 2.2],
                    vertical_alignment="center",
                )
                with col_status:
                    st.markdown(
                        """
                        <div style="display: flex; align-items: center; gap: 8px; line-height: 1.25;">
                            <span style="display: inline-flex; align-items: center; gap: 4px; background: #F1F5F9; color: #475569; border: 1px solid #CBD5E1; padding: 3px 8px; border-radius: 6px; font-size: 0.76rem; font-weight: 700; letter-spacing: 0.03em; text-transform: uppercase;">
                                ● Verification
                            </span>
                            <div style="display: flex; flex-direction: column;">
                                <span style="font-size: 0.82rem; font-weight: 600; color: #0F172A;">Officer Review Pending</span>
                                <span style="font-size: 0.72rem; color: #64748B;">Verify line items above, then lock to authorize Five-Cs scoring</span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with col_reset:
                    btn_reset_bottom = st.button("↺ Reset Extraction", type="secondary", key="btn_reset_extraction", width="stretch")
                with col_lock:
                    btn_lock_bottom = st.button("Confirm & Lock Financials", type="primary", key="btn_confirm_lock", width="stretch")

        if btn_reset_bottom:
            st.session_state.extracted_case = None
            st.session_state.extraction_meta = None
            st.session_state.editable_df = None
            st.session_state.original_df = None
            st.session_state.locked_case = None
            st.session_state.locked_financials_hash = None
            st.session_state.scored_financials_hash = None
            st.session_state.scored_composite_hash = None
            st.session_state.score_result = None
            st.session_state.executive_override = None
            st.session_state.officer_notes = []
            st.session_state.cam_markdown = None
            st.session_state.current_research_report = None
            st.session_state.last_research_cache_key = None
            st.session_state.hitl_confirmed = False

            # If in manual upload mode, reset custom extracted identifiers so sidebar cleanly reverts to Awaiting Document Extraction
            if cohort_selection == "None (Manual Upload)":
                st.session_state.custom_company_name = None
                st.session_state.custom_extracted_identifiers = None
                st.session_state.active_cin_selection = "UNREGISTERED_MSME"
                st.session_state.case_cin_input = "UNREGISTERED_MSME"
                st.session_state.active_pan_input = ""
                st.session_state.case_pan_input = ""
                st.session_state.app_id = generate_application_id("Manual Applicant Company")

            record_event(
                event_type="EXTRACTION_RESET_BY_OFFICER",
                actor="OFFICER",
                details={"cohort": cohort_selection, "action": "RESET_EXTRACTION"},
                severity="INFO",
            )
            st.rerun()

        if btn_lock_bottom:
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

                    active_company_name = (
                        st.session_state.get("custom_company_name") or "Manual Applicant Company"
                        if cohort_selection == "None (Manual Upload)"
                        else COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company")
                    )
                    active_cin_or_pan = (
                        (st.session_state.get("active_cin_selection") or st.session_state.get("active_pan_input") or "UNKNOWN")
                        if cohort_selection == "None (Manual Upload)"
                        else (cin_parsed_data.get("cin") if cin_parsed_data else (ctx.get("cin_input") or current_pan_val or "UNKNOWN"))
                    )
                    active_pan_final = (
                        st.session_state.get("active_pan_input")
                        or st.session_state.get("custom_extracted_pan")
                        or current_pan_val
                        or ""
                    )

                    collat_val_dec = Decimal(str(ctx.get("collateral_val", "0.0")))
                    cgtmse_cov_bool = bool(ctx.get("cgtmse_covered", False))

                    locked_case = CreditCase(
                        company_name=active_company_name,
                        cin_or_pan=active_cin_or_pan,
                        pan=active_pan_final,
                        requested_loan_amount=Decimal(str(loan_amount)),
                        proposed_emi=Decimal(str(proposed_emi)),
                        proposed_rate=Decimal(str(interest_rate)),
                        proposed_tenure_months=int(tenure_months),
                        collateral_value=collat_val_dec,
                        cgtmse_covered=cgtmse_cov_bool,
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
                    st.toast("Financial parameters verified & cryptographically locked.", icon="🔒")
                    st.rerun()
