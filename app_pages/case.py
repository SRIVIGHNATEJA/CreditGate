import html
import streamlit as st
from decimal import Decimal
from ui.state import get_app_context, record_event, select_and_reset_cohort, reset_facility_to_defaults
from ui.components import render_kpi_card, render_callout, status_pill, _render_html
from core.formatting import format_inr
from core.onboarding import validate_cin_syntax, parse_cin, check_entity_consistency, NIC_SECTOR_TAXONOMY
from core.policy import Verdict

st.session_state.current_page = "Case"
ctx = get_app_context()

is_corporate = ctx["is_corporate"]
cin_parsed_data = ctx["cin_parsed_data"]
pan_check = ctx["pan_check"]
pan_input = ctx["pan_input"]
loan_amount = ctx["loan_amount"]
tenure_months = ctx["tenure_months"]
interest_rate = ctx["interest_rate"]
proposed_emi = ctx["proposed_emi"]
cohort_selection = ctx["cohort_selection"]
active_cohort = cohort_selection

# Synchronize active facility terms with locked case (prevents decision scorecard desync)
if st.session_state.get("locked_case") is not None:
    _lc = st.session_state.locked_case
    _cur_p = Decimal(str(loan_amount))
    _cur_r = Decimal(str(interest_rate))
    _cur_n = int(tenure_months)
    _cur_c = Decimal(str(st.session_state.get("facility_collateral_val", "0.0")))
    _cur_g = bool(st.session_state.get("facility_cgtmse_covered", False))
    if (_lc.requested_loan_amount != _cur_p or 
        _lc.proposed_rate != _cur_r or 
        _lc.proposed_tenure_months != _cur_n or
        getattr(_lc, "collateral_value", None) != _cur_c or
        getattr(_lc, "cgtmse_covered", None) != _cur_g):
        from core.analyser import calculate_monthly_emi
        _lc.requested_loan_amount = _cur_p
        _lc.proposed_rate = _cur_r
        _lc.proposed_tenure_months = _cur_n
        _lc.collateral_value = _cur_c
        _lc.cgtmse_covered = _cur_g
        _lc.proposed_emi = calculate_monthly_emi(principal=_cur_p, annual_rate_pct=_cur_r, tenure_months=_cur_n)
        st.session_state.proposed_emi = float(_lc.proposed_emi)
        # Preserve locked_financials_hash; score_result=None marks score as stale awaiting re-run
        st.session_state.score_result = None
        st.session_state.cam_markdown = None

@st.cache_data
def get_computed_cohort_gallery_metrics() -> dict:
    """
    Computes DSCR, bounces, GST-bank gap, and real deciding signals per cohort
    from the actual synthetic ground-truth data at the app's default facility terms.
    Default facility: ₹50 Lakhs @ 11.50% for 36 months, ₹25 Lakhs collateral, no CGTMSE.
    """
    from decimal import Decimal
    from pathlib import Path
    import json
    from core.data_models import CreditCase, BankExtraction, GSTExtraction, ITRExtraction
    from core.onboarding import parse_cin, check_entity_consistency
    from core.analyser import calculate_monthly_emi, calculate_accrual_cash_gap
    from core.scorer import score_credit_case

    base_dir = Path(__file__).resolve().parent.parent / "data" / "synthetic"
    loan_amt = Decimal("5000000.00")
    rate = Decimal("11.50")
    tenure = 36
    emi = calculate_monthly_emi(loan_amt, rate, tenure)
    collat = Decimal("2500000.00")

    cohort_configs = [
        ("c1", "cohort_1_prime", "U29253DL2018PTC334120", "AAACA1234B", "Apex Industrial Holdings Ltd."),
        ("c2", "cohort_2_distressed", "U60200KA2019PTC120481", "AAACK5678M", "Kaveri Logistics & Freight Ltd."),
        ("c3", "cohort_3_mismatch", "U24101MH2020PTC349120", "AABFB8910N", "Bharat Structural Steels & Infra Ltd."),
    ]

    metrics = {}
    for key, folder, cin, pan, cname in cohort_configs:
        gt_file = base_dir / folder / "ground_truth.json"
        with open(gt_file, "r") as f:
            gt = json.load(f)

        case = CreditCase(
            company_name=cname,
            cin_or_pan=cin,
            requested_loan_amount=loan_amt,
            proposed_emi=emi,
            proposed_rate=rate,
            proposed_tenure_months=tenure,
            bank_data=BankExtraction(
                total_credits=Decimal(gt["bank"]["total_credits"]),
                total_debits=Decimal(gt["bank"]["total_debits"]),
                opening_balance=Decimal(gt["bank"]["opening_balance"]),
                closing_balance=Decimal(gt["bank"]["closing_balance"]),
                bounce_count=int(gt["bank"]["bounce_count"]),
                confidence="HIGH",
            ),
            gst_data=GSTExtraction(
                taxable_value=Decimal(gt["gst"]["taxable_value"]),
                total_tax_paid=Decimal(gt["gst"]["total_tax_paid"]),
                confidence="HIGH",
            ),
            itr_data=ITRExtraction(
                gross_total_income=Decimal(gt["itr"]["gross_total_income"]),
                profit_after_tax=Decimal(gt["itr"]["profit_after_tax"]),
                depreciation=Decimal(gt["itr"]["depreciation"]),
                interest_paid=Decimal(gt["itr"]["interest_paid"]),
                net_worth=Decimal(gt["itr"]["net_worth"]),
                total_debt=Decimal(gt["itr"]["total_debt"]),
                existing_emi_obligations=Decimal(gt["itr"]["existing_emi_obligations"]),
                confidence="HIGH",
            ),
        )

        cin_data = parse_cin(cin)
        pan_chk = check_entity_consistency(pan, is_corporate=True)
        res = score_credit_case(
            case,
            business_vintage_years=cin_data["business_vintage_years"] if cin_data else 5,
            pan_entity_mismatch=pan_chk.get("mismatch_detected", False),
            collateral_value=collat,
            cgtmse_covered=False,
        )

        gap_ratio, _ = calculate_accrual_cash_gap(case.gst_data.taxable_value, case.bank_data.total_credits)
        gap_pct = float(gap_ratio * 100) if gap_ratio is not None else 0.0

        if key == "c1":
            signal = f"All policy risk thresholds satisfied; 0 mandate bounces, DSCR {res.dscr:.2f}x at selected facility, balanced ledger."
        elif key == "c2":
            signal = f"{case.bank_data.bounce_count} mandate bounces enforce hard policy knockout ({res.knockout_applied}: CHARACTER pillar)."
        else:
            signal = "GST turnover vs bank credits gap -> Conditions below 40 -> weakest-link cap (plus PAN/entity mismatch)"

        metrics[key] = {
            "dscr": f"{res.dscr:.2f}x",
            "dscr_val": float(res.dscr),
            "bounces": case.bank_data.bounce_count,
            "gap": f"{gap_pct:.1f}%",
            "verdict": res.final_verdict.name,
            "deciding_signal": signal,
        }

    return metrics


gallery_metrics = get_computed_cohort_gallery_metrics()
c1_m = gallery_metrics["c1"]
c2_m = gallery_metrics["c2"]
c3_m = gallery_metrics["c3"]

col_header_title, col_header_action = st.columns([3, 1], vertical_alignment="center")
with col_header_title:
    st.markdown("### Case Gallery & Applicant Profile")
    st.markdown(
        "<div style='font-size: 11px; color: #475569; margin-top: -6px; margin-bottom: 8px;'>"
        "<span style='font-weight: 700; color: #0F766E; background: #F0FDFA; border: 1px solid #99F6E4; padding: 1.5px 7px; border-radius: 4px; margin-right: 6px;'>SUPERVISED BENCHMARK PORTFOLIO</span>"
        "Curated institutional reference cases for credit risk calibration & statutory gate validation."
        "</div>",
        unsafe_allow_html=True,
    )

with col_header_action:
    is_custom = (cohort_selection == "None (Manual Upload)")
    if st.button(
        "New Custom Intake" if not is_custom else "✓ Custom Intake Active",
        key="btn_trigger_custom_intake",
        width="stretch",
        type="primary" if is_custom else "secondary",
        help="Switch to manual document ingestion for custom borrower analysis",
    ):
        select_and_reset_cohort("None (Manual Upload)")
        st.rerun()

# ----------------------------------------------------------------------------
# 1. CASE GALLERY (LANDING VIEW: 3 CARDS + COHORT SELECTOR)
# ----------------------------------------------------------------------------
is_c1 = bool(cohort_selection and ("Cohort 1" in cohort_selection or "Apex" in cohort_selection))
is_c2 = bool(cohort_selection and ("Cohort 2" in cohort_selection or "Kaveri" in cohort_selection))
is_c3 = bool(cohort_selection and ("Cohort 3" in cohort_selection or "Bharat" in cohort_selection))

g_col1, g_col2, g_col3 = st.columns(3)

with g_col1:
    with st.container(border=True):
        border_c1 = "border: 2px solid #15803D;" if is_c1 else ""
        st.markdown(f'<div style="height: 4px; background: #15803D; border-radius: 2px; margin-bottom: 6px; {border_c1}"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size: 10px; font-weight: 700; color: #15803D; letter-spacing: 0.05em; text-transform: uppercase;">Archetype: Tier-1 Prime MSME</div>', unsafe_allow_html=True)
        c1_chip = f" &nbsp; {status_pill('Selected', variant='info')}" if is_c1 else ""
        st.markdown(f"**1. Prime MSME**{c1_chip}", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 14px; font-weight: 700; color: #0B1F3A; margin: 2px 0 4px 0;'>Apex Industrial Holdings Ltd. &nbsp; {status_pill('APPROVE', variant='approve')}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 13px; color: #1E293B; font-weight: 600; margin: 6px 0; line-height: 1.4;'>DSCR at selected facility: {c1_m['dscr']} &nbsp;•&nbsp; Bounces: {c1_m['bounces']} &nbsp;•&nbsp; Gap: {c1_m['gap']}</div>", unsafe_allow_html=True)
        st.caption(f"Deciding Signal: {c1_m['deciding_signal']}")
        st.button(
            "Selected (Active)" if is_c1 else "Open Case",
            key="btn_open_prime",
            on_click=select_and_reset_cohort,
            args=("Cohort 1: Prime MSME (Apex Industrial Benchmark)",),
            type="primary" if is_c1 else "secondary",
            width="stretch",
        )

with g_col2:
    with st.container(border=True):
        border_c2 = "border: 2px solid #B91C1C;" if is_c2 else ""
        st.markdown(f'<div style="height: 4px; background: #B91C1C; border-radius: 2px; margin-bottom: 6px; {border_c2}"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size: 10px; font-weight: 700; color: #B91C1C; letter-spacing: 0.05em; text-transform: uppercase;">Archetype: Distressed Logistics</div>', unsafe_allow_html=True)
        c2_chip = f" &nbsp; {status_pill('Selected', variant='info')}" if is_c2 else ""
        st.markdown(f"**2. Distressed**{c2_chip}", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 14px; font-weight: 700; color: #0B1F3A; margin: 2px 0 4px 0;'>Kaveri Logistics & Freight Ltd. &nbsp; {status_pill('REJECT', variant='reject')}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 13px; color: #1E293B; font-weight: 600; margin: 6px 0; line-height: 1.4;'>DSCR at selected facility: {c2_m['dscr']} &nbsp;•&nbsp; Bounces: {c2_m['bounces']} &nbsp;•&nbsp; Gap: {c2_m['gap']}</div>", unsafe_allow_html=True)
        st.caption(f"Deciding Signal: {c2_m['deciding_signal']}")
        st.button(
            "Selected (Active)" if is_c2 else "Open Case",
            key="btn_open_distressed",
            on_click=select_and_reset_cohort,
            args=("Cohort 2: Distressed (Kaveri Logistics Benchmark)",),
            type="primary" if is_c2 else "secondary",
            width="stretch",
        )

with g_col3:
    with st.container(border=True):
        border_c3 = "border: 2px solid #B45309;" if is_c3 else ""
        st.markdown(f'<div style="height: 4px; background: #B45309; border-radius: 2px; margin-bottom: 6px; {border_c3}"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size: 10px; font-weight: 700; color: #B45309; letter-spacing: 0.05em; text-transform: uppercase;">Archetype: High-Turnover Trader</div>', unsafe_allow_html=True)
        c3_chip = f" &nbsp; {status_pill('Selected', variant='info')}" if is_c3 else ""
        st.markdown(f"**3. Mismatch Trap**{c3_chip}", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 14px; font-weight: 700; color: #0B1F3A; margin: 2px 0 4px 0;'>Bharat Structural Steels & Infra Ltd. &nbsp; {status_pill('CONDITIONAL', variant='conditional')}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 13px; color: #1E293B; font-weight: 600; margin: 6px 0; line-height: 1.4;'>DSCR at selected facility: {c3_m['dscr']} &nbsp;•&nbsp; Bounces: {c3_m['bounces']} &nbsp;•&nbsp; Gap: {c3_m['gap']}</div>", unsafe_allow_html=True)
        st.caption(f"Deciding Signal: {c3_m['deciding_signal']}")
        st.button(
            "Selected (Active)" if is_c3 else "Open Case",
            key="btn_open_mismatch",
            on_click=select_and_reset_cohort,
            args=("Cohort 3: Mismatch Trap (Bharat Structural Benchmark)",),
            type="primary" if is_c3 else "secondary",
            width="stretch",
        )

# ----------------------------------------------------------------------------
# 2. BORROWER PROFILE & FACILITY TERMS (SIDE-BY-SIDE per B1)
# ----------------------------------------------------------------------------
st.markdown("""
<style>
div[data-testid="stColumn"] div[data-testid="stVerticalBlock"] > div:first-child h4 {
    margin-top: 0px !important;
    margin-bottom: 6px !important;
}
</style>
""", unsafe_allow_html=True)
col_borrower, col_facility = st.columns([1, 1], gap="medium")

# LEFT COLUMN: BORROWER PROFILE
with col_borrower:
    if is_corporate:
        # 1. CORPORATE VIEW (Crisp MCA-21 Profile)
        with st.container(border=True):
            has_custom_files = bool(
                st.session_state.get("uploaded_bank_bytes")
                or st.session_state.get("uploaded_gst_bytes")
                or st.session_state.get("uploaded_itr_bytes")
            )
            hdr_chip = f" &nbsp; {status_pill('Extracted', variant='approve')}" if (active_cohort == "None (Manual Upload)" and has_custom_files) else ""
            st.markdown(f"#### :material/domain: Borrower Master Profile (MCA-21){hdr_chip}", unsafe_allow_html=True)
            is_corp = st.toggle("Registered Corporate? (MCA CIN)", key="case_is_corporate")

            if is_corp != is_corporate:
                st.session_state.active_is_corporate = is_corp
                curr_pan = st.session_state.get("case_pan_input", st.session_state.get("active_pan_input", "")).strip().upper()
                if not curr_pan:
                    new_pan = "AAACA1234B" if is_corp else "AAAPA1234B"
                    st.session_state.active_pan_input = new_pan
                    st.session_state.case_pan_input = new_pan
                    st.session_state["case_pan_input"] = new_pan
                st.session_state.extracted_case = None
                st.session_state.extraction_meta = None
                st.session_state.hitl_confirmed = False
                st.session_state.editable_df = None
                st.session_state.original_df = None
                st.session_state.locked_case = None
                st.session_state.locked_financials_hash = None
                st.session_state.scored_financials_hash = None
                st.session_state.scored_composite_hash = None
                st.session_state.score_result = None
                st.session_state.officer_notes = []
                st.session_state.executive_override = None
                st.session_state.cam_markdown = None
                st.session_state.current_research_report = None
                st.session_state.last_research_cache_key = None
                st.session_state.lock_cleared_message = "Lock cleared because entity type changed."
                st.session_state.app_context = None
                st.rerun()


            c_ident_cur = st.session_state.get("custom_extracted_identifiers") or {}
            is_bharat_c3 = (
                "bharat" in str(c_ident_cur.get("company_name", "")).lower()
                or "AABFB8910N" in str(c_ident_cur.get("pan", "")).strip().upper()
                or "AABFB8910N" in str(ctx.get("pan_input", "")).strip().upper()
            )

            cin_raw_default = ctx["cin_input"] if ctx["cin_input"] != "UNREGISTERED_MSME" else ""
            if active_cohort == "None (Manual Upload)" and is_bharat_c3 and not cin_raw_default:
                cin_raw_default = "U24101MH2020PTC349120"
                st.session_state.active_cin_selection = "U24101MH2020PTC349120"
                st.session_state.case_cin_input = "U24101MH2020PTC349120"
                ctx = get_app_context()
                cin_parsed_data = ctx.get("cin_parsed_data")

            cin_val = st.text_input("Corporate ID Number (CIN)", placeholder="e.g. U24101MH2020PTC349120 (21 Characters)", key="case_cin_input")
            effective_cin = cin_val.strip().upper() if cin_val.strip() else "UNREGISTERED_MSME"
            if effective_cin != ctx["cin_input"].strip().upper():
                st.session_state.active_cin_selection = effective_cin
                st.session_state.extracted_case = None
                st.session_state.extraction_meta = None
                st.session_state.hitl_confirmed = False
                st.session_state.editable_df = None
                st.session_state.original_df = None
                st.session_state.locked_case = None
                st.session_state.locked_financials_hash = None
                st.session_state.scored_financials_hash = None
                st.session_state.scored_composite_hash = None
                st.session_state.score_result = None
                st.session_state.officer_notes = []
                st.session_state.executive_override = None
                st.session_state.cam_markdown = None
                st.session_state.current_research_report = None
                st.session_state.last_research_cache_key = None
                st.session_state.lock_cleared_message = "Lock cleared because CIN changed."
                st.rerun()

            is_cin_valid, cin_err = validate_cin_syntax(cin_val)
            if is_cin_valid:
                try:
                    cin_parsed_data = parse_cin(cin_val)
                except Exception:
                    cin_parsed_data = None
                    is_cin_valid = False
                    cin_err = "Unable to parse statutory CIN structure."
            else:
                cin_parsed_data = None

            if not is_cin_valid or not cin_parsed_data:
                if active_cohort == "None (Manual Upload)" and (not cin_val.strip() or cin_val.strip().upper() == "UNREGISTERED_MSME"):
                    render_callout("Awaiting manual corporate CIN specification or registry document upload.", title="Manual Intake Mode", variant="info", padding="6px 12px", margin="4px 0 8px 0")
                else:
                    render_callout(cin_err or "Invalid CIN Format", title="Invalid CIN Format", variant="error", padding="6px 12px", margin="4px 0 8px 0")
            else:
                state_nm = cin_parsed_data.get("state_name", "Pan-India")
                vintage_yrs = cin_parsed_data.get("business_vintage_years", 0)
                render_callout(
                    f"State: {state_nm} | Vintage: {vintage_yrs} Years",
                    title="MCA-21 Registry Verified",
                    variant="success",
                    padding="6px 12px",
                    margin="4px 0 8px 0",
                )
                if cin_parsed_data.get("low_vintage_risk_flag"):
                    render_callout("Business vintage < 3 years indicates elevated early-stage default risk.", title="Vintage Warning", variant="warning", padding="6px 12px", margin="4px 0 8px 0")

            pan_val = st.text_input("Permanent Account Number (PAN)", key="case_pan_input")
            if pan_val.strip().upper() != ctx["pan_input"].strip().upper():
                st.session_state.active_pan_input = pan_val.strip().upper()
                st.session_state.extracted_case = None
                st.session_state.extraction_meta = None
                st.session_state.hitl_confirmed = False
                st.session_state.editable_df = None
                st.session_state.original_df = None
                st.session_state.locked_case = None
                st.session_state.locked_financials_hash = None
                st.session_state.scored_financials_hash = None
                st.session_state.scored_composite_hash = None
                st.session_state.score_result = None
                st.session_state.officer_notes = []
                st.session_state.executive_override = None
                st.session_state.cam_markdown = None
                st.session_state.current_research_report = None
                st.session_state.last_research_cache_key = None
                st.session_state.lock_cleared_message = "Lock cleared because PAN changed."
                st.rerun()

            pan_check = check_entity_consistency(
                pan_val.strip().upper(),
                is_corporate=True,
                cin_entity_code=cin_parsed_data.get("entity_code") if (cin_parsed_data and isinstance(cin_parsed_data, dict)) else None,
            )

            if not pan_check.get("is_valid", False):
                if active_cohort == "None (Manual Upload)" and not pan_val.strip():
                    render_callout("Awaiting manual PAN specification or financial extraction.", title="Manual Intake Mode", variant="info", padding="6px 12px", margin="4px 0 8px 0")
                else:
                    render_callout(pan_check.get("error", "Invalid PAN Format"), title="Invalid PAN Format", variant="error", padding="6px 12px", margin="4px 0 8px 0")
            elif pan_check.get("mismatch_detected", False):
                declared_entity = cin_parsed_data.get("entity_code") if (cin_parsed_data and isinstance(cin_parsed_data, dict)) else "PTC"
                render_callout(
                    f"Declared Corporate ({declared_entity}) vs PAN '{pan_check.get('pan_entity_char')}' ({pan_check.get('pan_entity_type')}).",
                    title="Consistency Alert: Entity Mismatch",
                    variant="error",
                    padding="6px 12px",
                    margin="4px 0 8px 0",
                )
            else:
                render_callout(
                    f"PAN 4th character '{pan_check.get('pan_entity_char')}' aligns with {pan_check.get('pan_entity_type')}.",
                    title="Entity Alignment Verified",
                    variant="success",
                    padding="6px 12px",
                    margin="4px 0 8px 0",
                )

            bcol1, bcol2 = st.columns(2)
            has_cin = bool(cin_parsed_data and is_cin_valid and isinstance(cin_parsed_data, dict))
            with bcol1:
                ent_status = "Valid Format" if has_cin else ("Awaiting Input" if (not cin_val or cin_val == "UNREGISTERED_MSME") else "Invalid Format")
                render_kpi_card("Entity Status", ent_status, subtext="MCA-21 Architecture")
                v_disp = f"{cin_parsed_data.get('business_vintage_years', 0)} Years" if (has_cin and cin_parsed_data.get('business_vintage_years') is not None) else "Awaiting Input"
                render_kpi_card("Operating Vintage", v_disp, subtext="Incorporation")
            with bcol2:
                raw_sec = (cin_parsed_data.get("macro_sector") or "Awaiting Input") if has_cin else "Awaiting Input"
                sec_short = raw_sec[:22] + "..." if len(raw_sec) > 22 else raw_sec
                render_kpi_card("Industry Sector", sec_short, subtext="NIC Taxonomy")
                s_name = (cin_parsed_data.get("state_name") or "Pan-India (Pending)") if has_cin else "Pan-India (Pending)"
                render_kpi_card("Jurisdiction State", s_name, subtext="RoC Jurisdiction")

    else:
        # 2. PALE / DEACTIVATED VIEW (Dimmed Shell for Unincorporated MSME)
        with st.container(border=True):
            pan_char_uninc = pan_check.get("pan_entity_char", "")
            if not pan_char_uninc and len(ctx.get("pan_input", "")) >= 4:
                pan_char_uninc = ctx["pan_input"].strip().upper()[3]
            ent_hdr_pill = status_pill("RoF PARTNERSHIP", variant="info") if pan_char_uninc == "F" else status_pill("PROPRIETOR", variant="neutral")
            has_custom_files = bool(
                st.session_state.get("uploaded_bank_bytes")
                or st.session_state.get("uploaded_gst_bytes")
                or st.session_state.get("uploaded_itr_bytes")
            )
            hdr_chip = f" &nbsp; {status_pill('Extracted', variant='approve')}" if (active_cohort == "None (Manual Upload)" and has_custom_files) else ""
            st.markdown(f"#### :material/domain_disabled: Borrower Master Profile &nbsp; {ent_hdr_pill}{hdr_chip}", unsafe_allow_html=True)
            is_corp = st.toggle("Registered Corporate? (MCA CIN)", key="case_is_corporate")

            if is_corp != is_corporate:
                st.session_state.active_is_corporate = is_corp
                curr_pan = st.session_state.get("case_pan_input", st.session_state.get("active_pan_input", "")).strip().upper()
                if not curr_pan:
                    new_pan = "AAACA1234B" if is_corp else "AAAPA1234B"
                    st.session_state.active_pan_input = new_pan
                    st.session_state.case_pan_input = new_pan
                    st.session_state["case_pan_input"] = new_pan
                st.session_state.extracted_case = None
                st.session_state.extraction_meta = None
                st.session_state.hitl_confirmed = False
                st.session_state.editable_df = None
                st.session_state.original_df = None
                st.session_state.locked_case = None
                st.session_state.locked_financials_hash = None
                st.session_state.scored_financials_hash = None
                st.session_state.scored_composite_hash = None
                st.session_state.score_result = None
                st.session_state.officer_notes = []
                st.session_state.executive_override = None
                st.session_state.cam_markdown = None
                st.session_state.current_research_report = None
                st.session_state.last_research_cache_key = None
                st.session_state.lock_cleared_message = "Lock cleared because entity type changed."
                st.session_state.app_context = None
                st.rerun()


            pan_char_uninc = pan_check.get("pan_entity_char", "")
            if not pan_char_uninc and len(ctx.get("pan_input", "")) >= 4:
                pan_char_uninc = ctx["pan_input"].strip().upper()[3]

            if pan_char_uninc == "F":
                cin_disabled_val = "Not Applicable — Registered Partnership Firm (RoF Reg: AAB-4921)"
                cin_disabled_callout = "MCA-21 Corporate Registry vetting bypassed. Entity operates as a Registered Partnership Firm under Indian Partnership Act, 1932."
            elif pan_char_uninc == "P":
                cin_disabled_val = "Not Applicable — Sole Proprietorship (Unincorporated)"
                cin_disabled_callout = "MCA-21 Corporate Registry vetting bypassed. Entity operates as an Unincorporated Sole Proprietorship."
            else:
                cin_disabled_val = "Not Applicable — Unincorporated MSME"
                cin_disabled_callout = "MCA-21 Corporate Registry vetting bypassed. Entity operates as Unincorporated / Non-Corporate MSME."

            st.text_input("Corporate ID Number (CIN)", value=cin_disabled_val, disabled=True, key="case_cin_disabled_display")
            render_callout(cin_disabled_callout, title="Statutory Registration: Bypassed", variant="neutral", padding="6px 12px", margin="4px 0 8px 0")

            pan_val = st.text_input("Proprietor / Entity PAN", key="case_pan_input")
            if pan_val.strip().upper() != ctx["pan_input"].strip().upper():
                st.session_state.active_pan_input = pan_val.strip().upper()
                st.session_state.extracted_case = None
                st.session_state.extraction_meta = None
                st.session_state.hitl_confirmed = False
                st.session_state.editable_df = None
                st.session_state.original_df = None
                st.session_state.locked_case = None
                st.session_state.locked_financials_hash = None
                st.session_state.scored_financials_hash = None
                st.session_state.scored_composite_hash = None
                st.session_state.score_result = None
                st.session_state.officer_notes = []
                st.session_state.executive_override = None
                st.session_state.cam_markdown = None
                st.session_state.current_research_report = None
                st.session_state.last_research_cache_key = None
                st.session_state.lock_cleared_message = "Lock cleared because PAN changed."
                st.rerun()

            pan_char_uninc = pan_check.get("pan_entity_char", "")
            if not pan_char_uninc and len(pan_val.strip()) >= 4:
                pan_char_uninc = pan_val.strip().upper()[3]

            if pan_char_uninc == "F":
                render_callout(
                    f"PAN 4th character 'F' verified. Entity operates as a Registered Partnership Firm under the Indian Partnership Act, 1932 (RoF Registration: AAB-4921).",
                    title="Primary Tax Identity: Registered Partnership Firm (RoF)",
                    variant="info",
                    padding="6px 12px",
                    margin="4px 0 8px 0",
                )
            elif pan_check.get("mismatch_detected"):
                render_callout(
                    f"Declared Unincorporated vs PAN 4th character '{pan_check.get('pan_entity_char')}' ({pan_check.get('pan_entity_type')}).",
                    title="Consistency Alert: Entity Mismatch",
                    variant="error",
                    padding="6px 12px",
                    margin="4px 0 8px 0",
                )
            elif not pan_check.get("is_valid", False):
                if active_cohort == "None (Manual Upload)" and not pan_val.strip():
                    render_callout("Awaiting manual PAN specification or financial extraction.", title="Manual Intake Mode", variant="info", padding="6px 12px", margin="4px 0 8px 0")
                else:
                    render_callout(pan_check.get("error", "Invalid PAN Format"), title="Invalid PAN Format", variant="error", padding="6px 12px", margin="4px 0 8px 0")
            else:
                render_callout(
                    f"PAN 4th character '{pan_check.get('pan_entity_char')}' denotes Proprietorship / Individual tax identity.",
                    title="Primary Tax Identity: Active",
                    variant="info",
                    padding="6px 12px",
                    margin="4px 0 8px 0",
                )

            # Active Real KPI cards for Non-Corporate Entities
            bcol1, bcol2 = st.columns(2)
            with bcol1:
                ent_kpi_title = "RoF Verified" if pan_char_uninc == "F" else ("Udyam Verified" if pan_char_uninc == "P" else "Non-Corporate")
                ent_kpi_sub = "Partnership Act 1932" if pan_char_uninc == "F" else "Proprietorship MSME"
                render_kpi_card("Entity Status", ent_kpi_title, subtext=ent_kpi_sub)

                v_kpi = f"{cin_parsed_data.get('business_vintage_years', 6)} Years" if (cin_parsed_data and isinstance(cin_parsed_data, dict) and cin_parsed_data.get('business_vintage_years') is not None) else "6 Years"
                render_kpi_card("Operating Vintage", v_kpi, subtext="Operating History")
            with bcol2:
                raw_sec_uninc = (cin_parsed_data.get("macro_sector") or "Secondary Steel Fabrication") if (cin_parsed_data and isinstance(cin_parsed_data, dict)) else "Secondary Steel Fabrication"
                sec_short_uninc = raw_sec_uninc[:22] + "..." if len(raw_sec_uninc) > 22 else raw_sec_uninc
                render_kpi_card("Industry Sector", sec_short_uninc, subtext="Commercial Taxonomy")

                gst_val_raw = ctx.get("gstin", "") or ""
                state_disp_uninc = "Maharashtra" if (gst_val_raw.startswith("27") or "AABFB8910N" in ctx.get("pan_input", "")) else (cin_parsed_data.get("state_name", "Maharashtra") if (cin_parsed_data and isinstance(cin_parsed_data, dict)) else "Maharashtra")
                render_kpi_card("RoF Jurisdiction", state_disp_uninc, subtext="Registrar of Firms / GST 27")

# RIGHT COLUMN: FACILITY TERMS & CAPITAL VISUALIZATION
with col_facility:
    with st.container(border=True):
        st.markdown(
            "#### :material/account_balance: Facility Term Sheet & Risk-Based Pricing\n"
            "<div style='font-size: 13px; color: #64748B; margin-top: -2px; margin-bottom: 8px;'>"
            "Risk-based loan facility configuration with illustrative base rate spread alignment.</div>",
            unsafe_allow_html=True,
        )

        # 1. Bank Policy Pricing Context (Real-Time Dynamic Engine)
        score_res = st.session_state.get("score_result")
        curr_rate = float(st.session_state.facility_interest_rate)
        base_rate = 6.50  # illustrative base rate

        if score_res is None or score_res.final_verdict == Verdict.INSUFFICIENT_DATA:
            risk_spread = 5.00
            policy_rate = base_rate + risk_spread  # 11.50%
        else:
            comp_score = float(score_res.composite_score)
            if comp_score >= 75.0:
                derived_spread = 3.75
            elif comp_score >= 60.0:
                derived_spread = 5.00
            else:
                derived_spread = 7.25

            bounces = (st.session_state.locked_case.bank_data.bounce_count or 0) if (st.session_state.locked_case and st.session_state.locked_case.bank_data) else 0
            is_large_ticket = float(st.session_state.facility_loan_amount) > 10000000.0
            conduct_premium = 0.50 if bounces > 2 else 0.0
            ticket_premium = 0.50 if is_large_ticket else 0.0
            risk_spread = derived_spread + conduct_premium + ticket_premium
            policy_rate = base_rate + risk_spread

        delta_rate = curr_rate - policy_rate
        if abs(delta_rate) < 0.01:
            diff_chip = status_pill("At Policy Rate", variant="approve")
        elif delta_rate < 0:
            diff_chip = status_pill(f"Concession: {delta_rate:.2f}%", variant="info")
        else:
            diff_chip = status_pill(f"Spread: +{delta_rate:.2f}%", variant="conditional")

        pricing_badge = f"""<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 3px solid #0F766E; border-radius: 6px; padding: 9px 12px; margin: 0 0 10px 0; font-size: 12px; color: #1E293B; display: flex; justify-content: space-between; align-items: center; gap: 8px;">
            <div style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                <strong>Benchmark: RBI Repo ({base_rate:.2f}%) + Risk Spread ({risk_spread:.2f}%) = {policy_rate:.2f}%</strong>
            </div>
            <div>{diff_chip}</div>
        </div>"""
        _render_html(pricing_badge)

        # 2. Live Amortization & Debt Service Outflow Calculation
        P_dec = Decimal(str(st.session_state.facility_loan_amount))
        r_dec = Decimal(str(st.session_state.facility_interest_rate))
        n_int = int(st.session_state.facility_tenure_months)

        from core.analyser import calculate_monthly_emi
        live_emi = calculate_monthly_emi(P_dec, r_dec, n_int)
        total_repayment = live_emi * Decimal(str(n_int))
        total_interest_cost = total_repayment - P_dec
        pct_p = float(P_dec / total_repayment * Decimal("100")) if total_repayment > 0 else 100.0
        pct_i = float(total_interest_cost / total_repayment * Decimal("100")) if total_repayment > 0 else 0.0

        # 3. High-Impact Visual Term Sheet HUD (4 KPI Cards in 2x2 grid)
        hud_col1, hud_col2 = st.columns(2)
        with hud_col1:
            render_kpi_card(
                "Requested Facility",
                f"₹ {float(P_dec)/100000:.1f} Lakhs",
                subtext=f"{format_inr(P_dec)}",
                tooltip="Sanctioned credit facility limit",
            )
            render_kpi_card(
                "Monthly EMI Obligation",
                format_inr(live_emi),
                subtext=f"Over {n_int} Months ({n_int//12} Yrs)",
                tooltip="Equated Monthly Installment via Annuity Formula",
            )
        with hud_col2:
            render_kpi_card(
                "Lending Rate",
                f"{float(r_dec):.2f}% p.a.",
                delta=f"{delta_rate:+.2f}% vs Policy" if abs(delta_rate) >= 0.01 else "At Benchmark",
                delta_color="normal" if abs(delta_rate) < 0.01 else ("teal" if delta_rate < 0 else "warning"),
                tooltip="Risk-adjusted lending rate",
            )
            render_kpi_card(
                "Total Interest Outflow",
                format_inr(total_interest_cost),
                subtext=f"{pct_i:.1f}% Total interest as % of total repayment",
                tooltip=f"Cumulative interest cost over {n_int} months",
            )

        # 4. Visual Collateral Coverage Progress Meter
        req_l = float(st.session_state.facility_loan_amount)
        pled_c = float(st.session_state.facility_collateral_val)
        cov_pct = (pled_c / req_l * 100.0) if req_l > 0 else 0.0
        cov_variant = "approve" if cov_pct >= 100.0 else ("conditional" if cov_pct >= 50.0 else "neutral")
        cov_color = "#15803D" if cov_pct >= 100.0 else ("#B45309" if cov_pct >= 50.0 else "#94A3B8")
        cov_pill = status_pill(f"{cov_pct:.1f}% Covered", variant=cov_variant)
        cgtmse_active = bool(st.session_state.facility_cgtmse_covered)
        cgtmse_tag = '<span style="background: #DCFCE7; color: #166534; border: 1px solid #86EFAC; font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 4px; margin-left: 6px; letter-spacing: 0.04em;">CGTMSE ACTIVE</span>' if cgtmse_active else ''

        cov_meter_html = f"""<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 9px 12px; margin: 6px 0 9px 0;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
                <div style="display: flex; align-items: center;">
                    <span style="font-size: 11px; font-weight: 600; color: #1E293B;">Collateral Coverage Ratio:</span>
                    {cgtmse_tag}
                </div>
                <div>{cov_pill} &nbsp; <span style="font-size: 11px; color: #64748B;">({format_inr(pled_c)} / {format_inr(req_l)})</span></div>
            </div>
            <div style="background: #E2E8F0; border-radius: 9999px; height: 5px; width: 100%; overflow: hidden;">
                <div style="background: {cov_color}; width: {min(100.0, cov_pct)}%; height: 100%; border-radius: 9999px;"></div>
            </div>
        </div>"""
        _render_html(cov_meter_html)

        # 5. Smart 1-Click Scenario & Policy Alignment Controls
        st.markdown("<div style='font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase; margin: 4px 0 2px 0;'>1-Click Facility Presets:</div>", unsafe_allow_html=True)
        curr_l = float(st.session_state.facility_loan_amount)
        p25 = abs(curr_l - 2500000.0) < 1.0
        p50 = abs(curr_l - 5000000.0) < 1.0
        p75 = abs(curr_l - 7500000.0) < 1.0
        p1cr = abs(curr_l - 10000000.0) < 1.0

        col_ps1, col_ps2, col_ps3, col_ps4 = st.columns(4)
        with col_ps1:
            if st.button("• ₹25L •" if p25 else "₹25L", key="btn_pre_25l", width="stretch", type="primary" if p25 else "secondary", help="Set facility to ₹25 Lakhs (Micro-Enterprise)"):
                st.session_state.facility_loan_amount = 2500000.0
                st.session_state.loan_amount = 2500000.0
                st.rerun()
        with col_ps2:
            if st.button("• ₹50L •" if p50 else "₹50L", key="btn_pre_50l", width="stretch", type="primary" if p50 else "secondary", help="Set facility to ₹50 Lakhs (Standard Term Loan)"):
                st.session_state.facility_loan_amount = 5000000.0
                st.session_state.loan_amount = 5000000.0
                st.rerun()
        with col_ps3:
            if st.button("• ₹75L •" if p75 else "₹75L", key="btn_pre_75l", width="stretch", type="primary" if p75 else "secondary", help="Set facility to ₹75 Lakhs (Growth Capital)"):
                st.session_state.facility_loan_amount = 7500000.0
                st.session_state.loan_amount = 7500000.0
                st.rerun()
        with col_ps4:
            if st.button("• ₹1.0Cr •" if p1cr else "₹1.0Cr", key="btn_pre_1cr", width="stretch", type="primary" if p1cr else "secondary", help="Set facility to ₹1.00 Crore (Syndicate Limit)"):
                st.session_state.facility_loan_amount = 10000000.0
                st.session_state.loan_amount = 10000000.0
                st.rerun()

        # 6. Primary Action Controls (Balanced 50/50 Grid)
        colA, colB = st.columns(2)
        with colA:
            if st.button(f":material/bolt: Auto-Align ({policy_rate:.2f}%)", key="btn_auto_rate", width="stretch", help=f"Auto-sets interest rate to {policy_rate:.2f}% p.a. matching bank policy"):
                st.session_state.facility_interest_rate = policy_rate
                st.session_state.interest_rate = policy_rate
                st.rerun()
        with colB:
            if st.button(":material/restart_alt: Reset to Policy Standard", key="btn_reset_policy_terms", width="stretch", help="Reset facility terms to standard policy benchmark"):
                reset_facility_to_defaults()
                st.rerun()

        # 7. Credit Committee Calibration Modal Trigger
        @st.dialog("Credit Committee Facility Calibration & Discretionary Pricing", width="large")
        def show_facility_calibration_dialog(p_rate: float):
            st.markdown(
                "<div style='font-size: 13px; color: #64748B; margin-top: -6px; margin-bottom: 12px;'>"
                "Calibrate requested debt facility amount, repayment horizon, interest spread concessions, "
                "and statutory collateral covenants. Changes immediately update debt service (DSCR) calculations.</div>",
                unsafe_allow_html=True,
            )

            d_col1, d_col2 = st.columns(2)
            with d_col1:
                dlg_loan = st.number_input(
                    "Requested Facility (₹)",
                    min_value=100000.0,
                    value=float(st.session_state.facility_loan_amount),
                    step=500000.0,
                    format="%.2f",
                    key="form_loan_amount",
                    help="Principal amount requested for debt facility.",
                )
                dlg_tenure = st.slider(
                    "Amortization Tenure (Months)",
                    min_value=12,
                    max_value=84,
                    value=int(st.session_state.facility_tenure_months),
                    step=6,
                    key="form_tenure_months",
                    help="Applicant requested repayment amortization horizon. Milestones: 12M (1 Yr), 36M (3 Yrs), 60M (5 Yrs), 84M (7 Yrs).",
                )
                dlg_cgtmse = st.checkbox(
                    "CGTMSE Guarantee Covered",
                    value=bool(st.session_state.facility_cgtmse_covered),
                    key="form_cgtmse_covered",
                    help="Credit Guarantee Fund Trust for Micro and Small Enterprises coverage active.",
                )

            with d_col2:
                dlg_rate = st.slider(
                    "Policy Lending Rate (%)",
                    min_value=7.50,
                    max_value=24.00,
                    value=float(st.session_state.facility_interest_rate),
                    step=0.25,
                    key="form_interest_rate",
                    help=f"Derived from Bank Risk Policy matrix ({p_rate:.2f}% benchmark). Adjust if discretionary credit committee concession is sanctioned.",
                )
                dlg_collat = st.number_input(
                    "Pledged Tangible Collateral (₹)",
                    min_value=0.0,
                    value=float(st.session_state.facility_collateral_val),
                    step=100000.0,
                    format="%.2f",
                    key="form_collateral_val",
                    help="Immovable property, fixed assets, or liquid collateral pledged.",
                )
                if dlg_cgtmse:
                    st.caption(":material/verified_user: CGTMSE Trust Cover Active: Credit guarantee covers eligible debt.")

            st.markdown("<hr style='margin: 12px 0 16px 0; border: none; border-top: 1px solid #E2E8F0;' />", unsafe_allow_html=True)

            b1, b2, b3 = st.columns([1.1, 1.1, 1.3])
            with b1:
                if st.button(":material/percent: Apply Discretionary Concession (-0.50%)", key="btn_concession_quick", width="stretch", help="Apply 50 bps discretionary credit committee concession"):
                    new_c_rate = max(7.5, float(st.session_state.facility_interest_rate) - 0.50)
                    st.session_state.facility_interest_rate = new_c_rate
                    st.session_state.interest_rate = new_c_rate
                    st.rerun()

            with b2:
                if st.button(":material/restart_alt: Reset to Policy Standard", key="btn_reset_policy_terms_dlg", width="stretch", help="Reset facility terms to standard policy benchmark"):
                    reset_facility_to_defaults()
                    st.rerun()

            with b3:
                if st.button(":material/save: Apply & Recalculate Terms", key="btn_apply_facility_terms", type="primary", width="stretch", help="Commit updated facility terms and recalculate underwriting metrics"):
                    st.session_state.facility_loan_amount = float(dlg_loan)
                    st.session_state.facility_tenure_months = int(dlg_tenure)
                    st.session_state.facility_interest_rate = float(dlg_rate)
                    st.session_state.facility_collateral_val = float(dlg_collat)
                    st.session_state.facility_cgtmse_covered = bool(dlg_cgtmse)
                    st.session_state.loan_amount = float(dlg_loan)
                    st.session_state.tenure_months = int(dlg_tenure)
                    st.session_state.interest_rate = float(dlg_rate)
                    st.session_state.collateral_value = float(dlg_collat)
                    st.session_state.cgtmse_covered = bool(dlg_cgtmse)
                    st.rerun()

        calib_btn_label = "Calibrate Facility Covenants & Pricing"
        if bool(st.session_state.facility_cgtmse_covered):
            calib_btn_label = "Calibrate Facility Covenants & Pricing (CGTMSE Active)"
        if st.button(calib_btn_label, key="btn_open_calibration_dialog", width="stretch", help="Open Credit Committee facility terms & discretionary pricing calibration drawer"):
            show_facility_calibration_dialog(policy_rate)

# ----------------------------------------------------------------------------
# 3. ONBOARDING AUDIT & NAVIGATION
# ----------------------------------------------------------------------------
st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
if st.button("Proceed to Evidence Ingestion →", type="primary", key="btn_next_evidence", width="stretch"):
    record_event(
        event_type="ONBOARDING_VERIFIED",
        actor="OFFICER",
        details={
            "cin": cin_parsed_data.get("cin") if (cin_parsed_data and isinstance(cin_parsed_data, dict) and "cin" in cin_parsed_data) else (ctx.get("cin_input") or "UNKNOWN"),
            "pan": pan_input,
            "sector": cin_parsed_data.get("macro_sector", "Unknown") if (cin_parsed_data and isinstance(cin_parsed_data, dict)) else "Unknown",
            "vintage_years": cin_parsed_data.get("business_vintage_years", 0) if (cin_parsed_data and isinstance(cin_parsed_data, dict)) else 0,
            "loan_amount": str(loan_amount),
        }
    )
    st.session_state.current_page = "Evidence"
    st.switch_page("app_pages/evidence.py")
