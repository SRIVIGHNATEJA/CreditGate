import os
import streamlit as st
from typing import Optional
from core.parser import extract_entity_identifiers
from ui.state import select_and_reset_cohort, generate_application_id


def sync_extracted_identifiers_to_state(ident: dict):
    """
    Synchronizes extracted entity identifiers (Legal Name, CIN, PAN, Entity Type)
    directly into active session state so manual uploads immediately update the
    application context, sidebar, and intake forms.
    """
    if not ident:
        return

    st.session_state.custom_extracted_identifiers = ident
    st.session_state.active_cohort_selection = "None (Manual Upload)"

    # 1. Company Name & Application ID
    comp_name = ident.get("company_name")
    if comp_name:
        st.session_state.custom_company_name = comp_name
        st.session_state.app_id = generate_application_id(comp_name)

    # 2. Corporate vs Unincorporated Entity Type
    is_corp = bool(ident.get("is_corporate", True))
    if ident.get("is_corporate") is not None:
        is_corp = bool(ident["is_corporate"])
    st.session_state.active_is_corporate = is_corp

    # 3. Corporate Identification Number (CIN)
    if ident.get("cin"):
        cin_str = str(ident["cin"]).strip().upper()
        st.session_state.active_cin_selection = cin_str
    elif not is_corp:
        st.session_state.active_cin_selection = "UNREGISTERED_MSME"

    # 4. Permanent Account Number (PAN)
    if ident.get("pan"):
        doc_p = str(ident["pan"]).strip().upper()
        st.session_state.custom_extracted_pan = doc_p
        declared_p = str(st.session_state.get("active_pan_input") or "").strip().upper()
        if declared_p and declared_p != doc_p and declared_p not in ("AAACA1234B", "AAAPA1234B", ""):
            st.session_state.document_pan_mismatch = (declared_p, doc_p)
        st.session_state.active_pan_input = doc_p

    if ident.get("gstin"):
        st.session_state.active_gstin_input = str(ident["gstin"]).strip().upper()

    # 5. Widget state synchronization (gracefully handles pre-instantiated widgets)
    cin_target = st.session_state.get("active_cin_selection", "UNREGISTERED_MSME" if not is_corp else "")
    pan_target = st.session_state.get("active_pan_input", "")
    pending_widgets = {
        "case_is_corporate": is_corp,
        "case_cin_input": cin_target,
        "case_pan_input": pan_target,
        "sb_cin_input": cin_target,
        "sb_pan_input": pan_target,
        "sb_cohort_selection": "None (Manual Upload)",
        "case_cohort_select": "None (Manual Upload)",
    }
    for k, v in pending_widgets.items():
        try:
            st.session_state[k] = v
        except Exception:
            pass
    st.session_state["_pending_widget_sync"] = pending_widgets

    # Invalidate cached context so sidebar and pages re-evaluate fresh
    st.session_state.app_context = None


@st.dialog("Commercial Document Ingestion Room", width="large")
def show_custom_upload_dialog():
    st.caption("Upload commercial borrower PDF documents (Bank Statement, GSTR-3B, and ITR-V / Financial Statements). Max 1MB per document (Digital Text PDFs).")

    has_b = bool(st.session_state.get("uploaded_bank_bytes"))
    has_g = bool(st.session_state.get("uploaded_gst_bytes"))
    has_it = bool(st.session_state.get("uploaded_itr_bytes"))

    col_u1, col_u2, col_u3 = st.columns(3)
    with col_u1:
        st.markdown("**1. Operative Bank Statement**")
        status_b = "● Custom PDF Loaded" if has_b else "○ Using Benchmark Ground Truth"
        color_b = "#047857" if has_b else "#64748B"
        st.markdown(f"<span style='font-size: 11px; font-weight: 600; color: {color_b};'>{status_b}</span>", unsafe_allow_html=True)
        up_b_dlg = st.file_uploader("Bank Statement (PDF)", type=["pdf"], key="ev_up_bank", label_visibility="collapsed", help="Operative CA/CC bank ledger in digital PDF format (max 1MB)")
    with col_u2:
        st.markdown("**2. Statutory GSTR-3B**")
        status_g = "● Custom PDF Loaded" if has_g else "○ Using Benchmark Ground Truth"
        color_g = "#047857" if has_g else "#64748B"
        st.markdown(f"<span style='font-size: 11px; font-weight: 600; color: {color_g};'>{status_g}</span>", unsafe_allow_html=True)
        up_g_dlg = st.file_uploader("GSTR-3B Summary (PDF)", type=["pdf"], key="ev_up_gst", label_visibility="collapsed", help="Monthly statutory GST return filing summary (max 1MB)")
    with col_u3:
        st.markdown("**3. Audited ITR / Financials**")
        status_it = "● Custom PDF Loaded" if has_it else "○ Using Benchmark Ground Truth"
        color_it = "#047857" if has_it else "#64748B"
        st.markdown(f"<span style='font-size: 11px; font-weight: 600; color: {color_it};'>{status_it}</span>", unsafe_allow_html=True)
        up_it_dlg = st.file_uploader("ITR / Financials (PDF)", type=["pdf"], key="ev_up_itr", label_visibility="collapsed", help="Income tax return acknowledgement / audited financials (max 1MB)")

    # Immediate capture of newly uploaded files
    files_changed = False
    if up_b_dlg is not None:
        b_val = up_b_dlg.getvalue()
        if st.session_state.get("uploaded_bank_bytes") != b_val:
            st.session_state.uploaded_bank_bytes = b_val
            files_changed = True
    if up_g_dlg is not None:
        g_val = up_g_dlg.getvalue()
        if st.session_state.get("uploaded_gst_bytes") != g_val:
            st.session_state.uploaded_gst_bytes = g_val
            files_changed = True
    if up_it_dlg is not None:
        it_val = up_it_dlg.getvalue()
        if st.session_state.get("uploaded_itr_bytes") != it_val:
            st.session_state.uploaded_itr_bytes = it_val
            files_changed = True

    if files_changed:
        st.session_state.show_custom_upload_slots = True
        try:
            ident = extract_entity_identifiers(
                bank_bytes=st.session_state.get("uploaded_bank_bytes"),
                gst_bytes=st.session_state.get("uploaded_gst_bytes"),
                itr_bytes=st.session_state.get("uploaded_itr_bytes"),
            )
            sync_extracted_identifiers_to_state(ident)
        except Exception:
            pass
        select_and_reset_cohort("None (Manual Upload)")

    st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)
    c_act1, c_act2 = st.columns([1.5, 1.2])
    with c_act1:
        if st.button("✓ Apply & Return to Intake", key="btn_apply_custom_upload", type="primary", use_container_width=True):
            try:
                ident = extract_entity_identifiers(
                    bank_bytes=st.session_state.get("uploaded_bank_bytes"),
                    gst_bytes=st.session_state.get("uploaded_gst_bytes"),
                    itr_bytes=st.session_state.get("uploaded_itr_bytes"),
                )
                sync_extracted_identifiers_to_state(ident)
            except Exception:
                pass
            select_and_reset_cohort("None (Manual Upload)")
            st.session_state.app_context = None
            st.rerun()
    with c_act2:
        if st.button("↺ Restore Benchmark Defaults", key="btn_restore_benchmark_upload", type="secondary", use_container_width=True):
            st.session_state.uploaded_bank_bytes = None
            st.session_state.uploaded_gst_bytes = None
            st.session_state.uploaded_itr_bytes = None
            st.session_state.show_custom_upload_slots = False
            select_and_reset_cohort("Cohort 1: Prime MSME (Apex Industrial Benchmark)")
            st.rerun()
