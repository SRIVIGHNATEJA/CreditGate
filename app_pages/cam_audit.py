import time
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any
from decimal import Decimal

IST = ZoneInfo("Asia/Kolkata")

import streamlit as st
import pandas as pd

from core.policy import Verdict
from core.audit_logger import verify_chain
import importlib
try:
    from core.cam_generator import (
        generate_cam_markdown,
        generate_cam_html,
        generate_cam_docx,
        generate_cam_pdf,
        CamGenerationError,
        resolve_legal_entity_name,
        format_display_timestamp,
        format_display_docket,
    )
except ImportError:
    import core.cam_generator
    importlib.reload(core.cam_generator)
    from core.cam_generator import (
        generate_cam_markdown,
        generate_cam_html,
        generate_cam_docx,
        generate_cam_pdf,
        CamGenerationError,
        resolve_legal_entity_name,
        format_display_timestamp,
        format_display_docket,
    )

try:
    from ui.state import get_app_context, get_default_quorum_governance, record_event
except ImportError:
    import ui.state
    importlib.reload(ui.state)
    from ui.state import get_app_context, get_default_quorum_governance, record_event
from ui.components import render_callout, status_pill
from core.formatting import format_inr

st.session_state.current_page = "CAM & Audit"
ctx = get_app_context()

cin_parsed_data = ctx["cin_parsed_data"]
pan_input = ctx["pan_input"]


# ============================================================================
# EXECUTIVE CAM REPORT EXPORT
# ============================================================================

st.markdown("### Credit Appraisal Memorandum (CAM) Export")
st.caption("Standardized Banking Credit Committee Memorandum generated deterministically with full audit provenance.")

if st.session_state.score_result is None or st.session_state.locked_case is None:
    render_callout("Please complete Financial Verification (Evidence) and Scoring (Decision) before generating the CAM.", title="Prerequisites Incomplete", variant="info")
    if st.button("← Go to Decision Scorecard", type="primary", key="btn_back_to_decision"):
        st.session_state.current_page = "Decision"
        st.switch_page("app_pages/decision.py")
elif st.session_state.score_result.final_verdict == Verdict.INSUFFICIENT_DATA:
    render_callout("Under regulatory credit compliance, a formal Credit Appraisal Memorandum cannot be exported while mandatory financial parameters are missing.", title="Export Blocked: Insufficient Data", variant="error")
    for r in st.session_state.score_result.policy_reasons:
        st.markdown(f"- :material/close: {r}")
else:
    if st.session_state.get("quorum_governance") is None:
        st.session_state.quorum_governance = get_default_quorum_governance()

    try:
        st.session_state.cam_markdown = generate_cam_markdown(
            credit_case=st.session_state.locked_case,
            score_result=st.session_state.score_result,
            cin_parsed_data=cin_parsed_data,
            officer_notes=st.session_state.officer_notes,
            executive_override=st.session_state.executive_override,
            audit_events=st.session_state.audit_trail,
            adverse_media_report=st.session_state.current_research_report,
            app_id=st.session_state.app_id,
            pan=pan_input,
            quorum_governance=st.session_state.quorum_governance,
        )
    except (CamGenerationError, Exception) as e:
        render_callout(f"CAM Generation Notice: {e}", title="Generation Error", variant="error")

    if st.session_state.cam_markdown:
        cin_or_pan = cin_parsed_data.get("cin") if cin_parsed_data else (st.session_state.locked_case.cin_or_pan if st.session_state.locked_case else "")
        comp_nm = resolve_legal_entity_name(st.session_state.locked_case.company_name, cin_or_pan) if st.session_state.locked_case else "Applicant"
        c_slug = "".join(c for c in comp_nm if c.isalnum() or c in ('_', '-'))[:20]

        # Committee Sanction Summary Command Deck
        c_res = st.session_state.score_result
        c_case = st.session_state.locked_case
        c_v = c_res.final_verdict.name if c_res else "PENDING"
        v_pill = status_pill(c_v, variant="approve" if c_v == "APPROVE" else ("reject" if c_v == "REJECT" else "conditional"))

        gov_badge = '<span style="background: #ECFDF5; color: #047857; border: 1px solid #A7F3D0; font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 4px; text-transform: uppercase;">OFFICIAL APPRAISAL DOSSIER</span>'

        p_val = float(c_case.requested_loan_amount if c_case and c_case.requested_loan_amount else 5000000)
        if p_val <= 2500000:
            dofp_label = "Branch Head (BCSC)"
        elif p_val <= 20000000:
            dofp_label = "DGM / Zonal Head (ZCC)"
        else:
            dofp_label = "Board CAC (Corporate)"

        cov_list = st.session_state.quorum_governance.get("cro_covenants", []) if st.session_state.quorum_governance else []
        cov_cnt = len(cov_list)
        dofp_pill = f'<span style="background: #EDE9FE; color: #5B21B6; border: 1px solid #DDD6FE; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 4px;">Authority: {dofp_label}</span>'
        cov_pill = f'<span style="background: #E0F2FE; color: #0369A1; border: 1px solid #BAE6FD; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 4px;">{cov_cnt} Covenants Active</span>'

        collat_val_cam = getattr(c_case, "collateral_value", None) or Decimal("0.00")
        cgtmse_cov_cam = bool(getattr(c_case, "cgtmse_covered", False))
        if collat_val_cam > Decimal("0"):
            p_amt_dec = c_case.requested_loan_amount if (c_case and c_case.requested_loan_amount) else Decimal("5000000")
            cov_pct_cam = (collat_val_cam / (p_amt_dec if p_amt_dec > 0 else Decimal("1")) * Decimal("100")).quantize(Decimal("0.1"))
            sec_str = f"{format_inr(collat_val_cam)} ({cov_pct_cam}% Cover)"
        else:
            sec_str = "Nil (Collateral-Free)"
        sec_badge_txt = "+ CGTMSE Sovereign Cover" if cgtmse_cov_cam else "Standard Commercial Security (Non-CGTMSE)"
        guar_pill = '<span style="background: #DCFCE7; color: #15803D; border: 1px solid #BBF7D0; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 4px;">CGTMSE Active</span>' if cgtmse_cov_cam else '<span style="background: #F1F5F9; color: #475569; border: 1px solid #CBD5E1; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 4px;">Standard Security</span>'

        summary_strip_html = f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; padding: 14px 18px; margin: 10px 0 16px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px;">
                <div>
                    <div style="font-size: 11px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.08em;">Credit Committee Sanction Summary</div>
                    <div style="font-size: 12px; color: #64748B; margin-top: 2px;">Application Ref: <code style="font-size: 12px; font-weight: 600; color: #0B1F3A;">{st.session_state.app_id}</code> &nbsp;•&nbsp; Entity: <strong>{comp_nm}</strong></div>
                </div>
                <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">{dofp_pill} {guar_pill} {cov_pill} {gov_badge} {v_pill}</div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px;">
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 14px;">
                    <div style="font-size: 10px; font-weight: 600; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">Facility Proposed</div>
                    <div style="font-size: 15px; font-weight: 800; color: #0B1F3A; margin-top: 3px;">{format_inr(c_case.requested_loan_amount if c_case and c_case.requested_loan_amount else 5000000)}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 14px;">
                    <div style="font-size: 10px; font-weight: 600; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">Tenor & Pricing</div>
                    <div style="font-size: 15px; font-weight: 800; color: #0B1F3A; margin-top: 3px;">{c_case.proposed_tenure_months if c_case and c_case.proposed_tenure_months else 36}M @ {c_case.proposed_rate if c_case and c_case.proposed_rate else 11.5:.2f}%</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 14px;">
                    <div style="font-size: 10px; font-weight: 600; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">Monthly Debt Service</div>
                    <div style="font-size: 15px; font-weight: 800; color: #0B1F3A; margin-top: 3px;">{format_inr(c_case.proposed_emi if c_case and c_case.proposed_emi else 164994)}</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 14px;">
                    <div style="font-size: 10px; font-weight: 600; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">Five-Cs Composite</div>
                    <div style="font-size: 15px; font-weight: 800; color: #0B1F3A; margin-top: 3px;">{c_res.composite_score:.1f} / 100.0</div>
                </div>
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 10px 14px;">
                    <div style="font-size: 10px; font-weight: 600; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em;">Primary Security & Guarantee</div>
                    <div style="font-size: 13.5px; font-weight: 800; color: #0B1F3A; margin-top: 3px;">{sec_str}</div>
                    <div style="font-size: 10px; color: {'#15803D' if cgtmse_cov_cam else '#64748B'}; font-weight: 600; margin-top: 1px;">{sec_badge_txt}</div>
                </div>
            </div>
        </div>"""
        st.markdown("\n".join(line.strip() for line in summary_strip_html.splitlines() if line.strip()), unsafe_allow_html=True)

        st.markdown(
            f"<div style='margin-bottom: 12px; font-size: 12px; color: #475569;'><strong>Regulatory Compliance:</strong> &nbsp; "
            f"{status_pill('PII Masked: Zero Unmasked PANs', variant='approve')} &nbsp;|&nbsp; "
            f"{status_pill('Cryptographic SHA-256 Provenance', variant='info')} &nbsp;|&nbsp; "
            f"{status_pill('Zero-LLM Deterministic Engine', variant='info')}</div>",
            unsafe_allow_html=True
        )

        # Word and PDF download buttons in balanced 2-column action row
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            docx_bytes = generate_cam_docx(
                credit_case=st.session_state.locked_case,
                score_result=st.session_state.score_result,
                cin_parsed_data=cin_parsed_data,
                officer_notes=st.session_state.officer_notes,
                executive_override=st.session_state.executive_override,
                audit_events=st.session_state.audit_trail,
                adverse_media_report=st.session_state.current_research_report,
                app_id=st.session_state.app_id,
                pan=pan_input,
                quorum_governance=st.session_state.quorum_governance,
            )
            st.download_button(
                label=":material/description: Download Formal CAM (.docx)",
                data=docx_bytes,
                file_name=f"CAM_{c_slug}_{st.session_state.app_id}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                help="Formatted Microsoft Word document with natural pagination, data tables, and signature blocks.",
                type="primary",
                width="stretch",
            )
        with col_d2:
            pdf_bytes = generate_cam_pdf(
                credit_case=st.session_state.locked_case,
                score_result=st.session_state.score_result,
                cin_parsed_data=cin_parsed_data,
                officer_notes=st.session_state.officer_notes,
                executive_override=st.session_state.executive_override,
                audit_events=st.session_state.audit_trail,
                adverse_media_report=st.session_state.current_research_report,
                app_id=st.session_state.app_id,
                pan=pan_input,
                quorum_governance=st.session_state.quorum_governance,
            )
            st.download_button(
                label=":material/picture_as_pdf: Download Committee Sign-off PDF",
                data=pdf_bytes,
                file_name=f"CAM_{c_slug}_{st.session_state.app_id}.pdf",
                mime="application/pdf",
                help="Printable ReportLab PDF with structured headers, styled tables, and committee sign-off boxes.",
                type="secondary",
                width="stretch",
            )

        st.markdown("---")
        st.markdown("#### Live Memorandum Preview")

        # Memo in a bordered container with centered readable width
        col_pad_l, col_cam, col_pad_r = st.columns([1, 10, 1])
        with col_cam:
            st.markdown("""<style>
            .st-key-cam_memo_box {
                background: #FFFFFF !important;
                border: 1px solid #CBD5E1 !important;
                border-radius: 10px !important;
                padding: 24px 32px !important;
                box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08) !important;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
            }
            .st-key-cam_memo_box div[data-testid="stHeadingWithActionElements"] {
                margin-top: 0 !important;
                margin-bottom: 0 !important;
                padding-top: 0 !important;
                padding-bottom: 0 !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] table,
            .st-key-cam_memo_box table {
                width: 100% !important;
                border-collapse: collapse !important;
                margin: 8px 0 12px 0 !important;
                font-size: 12px !important;
                background: #FFFFFF !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] th,
            .st-key-cam_memo_box th {
                background-color: #0B1F3A !important;
                color: #FFFFFF !important;
                font-weight: 700 !important;
                text-transform: uppercase !important;
                letter-spacing: 0.05em !important;
                padding: 8px 12px !important;
                border: 1px solid #1E3A8A !important;
                text-align: left !important;
                font-size: 11px !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] td,
            .st-key-cam_memo_box td {
                padding: 6.5px 12px !important;
                border: 1px solid #E2E8F0 !important;
                color: #1E293B !important;
                font-size: 12px !important;
                vertical-align: middle !important;
                background-color: #FFFFFF !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] tr:nth-child(even) td,
            .st-key-cam_memo_box tr:nth-child(even) td {
                background-color: #F8FAFC !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] h1,
            .st-key-cam_memo_box h1 {
                color: #0B1F3A !important;
                font-size: 20px !important;
                font-weight: 800 !important;
                border-bottom: 2px solid #2563EB !important;
                padding-bottom: 4px !important;
                margin-top: 14px !important;
                margin-bottom: 4px !important;
                letter-spacing: -0.01em !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] h2,
            .st-key-cam_memo_box h2 {
                color: #0F766E !important;
                font-size: 15px !important;
                font-weight: 700 !important;
                border-bottom: 1px solid #CBD5E1 !important;
                padding-bottom: 4px !important;
                margin-top: 16px !important;
                margin-bottom: 6px !important;
                letter-spacing: 0.02em !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] h3,
            .st-key-cam_memo_box h3 {
                color: #1E3A8A !important;
                font-size: 12.5px !important;
                font-weight: 700 !important;
                margin-top: 6px !important;
                margin-bottom: 6px !important;
                text-transform: uppercase !important;
                letter-spacing: 0.04em !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] h4,
            .st-key-cam_memo_box h4 {
                color: #1E3A8A !important;
                font-size: 12px !important;
                font-weight: 700 !important;
                margin-top: 8px !important;
                margin-bottom: 4px !important;
                letter-spacing: 0.02em !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] p,
            .st-key-cam_memo_box p {
                color: #1E293B !important;
                font-size: 12.5px !important;
                line-height: 1.5 !important;
                margin-top: 2px !important;
                margin-bottom: 6px !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] strong,
            .st-key-cam_memo_box strong {
                color: #0B1F3A !important;
                font-weight: 700 !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] ul,
            .st-key-cam_memo_box ul {
                margin-top: 3px !important;
                margin-bottom: 6px !important;
                padding-left: 18px !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] li,
            .st-key-cam_memo_box li {
                margin-bottom: 2px !important;
                color: #1E293B !important;
                font-size: 12px !important;
                line-height: 1.4 !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] blockquote,
            .st-key-cam_memo_box blockquote {
                background-color: #F1F5F9 !important;
                border-left: 4px solid #0B1F3A !important;
                padding: 8px 14px !important;
                margin: 8px 0 !important;
                color: #0F172A !important;
                font-style: normal !important;
                font-weight: 500 !important;
                font-size: 12px !important;
                border-radius: 0 6px 6px 0 !important;
                line-height: 1.5 !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] blockquote p,
            .st-key-cam_memo_box blockquote p {
                color: #0F172A !important;
                font-style: normal !important;
                font-weight: 500 !important;
                margin: 0 !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] blockquote strong,
            .st-key-cam_memo_box blockquote strong {
                color: #0B1F3A !important;
                font-weight: 700 !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] code,
            .st-key-cam_memo_box code {
                background-color: #F1F5F9 !important;
                color: #0F172A !important;
                border: 1px solid #CBD5E1 !important;
                padding: 1.5px 5px !important;
                border-radius: 4px !important;
                font-size: 11.5px !important;
                font-weight: 600 !important;
            }
            .st-key-cam_memo_box div[data-testid="stMarkdownContainer"] hr,
            .st-key-cam_memo_box hr {
                border: none !important;
                border-top: 1px solid #E2E8F0 !important;
                margin: 12px 0 !important;
            }
            </style>""", unsafe_allow_html=True)
            with st.container(key="cam_memo_box", border=True):
                is_draft_stage = bool(st.session_state.quorum_governance and st.session_state.quorum_governance.get("stage") != "SANCTIONED") if st.session_state.get("quorum_governance") else True
                effective_v = c_res.final_verdict.name if c_res else "APPROVE"
                if is_draft_stage:
                    badge_html = f'<div style="display: inline-block; background: rgba(59, 130, 246, 0.2); border: 1px solid rgba(59, 130, 246, 0.4); color: #93C5FD; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.05em;">RECOMMENDATION: {effective_v} (COMMITTEE DOCKET)</div>'
                else:
                    badge_html = f'<div style="display: inline-block; background: rgba(16, 185, 129, 0.25); border: 1px solid rgba(16, 185, 129, 0.45); color: #6EE7B7; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.05em;">SANCTION RESOLUTION: APPROVED (SEALED)</div>'

                header_html = f"""<div style="background: linear-gradient(135deg, #0B1F3A 0%, #1E3A8A 100%); border-radius: 6px; padding: 10px 16px; margin-bottom: 12px; color: #FFFFFF; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 4px rgba(0,0,0,0.06);">
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <div style="width: 32px; height: 32px; background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.22); border-radius: 6px; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#93C5FD" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M3 21h18M3 10h18M5 10v11M9 10v11M15 10v11M19 10v11M12 2L2 7h20L12 2z"/>
                            </svg>
                        </div>
                        <div>
                            <div style="font-size: 10px; font-weight: 700; color: #93C5FD; letter-spacing: 0.12em; text-transform: uppercase;">CreditGate Risk Assessment Division</div>
                            <div style="font-size: 14px; font-weight: 800; color: #FFFFFF; letter-spacing: -0.01em;">Institutional Credit Dossier & Sanction Audit Trail</div>
                        </div>
                    </div>
                    <div style="text-align: right; font-size: 11px;">
                        {badge_html}
                        <div style="color: #CBD5E1; font-size: 10px; margin-top: 3px;">Ref: <strong>{format_display_docket(st.session_state.app_id)}</strong> &nbsp;|&nbsp; Digital Underwriting Seal (SHA-256 Provenance Sealed)</div>
                    </div>
                </div>"""
                st.markdown("\n".join(line.strip() for line in header_html.splitlines() if line.strip()), unsafe_allow_html=True)
                st.markdown(st.session_state.cam_markdown, unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# 3. EXECUTIVE COMPLETION DOCK (NAVIGATION & SIGNED PDF EXPORT)
# ----------------------------------------------------------------------------
st.markdown("---")
if st.session_state.get("cam_markdown") and "pdf_bytes" in locals():
    col_nav1, col_dock_pdf, col_nav2 = st.columns([1, 1.4, 1])
    with col_nav1:
        if st.button("← Back to Decision Scorecard", type="secondary", key="btn_cam_back_decision", width="stretch"):
            st.session_state.current_page = "Decision"
            st.switch_page("app_pages/decision.py")
    with col_dock_pdf:
        st.download_button(
            label=":material/picture_as_pdf: Download Committee Sign-off PDF",
            data=pdf_bytes,
            file_name=f"CAM_{c_slug}_{st.session_state.app_id}.pdf",
            mime="application/pdf",
            help="Printable ReportLab PDF with structured headers, styled tables, and committee sign-off boxes.",
            type="primary",
            width="stretch",
            key="btn_bottom_dock_download_pdf",
        )
    with col_nav2:
        if st.button("Restart Underwriting Pipeline ↺", type="secondary", key="btn_cam_restart", width="stretch"):
            st.session_state.current_page = "Case"
            st.switch_page("app_pages/case.py")
else:
    nav_cam1, nav_cam2 = st.columns(2)
    with nav_cam1:
        if st.button("← Back to Decision Scorecard", type="secondary", key="btn_cam_back_decision", width="stretch"):
            st.session_state.current_page = "Decision"
            st.switch_page("app_pages/decision.py")
    with nav_cam2:
        if st.button("Restart Underwriting Pipeline ↺", type="secondary", key="btn_cam_restart", width="stretch"):
            st.session_state.current_page = "Case"
            st.switch_page("app_pages/case.py")
