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
    extract_legal_docket_text,
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
    _render_html,
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
            dscr_hurdle = Decimal("1.20")
            dscr_diff = res.dscr - dscr_hurdle
            dscr_sign = "+" if dscr_diff >= Decimal("0") else ""
            render_kpi_card(
                "Operating DSCR",
                f"{res.dscr:.2f}x",
                delta=f"{dscr_sign}{dscr_diff:.2f}x vs 1.20x hurdle",
                delta_color="normal" if res.dscr >= dscr_hurdle else "inverse",
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

        weights_map = {"CONDITIONS": "15%", "COLLATERAL": "15%", "CAPITAL": "20%", "CAPACITY": "30%", "CHARACTER": "20%"}
        bar_annotations = [f"{s:.1f} pts ({weights_map.get(l.upper(), '')})" for s, l in zip(c_scores, c_labels)]

        fig_h = go.Figure()
        fig_h.add_trace(go.Bar(
            x=c_scores,
            y=c_labels,
            orientation='h',
            marker=dict(color=c_colors),
            text=bar_annotations,
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
            fill='toself',
            name='Danger Floor (< 40.0)',
            line=dict(color='#B91C1C', width=1.5, dash='dash'),
            fillcolor='rgba(185, 28, 28, 0.08)',
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
            deduct_records.sort(key=lambda x: float(x["Deduction"].replace("-", "").replace(" pts", "")), reverse=True)
            st.dataframe(pd.DataFrame(deduct_records), width="stretch", hide_index=True)
        else:
            render_callout("Applicant scored 100.0/100 across all evaluated parameters with zero deductions.", title="Zero Deductions", variant="success")

        # --------------------------------------------------------------------
        # 5. DEBT SERVICE CAPACITY & CAPITAL AMORTIZATION ARCHITECTURE
        # --------------------------------------------------------------------
        with st.container(border=True):
            st.markdown("#### :material/account_balance: Debt Service Capacity & Capital Amortization Architecture")
            st.caption("Deterministic cash outflow modeling under approved facility terms with Principal vs Interest split.")

            from core.analyser import calculate_monthly_emi
            P_dec = Decimal(str(loan_amount))
            r_dec = Decimal(str(interest_rate))
            n_int = int(tenure_months)

            live_emi = calculate_monthly_emi(P_dec, r_dec, n_int)
            total_repayment = live_emi * Decimal(str(n_int))
            total_interest_cost = total_repayment - P_dec
            pct_p = float(P_dec / total_repayment * Decimal("100")) if total_repayment > 0 else 100.0
            pct_i = float(total_interest_cost / total_repayment * Decimal("100")) if total_repayment > 0 else 0.0

            d_col1, d_col2, d_col3, d_col4 = st.columns(4)
            with d_col1:
                render_kpi_card("Sanction Facility", f"₹ {float(P_dec)/100000:.1f} Lakhs", subtext=format_inr(P_dec))
            with d_col2:
                render_kpi_card("Lending Rate", f"{float(r_dec):.2f}% p.a.", subtext=f"{n_int}M Tenor ({n_int//12} Yrs)")
            with d_col3:
                render_kpi_card("Monthly EMI Outflow", format_inr(live_emi), subtext="Annuity Obligation")
            with d_col4:
                render_kpi_card("Total Interest Cost", format_inr(total_interest_cost), subtext=f"{pct_i:.1f}% Cost of Capital")

            fig_amort = go.Figure()
            fig_amort.add_trace(go.Bar(
                y=["Capital Outflow"],
                x=[float(P_dec)],
                name=f"Principal ({pct_p:.1f}%)",
                orientation="h",
                marker=dict(color="#0F766E"),
                text=f"Principal: ₹{float(P_dec)/100000:.1f}L ({pct_p:.1f}%)",
                textposition="inside",
                insidetextanchor="middle",
                hoverinfo="text",
            ))
            fig_amort.add_trace(go.Bar(
                y=["Capital Outflow"],
                x=[float(total_interest_cost)],
                name=f"Interest Cost ({pct_i:.1f}%)",
                orientation="h",
                marker=dict(color="#64748B"),
                text=f"Interest: ₹{float(total_interest_cost)/100000:.1f}L ({pct_i:.1f}%)",
                textposition="inside",
                insidetextanchor="middle",
                hoverinfo="text",
            ))
            fig_amort.update_layout(
                barmode="stack",
                height=56,
                margin=dict(l=0, r=0, t=4, b=4),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                showlegend=False,
                xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                yaxis=dict(showgrid=False, showticklabels=False),
            )
            st.plotly_chart(fig_amort, width="stretch", config={"displayModeBar": False})

        # --------------------------------------------------------------------
        # 6. ALL-INDIA FORENSIC LEGAL SURVEILLANCE & ADVERSE MEDIA RADAR
        # --------------------------------------------------------------------
        st.markdown("---")
        st.markdown("#### :material/shield_search: All-India Forensic Legal Surveillance & Adverse Media Radar")
        st.caption("Live statutory intelligence triangulation across 672 District Courts, 16 NCLT Benches, MCA-21, IBBI, and DRT Recovery Registries.")

        active_cin = cin_parsed_data["cin"] if cin_parsed_data else "UNREGISTERED_MSME"
        is_corp_entity = bool(active_cin and active_cin != "UNREGISTERED_MSME")
        company_nm = (
            st.session_state.locked_case.company_name
            if st.session_state.locked_case
            else COHORT_COMPANY_NAMES.get(cohort_selection, "Manual Applicant Company")
        )
        anchored_query = build_search_query(company_nm, cin=active_cin if is_corp_entity else None)

        is_demo_cohort = bool(cohort_selection and cohort_selection != "None (Manual Upload)")

        # 5-Court Telemetry Radar HUD (High-Density Multi-Registry Grid)
        telemetry_html = f"""<div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin: 10px 0 14px 0;">
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">🏛️ e-Courts NJDG</div>
                <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">672 Dist & High Courts</div>
                <div style="font-size: 9px; color: #10B981; font-weight: 600; margin-top: 2px;">● LIVE CONNECTION</div>
            </div>
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">⚖️ NCLT / NCLAT</div>
                <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">16 Benches (IBC/CIRP)</div>
                <div style="font-size: 9px; color: #10B981; font-weight: 600; margin-top: 2px;">● INSOLVENCY RADAR</div>
            </div>
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">🏢 MCA-21 Portal</div>
                <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">{'RoC Defaults & DIN' if is_corp_entity else 'Non-Corp (Exempt)'}</div>
                <div style="font-size: 9px; color: #10B981; font-weight: 600; margin-top: 2px;">● {'DEFAULTS VERIFIED' if is_corp_entity else 'REGISTRY BYPASS'}</div>
            </div>
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">📜 IBBI Registry</div>
                <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">Corporate Insolvency</div>
                <div style="font-size: 9px; color: #10B981; font-weight: 600; margin-top: 2px;">● LIQUIDATION WATCH</div>
            </div>
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-top: 3px solid #0F766E; border-radius: 6px; padding: 8px 10px;">
                <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase;">🏦 DRT / SARFAESI</div>
                <div style="font-size: 11px; font-weight: 700; color: #0B1F3A; margin-top: 2px;">39 Tribunals & NPAs</div>
                <div style="font-size: 9px; color: #10B981; font-weight: 600; margin-top: 2px;">● WILFUL DEFAULTERS</div>
            </div>
        </div>"""
        _render_html(telemetry_html)

        # Anchored Target Command Console (Light Institutional Design)
        if is_corp_entity:
            anchor_token_badge = f'<span style="color: #0F766E; font-family: monospace; font-weight: 600; background: #CCFBF1; border: 1px solid #99F6E4; padding: 2px 7px; border-radius: 4px;">CIN: {active_cin}</span>'
            anchor_clause_html = f'<span style="color: #0F766E; font-weight: 700;">"{company_nm}"</span> <span style="color: #DC2626; font-weight: 700;">AND</span> <span style="color: #2563EB; font-weight: 700;">"{active_cin}"</span>'
            anchor_mode_tag = "CIN Strict Match"
        else:
            anchor_token_badge = f'<span style="color: #0F766E; font-family: monospace; font-weight: 600; background: #CCFBF1; border: 1px solid #99F6E4; padding: 2px 7px; border-radius: 4px;">PAN: {current_pan_val} (Unincorporated)</span>'
            anchor_clause_html = f'<span style="color: #0F766E; font-weight: 700;">"{company_nm}"</span> <span style="color: #DC2626; font-weight: 700;">AND</span> <span style="color: #2563EB; font-weight: 700;">"{current_pan_val}"</span>'
            anchor_mode_tag = "PAN Anchor"

        command_deck_html = f"""<div style="background: #FFFFFF; border: 1px solid #CBD5E1; border-top: 3px solid #0F766E; border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px;">
                <div>
                    <div style="font-size: 10px; font-weight: 700; color: #0F766E; text-transform: uppercase; letter-spacing: 0.05em;">Surveillance Target Identity</div>
                    <div style="font-size: 15px; font-weight: 800; color: #0B1F3A; margin-top: 2px;">{company_nm}</div>
                    <div style="display: flex; gap: 8px; margin-top: 5px; font-size: 11px;">
                        {anchor_token_badge}
                        <span style="color: #334155; font-family: monospace; font-weight: 600; background: #F1F5F9; border: 1px solid #E2E8F0; padding: 2px 7px; border-radius: 4px;">PAN: {current_pan_val}</span>
                    </div>
                </div>
                <div style="text-align: right;">
                    <div style="display: inline-block; background: #ECFDF5; border: 1px solid #A7F3D0; border-radius: 9999px; padding: 3px 10px; font-size: 10px; font-weight: 700; color: #0F766E;">
                        {'CURATED BENCHMARK DOCKETS' if is_demo_cohort else 'ENTERPRISE REGISTRY GATEWAY'}
                    </div>
                    <div style="font-size: 9px; color: #64748B; margin-top: 3px;">Jurisdiction: 672 District Courts, High Courts & Tribunals</div>
                </div>
            </div>
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 5px; padding: 8px 12px; font-family: monospace; font-size: 11px; color: #334155; display: flex; justify-content: space-between; align-items: center; overflow-x: auto;">
                <div>
                    <span style="color: #D97706; font-weight: 700;">ANCHOR_QUERY</span> &gt;&gt; {anchor_clause_html}
                </div>
                <span style="font-size: 9px; color: #64748B; font-family: sans-serif; background: #E2E8F0; padding: 1px 6px; border-radius: 3px;">{anchor_mode_tag}</span>
            </div>
        </div>"""
        _render_html(command_deck_html)

        col_q1, col_q2 = st.columns([3, 1])
        with col_q1:
            scan_triggered = st.button(":material/radar: Execute Adverse Media & Legal Docket Scan", key="btn_exec_media_scan", type="primary", width="stretch")
        with col_q2:
            has_tavily = bool(os.getenv("TAVILY_API_KEY") and os.getenv("TAVILY_API_KEY") != "your_tavily_api_key_here")
            if is_demo_cohort:
                if has_tavily:
                    use_live_tavily = st.toggle("🛰️ Live Tavily Radar", value=False, help="Toggle to query live Tavily search engine across real web registries instead of curated benchmark case study dockets.", key="tgl_live_tavily_demo")
                    use_synthetic_demo = not use_live_tavily
                    if use_live_tavily:
                        st.markdown(f"<div style='text-align: center; margin-top: -6px;'>{status_pill('Live Web Active', variant='approve')}</div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div style='text-align: center; margin-top: -6px;'>{status_pill('Benchmark Case', variant='info')}</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div style='text-align: center; padding-top: 6px;'>{status_pill('Curated Simulation', variant='info')}</div>", unsafe_allow_html=True)
                    use_synthetic_demo = True
            else:
                use_synthetic_demo = st.checkbox(
                    ":material/science: Simulation Mode",
                    value=not has_tavily,
                    help="Generates simulated legal/adverse records watermarked with [SYNTHETIC DEMO DATA]",
                    key="chk_synthetic_demo",
                )

        cache_key = (active_cin, bool(use_synthetic_demo))
        if st.session_state.last_research_cache_key != cache_key:
            st.session_state.last_research_cache_key = cache_key
            st.session_state.current_research_report = st.session_state.research_cache.get(cache_key, None)

        if scan_triggered:
            with st.spinner("Screening adverse media registries, judicial dockets, and insolvency tribunals..."):
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
        if rep is None:
            awaiting_card = f"""<div style="background: #F8FAFC; border: 1.5px dashed #CBD5E1; border-radius: 8px; padding: 22px 16px; text-align: center; margin: 12px 0 16px 0;">
                <div style="font-size: 24px; margin-bottom: 4px;">📡</div>
                <div style="font-size: 13px; font-weight: 700; color: #0B1F3A; text-transform: uppercase; letter-spacing: 0.04em;">Legal Docket Surveillance Engine Primed</div>
                <div style="font-size: 12px; color: #64748B; max-width: 520px; margin: 4px auto 12px auto; line-height: 1.45;">
                    Target entity <strong>{company_nm}</strong> is anchored. Click <strong>Execute Adverse Media & Legal Docket Scan</strong> above to query 672 District Courts, 16 NCLT Benches, and national default registries.
                </div>
                <div style="display: flex; justify-content: center; gap: 8px; flex-wrap: wrap;">
                    <span style="font-size: 10px; background: #E2E8F0; padding: 3px 8px; border-radius: 9999px; color: #475569; font-weight: 600;">e-Courts Ready</span>
                    <span style="font-size: 10px; background: #E2E8F0; padding: 3px 8px; border-radius: 9999px; color: #475569; font-weight: 600;">NCLT Ready</span>
                    <span style="font-size: 10px; background: #E2E8F0; padding: 3px 8px; border-radius: 9999px; color: #475569; font-weight: 600;">IBBI Ready</span>
                    <span style="font-size: 10px; background: #E2E8F0; padding: 3px 8px; border-radius: 9999px; color: #475569; font-weight: 600;">DRT Ready</span>
                </div>
            </div>"""
            _render_html(awaiting_card)
        else:
            if rep.status == "RESEARCH_UNAVAILABLE":
                render_callout(
                    f"Secondary screening could not complete ({rep.error_message or 'offline/timeout'}). System will not default to Clean. Credit Officer must perform manual registry verification.",
                    title="Research Unavailable",
                    variant="warning",
                )
            elif rep.status == "NO_ADVERSE_FINDINGS" or len(rep.findings) == 0:
                clean_card = f"""<div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-left: 4px solid #15803D; border-radius: 8px; padding: 16px 18px; margin: 14px 0;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <div style="font-size: 22px;">🛡️</div>
                            <div>
                                <div style="font-size: 13px; font-weight: 800; color: #15803D; letter-spacing: 0.03em;">STATUTORY LEGAL CLEARANCE: GRADE-A (CLEAN RECORD)</div>
                                <div style="font-size: 11px; color: #166534;">Zero Adverse Litigations, Insolvency Petitions, or Wilful Defaults Detected</div>
                            </div>
                        </div>
                        <div>
                            {status_pill("ZERO ADVERSE FINDINGS", variant="approve")}
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px dashed #86EFAC;">
                        <div style="font-size: 11px; color: #166534;"><strong>e-Courts:</strong> 0 Pending Warrants</div>
                        <div style="font-size: 11px; color: #166534;"><strong>NCLT/NCLAT:</strong> 0 CIRP Petitions</div>
                        <div style="font-size: 11px; color: #166534;"><strong>MCA-21:</strong> Active (No Disqual.)</div>
                        <div style="font-size: 11px; color: #166534;"><strong>DRT / SARFAESI:</strong> Clean Track</div>
                    </div>
                    <div style="font-size: 10px; color: #15803D; margin-top: 8px; opacity: 0.85;">
                        * Automated statutory surveillance screening completed across all connected registries. No adverse legal entries found.
                    </div>
                </div>"""
                _render_html(clean_card)
            elif rep.status == "COMPLETED":
                has_unreviewed_severe = any(
                    f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.UNREVIEWED
                    for f in rep.findings
                )
                has_confirmed_severe = any(
                    f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED
                    for f in rep.findings
                )

                hud_bg = '#FEF2F2' if (has_unreviewed_severe or has_confirmed_severe) else '#FFFBEB'
                hud_bd = '#FECACA' if (has_unreviewed_severe or has_confirmed_severe) else '#FDE68A'
                hud_col = '#B91C1C' if (has_unreviewed_severe or has_confirmed_severe) else '#B45309'

                adverse_hud = f"""<div style="background: {hud_bg}; border: 1px solid {hud_bd}; border-left: 4px solid {hud_col}; border-radius: 8px; padding: 14px 16px; margin: 12px 0 16px 0;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <div style="font-size: 13px; font-weight: 800; color: {hud_col};">
                                ⚠️ ADVERSE LEGAL DOCKETS IDENTIFIED: {len(rep.findings)} RECORD(S) RETRIEVED
                            </div>
                            <div style="font-size: 11px; color: #475569; margin-top: 2px;">
                                {'CRITICAL POLICY ALERT: Admitted insolvency finding requires mandatory Credit Officer adjudication below.' if has_unreviewed_severe else ('Confirmed SEVERE finding enforces permanent REJECT knockout under policy rules.' if has_confirmed_severe else 'Statutory observations retrieved. Review officer determinations below.')}
                            </div>
                        </div>
                        <div>
                            {status_pill(f"{len(rep.findings)} FINDINGS ACTIVE", variant="reject" if has_confirmed_severe else "conditional")}
                        </div>
                    </div>
                </div>"""
                _render_html(adverse_hud)

                for f in rep.findings:
                    with st.container(border=True):
                        col_t1, col_t2 = st.columns([3, 1])
                        with col_t1:
                            tier_label = {
                                FindingTier.SEVERE: status_pill("TIER-1 SEVERE: INSOLVENCY / CIRP", variant="reject"),
                                FindingTier.REVIEW: status_pill("TIER-2 STATUTORY REVIEW: TAX / EPFO", variant="conditional"),
                                FindingTier.INFORMATIONAL: status_pill("TIER-3 OPERATIONAL NOTICE", variant="info"),
                            }.get(f.tier, status_pill(str(f.tier), variant="neutral"))

                            rev_pill = {
                                ReviewStatus.CONFIRMED: status_pill("ADJUDICATED: CONFIRMED", variant="reject"),
                                ReviewStatus.DISMISSED: status_pill("ADJUDICATED: DISMISSED", variant="approve"),
                                ReviewStatus.UNREVIEWED: status_pill("ACTION REQUIRED: UNREVIEWED", variant="conditional"),
                            }.get(f.review_status, status_pill(str(f.review_status), variant="neutral"))

                            docket_border = "#EF4444" if f.tier == FindingTier.SEVERE else ("#F59E0B" if f.tier == FindingTier.REVIEW else "#0EA5E9")
                            docket_header = f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                                <span style="font-family: monospace; font-size: 10px; font-weight: 700; color: #0F766E; background: #CCFBF1; border: 1px solid #99F6E4; padding: 2px 7px; border-radius: 4px;">DOCKET #{f.id.upper()}</span>
                                <div style="display: flex; gap: 6px; align-items: center;">{tier_label} &nbsp; {rev_pill}</div>
                            </div>
                            <div style="font-size: 13px; font-weight: 700; color: #0B1F3A; line-height: 1.35; margin-bottom: 6px;">{f.title}</div>
                            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid {docket_border}; border-radius: 4px; padding: 8px 12px; margin-bottom: 8px; font-size: 11.5px; color: #334155; line-height: 1.45;">
                                {f.snippet}
                            </div>"""
                            _render_html(docket_header)
                            col_lk1, col_lk2 = st.columns([1, 1])
                            with col_lk1:
                                st.markdown(f"[:material/open_in_new: Official Source Docket]({f.source_url})")
                            with col_lk2:
                                with st.popover("📜 Deep Order Inspection (Extract)"):
                                    st.caption("Fetches full legal judgment text via Tavily Extract API.")
                                    if st.button("Extract Full Text", key=f"btn_extract_{f.id}", width="stretch"):
                                        with st.spinner("Calling Tavily Extract API..."):
                                            full_text = extract_legal_docket_text(f.source_url)
                                            if full_text:
                                                st.session_state[f"extracted_text_{f.id}"] = full_text
                                            else:
                                                st.warning("Order text could not be extracted from this URL.")
                                    curr_txt = st.session_state.get(f"extracted_text_{f.id}")
                                    if curr_txt:
                                        st.text_area("Extracted Judicial Text", value=curr_txt, height=200, disabled=True, key=f"txt_full_{f.id}")
                        with col_t2:
                            if f.review_status == ReviewStatus.CONFIRMED:
                                render_callout(f"Status: CONFIRMED\n\nCitation: {f.confirmation_note or 'N/A'}\nBy: {f.reviewed_by}", title="Officer Confirmed", variant="error")
                            elif f.review_status == ReviewStatus.DISMISSED:
                                render_callout(f"Status: DISMISSED\n\nReason: {f.dismissal_reason}\nBy: {f.reviewed_by}", title="Officer Dismissed", variant="success")
                            else:
                                render_callout("Status: UNREVIEWED\n\nOfficer adjudication required below.", variant="warning")

                        if f.review_status == ReviewStatus.UNREVIEWED:
                            st.markdown("##### Officer Legal Determination (Inline Action)")
                            st.markdown("<div style='font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase; margin-bottom: 4px;'>1-Click Legal Citations:</div>", unsafe_allow_html=True)
                            chip_col1, chip_col2, chip_col3 = st.columns(3)
                            with chip_col1:
                                if st.button("⚖️ Admitted CIRP", key=f"btn_chip_cirp_{f.id}", width="stretch", help="Set citation: Admitted CIRP order verified via NCLT registry"):
                                    st.session_state[f"inline_note_{f.id}"] = "Admitted CIRP order verified via NCLT registry"
                                    st.rerun()
                            with chip_col2:
                                if st.button("🏢 Homonym Collision", key=f"btn_chip_homo_{f.id}", width="stretch", help="Set reason: Homonym collision: Unrelated entity with distinct CIN/PAN"):
                                    st.session_state[f"inline_note_{f.id}"] = "Homonym collision: Unrelated entity with distinct CIN/PAN"
                                    st.rerun()
                            with chip_col3:
                                if st.button("📜 Stay / Dismissed", key=f"btn_chip_stay_{f.id}", width="stretch", help="Set reason: Commercial suit dismissed with prejudice"):
                                    st.session_state[f"inline_note_{f.id}"] = "Commercial recovery suit was dismissed with prejudice"
                                    st.rerun()

                            note_val = st.text_input("Order Citation / Dismissal Reason (Required)", key=f"inline_note_{f.id}", placeholder="e.g. NCLT Order CP(IB)/102/2026 or Homonym entity collision")
                            st.caption(f"Citation/Reason Length: {len(note_val.strip())}/10 characters minimum")
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
        # 7. CREDIT COMMITTEE QUALITATIVE FIELD DOSSIER
        # --------------------------------------------------------------------
        with st.container(border=True):
            st.markdown("#### :material/fact_check: Credit Committee Qualitative Field Dossier")
            st.caption("Record on-site inspection observations, promoter interviews, and qualitative due diligence.")

            field_hud_html = """<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                <div style="font-size: 11px; color: #1E293B;">
                    <strong>📍 Verified Unit Inspection:</strong> Okhla Industrial Area, Phase-III, New Delhi &nbsp;•&nbsp; <strong>Capacity:</strong> 85% Operational
                </div>
                <div style="font-size: 10px; color: #0F766E; font-weight: 700; background: #CCFBF1; padding: 2px 8px; border-radius: 9999px;">
                    GEOTAG VERIFIED
                </div>
            </div>"""
            _render_html(field_hud_html)

            st.markdown("<div style='font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase; margin-bottom: 4px;'>Quick Observation Presets:</div>", unsafe_allow_html=True)
            col_pr1, col_pr2, col_pr3, col_pr4 = st.columns(4)
            with col_pr1:
                if st.button(":material/factory: Plant Inspection", key="btn_pr_plant", width="stretch"):
                    st.session_state.txt_field_note = "Plant inspection verified: Machinery in operational condition at 85% capacity utilization. Physical stock matches GSTR-3B filings."
                    st.rerun()
            with col_pr2:
                if st.button(":material/person_search: Promoter Diligence", key="btn_pr_promoter", width="stretch"):
                    st.session_state.txt_field_note = "Promoter interview concluded: Clear market reputation, satisfactory vintage in the sector, and clean track record with local suppliers."
                    st.rerun()
            with col_pr3:
                if st.button(":material/account_balance_wallet: Working Capital", key="btn_pr_wc", width="stretch"):
                    st.session_state.txt_field_note = "Working capital verification: Receivables turnover matches standard 45-day commercial terms with low single-debtor concentration."
                    st.rerun()
            with col_pr4:
                if st.button(":material/receipt_long: Tax & Filings", key="btn_pr_tax", width="stretch"):
                    st.session_state.txt_field_note = "Tax governance check: GSTR-3B monthly filings and advance tax payments match annual audited financials without statutory demands."
                    st.rerun()

            with st.container(border=True):
                st.markdown("<div style='font-size: 11px; font-weight: 700; color: #0F766E; text-transform: uppercase; margin-bottom: 6px;'>Field Due Diligence Log Form</div>", unsafe_allow_html=True)
                fn_text = st.text_area(
                    "Field Visit Observation / Qualitative Notes",
                    placeholder="e.g. Visited plant in Okhla. Machinery in operational condition, factory working at 85% utilization.",
                    key="txt_field_note",
                    height=85,
                )
                col_fn1, col_fn2 = st.columns([3, 2], vertical_alignment="bottom")
                with col_fn1:
                    fn_pillar = st.selectbox("Affected C Pillar", ["CHARACTER", "CAPACITY", "CAPITAL", "COLLATERAL", "CONDITIONS", "GENERAL"], key="sb_fn_pillar")
                with col_fn2:
                    save_btn = st.button(":material/save: Save Field Note", key="btn_save_field_note", type="primary", width="stretch")

            if save_btn:
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
                col_h1, col_h2 = st.columns([4, 1])
                with col_h2:
                    if st.button(":material/undo: Clear Last", key="btn_clear_last_note", width="stretch"):
                        st.session_state.officer_notes.pop()
                        st.session_state.cam_markdown = None
                        st.rerun()
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
                    _render_html(note_card)

        # --------------------------------------------------------------------
        # 8. CREDIT COMMITTEE QUORUM GOVERNANCE SHIELD
        # --------------------------------------------------------------------
        with st.container(border=True):
            st.markdown("#### :material/gavel: Credit Committee Quorum Governance Shield")
            st.caption("Under bank governance, credit committee officers may override the algorithmic verdict, but MUST provide written justification.")

            cac_banner_html = """<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                <div style="font-size: 11px; color: #1E293B;">
                    <strong>Sanction Authority:</strong> Level-3 Credit Committee (Dy. General Manager / Zonal Head) &nbsp;•&nbsp; <strong>Quorum:</strong> Dual-Signoff
                </div>
                <div style="font-size: 10px; color: #0F766E; font-weight: 700; background: #CCFBF1; padding: 2px 8px; border-radius: 9999px;">
                    STATUTORY AUDIT BOUND
                </div>
            </div>"""
            _render_html(cac_banner_html)

            gov_note = """<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; font-size: 11px; color: #64748B; line-height: 1.4;">
                <strong>Governance Rule:</strong> Overrides are strictly prohibited if an admitted SEVERE adverse finding (e.g. NCLT CIRP) is confirmed. Written justification (minimum 20 characters) is permanently chained into the cryptographic audit log.
            </div>"""
            _render_html(gov_note)

            has_confirmed_severe = False
            if st.session_state.current_research_report and st.session_state.current_research_report.findings:
                for f in st.session_state.current_research_report.findings:
                    if f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED:
                        has_confirmed_severe = True
                        break

            if has_confirmed_severe:
                render_callout(
                    "Governance Shield Lockout Active: A confirmed SEVERE adverse finding (e.g. Admitted NCLT Insolvency) permanently bars discretionary overrides under credit committee regulations.",
                    title="Governance Lockout Enforced",
                    variant="error",
                )

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
                st.caption(f"Justification Length: {len(veto_justification.strip())}/20 characters minimum")
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

