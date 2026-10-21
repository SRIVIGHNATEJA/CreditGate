import time
import json
from typing import Dict, Any

import streamlit as st
import pandas as pd

from core.policy import Verdict
from core.audit_logger import verify_chain
from core.cam_generator import (
    generate_cam_markdown,
    generate_cam_html,
    generate_cam_docx,
    generate_cam_pdf,
    CamGenerationError,
)
from ui.state import get_app_context
from ui.components import render_callout, status_pill
from core.formatting import format_inr

st.session_state.current_page = "CAM & Audit"
ctx = get_app_context()

cin_parsed_data = ctx["cin_parsed_data"]
pan_input = ctx["pan_input"]

tab_cam, tab_audit = st.tabs([
    ":material/description: Credit Appraisal Memorandum (CAM)",
    ":material/history_edu: Session Audit Trail",
])

# ============================================================================
# SUB-TAB 1: EXECUTIVE CAM REPORT EXPORT
# ============================================================================

with tab_cam:
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
                    app_id=st.session_state.app_id,
                    pan=pan_input,
                )
            except (CamGenerationError, Exception) as e:
                render_callout(f"CAM Generation Notice: {e}", title="Generation Error", variant="error")

        if st.session_state.cam_markdown:
            comp_nm = st.session_state.locked_case.company_name if st.session_state.locked_case else "Applicant"
            c_slug = "".join(c for c in comp_nm if c.isalnum() or c in ('_', '-'))[:20]

            st.markdown(
                f"<div style='margin-bottom: 10px; font-size: 12px; color: #475569;'><strong>Regulatory Compliance:</strong> &nbsp; "
                f"{status_pill('PII Masked: Zero Unmasked PANs', variant='approve')} &nbsp;|&nbsp; "
                f"{status_pill('Cryptographic SHA-256 Provenance', variant='info')}</div>",
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

            # Committee Sanction Summary Strip
            c_res = st.session_state.score_result
            c_case = st.session_state.locked_case
            c_v = c_res.final_verdict.name if c_res else "PENDING"
            v_pill = status_pill(c_v, variant="approve" if c_v == "APPROVE" else ("reject" if c_v == "REJECT" else "conditional"))

            summary_strip_html = f"""<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px 16px; margin: 10px 0 16px 0;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;">
                    <div style="font-size: 11px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.06em;">Credit Committee Sanction Summary</div>
                    <div>{v_pill}</div>
                </div>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px;">
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px;">
                        <div style="font-size: 10px; font-weight: 600; color: #64748B; text-transform: uppercase;">Facility Proposed</div>
                        <div style="font-size: 14px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{format_inr(c_case.requested_loan_amount if c_case and c_case.requested_loan_amount else 5000000)}</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px;">
                        <div style="font-size: 10px; font-weight: 600; color: #64748B; text-transform: uppercase;">Tenor & Pricing</div>
                        <div style="font-size: 14px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{c_case.proposed_tenure_months if c_case and c_case.proposed_tenure_months else 36}M @ {c_case.proposed_rate if c_case and c_case.proposed_rate else 11.5}%</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px;">
                        <div style="font-size: 10px; font-weight: 600; color: #64748B; text-transform: uppercase;">Monthly Debt Service</div>
                        <div style="font-size: 14px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{format_inr(c_case.proposed_emi if c_case and c_case.proposed_emi else 164994)}</div>
                    </div>
                    <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px;">
                        <div style="font-size: 10px; font-weight: 600; color: #64748B; text-transform: uppercase;">Five-Cs Composite</div>
                        <div style="font-size: 14px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{c_res.composite_score:.1f} / 100.0</div>
                    </div>
                </div>
            </div>"""
            st.markdown("\n".join(line.strip() for line in summary_strip_html.splitlines() if line.strip()), unsafe_allow_html=True)

            st.markdown("#### Live Memorandum Preview")

            # Memo in a bordered container with centered readable width
            col_pad_l, col_cam, col_pad_r = st.columns([1, 10, 1])
            with col_cam:
                with st.container(border=True):
                    header_html = f"""<div style="border-bottom: 2px solid #0F766E; padding-bottom: 12px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <div style="font-size: 11px; font-weight: 700; color: #0F766E; letter-spacing: 0.08em; text-transform: uppercase;">CreditGate Risk Assessment Division</div>
                            <div style="font-size: 18px; font-weight: 800; color: #0B1F3A; letter-spacing: -0.02em;">CREDIT APPRAISAL MEMORANDUM</div>
                        </div>
                        <div style="text-align: right; font-size: 11px; color: #64748B;">
                            <div><strong>Ref:</strong> {st.session_state.app_id}</div>
                            <div><strong>Classification:</strong> CREDIT COMMITTEE CONFIDENTIAL</div>
                        </div>
                    </div>"""
                    st.markdown("\n".join(line.strip() for line in header_html.splitlines() if line.strip()), unsafe_allow_html=True)
                    st.markdown(st.session_state.cam_markdown)

# ============================================================================
# SUB-TAB 2: LIVE AUDIT TRAIL & PROVENANCE
# ============================================================================

with tab_audit:
    st.markdown("### Session Audit Trail & Event Stream")
    st.caption("Append-only event stream capturing every onboarding, extraction, and human review decision.")

    events = st.session_state.audit_trail
    if not events:
        render_callout("No audit events recorded for this session yet.", title="Audit Trail Empty", variant="info")
    else:
        # SHA-256 Hash Chain Integrity Verification
        is_chain_valid, chain_err = verify_chain(events)
        col_vr1, col_vr2 = st.columns([3, 1])
        with col_vr1:
            if is_chain_valid:
                render_callout(
                    f"SHA-256 Hash Chain Verified: All {len(events)} session events linked with zero breaks.",
                    title="Tamper-Evidence Verified",
                    variant="success",
                )
            else:
                render_callout(
                    f"SHA-256 Hash Chain Broken: {chain_err}",
                    title="Integrity Alert",
                    variant="error",
                )
        with col_vr2:
            if st.button(":material/verified: Re-verify Chain", key="btn_reverify_chain", width="stretch"):
                t_v0 = time.perf_counter()
                v_ok, v_err = verify_chain(events)
                t_v_el = (time.perf_counter() - t_v0) * 1000
                if v_ok:
                    st.toast(f"Cryptographic chain intact ({len(events)} blocks in {t_v_el:.1f}ms).", icon="🔒")
                else:
                    st.toast(f"Verification failed: {v_err}", icon="🚨")

        # Dual filters by Severity and Actor
        col_f1, col_f2, col_f3 = st.columns([1, 1, 1])
        with col_f1:
            selected_sev = st.selectbox(
                "Filter by Severity",
                ["ALL", "INFO", "WARNING", "ERROR"],
                index=0,
                key="audit_sev_filter",
            )
        with col_f2:
            actor_options = ["ALL", "OFFICER", "SYSTEM_POLICY", "PARSER", "RESEARCHER", "DETERMINISTIC_ENGINE", "OFFICER_VETO"]
            selected_actor = st.selectbox(
                "Filter by Actor",
                actor_options,
                index=0,
                key="audit_actor_filter",
            )
        with col_f3:
            st.markdown(f"**Total Events:** `{len(events)}`")
            st.markdown(f"**Chain Head:** `{events[-1].get('event_hash', 'N/A')[:8]}...`")

        filtered_events = [
            e for e in events
            if (selected_sev == "ALL" or e.get("severity") == selected_sev)
            and (selected_actor == "ALL" or e.get("actor") == selected_actor)
        ]

        def summarize_event(evt: Dict[str, Any]) -> str:
            etype = evt.get("event_type", "")
            d = evt.get("details", {})
            if etype == "EXTRACTION_COMPLETED":
                return f"3 docs parsed in {d.get('latency_sec')}s via {d.get('extraction_engine')}"
            elif etype == "HITL_CONFIRMED":
                return f"Locked {d.get('total_fields_reviewed')} fields ({d.get('modifications_made')} manual edits)"
            elif etype == "HITL_FIELD_CORRECTED":
                return f"Field '{d.get('parameter')}': {d.get('original_amount')} -> {d.get('corrected_amount')}"
            elif etype == "RECONCILIATION_EXCEPTION_LOGGED":
                return f"Ledger exception justification: {d.get('reason')}"
            elif etype == "SCORING_EVALUATED":
                return f"Score {d.get('composite_score')}/100 -> Verdict: {d.get('final_verdict')}"
            elif etype == "ADVERSE_MEDIA_SCAN_COMPLETED":
                return f"Adverse screening: {d.get('findings_count')} findings (Status: {d.get('status')})"
            elif etype == "FINDING_CONFIRMED":
                return f"Confirmed finding: {d.get('title', '')[:35]}..."
            elif etype == "FINDING_DISMISSED":
                return f"Dismissed finding: {d.get('title', '')[:35]}..."
            elif etype == "OFFICER_NOTE_ADDED":
                return f"Field visit note on {d.get('affected_c')}"
            elif etype == "EXECUTIVE_OVERRIDE_APPLIED":
                return f"Override {d.get('original_verdict')} -> {d.get('override_verdict')} by {d.get('officer_name')}"
            elif etype == "EXECUTIVE_OVERRIDE_REMOVED":
                return "Executive veto disabled by officer"
            elif etype == "ONBOARDING_VERIFIED":
                return f"CIN {d.get('cin')} onboarding verified"
            return ", ".join(f"{k}={v}" for k, v in list(d.items())[:3])

        # Render Audit Table
        if not filtered_events:
            render_callout(f"No audit events found matching filters.", title="No Matching Events", variant="info")
        else:
            audit_table_rows = [
                {
                    "Timestamp (UTC)": (e.get("timestamp") or "")[:19],
                    "Event Type": e.get("event_type", ""),
                    "Actor": e.get("actor", ""),
                    "Severity": e.get("severity", "INFO"),
                    "Summary": summarize_event(e),
                }
                for e in reversed(filtered_events)
            ]
            st.dataframe(pd.DataFrame(audit_table_rows), width="stretch", hide_index=True)

            st.markdown("#### Event Details (Structured Key-Value Inspection)")
            st.caption("Click any event to inspect full properties rendered as a structured key-value table (no raw JSON).")

            for idx, evt in enumerate(reversed(filtered_events)):
                sev = evt.get("severity", "INFO")
                color = "#15803D" if sev == "INFO" else "#B45309" if sev == "WARNING" else "#B91C1C"
                evt_ts = (evt.get("timestamp") or "")[:19]
                evt_h = (evt.get("event_hash") or "N/A")[:16]
                prev_h = (evt.get("prev_hash") or "N/A")[:16]
                is_genesis = (evt.get("prev_hash") == "0" * 64)
                genesis_badge = " [GENESIS BLOCK]" if is_genesis else ""
                exp_title = f"{evt_ts} | {evt.get('event_type', '')} ({evt.get('actor', '')}){genesis_badge}"
                with st.expander(exp_title, expanded=False, key=f"exp_audit_{idx}_{evt_h[:8]}"):
                    if is_genesis:
                        st.markdown(f"<span style='color: #0F766E; font-weight:700;'>• Genesis Block (Genesis Root Hash: {evt_h})</span>", unsafe_allow_html=True)
                    st.markdown(f"**Severity:** <span style='color:{color}; font-weight:700;'>{sev}</span> &nbsp;|&nbsp; **Hash:** `{evt_h}` &nbsp;|&nbsp; **Prev Hash:** `{prev_h}`", unsafe_allow_html=True)
                    details_dict = evt.get("details", {})
                    if isinstance(details_dict, dict) and details_dict:
                        kv_rows = [{"Property": str(k), "Value": str(v)} for k, v in details_dict.items()]
                        st.dataframe(pd.DataFrame(kv_rows), width="stretch", hide_index=True)
                    else:
                        st.write(str(details_dict))

        # Export raw JSONL
        jsonl_content = "\n".join(json.dumps(e, default=str) for e in events)
        st.download_button(
            label=":material/download: Export Full Audit Log Stream (.jsonl)",
            data=jsonl_content,
            file_name=f"audit_trail_{st.session_state.app_id}.jsonl",
            mime="application/x-ndjson",
            width="stretch",
        )

# ----------------------------------------------------------------------------
# 3. BIDIRECTIONAL PAGE NAVIGATION
# ----------------------------------------------------------------------------
st.markdown("---")
nav_cam1, nav_cam2 = st.columns(2)
with nav_cam1:
    if st.button("← Back to Decision Scorecard", type="secondary", key="btn_cam_back_decision", width="stretch"):
        st.session_state.current_page = "Decision"
        st.switch_page("app_pages/decision.py")
with nav_cam2:
    if st.button("Restart Underwriting Pipeline ↺", type="secondary", key="btn_cam_restart", width="stretch"):
        st.session_state.current_page = "Case"
        st.switch_page("app_pages/case.py")
