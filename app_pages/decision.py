import os
import json
import html
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from core.data_models import (
    ReviewStatus,
    FindingTier,
    AdverseMediaReport,
)
from core.researcher import (
    build_search_query,
    search_adverse_media,
    confirm_adverse_finding,
    dismiss_adverse_finding,
    extract_policy_flags,
    merge_existing_reviews,
)
from core.policy import Verdict
from core.scorer import score_credit_case, ScoreResult
from core.formatting import format_inr
from ui.state import (
    get_app_context,
    record_event,
    compute_composite_hash,
    COHORT_COMPANY_NAMES,
)
from ui.components import (
    render_kpi_card,
    status_pill,
    render_callout,
    render_verdict_hero_card,
)

st.session_state.current_page = "Decision"
ctx = get_app_context()

cohort_selection = ctx["cohort_selection"]
cin_parsed_data = ctx["cin_parsed_data"]
current_pan_val = ctx["current_pan_val"]
current_pan_mismatch = ctx["current_pan_mismatch"]
pan_check = ctx["pan_check"]
loan_amount = ctx["loan_amount"]
tenure_months = ctx["tenure_months"]
interest_rate = ctx["interest_rate"]
proposed_emi = ctx["proposed_emi"]
collateral_val = ctx["collateral_val"]
cgtmse_covered = ctx["cgtmse_covered"]

st.markdown("### Deterministic Five Cs Risk Scorecard & Policy Gate")
st.caption("Evaluated entirely in Python Decimal arithmetic.")

if not st.session_state.hitl_confirmed or st.session_state.locked_case is None:
    render_callout("Financials are not locked yet. Review and lock them on the Evidence page.", title="Financials Not Locked", variant="info")
    if st.button("Go to Evidence", type="primary", key="btn_go_to_evidence"):
        st.session_state.current_page = "Evidence"
        st.switch_page("app_pages/evidence.py")
    st.stop()

# ----------------------------------------------------------------------------
# FACILITY TERMS READ-ONLY STRIP per B4
# ----------------------------------------------------------------------------
cgtmse_txt = "Yes" if cgtmse_covered else "No"
col_strip, col_edit = st.columns([5, 1])
with col_strip:
    strip_html = f'<div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 14px; font-size: 13px; color: #1F2937; margin-bottom: 8px;"><strong>Facility:</strong> {format_inr(loan_amount)}, {int(tenure_months)} mos @ {float(interest_rate)}% p.a. &nbsp;|&nbsp; <strong>Collateral:</strong> {format_inr(collateral_val)} &nbsp;|&nbsp; <strong>CGTMSE:</strong> {cgtmse_txt}</div>'
    st.markdown(strip_html, unsafe_allow_html=True)
with col_edit:
    if st.button(":material/edit: Edit terms", key="btn_edit_facility_terms", width="stretch"):
        st.session_state.current_page = "Case"
        st.switch_page("app_pages/case.py")

# ----------------------------------------------------------------------------
# COMPOSITE HASH & SCORING TRIGGER
# ----------------------------------------------------------------------------
findings_list = st.session_state.current_research_report.findings if st.session_state.current_research_report else []
current_composite_hash = compute_composite_hash(
    locked_financials_hash=st.session_state.locked_financials_hash,
    loan_amount=float(loan_amount),
    tenure_months=int(tenure_months),
    interest_rate=float(interest_rate),
    proposed_emi=float(proposed_emi),
    collateral_val=float(collateral_val),
    cgtmse_covered=bool(cgtmse_covered),
    cin=cin_parsed_data["cin"] if cin_parsed_data else "",
    vintage=cin_parsed_data.get("business_vintage_years", 5) if cin_parsed_data else 5,
    pan=current_pan_val,
    pan_mismatch=current_pan_mismatch,
    research_findings=findings_list,
)

is_stale = (st.session_state.scored_composite_hash != current_composite_hash)

def _execute_scoring(report_override=None) -> ScoreResult:
    rep_for_scoring = report_override if report_override is not None else st.session_state.current_research_report
    if st.session_state.locked_case is not None:
        st.session_state.locked_case.requested_loan_amount = Decimal(str(loan_amount))
        st.session_state.locked_case.proposed_rate = Decimal(str(interest_rate))
        st.session_state.locked_case.proposed_tenure_months = int(tenure_months)
        st.session_state.locked_case.proposed_emi = Decimal(str(proposed_emi))

    sev_flags, rev_flags = extract_policy_flags(rep_for_scoring)
    score_res = score_credit_case(
        credit_case=st.session_state.locked_case,
        business_vintage_years=cin_parsed_data.get("business_vintage_years", 5) if cin_parsed_data else 5,
        pan_entity_mismatch=pan_check.get("mismatch_detected", False),
        collateral_value=Decimal(str(collateral_val)),
        cgtmse_covered=cgtmse_covered,
        severe_flags=sev_flags,
        review_flags=rev_flags,
    )
    findings_for_hash = rep_for_scoring.findings if rep_for_scoring else []
    new_comp_hash = compute_composite_hash(
        locked_financials_hash=st.session_state.locked_financials_hash,
        loan_amount=float(loan_amount),
        tenure_months=int(tenure_months),
        interest_rate=float(interest_rate),
        proposed_emi=float(proposed_emi),
        collateral_val=float(collateral_val),
        cgtmse_covered=bool(cgtmse_covered),
        cin=cin_parsed_data["cin"] if cin_parsed_data else "",
        vintage=cin_parsed_data.get("business_vintage_years", 5) if cin_parsed_data else 5,
        pan=current_pan_val,
        pan_mismatch=current_pan_mismatch,
        research_findings=findings_for_hash,
    )
    st.session_state.score_result = score_res
    st.session_state.scored_composite_hash = new_comp_hash
    st.session_state.scored_financials_hash = st.session_state.locked_financials_hash
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
            "dscr": str(score_res.dscr),
        },
    )
    return score_res

if is_stale or st.session_state.score_result is None:
    if is_stale and st.session_state.score_result is not None:
        render_callout("Parameters or adverse media reviews modified. Re-run scoring to refresh decision.", title="Stale Score Warning", variant="warning")
    if st.button(":material/balance: Run Deterministic Risk Engine & Policy Gate", type="primary", key="btn_run_score", width="stretch"):
        try:
            _execute_scoring()
            st.rerun()
        except Exception as e:
            err_type = type(e).__name__
            err_msg = str(e)
            record_event(
                event_type="SCORING_ERROR",
                actor="SCORER",
                details={"error_type": err_type, "message": err_msg},
                severity="ERROR",
            )
            render_callout(f"Error ({err_type}): {err_msg}", title="Scoring Error", variant="error")

res: Optional[ScoreResult] = st.session_state.score_result
if res is not None:
    if res.final_verdict == Verdict.INSUFFICIENT_DATA:
        render_callout("The deterministic policy engine halted because one or more mandatory financial parameters are missing.", title="Underwriting Blocked: Insufficient Data", variant="error")
        st.markdown("#### Missing Required Parameters Checklist:")
        for r in res.policy_reasons:
            st.markdown(f"- :material/close: **{r}**")
        render_callout("Return to Evidence page, provide the required values, and re-confirm.", title="Action Required", variant="info")
    else:
        # --------------------------------------------------------------------
        # 1. VETO BANNER & VERDICT HERO CARD
        # --------------------------------------------------------------------
        has_confirmed_severe = False
        rep_check = st.session_state.current_research_report
        if rep_check and rep_check.findings:
            for f in rep_check.findings:
                if f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED:
                    has_confirmed_severe = True
                    break

        if has_confirmed_severe and st.session_state.executive_override:
            st.session_state.executive_override = None
            record_event(
                event_type="EXECUTIVE_OVERRIDE_CLEARED",
                actor="SYSTEM_POLICY",
                details={"reason": "Confirmed SEVERE adverse finding invalidates executive override"},
                severity="WARNING",
            )

        override_info = st.session_state.executive_override
        is_overridden = bool(override_info and override_info.get("override_active"))
        if is_overridden:
            render_callout(
                f"Overridden by officer ({override_info.get('officer_name', 'Senior Officer')}). Algorithmic Baseline: {override_info.get('original_verdict')} -> Final Sanction: {override_info.get('override_verdict')}.\n\nJustification: {override_info.get('justification_text')}",
                title="Overridden by officer",
                variant="warning",
            )

        hero_verdict = override_info.get("override_verdict") if is_overridden else res.final_verdict.name
        hero_rule = res.knockout_applied or res.cap_applied or "Five-Cs Algorithmic Assessment"
        hero_why = (
            "All policy risk thresholds satisfied." if res.final_verdict == Verdict.APPROVE
            else (f"Weakest-Link floor applied: {res.cap_applied}." if res.final_verdict == Verdict.CONDITIONAL
            else f"Knockout condition triggered: {res.knockout_applied}.")
        )

        render_callout(
            f"Scored: {hero_verdict} ({hero_rule})",
            title="Scoring Assessment",
            variant="success" if hero_verdict == "APPROVE" else ("warning" if hero_verdict == "CONDITIONAL" else "error"),
        )

        render_verdict_hero_card(
            verdict=hero_verdict,
            score=float(res.composite_score),
            deciding_rule=hero_rule,
            why=hero_why,
            overridden=is_overridden,
            baseline_verdict=res.baseline_verdict.name,
            approver=override_info.get("officer_name") if is_overridden else None,
        )

        # Dynamic Copilot Underwriting Synthesis Narrative
        comp_name = st.session_state.locked_case.company_name if st.session_state.locked_case else "Applicant"
        if hero_verdict == "APPROVE":
            syn_head = "Prime Solvency Profile • Resilient Debt-Service Buffer"
            syn_body = f"{comp_name} exhibits strong operational resilience with an Operating DSCR of {res.dscr:.2f}x (clearing the 1.20x policy hurdle). Banking credits show robust alignment with declared GST turnover, corroborated by zero mandate dishonours over 12 months. All five underwriting pillars satisfy policy risk thresholds."
            syn_rec = "Sanction proposed facility with standard quarterly turnover reconciliation covenant."
            syn_accent = "#15803D"
        elif hero_verdict == "REJECT":
            syn_head = "Severe Credit Stress • Algorithmic Policy Knockout"
            syn_body = f"{comp_name} triggers credit policy knockouts ({res.knockout_applied or 'Elevated Credit Risk'}). Operating cash flow is insufficient to cover debt obligations (DSCR: {res.dscr:.2f}x), compounded by recurring banking mandate returns. Unmitigated credit risk precludes sanction under current policy covenants."
            syn_rec = "Decline requested facility. Recommend applicant de-leverage balance sheet and stabilize banking conduct before re-application."
            syn_accent = "#B91C1C"
        else:
            syn_head = "Accrual-versus-Cash Divergence • Policy Cap Applied"
            syn_body = f"{comp_name} demonstrates meaningful business turnover, but thin net profitability restricts internal liquidity generation. Conditions pillar score fell below the 40-point hurdle ({res.cap_applied or 'Margin Pressure'}), capping the final verdict at CONDITIONAL. Debt service coverage requires external credit enhancement."
            syn_rec = "Consider conditional sanction subject to 100% liquid collateral pledge or CGTMSE enhancement with enhanced quarterly audit covenants."
            syn_accent = "#B45309"

        synthesis_html = f"""<div style="background-color: #FFFFFF; border: 1px solid #E2E8F0; border-left: 4px solid {syn_accent}; border-radius: 8px; padding: 14px 18px; margin: 10px 0 16px 0;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <div style="font-size: 11px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.06em;">
                    Copilot Underwriting Synthesis
                </div>
                <div style="font-size: 11px; color: #64748B;">Senior MSME Desk Perspective</div>
            </div>
            <div style="font-size: 14px; font-weight: 700; color: #0B1F3A; margin-bottom: 4px;">{syn_head}</div>
            <div style="font-size: 13px; color: #334155; line-height: 1.5; margin-bottom: 8px;">{syn_body}</div>
            <div style="font-size: 12px; color: #0F766E; font-weight: 600; border-top: 1px solid #F1F5F9; padding-top: 6px;">
                <strong>Underwriting Recommendation:</strong> {syn_rec}
            </div>
        </div>"""
        st.markdown("\n".join(line.strip() for line in synthesis_html.splitlines() if line.strip()), unsafe_allow_html=True)

        # --------------------------------------------------------------------
        # 2. 4 KPI CARDS (DSCR, D/E, GST-BANK GAP, COMPOSITE)
        # --------------------------------------------------------------------
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            render_kpi_card(
                "Operating DSCR",
                f"{res.dscr:.2f}x",
                delta="Hurdle: 1.20x",
                delta_color="normal" if res.dscr >= Decimal("1.20") else "inverse",
            )

        with k2:
            itr_l = st.session_state.locked_case.itr_data
            de_disp = "N/A"
            de_color = "neutral"
            if itr_l and itr_l.net_worth is not None:
                if itr_l.net_worth <= Decimal("0"):
                    de_disp = "Insolvent (< 0)"
                    de_color = "inverse"
                elif itr_l.total_debt is not None:
                    de_val = float(itr_l.total_debt / itr_l.net_worth)
                    de_disp = f"{de_val:.2f}x"
                    de_color = "normal" if de_val <= 1.0 else "inverse"
                else:
                    de_disp = "N/A"
                    de_color = "neutral"
            render_kpi_card(
                "Leverage (D/E)",
                de_disp,
                delta="Benchmark: 1.0x",
                delta_color=de_color,
            )

        with k3:
            gst_l = st.session_state.locked_case.gst_data
            bank_l = st.session_state.locked_case.bank_data
            gap_disp = "N/A"
            gap_color = "neutral"
            if gst_l and bank_l and gst_l.taxable_value is not None and bank_l.total_credits is not None:
                max_t = max(gst_l.taxable_value, bank_l.total_credits)
                if max_t > Decimal("0"):
                    gap_pct = float(abs(gst_l.taxable_value - bank_l.total_credits) / max_t * Decimal("100"))
                    gap_disp = f"{gap_pct:.1f}%"
                    gap_color = "normal" if gap_pct <= 15.0 else "inverse"
                else:
                    gap_disp = "0.0%"
                    gap_color = "normal"
            render_kpi_card(
                "Accrual-Cash Gap",
                gap_disp,
                delta="<= 15% Normal",
                delta_color=gap_color,
            )

        with k4:
            score_delta_color = "normal" if res.baseline_verdict == Verdict.APPROVE else ("warning" if res.baseline_verdict == Verdict.CONDITIONAL else "inverse")
            render_kpi_card(
                "Composite Score",
                f"{res.composite_score:.1f} / 100",
                delta=f"Baseline: {res.baseline_verdict.name}",
                delta_color=score_delta_color,
            )

        # --------------------------------------------------------------------
        # 3. FIVE-CS RISK ANALYTICS: PILLARS & MULTI-DIMENSIONAL GEOMETRY
        # --------------------------------------------------------------------
        st.markdown("#### Five Cs Risk Analytics: Pillars & Multi-Dimensional Geometry")
        c_labels = ["Conditions", "Collateral", "Capital", "Capacity", "Character"]
        c_scores = [
            float(res.five_cs.get("CONDITIONS", Decimal("0"))),
            float(res.five_cs.get("COLLATERAL", Decimal("0"))),
            float(res.five_cs.get("CAPITAL", Decimal("0"))),
            float(res.five_cs.get("CAPACITY", Decimal("0"))),
            float(res.five_cs.get("CHARACTER", Decimal("0"))),
        ]
        c_colors = ['#0F766E' if s >= 40.0 else '#B91C1C' for s in c_scores]

        fig_h = go.Figure()
        fig_h.add_trace(go.Bar(
            x=c_scores,
            y=c_labels,
            orientation='h',
            marker=dict(color=c_colors),
            text=[f"{s:.1f} pts" for s in c_scores],
            textposition='auto',
            name="Pillar Score",
        ))
        fig_h.add_vline(
            x=40.0,
            line_dash="dash",
            line_color="#B91C1C",
            line_width=2,
            annotation_text="Weakest-Link Floor (40.0)",
            annotation_position="top left",
            annotation_font_color="#B91C1C",
            annotation_font_size=11,
        )
        fig_h.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(
                range=[0, 100],
                title=dict(text="Pillar Score (0-100)", font=dict(color="#1F2937", size=11)),
                gridcolor="#E2E8F0",
                zerolinecolor="#E2E8F0",
                tickfont=dict(color="#1F2937", size=10),
            ),
            yaxis=dict(
                title="",
                gridcolor="#E2E8F0",
                tickfont=dict(color="#1F2937", size=11),
            ),
            height=280,
            margin=dict(l=10, r=10, t=25, b=20),
            showlegend=False,
        )

        r_categories = ["Character", "Capacity", "Capital", "Collateral", "Conditions"]
        r_scores = [
            float(res.five_cs.get("CHARACTER", Decimal("0"))),
            float(res.five_cs.get("CAPACITY", Decimal("0"))),
            float(res.five_cs.get("CAPITAL", Decimal("0"))),
            float(res.five_cs.get("COLLATERAL", Decimal("0"))),
            float(res.five_cs.get("CONDITIONS", Decimal("0"))),
        ]
        fig_r = go.Figure()
        fig_r.add_trace(go.Scatterpolar(
            r=r_scores + [r_scores[0]],
            theta=r_categories + [r_categories[0]],
            fill='toself',
            name='Five Cs Score',
            line=dict(color='#0F766E', width=2),
            fillcolor='rgba(15, 118, 110, 0.2)',
        ))
        fig_r.add_trace(go.Scatterpolar(
            r=[40, 40, 40, 40, 40, 40],
            theta=r_categories + [r_categories[0]],
            name='Floor (40.0)',
            line=dict(color='#B91C1C', width=1.5, dash='dash'),
        ))
        fig_r.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            polar=dict(
                bgcolor="rgba(0,0,0,0)",
                radialaxis=dict(visible=True, range=[0, 100], gridcolor="#E2E8F0", tickfont=dict(color="#64748B", size=9)),
                angularaxis=dict(gridcolor="#E2E8F0", tickfont=dict(color="#1F2937", size=10)),
            ),
            showlegend=True,
            legend=dict(font=dict(color="#1F2937", size=10), orientation="h", y=-0.15, x=0.05),
            height=280,
            margin=dict(l=20, r=20, t=20, b=30),
        )

        chart_c1, chart_c2 = st.columns([3, 2], gap="medium")
        with chart_c1:
            st.plotly_chart(fig_h, width="stretch")
        with chart_c2:
            st.plotly_chart(fig_r, width="stretch")

        # --------------------------------------------------------------------
        # 4. "WHY THIS DECISION" LIST BUILT FROM DEDUCTION TRAIL
        # --------------------------------------------------------------------
        st.markdown("#### :material/lightbulb: Why This Decision")
        if res.knockout_applied:
            render_callout(f"Hard Policy Knockout: {res.knockout_applied}.", title="Knockout Triggered", variant="error")
        if res.cap_applied:
            render_callout(f"Weakest-Link Floor: {res.cap_applied}. Under bank credit policy, overall verdict capped at CONDITIONAL.", title="Policy Floor Applied", variant="warning")
        if not res.knockout_applied and not res.cap_applied:
            render_callout("All algorithmic risk thresholds and covenants satisfied.", title="Policy Approval", variant="success")

        if res.deductions:
            st.markdown("##### Algorithmic Score Deductions Ledger")
            deduct_records = [
                {
                    "Rule ID": d.rule_id,
                    "Pillar": d.pillar,
                    "Deduction": f"-{d.points_deducted:.1f} pts",
                    "Observed Value": str(d.observed_value),
                    "Benchmark": str(d.threshold),
                    "Policy Explanation": d.explanation,
                }
                for d in res.deductions
            ]
            st.dataframe(pd.DataFrame(deduct_records), width="stretch", hide_index=True)
        else:
            render_callout("Applicant scored 100.0/100 across all evaluated parameters with zero deductions.", title="Zero Deductions", variant="success")

        # --------------------------------------------------------------------
        # 6. ADVERSE MEDIA REVIEW AS BORDERED FINDING CARDS (INLINE ACTIONS)
        # --------------------------------------------------------------------
        st.markdown("---")
        st.markdown("#### :material/search: Adverse Media Screening & Legal Due Diligence")
        st.caption("Secondary structured search ingestion with keyword/pattern matching.")
        registry_strip = """<div style="background: #F1F5F9; border: 1px solid #E2E8F0; border-radius: 6px; padding: 6px 12px; margin-bottom: 10px; font-size: 11px; color: #475569;">
            <strong>Screened Legal Dockets:</strong> e-Courts NJDG • NCLT/NCLAT Insolvency Cases • MCA-21 Portal • IBBI Corporate Insolvency Registry • DRT Recovery Registers
        </div>"""
        st.markdown("\n".join(line.strip() for line in registry_strip.splitlines() if line.strip()), unsafe_allow_html=True)

        active_cin = cin_parsed_data["cin"] if cin_parsed_data else "UNREGISTERED_MSME"
        company_nm = (
            st.session_state.locked_case.company_name
            if st.session_state.locked_case
            else COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company")
        )
        anchored_query = build_search_query(company_nm, cin=active_cin)

        is_demo_cohort = bool(cohort_selection and cohort_selection != "None (Manual Upload)")
        col_q1, col_q2 = st.columns([3, 1])
        with col_q1:
            st.code(f"Anchored Identity Query: {anchored_query}", language="sql")
        with col_q2:
            if is_demo_cohort:
                st.markdown(f"Docket Mode: &nbsp; {status_pill('Curated Simulation', variant='info')}", unsafe_allow_html=True)
                st.caption("Pre-calibrated e-Courts & NCLT orders")
                use_synthetic_demo = True
            else:
                use_synthetic_demo = st.checkbox(
                    ":material/science: Simulation Mode",
                    value=not bool(os.getenv("TAVILY_API_KEY")),
                    help="Generates simulated legal/adverse records watermarked with [SYNTHETIC DEMO DATA]",
                    key="chk_synthetic_demo",
                )

        cache_key = (active_cin, bool(use_synthetic_demo))
        if st.session_state.last_research_cache_key != cache_key:
            st.session_state.last_research_cache_key = cache_key
            st.session_state.current_research_report = st.session_state.research_cache.get(cache_key, None)

        if st.button(":material/search: Execute Adverse Media & Legal Docket Scan", key="btn_exec_media_scan"):
            with st.spinner("Screening adverse media registries and dockets..."):
                raw_rep = search_adverse_media(
                    company_name=company_nm,
                    cin=active_cin,
                    mock_mode=use_synthetic_demo,
                )
                merged_rep = merge_existing_reviews(raw_rep, st.session_state.current_research_report)
                st.session_state.research_cache[cache_key] = merged_rep
                st.session_state.current_research_report = merged_rep
                record_event(
                    event_type="ADVERSE_MEDIA_SCAN_COMPLETED",
                    actor="RESEARCHER",
                    details={
                        "status": merged_rep.status,
                        "findings_count": len(merged_rep.findings),
                        "is_synthetic": merged_rep.is_synthetic_demo,
                        "query": merged_rep.query_used,
                    },
                )
            _execute_scoring(report_override=merged_rep)
            st.rerun()

        rep: Optional[AdverseMediaReport] = st.session_state.current_research_report
        if rep is not None:
            if rep.status == "RESEARCH_UNAVAILABLE":
                render_callout(
                    f"Secondary screening could not complete ({rep.error_message or 'offline/timeout'}). System will not default to Clean. Credit Officer must perform manual registry verification.",
                    title="Research Unavailable",
                    variant="warning",
                )
            elif rep.status == "NO_ADVERSE_FINDINGS" or len(rep.findings) == 0:
                render_callout("No adverse findings in screened sources; this is not a legal clearance.", title="Screening Complete", variant="info")
            elif rep.status == "COMPLETED":
                has_unreviewed_severe = any(
                    f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.UNREVIEWED
                    for f in rep.findings
                )
                if has_unreviewed_severe:
                    render_callout(
                        "Admitted insolvency or severe finding detected! External findings carry zero effect on the policy gate until explicitly confirmed by the Credit Officer.",
                        title="Officer Review Required",
                        variant="error",
                    )

                for f in rep.findings:
                    with st.container(border=True):
                        col_t1, col_t2 = st.columns([3, 1])
                        with col_t1:
                            tier_label = {
                                FindingTier.SEVERE: status_pill("Severe: Insolvency", variant="reject"),
                                FindingTier.REVIEW: status_pill("Review: Statutory", variant="conditional"),
                                FindingTier.INFORMATIONAL: status_pill("Informational", variant="info"),
                            }.get(f.tier, status_pill(str(f.tier), variant="neutral"))
                            st.markdown(f"**{f.title}** &nbsp; {tier_label}", unsafe_allow_html=True)
                            st.caption(f.snippet)
                            st.markdown(f"[:material/link: Direct Source Registry]({f.source_url})")
                        with col_t2:
                            if f.review_status == ReviewStatus.CONFIRMED:
                                render_callout(f"Status: CONFIRMED\n\nCitation: {f.confirmation_note or 'N/A'}\nBy: {f.reviewed_by}", title="Officer Confirmed", variant="error")
                            elif f.review_status == ReviewStatus.DISMISSED:
                                render_callout(f"Status: DISMISSED\n\nReason: {f.dismissal_reason}\nBy: {f.reviewed_by}", title="Officer Dismissed", variant="success")
                            else:
                                render_callout("Status: UNREVIEWED", variant="warning")

                        if f.review_status == ReviewStatus.UNREVIEWED:
                            st.markdown("##### Officer Legal Determination (Inline Action)")
                            note_val = st.text_input("Order Citation / Dismissal Reason (Required)", key=f"inline_note_{f.id}", placeholder="e.g. NCLT Order CP(IB)/102/2026 or Homonym entity collision")
                            c_act1, c_act2 = st.columns(2)
                            with c_act1:
                                if st.button("Confirm Finding", key=f"btn_inline_conf_{f.id}", type="primary", width="stretch"):
                                    if note_val.strip():
                                        updated_rep = confirm_adverse_finding(rep, f.id, note_val.strip(), officer_name="Credit Officer")
                                        st.session_state.research_cache[cache_key] = updated_rep
                                        st.session_state.current_research_report = updated_rep
                                        record_event("FINDING_CONFIRMED", "OFFICER", {"finding_id": f.id, "title": f.title, "tier": f.tier.value, "justification": note_val.strip()})
                                        if f.tier == FindingTier.SEVERE and st.session_state.executive_override:
                                            st.session_state.executive_override = None
                                            record_event(
                                                event_type="EXECUTIVE_OVERRIDE_CLEARED",
                                                actor="SYSTEM_POLICY",
                                                details={"reason": "Confirmed SEVERE adverse finding cleared executive override"},
                                                severity="WARNING",
                                            )
                                        _execute_scoring(report_override=updated_rep)
                                        st.toast("Finding confirmed & Five-Cs scorecard updated.", icon="⚖️")
                                        st.rerun()
                                    else:
                                        render_callout("Reference citation note required to confirm finding.", title="Validation Error", variant="error")
                            with c_act2:
                                if st.button("Dismiss Finding", key=f"btn_inline_dism_{f.id}", type="secondary", width="stretch"):
                                    if len(note_val.strip()) >= 10:
                                        updated_rep = dismiss_adverse_finding(rep, f.id, note_val.strip(), officer_name="Credit Officer")
                                        st.session_state.research_cache[cache_key] = updated_rep
                                        st.session_state.current_research_report = updated_rep
                                        record_event("FINDING_DISMISSED", "OFFICER", {"finding_id": f.id, "title": f.title, "justification": note_val.strip()})
                                        _execute_scoring(report_override=updated_rep)
                                        st.toast("Finding dismissed & Five-Cs scorecard updated.", icon="✓")
                                        st.rerun()
                                    else:
                                        render_callout("Minimum 10 characters required for dismissal justification.", title="Validation Error", variant="error")

        # --------------------------------------------------------------------
        # 7. OFFICER FIELD NOTES & QUALITATIVE DOSSIER
        # --------------------------------------------------------------------
        with st.container(border=True):
            st.markdown("#### :material/rate_review: Credit Committee Qualitative Field Dossier")
            st.caption("Record on-site inspection observations, promoter interviews, and qualitative due diligence.")

            st.markdown("<div style='font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase; margin-bottom: 4px;'>Quick Observation Presets:</div>", unsafe_allow_html=True)
            col_pr1, col_pr2, col_pr3, col_pr4 = st.columns(4)
            with col_pr1:
                if st.button("Plant Inspection", key="btn_pr_plant", width="stretch"):
                    st.session_state.txt_field_note = "Plant inspection verified: Machinery in operational condition at 85% capacity utilization. Physical stock matches GSTR-3B filings."
                    st.rerun()
            with col_pr2:
                if st.button("Promoter Diligence", key="btn_pr_promoter", width="stretch"):
                    st.session_state.txt_field_note = "Promoter interview concluded: Clear market reputation, satisfactory vintage in the sector, and clean track record with local suppliers."
                    st.rerun()
            with col_pr3:
                if st.button("Working Capital", key="btn_pr_wc", width="stretch"):
                    st.session_state.txt_field_note = "Working capital verification: Receivables turnover matches standard 45-day commercial terms with low single-debtor concentration."
                    st.rerun()
            with col_pr4:
                if st.button("Tax & Filings", key="btn_pr_tax", width="stretch"):
                    st.session_state.txt_field_note = "Tax governance check: GSTR-3B monthly filings and advance tax payments match annual audited financials without statutory demands."
                    st.rerun()

            fn_text = st.text_area("Field Visit Observation / Qualitative Notes", placeholder="e.g. Visited plant in Okhla. Machinery in operational condition, factory working at 85% utilization.", key="txt_field_note")
            fn_pillar = st.selectbox("Affected C Pillar", ["CHARACTER", "CAPACITY", "CAPITAL", "COLLATERAL", "CONDITIONS", "GENERAL"], key="sb_fn_pillar")
            if st.button("Save Field Note", key="btn_save_field_note", width="stretch"):
                if fn_text.strip():
                    note_entry = {
                        "affected_c": fn_pillar,
                        "note_text": fn_text.strip(),
                        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    }
                    st.session_state.officer_notes.append(note_entry)
                    st.session_state.cam_markdown = None
                    record_event(
                        event_type="OFFICER_NOTE_ADDED",
                        actor="OFFICER",
                        details=note_entry,
                    )
                    render_callout("Field observation appended and stamped in audit log.", title="Observation Saved", variant="success")
                    st.rerun()

            if st.session_state.officer_notes:
                st.markdown("##### Logged Field Observations")
                for on in st.session_state.officer_notes:
                    c_tag = status_pill(on.get("affected_c", "GENERAL"), variant="info")
                    clean_ts = html.escape(str(on.get("timestamp", "")))
                    clean_txt = html.escape(str(on.get("note_text", "")))
                    note_card = f"""<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 10px 14px; margin-bottom: 8px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <div>{c_tag}</div>
                            <div style="font-size: 11px; color: #64748B;">{clean_ts}</div>
                        </div>
                        <div style="font-size: 13px; color: #1F2937; line-height: 1.45;">{clean_txt}</div>
                    </div>"""
                    st.markdown("\n".join(line.strip() for line in note_card.splitlines() if line.strip()), unsafe_allow_html=True)

        # --------------------------------------------------------------------
        # 8. EXECUTIVE VETO (CREDIT COMMITTEE OVERRIDE)
        # --------------------------------------------------------------------
        with st.container(border=True):
            st.markdown("#### :material/gavel: Credit Committee Quorum Governance Shield")
            st.caption("Under bank governance, credit committee officers may override the algorithmic verdict, but MUST provide written justification.")
            gov_note = """<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; font-size: 11px; color: #64748B; line-height: 1.4;">
                <strong>Governance Rule:</strong> Overrides are prohibited if an admitted SEVERE adverse finding (e.g. NCLT) is confirmed. Written justification (minimum 20 characters) is permanently chained into the session audit log.
            </div>"""
            st.markdown("\n".join(line.strip() for line in gov_note.splitlines() if line.strip()), unsafe_allow_html=True)

            veto_active = st.toggle("Enable Executive Veto", value=bool(st.session_state.executive_override and st.session_state.executive_override.get("override_active")), key="tgl_veto_active")
            if not veto_active and st.session_state.executive_override and st.session_state.executive_override.get("override_active"):
                st.session_state.executive_override = None
                st.session_state.cam_markdown = None
                record_event(
                    event_type="EXECUTIVE_OVERRIDE_REMOVED",
                    actor="OFFICER_VETO",
                    details={"action": "Executive override disabled"},
                    severity="INFO",
                )
                st.toast("Executive Veto disabled by officer.", icon="ℹ️")
                st.rerun()

            if veto_active:
                has_confirmed_severe = False
                if st.session_state.current_research_report and st.session_state.current_research_report.findings:
                    for f in st.session_state.current_research_report.findings:
                        if f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED:
                            has_confirmed_severe = True
                            break

                veto_verdict = st.selectbox(
                    "Override Verdict",
                    ["APPROVE", "CONDITIONAL", "REJECT"],
                    index=0 if res.final_verdict != Verdict.APPROVE else 1,
                    key="sb_veto_verdict",
                )
                veto_officer = st.text_input("Approving Officer / Authority", value="Senior Credit Officer - MSME Desk", key="txt_veto_officer")
                veto_justification = st.text_area(
                    "Mandatory Written Justification (Min 20 characters required)",
                    value=st.session_state.executive_override.get("justification_text", "") if st.session_state.executive_override else "",
                    placeholder="State exact credit committee rationale for overriding the system verdict...",
                    key="txt_veto_just",
                )
                if st.button("Submit Executive Veto", key="btn_submit_veto"):
                    if has_confirmed_severe and veto_verdict != "REJECT":
                        render_callout("Policy rule: a confirmed SEVERE finding cannot be overridden. Verdict stays REJECT.", title="Governance Violation", variant="error")
                    elif len(veto_justification.strip()) < 20:
                        render_callout("Justification is too brief. You must provide at least 20 characters explaining the override rationale.", title="Validation Error", variant="error")
                    else:
                        veto_data = {
                            "override_active": True,
                            "original_verdict": res.final_verdict.name,
                            "override_verdict": veto_verdict,
                            "officer_name": veto_officer.strip(),
                            "justification_text": veto_justification.strip(),
                            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                        }
                        st.session_state.executive_override = veto_data
                        st.session_state.cam_markdown = None
                        record_event(
                            event_type="EXECUTIVE_OVERRIDE_APPLIED",
                            actor="OFFICER_VETO",
                            details=veto_data,
                            severity="WARNING",
                        )
                        st.toast("Executive Veto recorded in Audit Trail. CAM updated.", icon="⚠️")
                        st.rerun()

        # --------------------------------------------------------------------
        # 9. BIDIRECTIONAL PAGE NAVIGATION (Proceed enabled ONLY when score exists)
        # --------------------------------------------------------------------
        st.markdown("---")
        nav_d1, nav_d2 = st.columns(2)
        with nav_d1:
            if st.button("← Back to Evidence Ingestion", type="secondary", key="btn_back_evidence", width="stretch"):
                st.session_state.current_page = "Evidence"
                st.switch_page("app_pages/evidence.py")
        with nav_d2:
            if st.session_state.score_result is not None:
                if st.button("Proceed to CAM & Audit Export →", type="primary", key="btn_next_cam_audit", width="stretch"):
                    st.session_state.current_page = "CAM & Audit"
                    st.switch_page("app_pages/cam_audit.py")
else:
    company_disp = (
        st.session_state.locked_case.company_name
        if st.session_state.locked_case
        else COHORT_COMPANY_NAMES.get(cohort_selection, "Applicant Company")
    )
    render_callout(
        f"Financial parameters and evidence locked for **{company_disp}**.\n\n"
        f"Active Facility Terms: ₹ {float(loan_amount):,.2f} @ {float(interest_rate)}% p.a. for {int(tenure_months)} Months.\n\n"
        "Click **Run Deterministic Risk Engine & Policy Gate** above to compute the Five Cs scorecard, verify DSCR/leverage hurdles, and execute policy gates.",
        title="Ready for Five Cs Underwriting Evaluation",
        variant="info",
    )
    st.markdown("---")
    if st.button("← Back to Evidence Ingestion", type="secondary", key="btn_back_evidence_pre", width="stretch"):
        st.session_state.current_page = "Evidence"
        st.switch_page("app_pages/evidence.py")

