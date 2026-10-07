import json
from typing import Dict, Any

import streamlit as st
import pandas as pd

from core.policy import Verdict
from core.audit_logger import verify_chain
from core.cam_generator import (
    generate_cam_markdown,
    generate_cam_html,
    CamGenerationError,
)
from ui.state import get_app_context
from ui.components import render_callout

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
            # Markdown and HTML download buttons in one balanced action row above the memo
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                st.download_button(
                    label=":material/download: Download Formal CAM (.md)",
                    data=st.session_state.cam_markdown,
                    file_name=f"CAM_{st.session_state.app_id}.md",
                    mime="text/markdown",
                    width="stretch",
                )
            with col_d2:
                html_cam = generate_cam_html(
                    cam_markdown=st.session_state.cam_markdown,
                    app_id=st.session_state.app_id,
                )
                st.download_button(
                    label=":material/download: Download Formal CAM (.html)",
                    data=html_cam,
                    file_name=f"CAM_{st.session_state.app_id}.html",
                    mime="text/html",
                    width="stretch",
                )

            st.markdown("---")
            st.markdown("#### Live Memorandum Preview")

            # Memo in a bordered container with centered readable width
            col_pad_l, col_cam, col_pad_r = st.columns([1, 10, 1])
            with col_cam:
                with st.container(border=True):
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

        # Severity filter
        col_f1, col_f2 = st.columns([1, 2])
        with col_f1:
            selected_sev = st.selectbox(
                "Filter Audit Events by Severity",
                ["ALL", "INFO", "WARNING", "ERROR"],
                index=0,
                key="audit_sev_filter",
            )
        with col_f2:
            st.markdown(f"**Total Events Recorded:** `{len(events)}`")

        filtered_events = [
            e for e in events
            if selected_sev == "ALL" or e.get("severity") == selected_sev
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
            render_callout(f"No audit events found with severity '{selected_sev}'.", title="No Matching Events", variant="info")
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
                exp_title = f"{evt_ts} | {evt.get('event_type', '')} ({evt.get('actor', '')})"
                with st.expander(exp_title, expanded=False, key=f"exp_audit_{idx}_{evt_h[:8]}"):
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
